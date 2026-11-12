import pytest

from mini_redis.clock import FakeClock
from mini_redis.errors import (
    CapacityExceeded,
    InvalidKey,
    KeyNotFound,
    ValueTooLarge,
)
from mini_redis.store import Store


@pytest.fixture
def clock():
    return FakeClock(1_000_000)


@pytest.fixture
def store(clock):
    return Store(clock=clock)


def test_set_new_key_then_get_returns_value(store):
    store.set("a", "1")
    assert store.get("a").value == "1"


def test_set_existing_key_overwrites_value(store):
    store.set("a", "1")
    store.set("a", "2")
    assert store.get("a").value == "2"
    assert store.size() == 1


def test_set_empty_string_value_returns_empty_not_missing(store):
    store.set("a", "")
    assert store.get("a").value == ""
    assert store.exists("a") is True


def test_set_without_expire_overwrite_removes_old_ttl(store):
    store.set("a", "1", expire_at_ms=1_000_100)
    store.set("a", "2")
    assert store.get("a").expire_at_ms is None


def test_set_with_expire_overwrite_replaces_old_ttl(store):
    store.set("a", "1", expire_at_ms=1_000_100)
    store.set("a", "2", expire_at_ms=1_000_500)
    assert store.get("a").expire_at_ms == 1_000_500


def test_get_missing_key_raises_404(store):
    with pytest.raises(KeyNotFound) as e:
        store.get("nope")
    assert (e.value.status, e.value.code) == (404, "KEY_NOT_FOUND")


def test_get_before_expire_returns_value(store, clock):
    store.set("a", "1", expire_at_ms=1_001_000)
    clock.advance(999)
    assert store.get("a").value == "1"


def test_get_at_expire_time_raises_404(store, clock):
    store.set("a", "1", expire_at_ms=1_001_000)
    clock.advance(1000)
    with pytest.raises(KeyNotFound):
        store.get("a")


def test_get_expired_key_is_removed_lazily(store, clock):
    store.set("a", "1", expire_at_ms=1_001_000)
    clock.advance(1000)
    assert store.size() == 1  # 아직 회수 전
    with pytest.raises(KeyNotFound):
        store.get("a")
    assert store.size() == 0


def test_set_after_expire_stores_as_new_key(store, clock):
    store.set("a", "old", expire_at_ms=1_001_000)
    clock.advance(2000)
    store.set("a", "new")
    assert store.get("a").value == "new"
    assert store.get("a").expire_at_ms is None


def test_delete_existing_key_returns_true_and_removes(store):
    store.set("a", "1")
    assert store.delete("a") is True
    assert store.exists("a") is False


def test_delete_missing_key_returns_false_without_creating(store):
    assert store.delete("nope") is False
    assert store.size() == 0


def test_delete_expired_key_returns_false(store, clock):
    store.set("a", "1", expire_at_ms=1_001_000)
    clock.advance(1000)
    assert store.delete("a") is False


def test_exists_existing_missing_and_expired(store, clock):
    store.set("a", "1", expire_at_ms=1_001_000)
    assert store.exists("a") is True
    assert store.exists("b") is False
    clock.advance(1000)
    assert store.exists("a") is False


@pytest.mark.parametrize("op", ["set", "get", "delete", "exists"])
def test_commands_empty_key_raise_invalid_key(store, op):
    args = ("", "v") if op == "set" else ("",)
    with pytest.raises(InvalidKey):
        getattr(store, op)(*args)


def test_set_key_over_256_bytes_raises_invalid_key(store):
    with pytest.raises(InvalidKey):
        store.set("x" * 257, "v")


def test_set_key_with_slash_space_and_hangul_roundtrips(store):
    store.set("a/b 한글", "v")
    assert store.get("a/b 한글").value == "v"


def test_set_value_over_1mb_raises_413_and_stores_nothing(store):
    with pytest.raises(ValueTooLarge):
        store.set("a", "x" * (1024 * 1024 + 1))
    assert store.size() == 0


def test_set_new_key_over_capacity_raises_507():
    s = Store(clock=FakeClock(0), max_keys=2)
    s.set("a", "1")
    s.set("b", "2")
    with pytest.raises(CapacityExceeded) as e:
        s.set("c", "3")
    assert (e.value.status, e.value.code) == (507, "CAPACITY_EXCEEDED")


def test_set_existing_key_at_capacity_allowed():
    s = Store(clock=FakeClock(0), max_keys=2)
    s.set("a", "1")
    s.set("b", "2")
    s.set("a", "9")
    assert s.get("a").value == "9"
