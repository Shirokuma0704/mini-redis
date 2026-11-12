"""디스크 쓰기가 계속 실패할 때 AofWriter가 몇 초 뒤 쓰기를 거절하는지 확인해요 (#12, CP4 K2).

time.sleep은 쓰지 않아요. 단조 시계는 가짜로 바꿔 끼우고, 디스크 실패는 가짜 open으로 만들어요.
"""

import builtins

import pytest
from fastapi.testclient import TestClient

import mini_redis.persistence.aof as aof_module
from mini_redis import config
from mini_redis.api.app import create_app
from mini_redis.clock import FakeClock
from mini_redis.persistence.aof import (
    AofBrokenError,
    AofWriter,
    decode_line,
    set_record,
)
from mini_redis.store import Store

HUGE_INTERVAL_S = 3600
T0 = 1_000_000


class FakeMonotonic:
    """부르면 지금 값을 돌려주고, advance로 시간을 앞으로 보내는 가짜 단조 시계예요."""

    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class _CloseFailsFile:
    """write, flush, fsync는 진짜로 하고 close만 실패시켜요. 줄은 디스크에 남아요."""

    def __init__(self, real_file, close_error):
        self._real = real_file
        self._close_error = close_error

    def tell(self):
        return self._real.tell()

    def write(self, *args, **kwargs):
        return self._real.write(*args, **kwargs)

    def flush(self):
        return self._real.flush()

    def fileno(self):
        return self._real.fileno()

    def truncate(self, *args, **kwargs):
        return self._real.truncate(*args, **kwargs)

    def close(self):
        self._real.close()
        raise self._close_error


def _fail_open_for(monkeypatch, path, error):
    """AOF 경로를 열려고 하면 error를 던지고, 다른 경로는 진짜 open으로 열어요."""
    real_open = builtins.open

    def fake_open(file, *args, **kwargs):
        if str(file) == str(path):
            raise error
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(aof_module, "open", fake_open, raising=False)


def _make_writer(path, mono):
    return AofWriter(path, interval_s=HUGE_INTERVAL_S, reject_after_s=2, monotonic=mono)


def _try_flush(writer):
    try:
        writer.flush()
    except OSError:
        pass


def _read_records(path):
    return [decode_line(line) for line in path.read_bytes().decode("utf-8").splitlines()]


def test_config_AOF_REJECT_AFTER_S_기본값은_2초다():
    assert config.AOF_REJECT_AFTER_S == 2


def test_append_첫_실패_시각이_failing_since에_남고_2초가_되는_순간부터_거절한다(tmp_path, monkeypatch):
    path = tmp_path / "appendonly.aof"
    mono = FakeMonotonic(100.0)
    writer = _make_writer(path, mono)
    assert writer.failing_since is None
    _fail_open_for(monkeypatch, path, OSError("디스크가 막혔어요"))

    from mini_redis.persistence.aof import AofUnavailableError

    writer.append(set_record("a", "1", None))
    _try_flush(writer)
    assert writer.failing_since == 100.0

    mono.advance(1.9)
    writer.append(set_record("b", "2", None))  # 1.9초까지는 아직 받아요
    _try_flush(writer)
    assert writer.failing_since == 100.0  # 이어서 실패해도 처음 시각을 지켜요

    mono.advance(0.1)  # 정확히 +2.0초
    with pytest.raises(AofUnavailableError):
        writer.append(set_record("c", "3", None))


def test_append_저장소가_돌아오면_다음_flush가_failing_since를_지우고_받은_줄이_한_번씩_순서대로_남는다(tmp_path, monkeypatch):
    path = tmp_path / "appendonly.aof"
    mono = FakeMonotonic(100.0)
    writer = _make_writer(path, mono)
    _fail_open_for(monkeypatch, path, OSError("디스크가 막혔어요"))

    from mini_redis.persistence.aof import AofUnavailableError

    a = set_record("a", "1", None)
    b = set_record("b", "2", None)
    writer.append(a)
    _try_flush(writer)
    mono.advance(1.9)
    writer.append(b)
    _try_flush(writer)
    mono.advance(0.1)
    with pytest.raises(AofUnavailableError):
        writer.append(set_record("rejected", "x", None))

    monkeypatch.undo()  # 가짜 open을 걷어내요. 이제 디스크가 멀쩡해요.
    assert writer.flush() == 2
    assert writer.failing_since is None
    c = set_record("c", "3", None)
    writer.append(c)  # 다시 받아요
    assert writer.flush() == 1
    assert _read_records(path) == [a, b, c]  # 거절된 줄은 없고, 받은 줄은 한 번씩이에요


def test_flush_durable_닫기만_실패해도_failing_since가_시작되고_다음_좋은_flush에서_지워진다(tmp_path, monkeypatch):
    path = tmp_path / "appendonly.aof"
    mono = FakeMonotonic(100.0)
    writer = _make_writer(path, mono)
    real_open = builtins.open
    close_error = OSError("close 실패")

    def fake_open(*args, **kwargs):
        return _CloseFailsFile(real_open(*args, **kwargs), close_error)

    monkeypatch.setattr(aof_module, "open", fake_open, raising=False)
    a = set_record("a", "1", None)
    writer.append(a)
    with pytest.raises(OSError):
        writer.flush()
    assert writer.failing_since == 100.0
    assert writer._buffer == []  # 줄은 이미 디스크에 있어서 되돌리지 않아요

    mono.advance(5.0)
    monkeypatch.undo()
    # 버퍼도 대기 중인 줄도 없는 좋은 flush는 끝까지 성공이라 상태를 지워요.
    assert writer.flush() == 0
    assert writer.failing_since is None
    b = set_record("b", "2", None)
    writer.append(b)
    assert writer.flush() == 1
    assert _read_records(path) == [a, b]


def test_append_깨진_writer는_AofBrokenError를_던지고_그건_PersistenceUnavailable이면서_RuntimeError다(tmp_path):
    from mini_redis.errors import PersistenceUnavailable

    writer = AofWriter(tmp_path / "appendonly.aof", interval_s=HUGE_INTERVAL_S)
    writer.broken = True
    with pytest.raises(AofBrokenError) as info:
        writer.append(set_record("a", "1", None))
    assert isinstance(info.value, PersistenceUnavailable)
    assert isinstance(info.value, RuntimeError)


def test_PersistenceUnavailable_상태코드는_503이고_코드는_PERSISTENCE_UNAVAILABLE이다():
    from mini_redis.errors import MiniRedisError, PersistenceUnavailable

    assert issubclass(PersistenceUnavailable, MiniRedisError)
    assert PersistenceUnavailable.status == 503
    assert PersistenceUnavailable.code == "PERSISTENCE_UNAVAILABLE"


def test_AofUnavailableError는_PersistenceUnavailable이다():
    from mini_redis.errors import PersistenceUnavailable
    from mini_redis.persistence.aof import AofUnavailableError

    assert issubclass(AofUnavailableError, PersistenceUnavailable)


def test_PUT_거절_상태의_writer면_503_PERSISTENCE_UNAVAILABLE이고_GET은_404다(tmp_path, monkeypatch):
    path = tmp_path / "appendonly.aof"
    mono = FakeMonotonic(100.0)
    writer = _make_writer(path, mono)
    _fail_open_for(monkeypatch, path, OSError("디스크가 막혔어요"))
    writer.append(set_record("seed", "1", None))
    _try_flush(writer)
    mono.advance(2.0)

    client = TestClient(create_app(Store(FakeClock(T0), aof=writer)), raise_server_exceptions=False)
    r = client.put("/keys/a", json={"value": "1"})
    assert r.status_code == 503
    assert r.json()["error"] == "PERSISTENCE_UNAVAILABLE"
    assert client.get("/keys/a").status_code == 404  # 기록이 거절됐으니 저장되지 않았다


def test_flush_건강한_저장소에서는_여러_번_flush해도_failing_since가_None이다(tmp_path):
    path = tmp_path / "appendonly.aof"
    mono = FakeMonotonic(100.0)
    writer = _make_writer(path, mono)
    for i in range(4):
        mono.advance(10.0)
        writer.append(set_record(f"k{i}", str(i), None))
        assert writer.flush() == 1
        assert writer.failing_since is None
    assert writer.flush() == 0  # 빈 flush도 None을 지켜요
    assert writer.failing_since is None
    assert len(_read_records(path)) == 4
