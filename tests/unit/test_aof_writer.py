"""AofWriter(메모리 버퍼 + 1초 flush 스레드) 단위 테스트.

time.sleep은 쓰지 않아요. flush()를 직접 부르고, 스레드 테스트는 interval_s를 아주 크게 잡아
루프가 혼자 돌지 않게 한 뒤 stop()에 기대요.
"""

import builtins
import os
import threading

import mini_redis.persistence.aof as aof_module
from mini_redis.persistence.aof import (
    AofWriter,
    decode_line,
    del_record,
    encode_record,
    set_record,
)

HUGE_INTERVAL_S = 3600

# CP1 표: 가짜 now = 1_000_000
NOW_MS = 1_000_000
CP1_RECORDS = [
    set_record("a", "1", None),
    set_record("b", "2", NOW_MS + 10_000),
    del_record("a"),
]


def expected_bytes(records):
    return b"".join(encode_record(r).encode("utf-8") for r in records)


def read_lines(path):
    return path.read_bytes().decode("utf-8").splitlines()


# 항목 1
def test_AofWriter_생성만_하면_파일을_만들지_않는다(tmp_path):
    path = tmp_path / "appendonly.aof"
    AofWriter(path)
    assert not path.exists()


# 항목 2
def test_append_세_번_불러도_파일은_아직_없다(tmp_path):
    path = tmp_path / "appendonly.aof"
    writer = AofWriter(path)
    for record in CP1_RECORDS:
        writer.append(record)
    assert not path.exists()


# 항목 3
def test_flush_한_번이면_세_줄이_순서대로_파일에_적히고_3을_돌려준다(tmp_path):
    path = tmp_path / "appendonly.aof"
    writer = AofWriter(path)
    for record in CP1_RECORDS:
        writer.append(record)
    assert writer.flush() == 3
    assert path.read_bytes() == expected_bytes(CP1_RECORDS)


# 항목 4
def test_flush_추가된_줄이_없으면_0을_돌려주고_파일이_그대로다(tmp_path):
    path = tmp_path / "appendonly.aof"
    writer = AofWriter(path)
    for record in CP1_RECORDS:
        writer.append(record)
    writer.flush()
    before = path.read_bytes()
    size_before = path.stat().st_size
    assert writer.flush() == 0
    assert path.read_bytes() == before
    assert path.stat().st_size == size_before


# 항목 5
def test_flush_두_번_나눠_해도_모든_줄이_순서대로_남는다(tmp_path):
    path = tmp_path / "appendonly.aof"
    writer = AofWriter(path)
    writer.append(CP1_RECORDS[0])
    writer.append(CP1_RECORDS[1])
    writer.flush()
    writer.append(CP1_RECORDS[2])
    writer.flush()
    assert path.read_bytes() == expected_bytes(CP1_RECORDS)


# 항목 6
def test_파일_내용은_줄바꿈이_LF뿐이고_CRLF가_없다(tmp_path):
    path = tmp_path / "appendonly.aof"
    writer = AofWriter(path)
    for record in CP1_RECORDS:
        writer.append(record)
    writer.flush()
    raw = path.read_bytes()
    assert b"\r\n" not in raw
    assert raw.count(b"\n") == 3


# 항목 7
def test_한글_값은_UTF8로_적히고_디코딩하면_되돌아온다(tmp_path):
    path = tmp_path / "appendonly.aof"
    writer = AofWriter(path)
    records = [set_record("이름", "상철님", None), set_record("k", "안녕 세계", 5000)]
    for record in records:
        writer.append(record)
    writer.flush()
    text = path.read_bytes().decode("utf-8")  # UTF-8이 아니면 여기서 예외
    assert [decode_line(line) for line in text.splitlines()] == records


# 항목 8
def test_파일의_줄을_디코딩하면_append한_레코드와_같다(tmp_path):
    path = tmp_path / "appendonly.aof"
    writer = AofWriter(path)
    for record in CP1_RECORDS:
        writer.append(record)
    writer.flush()
    assert [decode_line(line) for line in read_lines(path)] == CP1_RECORDS


# 항목 9
def test_start_append_stop_이면_줄이_파일에_있고_스레드가_끝난다(tmp_path):
    path = tmp_path / "appendonly.aof"
    threads_before = threading.active_count()
    writer = AofWriter(path, interval_s=HUGE_INTERVAL_S)
    writer.start()
    assert threading.active_count() == threads_before + 1
    writer.append(CP1_RECORDS[0])
    writer.stop()
    assert path.read_bytes() == expected_bytes([CP1_RECORDS[0]])
    assert threading.active_count() == threads_before


# 항목 10
def test_stop_start_없이_불러도_남은_줄을_flush하고_예외가_없다(tmp_path):
    path = tmp_path / "appendonly.aof"
    writer = AofWriter(path, interval_s=HUGE_INTERVAL_S)
    for record in CP1_RECORDS:
        writer.append(record)
    writer.stop()
    assert path.read_bytes() == expected_bytes(CP1_RECORDS)


# 항목 11
def test_start_두_번_불러도_스레드는_하나고_stop_뒤_원래_개수로_돌아온다(tmp_path):
    path = tmp_path / "appendonly.aof"
    threads_before = threading.active_count()
    writer = AofWriter(path, interval_s=HUGE_INTERVAL_S)
    writer.start()
    writer.start()
    assert threading.active_count() == threads_before + 1
    writer.stop()
    assert threading.active_count() == threads_before


# 항목 12
def test_여러_스레드가_append하며_flush해도_1000줄이_스레드별_순서대로_남는다(tmp_path):
    path = tmp_path / "appendonly.aof"
    writer = AofWriter(path)
    thread_count = 4
    per_thread = 250

    def appender(thread_id):
        for i in range(per_thread):
            writer.append(set_record(f"t{thread_id}-{i}", str(i), None))

    threads = [threading.Thread(target=appender, args=(t,)) for t in range(thread_count)]
    for t in threads:
        t.start()
    while any(t.is_alive() for t in threads):
        writer.flush()
    for t in threads:
        t.join()
    writer.flush()

    lines = read_lines(path)
    assert len(lines) == thread_count * per_thread
    records = [decode_line(line) for line in lines]  # 하나라도 깨졌으면 ValueError

    seen = {t: [] for t in range(thread_count)}
    for record in records:
        thread_part, index_part = record["k"].split("-")
        seen[int(thread_part[1:])].append(int(index_part))
    for t in range(thread_count):
        assert seen[t] == list(range(per_thread))


# 항목 13
def test_flush_open이_한_번_실패하면_줄이_버퍼에_남아_다음_flush에_순서대로_적힌다(tmp_path, monkeypatch):
    path = tmp_path / "appendonly.aof"
    writer = AofWriter(path)
    real_open = builtins.open
    error = PermissionError("잠시 막혔어요")
    calls = []

    def flaky_open(*args, **kwargs):
        if not calls:
            calls.append(1)
            raise error
        return real_open(*args, **kwargs)

    monkeypatch.setattr(aof_module, "open", flaky_open, raising=False)
    a = set_record("a", "1", None)
    b = set_record("b", "2", None)
    writer.append(a)
    try:
        writer.flush()
        raised = None
    except OSError as e:
        raised = e
    assert raised is error
    assert writer.last_error is error
    writer.append(b)
    assert writer.flush() == 2
    assert [decode_line(line) for line in read_lines(path)] == [a, b]
    assert writer.last_error is None


# 항목 14
def test_flush_fsync가_쓰기_뒤에_실패해도_다음_flush에_각_줄이_정확히_한_번씩만_남는다(tmp_path, monkeypatch):
    path = tmp_path / "appendonly.aof"
    writer = AofWriter(path)
    real_fsync = os.fsync
    calls = []

    def flaky_fsync(fd):
        if not calls:
            calls.append(1)
            raise OSError("fsync 실패")
        return real_fsync(fd)

    monkeypatch.setattr(aof_module.os, "fsync", flaky_fsync)
    a = set_record("a", "1", None)
    b = set_record("b", "2", None)
    writer.append(a)
    writer.append(b)
    try:
        writer.flush()
        raised = None
    except OSError as e:
        raised = e
    assert raised is not None
    assert writer.last_error is raised
    assert writer.flush() == 2
    assert path.read_bytes() == expected_bytes([a, b])
    assert writer.last_error is None


# 항목 15
def test_stop_flush가_실패한_뒤에_불러도_실패했던_줄이_파일에_남는다(tmp_path, monkeypatch):
    path = tmp_path / "appendonly.aof"
    writer = AofWriter(path, interval_s=HUGE_INTERVAL_S)
    real_open = builtins.open
    calls = []

    def flaky_open(*args, **kwargs):
        if not calls:
            calls.append(1)
            raise PermissionError("잠시 막혔어요")
        return real_open(*args, **kwargs)

    monkeypatch.setattr(aof_module, "open", flaky_open, raising=False)
    record = set_record("a", "1", None)
    writer.append(record)
    try:
        writer.flush()
    except OSError:
        pass
    writer.stop()
    assert path.read_bytes() == expected_bytes([record])


# 항목 16
def test_flush_fsync가_정말_불리고_빈_flush는_더_부르지_않는다(tmp_path, monkeypatch):
    path = tmp_path / "appendonly.aof"
    writer = AofWriter(path)
    real_fsync = os.fsync
    calls = []

    def counting_fsync(fd):
        calls.append(fd)
        return real_fsync(fd)

    monkeypatch.setattr(aof_module.os, "fsync", counting_fsync)
    writer.append(CP1_RECORDS[0])
    assert writer.flush() == 1
    assert len(calls) == 1
    assert writer.flush() == 0
    assert len(calls) == 1


# 항목 17
def test_stop_없이도_interval마다_flush가_돌아_줄이_파일에_적힌다(tmp_path, monkeypatch):
    path = tmp_path / "appendonly.aof"
    writer = AofWriter(path, interval_s=0.01)
    real_fsync = os.fsync
    done = threading.Event()

    def signaling_fsync(fd):
        real_fsync(fd)
        done.set()

    monkeypatch.setattr(aof_module.os, "fsync", signaling_fsync)
    writer.append(CP1_RECORDS[0])
    writer.start()
    try:
        assert done.wait(5) is True
        assert path.read_bytes() == expected_bytes([CP1_RECORDS[0]])
        assert writer._thread.is_alive()
    finally:
        writer.stop()


# 항목 18
def test_백그라운드_스레드는_flush가_실패해도_살아남아_다음_주기에_줄을_적는다(tmp_path, monkeypatch):
    path = tmp_path / "appendonly.aof"
    writer = AofWriter(path, interval_s=0.01)
    real_fsync = os.fsync
    calls = []
    done = threading.Event()

    def flaky_fsync(fd):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("fsync 실패")
        real_fsync(fd)
        done.set()

    monkeypatch.setattr(aof_module.os, "fsync", flaky_fsync)
    writer.append(CP1_RECORDS[0])
    writer.start()
    try:
        assert done.wait(5) is True
        assert writer._thread.is_alive()
        assert path.read_bytes() == expected_bytes([CP1_RECORDS[0]])
    finally:
        writer.stop()


# 항목 19
def test_flush_한_쪽이_쓰기_락_안에서_멈춰_있어도_1000줄이_스레드별_순서대로_남는다(tmp_path, monkeypatch):
    # 한 flush가 fsync에서 멈춘 동안(=쓰기 락을 쥔 채) 다른 스레드가 append·flush를 계속해도
    # 줄이 섞이거나 사라지지 않는지 봐요. 멈추는 지점은 Event로 고정해서 sleep이 필요 없어요.
    path = tmp_path / "appendonly.aof"
    writer = AofWriter(path)
    appender_count = 4
    half = 125
    in_fsync = threading.Event()
    release = threading.Event()
    phase2 = threading.Event()
    barrier = threading.Barrier(appender_count + 1)
    errors = []
    results = {}
    real_fsync = os.fsync
    fsync_calls = []

    def gated_fsync(fd):
        fsync_calls.append(1)
        if len(fsync_calls) == 1:
            in_fsync.set()
            assert release.wait(5) is True
        return real_fsync(fd)

    monkeypatch.setattr(aof_module.os, "fsync", gated_fsync)

    def guarded(fn):
        try:
            fn()
        except BaseException as e:  # 스레드 안의 예외를 테스트가 볼 수 있게 모아요
            errors.append(e)

    def appender(thread_id):
        for i in range(half):
            writer.append(set_record(f"t{thread_id}-{i}", str(i), None))
        barrier.wait(5)
        assert phase2.wait(5) is True
        for i in range(half, 2 * half):
            writer.append(set_record(f"t{thread_id}-{i}", str(i), None))

    def flusher(name):
        results[name] = writer.flush()

    appenders = [
        threading.Thread(target=guarded, args=(lambda t=t: appender(t),))
        for t in range(appender_count)
    ]
    flusher_a = threading.Thread(target=guarded, args=(lambda: flusher("A"),))
    flusher_b = threading.Thread(target=guarded, args=(lambda: flusher("B"),))
    try:
        for t in appenders:
            t.start()
        barrier.wait(5)  # 네 스레드가 앞 125개씩(500줄)을 다 넣었어요
        flusher_a.start()
        assert in_fsync.wait(5) is True  # A가 500줄을 쥐고 락 안 fsync에서 멈췄어요
        phase2.set()
        flusher_b.start()
        for t in appenders:
            t.join(timeout=5)
            assert not t.is_alive()
    finally:
        release.set()
    for t in (flusher_a, flusher_b):
        t.join(timeout=5)
        assert not t.is_alive()
    last = writer.flush()

    assert errors == []
    assert results["A"] == 500
    assert results["B"] + last == 500
    lines = read_lines(path)
    assert len(lines) == 1000
    seen = {t: [] for t in range(appender_count)}
    for line in lines:
        record = decode_line(line)
        thread_part, index_part = record["k"].split("-")
        seen[int(thread_part[1:])].append(int(index_part))
    for t in range(appender_count):
        assert seen[t] == list(range(2 * half))


# 항목 20
def test_append_인코딩할_수_없는_값은_예외를_내고_앞서_넣은_줄은_잃지_않는다(tmp_path):
    path = tmp_path / "appendonly.aof"
    writer = AofWriter(path)
    good = set_record("good", "1")
    writer.append(good)
    try:
        writer.append(set_record("bad", "\ud800"))
        raised = None
    except UnicodeEncodeError as e:
        raised = e
    assert raised is not None
    assert isinstance(raised, ValueError)
    assert writer.flush() == 1
    assert path.read_bytes() == expected_bytes([good])
    assert writer.last_error is None


class _TruncateFailsFile:
    """진짜 파일을 감싸고 truncate만 실패시키는 얇은 래퍼예요."""

    def __init__(self, real_file, truncate_error):
        self._real = real_file
        self._truncate_error = truncate_error

    def truncate(self, *args, **kwargs):
        raise self._truncate_error

    def write(self, *args, **kwargs):
        return self._real.write(*args, **kwargs)

    def flush(self):
        return self._real.flush()

    def fileno(self):
        return self._real.fileno()

    def tell(self):
        return self._real.tell()

    def close(self):
        return self._real.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return self._real.__exit__(*exc_info)


def _make_broken_writer(path, monkeypatch, interval_s=HUGE_INTERVAL_S):
    """fsync도 truncate도 실패하게 만들어 broken 상태의 writer를 돌려줘요."""
    writer = AofWriter(path, interval_s=interval_s)
    real_open = builtins.open
    fsync_error = OSError("fsync 실패")
    truncate_error = OSError("truncate 실패")

    def fake_open(*args, **kwargs):
        return _TruncateFailsFile(real_open(*args, **kwargs), truncate_error)

    def failing_fsync(fd):
        raise fsync_error

    monkeypatch.setattr(aof_module, "open", fake_open, raising=False)
    monkeypatch.setattr(aof_module.os, "fsync", failing_fsync)
    writer.append(set_record("a", "1", None))
    try:
        writer.flush()
        raised = None
    except OSError as e:
        raised = e
    return writer, raised, fsync_error, truncate_error


# 항목 21
def test_flush_되돌리기까지_실패하면_broken이_되고_원래_예외와_되돌리기_예외를_따로_남긴다(tmp_path, monkeypatch):
    path = tmp_path / "appendonly.aof"
    writer, raised, fsync_error, truncate_error = _make_broken_writer(path, monkeypatch)
    assert raised is fsync_error
    assert writer.broken is True
    assert writer.last_error is fsync_error
    assert writer.rollback_error is truncate_error


# 항목 22
def test_broken_writer는_flush가_파일을_열지_않고_AofBrokenError를_내며_append도_막는다(tmp_path, monkeypatch):
    path = tmp_path / "appendonly.aof"
    writer, _, _, _ = _make_broken_writer(path, monkeypatch)
    monkeypatch.undo()  # 진짜 open과 진짜 fsync로 되돌려요
    real_open = builtins.open
    open_calls = []

    def counting_open(*args, **kwargs):
        open_calls.append(1)
        return real_open(*args, **kwargs)

    monkeypatch.setattr(aof_module, "open", counting_open, raising=False)
    before = path.read_bytes()
    try:
        writer.flush()
        flush_raised = None
    except aof_module.AofBrokenError as e:
        flush_raised = e
    assert flush_raised is not None
    assert open_calls == []
    assert path.read_bytes() == before
    try:
        writer.append(set_record("x", "1", None))
        append_raised = None
    except aof_module.AofBrokenError as e:
        append_raised = e
    assert append_raised is not None


# 항목 23
def test_broken_writer의_백그라운드_스레드는_예외를_삼키고_살아있다가_stop에서_AofBrokenError를_낸다(tmp_path, monkeypatch):
    path = tmp_path / "appendonly.aof"
    writer, _, _, _ = _make_broken_writer(path, monkeypatch, interval_s=0.01)
    monkeypatch.undo()
    original_flush = writer.flush
    calls = []
    reached_two = threading.Event()

    def counting_flush():
        calls.append(1)
        if len(calls) >= 2:
            reached_two.set()
        return original_flush()

    writer.flush = counting_flush
    writer.start()
    thread = writer._thread  # stop()이 _thread를 None으로 비우기 전에 잡아 둬요.
    try:
        assert reached_two.wait(5) is True
        assert thread.is_alive()
    finally:
        try:
            writer.stop()
            stop_raised = None
        except aof_module.AofBrokenError as e:
            stop_raised = e
    assert stop_raised is not None
    assert not thread.is_alive()


# 항목 24
def test_flush_되돌리기가_성공한_실패는_broken이_아니고_다음_flush에_각_줄이_한_번씩만_남는다(tmp_path, monkeypatch):
    path = tmp_path / "appendonly.aof"
    writer = AofWriter(path)
    real_fsync = os.fsync
    calls = []

    def flaky_fsync(fd):
        if not calls:
            calls.append(1)
            raise OSError("fsync 실패")
        return real_fsync(fd)

    monkeypatch.setattr(aof_module.os, "fsync", flaky_fsync)
    a = set_record("a", "1", None)
    b = set_record("b", "2", None)
    writer.append(a)
    writer.append(b)
    try:
        writer.flush()
        raised = None
    except OSError as e:
        raised = e
    assert raised is not None
    assert writer.broken is False
    assert writer.rollback_error is None
    assert writer.flush() == 2
    assert path.read_bytes() == expected_bytes([a, b])


class _EverythingFailsFile:
    """write·truncate·close가 모두 각자 다른 예외를 내는 가짜 파일이에요. __exit__는 close를 불러요."""

    def __init__(self, write_error, truncate_error, close_error):
        self._write_error = write_error
        self._truncate_error = truncate_error
        self._close_error = close_error

    def tell(self):
        return 0

    def write(self, *args, **kwargs):
        raise self._write_error

    def flush(self):
        return None

    def fileno(self):
        return 0

    def truncate(self, *args, **kwargs):
        raise self._truncate_error

    def close(self):
        raise self._close_error

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        self.close()
        return False


# 항목 25
def test_flush_쓰기_되돌리기_닫기가_모두_실패해도_처음_쓰기_예외가_올라오고_들고_나간_줄은_버퍼에_남는다(tmp_path, monkeypatch):
    # close 예외가 with의 __exit__에서 먼저 터져도 처음 실패(A)가 가려지면 안 돼요.
    path = tmp_path / "appendonly.aof"
    writer = AofWriter(path)
    write_error = OSError("write 실패")
    truncate_error = OSError("truncate 실패")
    close_error = OSError("close 실패")

    def fake_open(*args, **kwargs):
        return _EverythingFailsFile(write_error, truncate_error, close_error)

    monkeypatch.setattr(aof_module, "open", fake_open, raising=False)
    record = set_record("a", "1", None)
    writer.append(record)
    try:
        writer.flush()
        raised = None
    except OSError as e:
        raised = e
    assert raised is write_error
    assert writer.last_error is write_error
    assert writer.rollback_error is truncate_error
    assert writer.broken is True
    assert writer._buffer == [encode_record(record).encode("utf-8")]


# 항목 26
def test_flush_되돌리기까지_실패해도_들고_나간_줄이_append_순서대로_버퍼에_그대로_남는다(tmp_path, monkeypatch):
    path = tmp_path / "appendonly.aof"
    writer = AofWriter(path, interval_s=HUGE_INTERVAL_S)
    # 항목 21의 broken 경로를 만들되, 줄이 둘 이상이어야 순서를 볼 수 있어서 직접 더 넣어요.
    real_open = builtins.open

    def fake_open(*args, **kwargs):
        return _TruncateFailsFile(real_open(*args, **kwargs), OSError("truncate 실패"))

    def failing_fsync(fd):
        raise OSError("fsync 실패")

    monkeypatch.setattr(aof_module, "open", fake_open, raising=False)
    monkeypatch.setattr(aof_module.os, "fsync", failing_fsync)
    first = set_record("a", "1", None)
    second = set_record("b", "2", None)
    writer.append(first)
    writer.append(second)
    try:
        writer.flush()
        raised = None
    except OSError as e:
        raised = e
    assert raised is not None
    assert writer.broken is True
    assert writer._buffer == [
        encode_record(first).encode("utf-8"),
        encode_record(second).encode("utf-8"),
    ]
