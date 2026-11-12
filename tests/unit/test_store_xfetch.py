import pytest

from mini_redis.clock import FakeClock
from mini_redis.config import LEASE_TTL_MS
from mini_redis.errors import KeyNotFound, LeaseInvalid
from mini_redis.store import Store

T0 = 1_000_000
FORCE = 0.999999  # random()이 이 값이면 u가 1e-6이라 -ln(u)=13.8, 앞당김이 가장 크다
NEVER = 0.0  # u=1이면 만료 시각 전에는 당첨되지 않는다


class Rng:
    def __init__(self, value=NEVER):
        self.value = value

    def __call__(self):
        return self.value


@pytest.fixture
def clock():
    return FakeClock(T0)


@pytest.fixture
def rng():
    return Rng()


@pytest.fixture
def store(clock, rng):
    return Store(clock=clock, random_fn=rng)


def fill_via_lease(store, clock, key="hot", ttl_ms=10_000, work_ms=200, value="v1"):
    """miss -> lease 발급 -> 원본 호출(work_ms) -> lease로 PUT. 서버가 delta를 잰다."""
    with pytest.raises(KeyNotFound) as e:
        store.get(key, lease=True)
    clock.advance(work_ms)
    store.set(key, value, expire_at_ms=clock() + ttl_ms, lease=e.value.extra["lease"])


def delta_of(store, key="hot"):
    return store._table.get(key).delta_ms


# --- delta 측정 ---

def test_set_with_lease_records_delta_as_issue_to_put_time(store, clock):
    fill_via_lease(store, clock, work_ms=200)
    assert delta_of(store) == 200


def test_set_with_lease_delta_follows_actual_elapsed_time(store, clock):
    fill_via_lease(store, clock, work_ms=1234)
    assert delta_of(store) == 1234


def test_set_without_lease_has_no_delta(store):
    store.set("hot", "v", expire_at_ms=T0 + 10_000)
    assert delta_of(store) is None


def test_set_without_lease_overwrite_clears_old_delta(store, clock):
    fill_via_lease(store, clock)
    store.set("hot", "v2", expire_at_ms=clock() + 10_000)
    assert delta_of(store) is None


def test_set_with_lease_after_expiry_refill_replaces_delta_with_new_measurement(store, clock):
    fill_via_lease(store, clock, ttl_ms=1000, work_ms=200)
    clock.advance(1000)  # 만료 -> miss -> 새 lease
    fill_via_lease(store, clock, ttl_ms=1000, work_ms=500, value="v2")
    assert delta_of(store) == 500


def test_expire_keeps_delta_because_value_is_unchanged(store, clock):
    fill_via_lease(store, clock, work_ms=200)
    store.expire("hot", 30)
    assert delta_of(store) == 200


def test_incr_clears_delta_because_value_changed(store, clock):
    fill_via_lease(store, clock, value="5")
    store.incr("hot")
    assert delta_of(store) is None


# --- 조기 갱신 추첨 ---

def test_get_hit_with_lease_forced_draw_returns_value_and_lease_token(store, clock, rng):
    fill_via_lease(store, clock, ttl_ms=1000, work_ms=200)
    clock.advance(500)  # 남은 500ms, delta 200
    rng.value = FORCE
    item = store.get("hot", lease=True)
    assert item.value == "v1"
    assert item.lease is not None


def test_get_hit_with_lease_losing_draw_returns_plain_hit(store, clock):
    fill_via_lease(store, clock, ttl_ms=1000, work_ms=200)
    clock.advance(500)
    assert store.get("hot", lease=True).lease is None  # rng=NEVER


def test_get_hit_without_lease_flag_never_issues_lease_even_if_forced(store, clock, rng):
    fill_via_lease(store, clock, ttl_ms=1000)
    rng.value = FORCE
    assert store.get("hot").lease is None
    assert store.get("hot", lease=False).lease is None


def test_get_hit_far_from_expiry_does_not_fire_even_if_forced(store, clock, rng):
    fill_via_lease(store, clock, ttl_ms=3_600_000, work_ms=200)  # 1시간
    rng.value = FORCE  # 앞당김 최대 2.7초
    assert store.get("hot", lease=True).lease is None


def test_get_hit_without_measured_delta_never_fires(store, clock, rng):
    store.set("hot", "v", expire_at_ms=T0 + 100)
    rng.value = FORCE
    assert store.get("hot", lease=True).lease is None


def test_get_hit_without_ttl_never_fires(store, clock, rng):
    with pytest.raises(KeyNotFound) as e:
        store.get("hot", lease=True)
    clock.advance(200)
    store.set("hot", "v", lease=e.value.extra["lease"])  # 만료 없음
    rng.value = FORCE
    assert store.get("hot", lease=True).lease is None


def test_get_hit_only_one_client_gets_early_lease_others_get_plain_hit(store, clock, rng):
    fill_via_lease(store, clock, ttl_ms=1000, work_ms=200)
    clock.advance(500)
    rng.value = FORCE
    items = [store.get("hot", lease=True) for _ in range(50)]
    assert sum(i.lease is not None for i in items) == 1
    assert all(i.value == "v1" for i in items)  # 모두 값은 받는다 (기다림 없음)


def test_get_hit_early_lease_expires_after_lease_ttl_then_another_can_take_over(store, clock, rng):
    fill_via_lease(store, clock, ttl_ms=60_000, work_ms=9_000)  # delta를 크게 해서 멀리서도 당첨 (lease 10초 안)
    clock.advance(40_000)
    rng.value = FORCE
    first = store.get("hot", lease=True).lease
    assert first is not None
    assert store.get("hot", lease=True).lease is None
    clock.advance(LEASE_TTL_MS)  # 보유자가 죽었다
    second = store.get("hot", lease=True).lease
    assert second is not None and second != first


def test_get_hit_after_expiry_is_a_miss_not_early_refresh(store, clock, rng):
    fill_via_lease(store, clock, ttl_ms=1000)
    clock.advance(1000)
    rng.value = FORCE
    with pytest.raises(KeyNotFound) as e:
        store.get("hot", lease=True)
    assert "lease" in e.value.extra  # 일반 miss의 lease


# --- 조기 갱신 PUT ---

def test_early_refresh_put_with_token_replaces_value_and_extends_ttl(store, clock, rng):
    fill_via_lease(store, clock, ttl_ms=1000, work_ms=200)
    clock.advance(500)
    rng.value = FORCE
    token = store.get("hot", lease=True).lease
    clock.advance(180)  # 원본 호출
    store.set("hot", "v2", expire_at_ms=clock() + 10_000, lease=token)
    item = store.get("hot")
    assert item.value == "v2"
    assert item.expire_at_ms == clock() + 10_000
    assert delta_of(store) == 180  # 새로 측정한 delta로 바뀐다


def test_early_refresh_token_is_single_use(store, clock, rng):
    fill_via_lease(store, clock, ttl_ms=1000)
    clock.advance(500)
    rng.value = FORCE
    token = store.get("hot", lease=True).lease
    store.set("hot", "v2", expire_at_ms=clock() + 10_000, lease=token)
    with pytest.raises(LeaseInvalid):
        store.set("hot", "v3", expire_at_ms=clock() + 10_000, lease=token)


def test_early_refresh_token_invalid_after_delete(store, clock, rng):
    fill_via_lease(store, clock, ttl_ms=1000)
    clock.advance(500)
    rng.value = FORCE
    token = store.get("hot", lease=True).lease
    store.delete("hot")
    with pytest.raises(LeaseInvalid):
        store.set("hot", "stale", expire_at_ms=clock() + 10_000, lease=token)
    assert store.exists("hot") is False


def test_early_refresh_token_invalid_after_plain_overwrite(store, clock, rng):
    fill_via_lease(store, clock, ttl_ms=1000)
    clock.advance(500)
    rng.value = FORCE
    token = store.get("hot", lease=True).lease
    store.set("hot", "newer", expire_at_ms=clock() + 10_000)  # 원본이 먼저 바뀌어 직접 쓴 경우
    with pytest.raises(LeaseInvalid):
        store.set("hot", "stale", expire_at_ms=clock() + 10_000, lease=token)
    assert store.get("hot").value == "newer"


def test_early_refresh_holder_dies_value_still_served_until_real_expiry(store, clock, rng):
    fill_via_lease(store, clock, ttl_ms=1000)
    clock.advance(500)
    rng.value = FORCE
    store.get("hot", lease=True)  # 토큰을 받았지만 PUT하지 않는다
    clock.advance(499)
    assert store.get("hot").value == "v1"  # 평범한 히트는 계속 받는다


# --- 효과: 핫 키가 만료로 miss를 내지 않는다 ---

def simulate_hot_key(store, clock, *, total_ms=120_000, poll_ms=5, ttl_ms=1000, origin_ms=200):
    """5ms마다 한 클라이언트가 GET ?lease=1을 한다. lease를 받으면 origin_ms 뒤에 PUT한다.

    miss 응답 수(= 갱신이 끝나기를 기다리며 404를 받은 요청)와 조기 갱신 횟수를 센다.
    """
    misses = early = 0
    pending = None  # (token, 완료 시각)
    end = clock() + total_ms
    while clock() < end:
        if pending is not None and clock() >= pending[1]:
            store.set("hot", "v", expire_at_ms=clock() + ttl_ms, lease=pending[0])
            pending = None
        try:
            item = store.get("hot", lease=True)
            if item.lease is not None and pending is None:
                early += 1
                pending = (item.lease, clock() + origin_ms)
        except KeyNotFound as e:
            misses += 1
            token = e.extra.get("lease")
            if token is not None and pending is None:
                pending = (token, clock() + origin_ms)
        clock.advance(poll_ms)
    return misses, early


def test_hot_key_with_xfetch_almost_never_misses_but_without_it_misses_every_cycle():
    import random

    c1 = FakeClock(T0)
    without = Store(clock=c1, random_fn=Rng(NEVER))  # 추첨이 절대 당첨되지 않는다 = lease만 쓴 경우
    misses_without, early_without = simulate_hot_key(without, c1)

    c2 = FakeClock(T0)
    with_xfetch = Store(clock=c2, random_fn=random.Random(1).random)
    misses_with, early_with = simulate_hot_key(with_xfetch, c2)

    assert early_without == 0
    assert misses_without >= 1000  # 약 1.2초마다 만료되고, 갱신하는 200ms 동안 5ms마다 오는 요청이 모두 miss (실측 4000)
    assert early_with >= 50  # 만료 전에 미리 갱신이 일어난다 (실측 271)
    assert misses_with <= misses_without * 0.1
