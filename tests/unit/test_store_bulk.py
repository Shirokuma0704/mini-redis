import pytest

from mini_redis.clock import FakeClock
from mini_redis.errors import ConfirmRequired, EmptyPrefix, InvalidValue
from mini_redis.store import Store


@pytest.fixture
def clock():
    return FakeClock(1_000_000)


@pytest.fixture
def store(clock):
    s = Store(clock=clock)
    for k in ["user:1", "user:2", "user:10", "post:1", "usr", "user"]:
        s.set(k, "v")
    return s


# --- KEYS ---

def test_keys_prefix_returns_matching_keys_sorted(store):
    assert store.keys("user:") == ["user:1", "user:10", "user:2"]


def test_keys_empty_prefix_returns_all_keys(store):
    assert len(store.keys("")) == 6


def test_keys_no_match_returns_empty_list(store):
    assert store.keys("zzz") == []


def test_keys_prefix_is_case_sensitive(store):
    assert store.keys("USER") == []


def test_keys_excludes_expired_keys(store, clock):
    store.set("user:tmp", "v", expire_at_ms=1_001_000)
    clock.advance(1000)
    assert "user:tmp" not in store.keys("user:")


def test_keys_removes_expired_keys_lazily(store, clock):
    store.set("user:tmp", "v", expire_at_ms=1_001_000)
    before = store.size()
    clock.advance(1000)
    store.keys("user:")
    assert store.size() == before - 1


def test_keys_does_not_remove_unexpired_keys(store):
    store.keys("user:")
    assert store.size() == 6


def test_keys_non_string_prefix_raises_invalid_value(store):
    with pytest.raises(InvalidValue):
        store.keys(None)


def test_keys_hangul_prefix_matches(store):
    store.set("한글:1", "v")
    assert store.keys("한글") == ["한글:1"]


# --- prefix 삭제 ---

def test_delete_prefix_returns_deleted_count(store):
    assert store.delete_prefix("user:") == 3


def test_delete_prefix_removes_only_matching_keys(store):
    store.delete_prefix("user:")
    assert store.keys("") == ["post:1", "user", "usr"]


def test_delete_prefix_no_match_returns_zero(store):
    assert store.delete_prefix("zzz") == 0
    assert store.size() == 6


def test_delete_prefix_empty_raises_400_and_deletes_nothing(store):
    with pytest.raises(EmptyPrefix) as e:
        store.delete_prefix("")
    assert (e.value.status, e.value.code) == (400, "EMPTY_PREFIX")
    assert store.size() == 6


def test_delete_prefix_expired_keys_removed_but_not_counted(store, clock):
    store.set("user:tmp", "v", expire_at_ms=1_001_000)
    clock.advance(1000)
    assert store.delete_prefix("user:") == 3
    assert store.size() == 3


def test_delete_prefix_exact_key_equal_to_prefix_is_deleted(store):
    assert store.delete_prefix("user") == 4  # user, user:1, user:2, user:10
    assert store.exists("user") is False


def test_delete_prefix_many_keys_across_resize_deletes_all(clock):
    s = Store(clock=clock)
    for i in range(3000):
        s.set(f"bulk:{i}", "v")
    s.set("keep", "v")
    assert s.delete_prefix("bulk:") == 3000
    assert s.keys("") == ["keep"]


def test_delete_prefix_non_string_raises_invalid_value(store):
    with pytest.raises(InvalidValue):
        store.delete_prefix(123)


# --- FLUSH ---

def test_flush_confirm_true_empties_store(store):
    store.flush(confirm=True)
    assert store.size() == 0
    assert store.keys("") == []


def test_flush_get_after_flush_raises_404(store):
    from mini_redis.errors import KeyNotFound

    store.flush(confirm=True)
    with pytest.raises(KeyNotFound):
        store.get("user:1")


@pytest.mark.parametrize("confirm", [False, None, 1, "true"])
def test_flush_without_true_confirm_raises_400_and_keeps_data(store, confirm):
    with pytest.raises(ConfirmRequired) as e:
        store.flush(confirm=confirm)
    assert (e.value.status, e.value.code) == (400, "CONFIRM_REQUIRED")
    assert store.size() == 6


def test_flush_no_argument_raises_400(store):
    with pytest.raises(ConfirmRequired):
        store.flush()


def test_flush_store_usable_after_flush(store):
    store.flush(confirm=True)
    store.set("a", "1")
    assert store.get("a").value == "1"
    assert store.size() == 1


def test_flush_swaps_table_object(store):
    old = store._table
    store.flush(confirm=True)
    assert store._table is not old


def test_flush_resets_capacity_limit_count(clock):
    s = Store(clock=clock, max_keys=2)
    s.set("a", "1")
    s.set("b", "2")
    s.flush(confirm=True)
    s.set("c", "3")
    s.set("d", "4")
    assert s.size() == 2
