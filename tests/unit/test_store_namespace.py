import pytest

from mini_redis.clock import FakeClock
from mini_redis.errors import (
    GuardRequired,
    InvalidKey,
    InvalidValue,
    KeyNotFound,
    LeaseInvalid,
    TtlRequired,
    VersionMismatch,
)
from mini_redis.store import Store

T0 = 1_000_000
EXP = T0 + 60_000


@pytest.fixture
def clock():
    return FakeClock(T0)


@pytest.fixture
def store(clock):
    return Store(clock=clock)


def miss(store, key="k", ns="user", lease=False):
    """ns를 붙인 GET의 miss가 404에 붙여 주는 필드 (version, lease ...)."""
    with pytest.raises(KeyNotFound) as e:
        store.get(key, lease=lease, ns=ns)
    return e.value.extra


# --- 버전 테이블 ---

def test_version_first_use_starts_at_creation_time_ms(store, clock):
    assert miss(store)["version"] == T0
    clock.advance(5000)
    assert miss(store)["version"] == T0  # 처음 본 시각에 정해지고 그대로다


def test_invalidate_same_ms_bumps_version_by_one(store):
    miss(store)
    assert store.invalidate("user") == T0 + 1
    assert store.invalidate("user") == T0 + 2


def test_invalidate_after_time_passes_jumps_to_now_ms(store, clock):
    miss(store)
    clock.advance(7000)
    assert store.invalidate("user") == T0 + 7000


def test_invalidate_clock_went_back_still_increases(store, clock):
    miss(store)
    clock.advance(7000)
    store.invalidate("user")
    clock.advance(-60_000)
    assert store.invalidate("user") == T0 + 7001


def test_invalidate_unseen_namespace_returns_creation_time_plus_one(store):
    assert store.invalidate("new") == T0 + 1


def test_invalidate_one_namespace_leaves_others_unchanged(store):
    store.set("k", "b", EXP, ns="b", v=miss(store, ns="b")["version"])
    store.invalidate("a")
    assert store.get("k", ns="b").value == "b"


def test_flush_keeps_version_table(store):
    store.invalidate("user")
    store.flush(confirm=True)
    assert miss(store)["version"] == T0 + 1


@pytest.mark.parametrize("ns", ["", "x" * 257, 1])
def test_invalidate_invalid_namespace_raises_invalid_key(store, ns):
    with pytest.raises(InvalidKey):
        store.invalidate(ns)


# --- 내부 키와 GET ---

def test_set_ns_with_read_version_stores_under_versioned_key(store):
    v = miss(store)["version"]
    store.set("k", "fresh", EXP, ns="user", v=v)
    assert store.keys() == [f"user:v{T0}:k"]
    item = store.get("k", ns="user")
    assert (item.value, item.version) == ("fresh", T0)


def test_get_without_ns_has_no_version_and_ignores_ns_keys(store):
    store.set("k", "plain")
    store.set("k", "ns", EXP, ns="user", v=miss(store)["version"])
    assert store.get("k").version is None
    assert store.get("k").value == "plain"


def test_get_ns_after_invalidate_hides_old_value(store):
    store.set("k", "old", EXP, ns="user", v=miss(store)["version"])
    new_version = store.invalidate("user")
    assert miss(store)["version"] == new_version
    assert store.exists(f"user:v{T0}:k")  # 지우지 않았고 TTL로 사라진다


def test_get_ns_long_key_validates_user_key_not_internal_key(store):
    key = "k" * 256  # 내부 키는 256바이트를 넘는다
    store.set(key, "v", EXP, ns="user", v=miss(store, key)["version"])
    assert store.get(key, ns="user").value == "v"


# --- ns PUT 규칙 ---

def test_set_ns_slow_put_between_invalidate_raises_version_mismatch(store):
    v = miss(store)["version"]  # 읽고 원본을 부르는 사이에
    store.invalidate("user")  # 원본이 바뀌어 무효화됐다
    with pytest.raises(VersionMismatch) as e:
        store.set("k", "stale", EXP, ns="user", v=v)
    assert "version" not in e.value.extra  # 새 버전을 알려 주면 낡은 값을 그대로 다시 쓸 수 있다
    assert store.size() == 0


def test_set_ns_lease_put_after_invalidate_raises_lease_invalid(store):
    token = miss(store, lease=True)["lease"]
    store.invalidate("user")
    with pytest.raises(LeaseInvalid):
        store.set("k", "stale", EXP, lease=token, ns="user")
    assert store.size() == 0


def test_set_ns_lease_put_without_invalidate_stores(store):
    token = miss(store, lease=True)["lease"]
    store.set("k", "v", EXP, lease=token, ns="user")
    assert store.get("k", ns="user").value == "v"


def test_set_ns_without_ttl_raises_ttl_required(store):
    with pytest.raises(TtlRequired):
        store.set("k", "v", ns="user", v=T0)
    assert store.size() == 0


def test_set_ns_without_lease_or_version_raises_guard_required(store):
    with pytest.raises(GuardRequired):
        store.set("k", "v", EXP, ns="user")
    assert store.size() == 0


@pytest.mark.parametrize("v", ["7", 1.5, True])
def test_set_ns_non_integer_version_raises_invalid_value(store, v):
    with pytest.raises(InvalidValue):
        store.set("k", "v", EXP, ns="user", v=v)


def test_set_version_without_ns_raises_invalid_value(store):
    with pytest.raises(InvalidValue):
        store.set("k", "v", EXP, v=T0)


# --- 다른 키 명령도 같은 내부 키를 쓴다 ---

def test_key_commands_with_ns_use_current_version_key(store, clock):
    store.set("k", "1", EXP, ns="user", v=miss(store)["version"])
    store.set("k", "plain")
    assert store.exists("k", ns="user")
    assert store.incr("k", ns="user") == 2
    assert store.ttl("k", ns="user") == 60
    assert store.expire("k", 30, ns="user")
    assert store.delete("k", ns="user")
    assert store.get("k").value == "plain"  # ns 없는 같은 이름 키는 그대로다


def test_key_commands_with_ns_after_invalidate_see_nothing(store):
    store.set("k", "1", EXP, ns="user", v=miss(store)["version"])
    store.invalidate("user")
    assert not store.exists("k", ns="user")
    assert store.ttl("k", ns="user") == -2
    assert not store.delete("k", ns="user")


def test_incr_ns_missing_key_raises_ttl_required(store):
    with pytest.raises(TtlRequired):
        store.incr("k", ns="user")
    assert store.size() == 0
