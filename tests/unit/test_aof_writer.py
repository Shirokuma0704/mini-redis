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
def test_flush_스레드_둘이_동시에_돌아도_1000줄이_스레드별_순서대로_남는다(tmp_path):
    path = tmp_path / "appendonly.aof"
    writer = AofWriter(path)
    appender_count = 4
    flusher_count = 2
    per_thread = 250
    barrier = threading.Barrier(appender_count + flusher_count)
    appenders_done = threading.Event()

    def appender(thread_id):
        barrier.wait()
        for i in range(per_thread):
            writer.append(set_record(f"t{thread_id}-{i}", str(i), None))

    def flusher():
        barrier.wait()
        while not appenders_done.is_set():
            writer.flush()

    appenders = [threading.Thread(target=appender, args=(t,)) for t in range(appender_count)]
    flushers = [threading.Thread(target=flusher) for _ in range(flusher_count)]
    for t in appenders + flushers:
        t.start()
    for t in appenders:
        t.join()
    appenders_done.set()
    for t in flushers:
        t.join()
    writer.flush()

    lines = read_lines(path)
    assert len(lines) == appender_count * per_thread
    records = [decode_line(line) for line in lines]
    seen = {t: [] for t in range(appender_count)}
    for record in records:
        thread_part, index_part = record["k"].split("-")
        seen[int(thread_part[1:])].append(int(index_part))
    for t in range(appender_count):
        assert seen[t] == list(range(per_thread))
