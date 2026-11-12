"""AofWriter.rotate: 락 안에서 파일 I/O 없이 버퍼만 바꿔 끼우는 회전이 맞는지 확인해요.

time.sleep은 쓰지 않아요. flush()를 직접 부르고, 실패는 가짜 open으로 만들어요.
"""

import builtins
import os

import pytest

import mini_redis.persistence.aof as aof_module
from mini_redis.persistence.aof import AofBrokenError, AofWriter, encode_record, set_record

HUGE_INTERVAL_S = 3600

REC_A = set_record("a", "1", None)
REC_B = set_record("b", "2", None)
REC_C = set_record("c", "3", None)


def line(record):
    return encode_record(record).encode("utf-8")


def read_bytes(path):
    return path.read_bytes() if path.exists() else b""


class _ProxyFile:
    """진짜 파일을 감싸서 필요한 메서드만 바꿔 끼우는 가짜 파일이에요."""

    def __init__(self, real, on_write=None, close_error=None):
        self._real = real
        self._on_write = on_write
        self._close_error = close_error

    def write(self, data):
        if self._on_write is not None:
            self._on_write()
        return self._real.write(data)

    def close(self):
        self._real.close()
        if self._close_error is not None:
            raise self._close_error

    def __getattr__(self, name):
        return getattr(self._real, name)


class FakeOpen:
    """경로별로 open 실패, write 중 훅, close 실패를 걸 수 있는 가짜 open이에요."""

    def __init__(self):
        self.real_open = builtins.open
        self.open_fail = set()  # open 자체가 OSError
        self.close_fail = set()  # 다 쓴 뒤 close만 OSError
        self.write_hook = {}  # 경로 -> write 직전에 부를 함수

    def __call__(self, path, *args, **kwargs):
        key = os.fspath(path)
        if key in self.open_fail:
            raise OSError("open 실패")
        real = self.real_open(path, *args, **kwargs)
        hook = self.write_hook.get(key)
        close_error = OSError("close 실패") if key in self.close_fail else None
        if hook is not None or close_error is not None:
            return _ProxyFile(real, on_write=hook, close_error=close_error)
        return real

    def clear(self):
        self.open_fail.clear()
        self.close_fail.clear()
        self.write_hook.clear()


@pytest.fixture
def fake_open(monkeypatch):
    fake = FakeOpen()
    monkeypatch.setattr(aof_module, "open", fake, raising=False)
    return fake


def test_회전_전에_적은_줄은_옛_파일에_회전_뒤에_적은_줄은_새_파일에_적히고_2를_돌려준다(tmp_path):
    p1, p2 = tmp_path / "one.aof", tmp_path / "two.aof"
    writer = AofWriter(p1, interval_s=HUGE_INTERVAL_S)
    writer.append(REC_A)
    writer.rotate(p2)
    writer.append(REC_B)
    assert writer.flush() == 2
    assert read_bytes(p1) == line(REC_A)
    assert read_bytes(p2) == line(REC_B)


def test_버퍼가_빈_채로_회전하면_옛_파일에_쓸_배치가_생기지_않고_새_경로에만_적힌다(tmp_path):
    p1, p2 = tmp_path / "one.aof", tmp_path / "two.aof"
    writer = AofWriter(p1, interval_s=HUGE_INTERVAL_S)
    writer.rotate(p2)
    assert writer._pending == []
    writer.append(REC_A)
    assert writer.flush() == 1
    assert not p1.exists()
    assert read_bytes(p2) == line(REC_A)


def test_회전은_파일_IO를_하지_않는다(tmp_path, monkeypatch):
    p1, p2 = tmp_path / "one.aof", tmp_path / "two.aof"
    writer = AofWriter(p1, interval_s=HUGE_INTERVAL_S)

    def boom_open(*args, **kwargs):
        raise AssertionError("회전 중에 open을 부르면 안 돼요")

    def boom_fsync(fd):
        raise AssertionError("회전 중에 fsync를 부르면 안 돼요")

    monkeypatch.setattr(aof_module, "open", boom_open, raising=False)
    monkeypatch.setattr(os, "fsync", boom_fsync)
    writer.append(REC_A)
    writer.rotate(p2)
    assert writer.path == p2
    assert not p1.exists()
    assert not p2.exists()


def test_current_bytes는_지금_파일에_fsync까지_끝난_바이트이고_회전하면_0으로_돌아간다(tmp_path):
    p1, p2 = tmp_path / "one.aof", tmp_path / "two.aof"
    writer = AofWriter(p1, interval_s=HUGE_INTERVAL_S)
    assert writer.current_bytes == 0
    writer.append(REC_A)
    assert writer.current_bytes == 0  # 아직 flush 전이에요.
    writer.flush()
    assert writer.current_bytes == len(line(REC_A))
    writer.rotate(p2)
    assert writer.current_bytes == 0
    writer.append(REC_B)
    writer.flush()
    assert writer.current_bytes == len(line(REC_B)) == p2.stat().st_size


def test_옛_파일에_쓰기가_실패하면_새_파일은_시도하지_않고_다음_flush가_둘_다_순서대로_적는다(tmp_path, fake_open):
    p1, p2 = tmp_path / "one.aof", tmp_path / "two.aof"
    writer = AofWriter(p1, interval_s=HUGE_INTERVAL_S)
    fake_open.open_fail.add(os.fspath(p1))
    writer.append(REC_A)
    writer.rotate(p2)
    writer.append(REC_B)
    with pytest.raises(OSError):
        writer.flush()
    assert read_bytes(p1) == b""
    assert not p2.exists()  # p2는 시도조차 하지 않았어요.
    fake_open.clear()
    assert writer.flush() == 2
    assert read_bytes(p1) == line(REC_A)
    assert read_bytes(p2) == line(REC_B)


def test_옛_파일은_성공하고_새_파일이_실패하면_옛_줄은_다시_쓰지_않고_새_줄만_다음에_적는다(tmp_path, fake_open):
    p1, p2 = tmp_path / "one.aof", tmp_path / "two.aof"
    writer = AofWriter(p1, interval_s=HUGE_INTERVAL_S)
    fake_open.open_fail.add(os.fspath(p2))
    writer.append(REC_A)
    writer.rotate(p2)
    writer.append(REC_B)
    with pytest.raises(OSError):
        writer.flush()
    assert read_bytes(p1) == line(REC_A)
    assert not p2.exists()
    fake_open.clear()
    assert writer.flush() == 1
    assert read_bytes(p1) == line(REC_A)  # A가 정확히 한 번이에요.
    assert read_bytes(p2) == line(REC_B)


def test_실패한_flush_도중_회전이_일어나도_되돌린_줄은_제자리로_가고_중복되지_않는다(tmp_path, fake_open):
    p2, p3 = tmp_path / "two.aof", tmp_path / "three.aof"
    writer = AofWriter(p2, interval_s=HUGE_INTERVAL_S)
    writer.append(REC_B)

    def rotate_and_fail():
        # flush가 p2에 쓰는 도중 다른 스레드가 회전하고 새 줄을 더한 상황이에요.
        writer.rotate(p3)
        writer.append(REC_C)
        raise OSError("write 실패")

    fake_open.write_hook[os.fspath(p2)] = rotate_and_fail
    with pytest.raises(OSError):
        writer.flush()
    fake_open.clear()
    assert writer.flush() == 2
    assert read_bytes(p2) == line(REC_B)
    assert read_bytes(p3) == line(REC_C)
    assert writer.flush() == 0


def test_옛_파일_배치가_close에서만_실패하면_다시_넣지_않고_새_파일_배치는_다음_flush가_적는다(tmp_path, fake_open):
    p1, p2 = tmp_path / "one.aof", tmp_path / "two.aof"
    writer = AofWriter(p1, interval_s=HUGE_INTERVAL_S)
    fake_open.close_fail.add(os.fspath(p1))
    writer.append(REC_A)
    writer.rotate(p2)
    writer.append(REC_B)
    with pytest.raises(OSError):
        writer.flush()
    assert read_bytes(p1) == line(REC_A)  # fsync까지 끝나서 디스크에 이미 있어요.
    assert not p2.exists()
    fake_open.clear()
    assert writer.flush() == 1
    assert read_bytes(p1) == line(REC_A)  # 다시 적히지 않았어요.
    assert read_bytes(p2) == line(REC_B)


def test_drop_pending은_회전으로_밀린_줄을_버리고_그_줄_수를_돌려준다(tmp_path):
    p1, p2 = tmp_path / "one.aof", tmp_path / "two.aof"
    writer = AofWriter(p1, interval_s=HUGE_INTERVAL_S)
    writer.append(REC_A)
    writer.rotate(p2)
    assert writer.drop_pending() == 1
    assert writer.flush() == 0
    assert not p1.exists()
    assert not p2.exists()


def test_깨진_writer에_회전을_부르면_AofBrokenError가_난다(tmp_path):
    writer = AofWriter(tmp_path / "one.aof", interval_s=HUGE_INTERVAL_S)
    writer.broken = True
    with pytest.raises(AofBrokenError):
        writer.rotate(tmp_path / "two.aof")
