"""AofWriter(메모리 버퍼 + 1초 flush 스레드) 단위 테스트.

time.sleep은 쓰지 않아요. flush()를 직접 부르고, 스레드 테스트는 interval_s를 아주 크게 잡아
루프가 혼자 돌지 않게 한 뒤 stop()에 기대요.
"""

import threading

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
