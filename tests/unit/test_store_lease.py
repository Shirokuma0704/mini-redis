import re

import pytest

from mini_redis.clock import FakeClock
from mini_redis.config import LEASE_RETRY_MS, LEASE_TTL_MS
from mini_redis.errors import CapacityExceeded, InvalidValue, KeyNotFound, LeaseInvalid
from mini_redis.store import Store

T0 = 1_000_000


@pytest.fixture
def clock():
    return FakeClock(T0)


@pytest.fixture
def store(clock):
    return Store(clock=clock)


def miss(store, key="k"):
    """GET ?lease=1의 miss가 404에 붙여 주는 필드 (lease 또는 retry_after_ms)."""
    with pytest.raises(KeyNotFound) as e:
        store.get(key, lease=True)
    return e.value.extra


def issue(store, key="k"):
    return miss(store, key)["lease"]


# --- 발급과 대기 ---

def test_get_lease_miss_issues_64bit_hex_token(store):
    assert re.fullmatch(r"[0-9a-f]{16}", issue(store))


def test_get_lease_while_held_returns_retry_after_without_token(store):
    issue(store)
    assert miss(store) == {"retry_after_ms": LEASE_RETRY_MS}


def test_get_lease_near_lease_end_retry_after_capped_to_remaining(store, clock):
    issue(store)
    clock.advance(LEASE_TTL_MS - 30)
    assert miss(store) == {"retry_after_ms": 30}


def test_get_lease_hit_returns_value_without_issuing(store):
    store.set("k", "v")
    assert store.get("k", lease=True).value == "v"
    store.delete("k")
    assert "lease" in miss(store)  # hit에서 lease가 생기지 않았다


def test_get_without_lease_flag_miss_issues_nothing(store):
    with pytest.raises(KeyNotFound) as e:
        store.get("k")
    assert e.value.extra == {}
    assert "lease" in miss(store)


def test_get_lease_expired_key_issues_token(store, clock):
    store.set("k", "v", expire_at_ms=T0 + 1000)
    clock.advance(1000)
    assert "lease" in miss(store)


def test_get_lease_different_keys_issue_independent_tokens(store):
    assert issue(store, "a") != issue(store, "b")


# --- PUT 검증 ---

def test_set_with_valid_token_stores_and_clears_lease(store):
    token = issue(store)
    store.set("k", "v", lease=token)
    assert store.get("k").value == "v"
    with pytest.raises(LeaseInvalid):
        store.set("k", "v2", lease=token)  # 한 번 쓴 토큰은 다시 못 쓴다
    assert store.get("k").value == "v"


def test_set_with_valid_token_keeps_ttl(store, clock):
    token = issue(store)
    store.set("k", "v", expire_at_ms=T0 + 5000, lease=token)
    assert store.ttl("k") == 5


def test_set_with_wrong_token_raises_lease_invalid_and_keeps_lease(store):
    token = issue(store)
    with pytest.raises(LeaseInvalid):
        store.set("k", "v", lease="0" * 16)
    assert not store.exists("k")
    store.set("k", "v", lease=token)  # 진짜 보유자는 여전히 쓸 수 있다
    assert store.get("k").value == "v"


def test_set_with_token_never_issued_raises_lease_invalid(store):
    with pytest.raises(LeaseInvalid):
        store.set("k", "v", lease="0123456789abcdef")
    assert not store.exists("k")


def test_set_with_token_of_other_key_raises_lease_invalid(store):
    token = issue(store, "a")
    with pytest.raises(LeaseInvalid):
        store.set("b", "v", lease=token)


@pytest.mark.parametrize("token", [123, True, ["t"], {"t": 1}])
def test_set_with_non_string_token_raises_invalid_value(store, token):
    issue(store)
    with pytest.raises(InvalidValue):
        store.set("k", "v", lease=token)
    assert not store.exists("k")


def test_set_with_token_on_full_store_raises_capacity_and_keeps_lease(clock):
    store = Store(clock=clock, max_keys=1)
    token = issue(store, "k")
    store.set("other", "x")
    with pytest.raises(CapacityExceeded):
        store.set("k", "v", lease=token)
    store.delete("other")
    store.set("k", "v", lease=token)  # 용량 때문에 실패한 PUT은 lease를 소비하지 않았다


# --- 무효화: 삭제 뒤 늦게 온 PUT은 409 (stale set 방지) ---

@pytest.mark.parametrize(
    "invalidate",
    [
        lambda s: s.delete("k"),
        lambda s: s.delete_prefix("k"),
        lambda s: s.flush(confirm=True),
    ],
    ids=["del", "prefix", "flush"],
)
def test_set_after_delete_while_key_missing_raises_lease_invalid(store, invalidate):
    token = issue(store)
    invalidate(store)  # 키는 원래 없지만 lease는 무효화된다
    with pytest.raises(LeaseInvalid):
        store.set("k", "stale", lease=token)
    assert not store.exists("k")
    assert "lease" in miss(store)  # 다음 요청은 새 lease를 받는다


def test_delete_prefix_other_prefix_keeps_lease(store):
    token = issue(store, "user:1")
    assert store.delete_prefix("post:") == 0
    store.set("user:1", "v", lease=token)


def test_delete_prefix_lease_not_counted_as_deleted_key(store):
    issue(store, "user:1")
    store.set("user:2", "v")
    assert store.delete_prefix("user:") == 1


def test_set_without_token_stores_and_invalidates_lease(store):
    token = issue(store)
    store.set("k", "fresh")  # ns 없는 키의 토큰 없는 PUT은 일반 쓰기
    with pytest.raises(LeaseInvalid):
        store.set("k", "stale", lease=token)
    assert store.get("k").value == "fresh"


def test_incr_invalidates_lease(store):
    token = issue(store)
    assert store.incr("k") == 1
    with pytest.raises(LeaseInvalid):
        store.set("k", "stale", lease=token)
    assert store.get("k").value == "1"


# --- 10초 자동 만료 ---

def test_set_with_token_just_before_lease_end_succeeds(store, clock):
    token = issue(store)
    clock.advance(LEASE_TTL_MS - 1)
    store.set("k", "v", lease=token)
    assert store.get("k").value == "v"


def test_set_with_token_after_lease_ttl_raises_lease_invalid(store, clock):
    token = issue(store)
    clock.advance(LEASE_TTL_MS)
    with pytest.raises(LeaseInvalid):
        store.set("k", "v", lease=token)
    assert not store.exists("k")


def test_get_lease_after_lease_ttl_issues_new_token_and_old_one_fails(store, clock):
    old = issue(store)
    clock.advance(LEASE_TTL_MS - 1)
    assert "retry_after_ms" in miss(store)
    clock.advance(1)
    new = issue(store)
    assert new != old
    with pytest.raises(LeaseInvalid):
        store.set("k", "v", lease=old)
    store.set("k", "v", lease=new)


# --- lease는 키가 아니다 (스냅샷이 복사하는 해시테이블에 들어가지 않는다) ---

def test_lease_not_visible_as_key(store):
    issue(store, "k")
    assert store.size() == 0
    assert store.keys() == []
    assert not store.exists("k")
    assert store.ttl("k") == -2
