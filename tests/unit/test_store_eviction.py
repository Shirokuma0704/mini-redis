"""키 상한에서의 SIEVE 축출(#25)과 다른 기능(AOF, lease, 타이밍 휠, 네임스페이스, 복구)이 맞물리는 곳."""
import pytest

from mini_redis.clock import FakeClock
from mini_redis.errors import CapacityExceeded, KeyNotFound, LeaseInvalid, NotInteger
from mini_redis.persistence.aof import AofBrokenError, del_record, set_record
from mini_redis.persistence.manager import Persistence
from mini_redis.store import Store

T0 = 1_000_000
IDLE = 3600.0


class RecordingAof:
    def __init__(self):
        self.records = []

    def append(self, record):
        self.records.append(record)


class BrokenAof:
    def append(self, record):
        raise AofBrokenError("깨진 AOF")


@pytest.fixture
def clock():
    return FakeClock(T0)


def sieve(clock, max_keys, **kw):
    return Store(clock=clock, max_keys=max_keys, eviction="sieve", **kw)


def live_keys(store):
    return store.keys()  # KEYS는 접근이 아니라 visited를 바꾸지 않는다


# --- 기본 동작 ---

def test_store_unknown_eviction_policy_raises_value_error():
    with pytest.raises(ValueError):
        Store(eviction="lru")


def test_store_default_policy_keeps_507_at_capacity(clock):
    store = Store(clock=clock, max_keys=1)
    store.set("a", "1")
    with pytest.raises(CapacityExceeded):
        store.set("b", "1")


def test_sieve_set_at_capacity_evicts_oldest_unvisited_key(clock):
    store = sieve(clock, 3)
    for k in "abc":
        store.set(k, k)
    store.set("d", "d")
    assert live_keys(store) == ["b", "c", "d"]
    assert store.size() == 3


def test_sieve_get_hit_key_survives_and_hand_resumes_after_last_victim(clock):
    store = sieve(clock, 3)
    for k in "abc":
        store.set(k, k)
    store.get("a")
    store.set("d", "d")
    assert live_keys(store) == ["a", "c", "d"]  # 접근한 a 대신 b가 나간다
    store.get("a")
    store.get("c")
    store.set("e", "e")
    assert live_keys(store) == ["a", "c", "e"]  # hand는 b 다음(c)에서 이어가고, 읽은 c를 지나 d를 고른다


def test_sieve_one_hit_stream_evicts_new_keys_and_keeps_accessed_old_key(clock):
    store = sieve(clock, 3)
    for k in "abc":
        store.set(k, k)
    store.get("a")
    for i in range(50):  # 한 번 쓰고 다시 안 읽는 키가 몰려와도
        store.set(f"scan{i}", "x")
    assert "a" in live_keys(store)  # hand는 머리 근처에서 새 키만 내보낸다 (FIFO라면 a가 먼저 나간다)


def test_sieve_all_accessed_one_lap_clears_bits_and_oldest_goes(clock):
    store = sieve(clock, 3)
    for k in "abc":
        store.set(k, k)
        store.get(k)
    store.set("d", "d")
    assert live_keys(store) == ["b", "c", "d"]  # visited는 영원한 보호가 아니라 한 번의 유예다
    store.set("e", "e")
    assert live_keys(store) == ["c", "d", "e"]


def test_sieve_overwrite_existing_key_at_capacity_does_not_evict(clock):
    store = sieve(clock, 2)
    store.set("a", "1")
    store.set("b", "1")
    store.set("a", "2")
    assert live_keys(store) == ["a", "b"]


def test_sieve_incr_new_key_at_capacity_evicts_instead_of_507(clock):
    store = sieve(clock, 1)
    store.set("a", "1")
    assert store.incr("n") == 1
    assert live_keys(store) == ["n"]


# --- 무엇을 접근으로 치나 ---

ACCESS = {
    "get": lambda s: s.get("a"),
    "set_overwrite": lambda s: s.set("a", "2"),
    "incr": lambda s: s.incr("a"),
    "expire": lambda s: s.expire("a", 60),
}
NOT_ACCESS = {
    "exists": lambda s: s.exists("a"),
    "ttl": lambda s: s.ttl("a"),
    "keys": lambda s: s.keys(),
}


@pytest.mark.parametrize("op", ACCESS.values(), ids=ACCESS.keys())
def test_sieve_access_command_marks_visited_key_survives(clock, op):
    store = sieve(clock, 2)
    store.set("a", "1")
    store.set("b", "1")
    op(store)
    store.set("c", "1")
    assert live_keys(store) == ["a", "c"]


@pytest.mark.parametrize("op", NOT_ACCESS.values(), ids=NOT_ACCESS.keys())
def test_sieve_metadata_command_not_access_key_evicted(clock, op):
    store = sieve(clock, 2)
    store.set("a", "1")
    store.set("b", "1")
    op(store)
    store.set("c", "1")
    assert live_keys(store) == ["b", "c"]


# --- 거절되는 요청은 내보내지 않는다 ---

def test_sieve_lease_invalid_at_capacity_rejects_without_evicting(clock):
    store = sieve(clock, 1)
    store.set("a", "1")
    with pytest.raises(LeaseInvalid):
        store.set("k", "v", lease="not-a-token")
    assert live_keys(store) == ["a"]


def test_sieve_incr_overflow_at_capacity_rejects_without_evicting(clock):
    store = sieve(clock, 1)
    store.set("a", "1")
    with pytest.raises(NotInteger):
        store.incr("n", 2**63)
    assert live_keys(store) == ["a"]


# --- AOF ---

def test_sieve_eviction_logs_del_before_new_set(clock):
    aof = RecordingAof()
    store = sieve(clock, 1, aof=aof)
    store.set("a", "1")
    store.set("b", "2")
    assert aof.records == [set_record("a", "1", None), del_record("a"), set_record("b", "2", None)]


def test_sieve_eviction_aof_failure_changes_nothing(clock):
    store = sieve(clock, 1)
    store.set("a", "1")
    store.attach_aof(BrokenAof())
    with pytest.raises(AofBrokenError):
        store.set("b", "2")
    assert live_keys(store) == ["a"]  # 기록 먼저: DEL을 못 적었으면 a도 그대로다


def test_sieve_evicted_key_stays_gone_after_restart(tmp_path, clock):
    store1 = sieve(clock, 2)
    p1 = Persistence(tmp_path, aof_interval_s=IDLE, snapshot_interval_s=IDLE, check_interval_s=IDLE)
    p1.start(store1)
    for k in "abc":
        store1.set(k, k)
    p1.stop()

    store2 = sieve(clock, 2)
    p2 = Persistence(tmp_path, aof_interval_s=IDLE, snapshot_interval_s=IDLE, check_interval_s=IDLE)
    try:
        p2.start(store2)
        assert live_keys(store2) == ["b", "c"]  # 재생한 DEL 덕분에 내보낸 a가 되살아나지 않는다
    finally:
        p2.stop()


# --- 스냅샷·복구 뒤의 큐 ---

def test_sieve_restore_rebuilds_queue_in_restore_order_with_visited_cleared(clock):
    store = sieve(clock, 2)
    store.restore_entries([("a", "1", None), ("b", "2", None)])
    store.set("c", "3")
    assert live_keys(store) == ["b", "c"]  # 복원한 순서가 큐 순서다


def test_sieve_restore_over_capacity_evicts_instead_of_failing(clock):
    store = sieve(clock, 1)
    store.restore_entries([("a", "1", None), ("b", "2", None)])
    assert live_keys(store) == ["b"]


# --- lease ---

def test_sieve_lease_put_on_full_store_evicts_and_stores(clock):
    store = sieve(clock, 1)
    with pytest.raises(KeyNotFound) as e:
        store.get("k", lease=True)
    store.set("other", "x")
    store.set("k", "v", lease=e.value.extra["lease"])  # noeviction이면 507
    assert live_keys(store) == ["k"]


def test_sieve_evicting_key_keeps_its_lease_so_refresh_put_succeeds(clock):
    # XFetch 조기 갱신: 값이 살아 있는 키에 lease가 걸린 유일한 경우다
    store = sieve(clock, 1, random_fn=lambda: 0.999999)
    with pytest.raises(KeyNotFound) as e:
        store.get("hot", lease=True)
    clock.advance(200)
    store.set("hot", "v1", expire_at_ms=clock() + 10_000, lease=e.value.extra["lease"])
    clock.advance(9_000)
    token = store.get("hot", lease=True).lease
    assert token is not None
    store.set("x", "1")  # hot은 visited지만 한 바퀴 돌아 내보내진다
    assert live_keys(store) == ["x"]
    store.set("hot", "v2", expire_at_ms=clock() + 10_000, lease=token)  # 축출은 원본 변경이 아니라 lease를 남긴다
    assert store.get("hot").value == "v2"


# --- 타이밍 휠의 낡은 항목 ---

def test_sieve_evicted_key_wheel_item_is_ignored_at_its_expiry(clock):
    aof = RecordingAof()
    store = sieve(clock, 1, aof=aof)
    store.set("a", "1", expire_at_ms=T0 + 1_000)
    store.set("b", "2")
    clock.advance(1_100)
    assert store.expire_tick() == 0
    assert aof.records.count(del_record("a")) == 1  # 축출 때 한 번만


def test_sieve_wheel_item_of_evicted_key_does_not_delete_reinserted_key(clock):
    store = sieve(clock, 1)
    store.set("a", "1", expire_at_ms=T0 + 1_000)
    store.set("b", "2")  # a를 내보낸다
    store.set("a", "3", expire_at_ms=T0 + 5_000)  # b를 내보내고 a를 새 TTL로
    clock.advance(1_100)
    store.expire_tick()
    assert store.get("a").value == "3"
    clock.advance(4_000)
    assert store.expire_tick() == 1
    assert live_keys(store) == []


# --- 네임스페이스 버전 키 ---

def ns_put(store, key, value, ns="user"):
    with pytest.raises(KeyNotFound) as e:
        store.get(key, ns=ns)
    store.set(key, value, expire_at_ms=T0 + 60_000, ns=ns, v=e.value.extra["version"])


def test_sieve_old_namespace_version_key_evicted_and_version_kept(clock):
    store = sieve(clock, 2)
    ns_put(store, "k", "v1")
    old_key = live_keys(store)[0]
    version = store.invalidate("user")
    ns_put(store, "k", "v2")
    store.get("k", ns="user")
    store.set("other", "x")
    assert old_key not in live_keys(store)  # 다시 읽히지 않는 옛 버전 키가 나간다
    item = store.get("k", ns="user")
    assert (item.value, item.version) == ("v2", version)  # 버전 표는 키 상한·축출과 상관없다
