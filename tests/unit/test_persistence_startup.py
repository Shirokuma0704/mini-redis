"""Persistence.start/stop: 복구 -> 로드 -> AOF 연결 -> 스냅샷 스레드 순서와 종료를 확인한다."""
import os
import pathlib

import pytest

from mini_redis.clock import FakeClock
from mini_redis.errors import KeyNotFound
from mini_redis.persistence.aof import decode_line
from mini_redis.persistence.layout import aof_name
from mini_redis.persistence.manager import Persistence
from mini_redis.persistence.recovery import CorruptAofError
from mini_redis.store import Store

T0 = 1_000_000
IDLE = 3600.0  # 스레드가 테스트 중에 깨어나지 않게 아주 길게 잡는다


@pytest.fixture
def clock():
    return FakeClock(T0)


@pytest.fixture
def make_persistence():
    """Persistence를 만들어 주고, 테스트가 끝나면 전부 stop()한다 (stop은 여러 번 불러도 된다)."""
    created = []

    def factory(data_dir):
        p = Persistence(data_dir, aof_interval_s=IDLE, snapshot_interval_s=IDLE, check_interval_s=IDLE)
        created.append(p)
        return p

    yield factory
    for p in created:
        p.stop()


def read_records(path):
    lines = pathlib.Path(path).read_bytes().decode("utf-8").splitlines(keepends=True)
    return [decode_line(line) for line in lines]


def test_empty_dir_starts_at_aof_1_and_logs_new_writes(tmp_path, clock, make_persistence):
    data_dir = tmp_path / "data"
    store = Store(clock=clock)
    p = make_persistence(data_dir)

    state = p.start(store)
    assert state.next_aof == 1
    assert store.size() == 0

    store.set("k", "v")
    p.stop()

    assert read_records(data_dir / aof_name(1)) == [{"op": "set", "k": "k", "v": "v", "exp": None}]


def test_restart_restores_values_and_ttls_and_moves_to_next_aof(tmp_path, clock, make_persistence):
    exp = T0 + 10_000
    store1 = Store(clock=clock)
    p1 = make_persistence(tmp_path)
    p1.start(store1)
    store1.set("a", "1")
    store1.set("b", "2", exp)
    p1.stop()

    store2 = Store(clock=clock)
    p2 = make_persistence(tmp_path)
    state = p2.start(store2)

    assert state.next_aof == 2
    a, b = store2.get("a"), store2.get("b")
    assert (a.value, a.expire_at_ms) == ("1", None)
    assert (b.value, b.expire_at_ms) == ("2", exp)
    assert pathlib.Path(p2.aof.path) == tmp_path / aof_name(2)


def test_loading_recovered_keys_is_not_logged_again(tmp_path, clock, make_persistence):
    store1 = Store(clock=clock)
    p1 = make_persistence(tmp_path)
    p1.start(store1)
    store1.set("a", "1")
    store1.set("b", "2", T0 + 10_000)
    p1.stop()

    store2 = Store(clock=clock)
    p2 = make_persistence(tmp_path)
    p2.start(store2)
    p2.stop()  # 새 쓰기 없이 종료

    second = tmp_path / aof_name(2)
    assert not second.exists() or second.stat().st_size == 0


def test_recovered_ttl_is_registered_in_timing_wheel(tmp_path, clock, make_persistence):
    exp = T0 + 10_000
    store1 = Store(clock=clock)
    p1 = make_persistence(tmp_path)
    p1.start(store1)
    store1.set("a", "1")
    store1.set("b", "2", exp)
    p1.stop()

    store2 = Store(clock=clock)
    p2 = make_persistence(tmp_path)
    p2.start(store2)
    assert store2.size() == 2

    clock.advance(10_001)
    assert store2.expire_tick() == 1
    assert store2.size() == 1  # b만 빠지고 a는 남는다


def test_key_expired_at_recovery_time_is_not_loaded(tmp_path, clock, make_persistence):
    store1 = Store(clock=clock)
    p1 = make_persistence(tmp_path)
    p1.start(store1)
    store1.set("live", "x")
    store1.set("c", "y", T0 + 1_000)
    p1.stop()

    later_clock = FakeClock(T0 + 2_000)
    store2 = Store(clock=later_clock)
    p2 = make_persistence(tmp_path)
    p2.start(store2)

    with pytest.raises(KeyNotFound):
        store2.get("c")
    assert store2.size() == 1
    assert store2.get("live").value == "x"


class _RecordingAof:
    def __init__(self):
        self.records = []

    def append(self, record):
        self.records.append(record)


def test_start_on_store_with_aof_already_attached_raises(tmp_path, clock, make_persistence):
    data_dir = tmp_path / "data"
    attached = _RecordingAof()
    store = Store(clock=clock, aof=attached)
    p = make_persistence(data_dir)

    with pytest.raises(RuntimeError):
        p.start(store)

    # 데이터 디렉터리는 만들어졌을 수 있지만, 그 안에는 아무 파일도 없어야 한다
    assert not data_dir.exists() or os.listdir(data_dir) == []
    assert attached.records == []


def test_corrupt_middle_line_makes_start_raise(tmp_path, clock, make_persistence):
    good1 = '{"op":"set","k":"a","v":"1","exp":null}\n'
    good2 = '{"op":"set","k":"b","v":"2","exp":null}\n'
    (tmp_path / aof_name(1)).write_bytes((good1 + "this is not json\n" + good2).encode("utf-8"))
    store = Store(clock=clock)
    p = make_persistence(tmp_path)

    with pytest.raises(CorruptAofError):
        p.start(store)

    assert store.size() == 0  # 깨진 파일이면 아무것도 싣지 않는다


def test_construction_does_no_file_io(tmp_path, monkeypatch):
    missing = tmp_path / "not-there"
    Persistence(missing)
    assert not missing.exists()

    # 기본 data_dir("data")도 만들기만 해서는 cwd에 아무것도 생기지 않는다
    monkeypatch.chdir(tmp_path)
    Persistence()
    assert os.listdir(tmp_path) == []


def test_stop_without_start_and_stop_twice_do_not_raise(tmp_path, clock):
    never_started = Persistence(tmp_path / "x", aof_interval_s=IDLE, snapshot_interval_s=IDLE, check_interval_s=IDLE)
    never_started.stop()
    never_started.stop()

    store = Store(clock=clock)
    p = Persistence(tmp_path / "y", aof_interval_s=IDLE, snapshot_interval_s=IDLE, check_interval_s=IDLE)
    p.start(store)
    p.stop()
    p.stop()
