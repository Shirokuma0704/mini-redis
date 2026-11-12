import threading

import pytest

from mini_redis.clock import FakeClock
from mini_redis.errors import CapacityExceeded, InvalidKey, InvalidValue, NotInteger
from mini_redis.store import INT64_MAX, INT64_MIN, Store


@pytest.fixture
def clock():
    return FakeClock(1_000_000)


@pytest.fixture
def store(clock):
    return Store(clock=clock)


def test_incr_missing_key_starts_from_zero_returns_one(store):
    assert store.incr("n") == 1
    assert store.get("n").value == "1"


def test_incr_missing_key_has_no_ttl(store):
    store.incr("n")
    assert store.get("n").expire_at_ms is None


def test_incr_existing_integer_adds_one(store):
    store.set("n", "41")
    assert store.incr("n") == 42


def test_incr_custom_amount_adds_amount(store):
    store.set("n", "10")
    assert store.incr("n", 5) == 15


def test_incr_negative_amount_decrements(store):
    store.set("n", "10")
    assert store.incr("n", -15) == -5
    assert store.get("n").value == "-5"


def test_incr_negative_stored_value_works(store):
    store.set("n", "-3")
    assert store.incr("n") == -2


def test_incr_keeps_existing_ttl(store):
    store.set("n", "1", expire_at_ms=1_005_000)
    store.incr("n")
    assert store.get("n").expire_at_ms == 1_005_000


def test_incr_expired_key_starts_from_zero_without_old_ttl(store, clock):
    store.set("n", "100", expire_at_ms=1_001_000)
    clock.advance(1000)
    assert store.incr("n") == 1
    assert store.get("n").expire_at_ms is None


@pytest.mark.parametrize("bad", ["abc", "1.5", "", " 5", "5 ", "1_0", "+5", "１２", "0x10"])
def test_incr_non_integer_value_raises_409_and_keeps_value(store, bad):
    store.set("n", bad)
    with pytest.raises(NotInteger) as e:
        store.incr("n")
    assert (e.value.status, e.value.code) == (409, "NOT_INTEGER")
    assert store.get("n").value == bad


def test_incr_result_over_int64_raises_and_keeps_value(store):
    store.set("n", str(INT64_MAX))
    with pytest.raises(NotInteger):
        store.incr("n")
    assert store.get("n").value == str(INT64_MAX)


def test_incr_result_under_int64_min_raises(store):
    store.set("n", str(INT64_MIN))
    with pytest.raises(NotInteger):
        store.incr("n", -1)


def test_incr_stored_value_beyond_int64_raises(store):
    store.set("n", "9" * 30)
    with pytest.raises(NotInteger):
        store.incr("n")


def test_incr_huge_digit_string_raises_without_crashing(store):
    store.set("n", "9" * 100_000)
    with pytest.raises(NotInteger):
        store.incr("n")


@pytest.mark.parametrize("amount", [1.5, "1", None, True])
def test_incr_non_integer_amount_raises_invalid_value(store, amount):
    with pytest.raises(InvalidValue):
        store.incr("n", amount)


def test_incr_empty_key_raises_invalid_key(store):
    with pytest.raises(InvalidKey):
        store.incr("")


def test_incr_new_key_over_capacity_raises_507(clock):
    s = Store(clock=clock, max_keys=1)
    s.set("a", "1")
    with pytest.raises(CapacityExceeded):
        s.incr("n")
    assert s.exists("n") is False


def test_incr_existing_key_at_capacity_allowed(clock):
    s = Store(clock=clock, max_keys=1)
    s.set("a", "1")
    assert s.incr("a") == 2


def test_incr_concurrent_10_threads_1000_times_final_equals_total(store):
    def work():
        for _ in range(1000):
            store.incr("counter")

    threads = [threading.Thread(target=work) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert store.get("counter").value == "10000"


def test_incr_concurrent_mixed_amounts_final_equals_sum(store):
    def work(amount):
        for _ in range(500):
            store.incr("c", amount)

    threads = [threading.Thread(target=work, args=(a,)) for a in (1, 2, 3, -1, 5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert store.get("c").value == str(500 * (1 + 2 + 3 - 1 + 5))
