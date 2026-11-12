"""복구한 키는 내부 식별자라 외부 키 검증을 거치지 않는다: 256바이트 키의 ns 버전 키도 재시작을 막지 않는다 (#14)."""
import pytest

from mini_redis.clock import FakeClock
from mini_redis.errors import CapacityExceeded, KeyNotFound
from mini_redis.persistence.manager import Persistence
from mini_redis.store import Store

T0 = 1_000_000
EXP = T0 + 3_600_000  # 시계가 멈춰 있어서 이 키는 테스트 내내 살아 있다
IDLE = 3600.0  # 스레드가 테스트 중에 깨어나지 않게 아주 길게 잡는다
LONG_KEY = "x" * 256  # 외부 키 한도(256바이트)에 딱 맞는 키. ns를 붙이면 내부 키는 한도를 넘는다


class RecordingAof:
    def __init__(self):
        self.records = []

    def append(self, record):
        self.records.append(record)


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


def first_run_with_long_key(data_dir, clock, make_persistence):
    p = make_persistence(data_dir)
    store = Store(clock=clock)
    p.start(store)
    with pytest.raises(KeyNotFound) as miss:
        store.get(LONG_KEY, ns="n")
    store.set(LONG_KEY, "1", EXP, ns="n", v=miss.value.extra["version"])
    p.stop()


def restart(data_dir, clock, make_persistence):
    p = make_persistence(data_dir)
    store = Store(clock=clock)
    p.start(store)
    return p, store


def test_persistence_start_long_namespaced_key_restarts_without_error(tmp_path, clock, make_persistence):
    first_run_with_long_key(tmp_path, clock, make_persistence)

    _, store2 = restart(tmp_path, clock, make_persistence)

    assert store2.get(LONG_KEY, ns="n").value == "1"


def test_persistence_snapshot_long_namespaced_key_survives_second_restart(tmp_path, clock, make_persistence):
    first_run_with_long_key(tmp_path, clock, make_persistence)

    p2, store2 = restart(tmp_path, clock, make_persistence)
    assert p2.snapshotter.snapshot() is True
    p2.stop()

    _, store3 = restart(tmp_path, clock, make_persistence)
    assert store3.get(LONG_KEY, ns="n").value == "1"


def test_restore_entries_with_ttl_registers_expiry(clock):
    store = Store(clock=clock)
    store.restore_entries([("k", "v", T0 + 1000)])

    clock.advance(1001)

    assert store.expire_tick() == 1
    with pytest.raises(KeyNotFound):
        store.get("k")


def test_restore_entries_with_aof_attached_raises_runtime_error(clock):
    store = Store(clock=clock, aof=RecordingAof())

    with pytest.raises(RuntimeError):
        store.restore_entries([("k", "v", None)])


def test_restore_entries_over_capacity_raises_capacity_exceeded(clock):
    store = Store(clock=clock, max_keys=1)

    with pytest.raises(CapacityExceeded):
        store.restore_entries([("a", "1", None), ("b", "2", None)])


def test_restore_entries_is_not_logged(clock):
    store = Store(clock=clock)
    store.restore_entries([("a", "1", None), ("b", "2", EXP)])

    aof = RecordingAof()
    store.attach_aof(aof)

    assert aof.records == []
    assert store.get("a").value == "1"
