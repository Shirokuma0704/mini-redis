import threading

import pytest

from mini_redis.clock import FakeClock
from mini_redis.errors import CapacityExceeded, InvalidKey, InvalidValue, ValueTooLarge
from mini_redis.store import Store, validate_key, validate_value


def test_store_fake_clock_injected_returns_clock_time():
    clock = FakeClock(1000)
    store = Store(clock=clock)
    assert store.now_ms() == 1000
    clock.advance(500)
    assert store.now_ms() == 1500


def test_store_default_clock_returns_epoch_ms():
    assert Store().now_ms() > 1_600_000_000_000


def test_store_new_store_size_is_zero():
    assert Store().size() == 0


@pytest.mark.parametrize("key", ["a", "한글", "a/b c", "x" * 256, "한" * 85])
def test_validate_key_valid_key_passes(key):
    validate_key(key)


@pytest.mark.parametrize("key", ["", "x" * 257, "한" * 86, None, 123])
def test_validate_key_invalid_key_raises_invalid_key(key):
    with pytest.raises(InvalidKey):
        validate_key(key)


def test_validate_key_error_has_400_and_code():
    with pytest.raises(InvalidKey) as e:
        validate_key("")
    assert (e.value.status, e.value.code) == (400, "INVALID_KEY")


def test_validate_value_empty_string_allowed():
    validate_value("")


def test_validate_value_exactly_1mb_allowed():
    validate_value("x" * (1024 * 1024))


def test_validate_value_over_1mb_raises_413():
    with pytest.raises(ValueTooLarge) as e:
        validate_value("x" * (1024 * 1024 + 1))
    assert (e.value.status, e.value.code) == (413, "VALUE_TOO_LARGE")


def test_validate_value_multibyte_counts_bytes_not_chars():
    with pytest.raises(ValueTooLarge):
        validate_value("한" * (1024 * 1024 // 3 + 1))


def test_validate_value_non_string_raises_invalid_value():
    with pytest.raises(InvalidValue):
        validate_value(123)


def test_check_capacity_new_key_at_limit_raises_507():
    store = Store(max_keys=2)
    store._table.put("a", "1")
    store._table.put("b", "2")
    with pytest.raises(CapacityExceeded) as e:
        store._check_capacity("c")
    assert (e.value.status, e.value.code) == (507, "CAPACITY_EXCEEDED")


def test_check_capacity_existing_key_at_limit_allowed():
    store = Store(max_keys=2)
    store._table.put("a", "1")
    store._table.put("b", "2")
    store._check_capacity("a")  # 덮어쓰기는 허용


def test_store_public_method_blocks_while_lock_held_by_other_thread():
    store = Store()
    done = threading.Event()
    store._lock.acquire()
    t = threading.Thread(target=lambda: (store.size(), done.set()))
    t.start()
    assert not done.wait(0.1)  # 락이 잡혀 있는 동안 대기
    store._lock.release()
    assert done.wait(1)
    t.join()


def test_store_public_method_reentrant_lock_does_not_deadlock():
    store = Store()
    with store._lock:
        assert store.size() == 0  # 같은 스레드가 다시 잡아도 교착 없음
