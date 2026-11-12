import time

import pytest

from mini_redis.clock import FakeClock
from mini_redis.config import EXPIRE_MAX_PER_TICK
from mini_redis.errors import KeyNotFound
from mini_redis.store import Store

T0 = 1_000_000


@pytest.fixture
def clock():
    return FakeClock(T0)


@pytest.fixture
def store(clock):
    return Store(clock=clock)


def test_expire_tick_expired_key_removed_without_access(store, clock):
    store.set("a", "1", expire_at_ms=T0 + 1000)
    clock.advance(1100)
    assert store.size() == 1  # 아무도 접근하지 않았다
    assert store.expire_tick() == 1
    assert store.size() == 0


def test_expire_tick_before_expiry_keeps_key(store, clock):
    store.set("a", "1", expire_at_ms=T0 + 1000)
    clock.advance(900)
    assert store.expire_tick() == 0
    assert store.get("a").value == "1"


def test_expire_tick_key_without_ttl_never_removed(store, clock):
    store.set("a", "1")
    clock.advance(10_000_000)
    assert store.expire_tick() == 0
    assert store.exists("a") is True


def test_expire_tick_overwrite_without_ttl_survives_old_wheel_item(store, clock):
    store.set("a", "1", expire_at_ms=T0 + 1000)
    store.set("a", "2")  # TTL 제거
    clock.advance(2000)
    store.expire_tick()
    assert store.get("a").value == "2"


def test_expire_tick_overwrite_with_later_ttl_deleted_at_new_time(store, clock):
    store.set("a", "1", expire_at_ms=T0 + 1000)
    store.set("a", "2", expire_at_ms=T0 + 5000)
    clock.advance(1100)
    store.expire_tick()  # 옛 항목이 꺼내져도 키는 산다
    assert store.get("a").value == "2"
    clock.advance(4000)
    store.expire_tick()
    assert store.size() == 0


def test_expire_tick_overwrite_with_earlier_ttl_deleted_at_new_time(store, clock):
    store.set("a", "1", expire_at_ms=T0 + 5000)
    store.set("a", "2", expire_at_ms=T0 + 1000)
    clock.advance(1100)
    store.expire_tick()
    assert store.size() == 0


def test_expire_tick_later_ttl_keeps_one_wheel_item_per_key(store, clock):
    store.set("a", "1", expire_at_ms=T0 + 1000)
    store.set("a", "2", expire_at_ms=T0 + 5000)  # 늦춤: add하지 않는다
    clock.advance(1100)
    store.expire_tick()  # 콜백이 5000으로 한 번만 재등록
    clock.advance(4000)
    assert store.expire_tick() == 1


def test_expire_tick_incr_keeps_ttl_and_still_expires(store, clock):
    store.set("n", "1", expire_at_ms=T0 + 1000)
    store.incr("n")
    clock.advance(1100)
    store.expire_tick()
    assert store.size() == 0


def test_expire_tick_deleted_key_resaved_without_ttl_survives(store, clock):
    store.set("a", "1", expire_at_ms=T0 + 1000)
    store.delete("a")
    store.set("a", "2")
    clock.advance(2000)
    store.expire_tick()
    assert store.get("a").value == "2"


def test_expire_tick_expired_then_resaved_with_ttl_uses_new_expiry(store, clock):
    store.set("a", "old", expire_at_ms=T0 + 1000)
    clock.advance(2000)
    store.set("a", "new", expire_at_ms=T0 + 9000)  # lazy로 옛 엔트리가 지워진 뒤 새 키
    store.expire_tick()
    assert store.get("a").value == "new"
    clock.advance(7000)
    store.expire_tick()
    assert store.size() == 0


def test_expire_tick_over_limit_deletes_at_most_limit_per_tick(store, clock):
    n = EXPIRE_MAX_PER_TICK + 500
    for i in range(n):
        store.set(f"k{i}", "v", expire_at_ms=T0 + 1000)
    clock.advance(1100)
    assert store.expire_tick() == EXPIRE_MAX_PER_TICK
    assert store.size() == 500
    assert store.expire_tick() == 500
    assert store.size() == 0


def test_expire_tick_far_future_ttl_beyond_wheel_range_deleted_on_time(store, clock):
    store.set("a", "1", expire_at_ms=T0 + 3_600_000)  # 1시간, 2단 범위(약 6.8분) 밖
    clock.advance(3_599_000)
    store.expire_tick()
    assert store.size() == 1
    clock.advance(2000)
    store.expire_tick()
    assert store.size() == 0


def test_flush_swaps_wheel_and_old_items_do_not_touch_new_keys(store, clock):
    store.set("a", "1", expire_at_ms=T0 + 1000)
    old_wheel = store._wheel
    store.flush(confirm=True)
    assert store._wheel is not old_wheel
    store.set("a", "2")
    clock.advance(2000)
    assert store.expire_tick() == 0
    assert store.get("a").value == "2"


def test_flush_new_wheel_still_expires_new_keys(store, clock):
    store.flush(confirm=True)
    store.set("a", "1", expire_at_ms=T0 + 1000)
    clock.advance(1100)
    assert store.expire_tick() == 1


def test_expiry_thread_started_removes_expired_keys_in_background(store, clock):
    store.set("a", "1", expire_at_ms=T0 + 1000)
    clock.advance(1100)
    store.start_expiry(interval_ms=5)
    try:
        deadline = time.monotonic() + 2
        while store.size() != 0 and time.monotonic() < deadline:
            time.sleep(0.005)
        assert store.size() == 0
    finally:
        store.stop_expiry()


def test_expiry_thread_start_twice_runs_single_thread(store):
    store.start_expiry(interval_ms=1000)
    first = store._expiry_stop
    store.start_expiry(interval_ms=1000)
    assert store._expiry_stop is first
    store.stop_expiry()


def test_expiry_thread_stop_without_start_is_noop(store):
    store.stop_expiry()


def test_get_expired_key_not_yet_ticked_still_raises_404(store, clock):
    store.set("a", "1", expire_at_ms=T0 + 1000)
    clock.advance(1000)
    with pytest.raises(KeyNotFound):
        store.get("a")
