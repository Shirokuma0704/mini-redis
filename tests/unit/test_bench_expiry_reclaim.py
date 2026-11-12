from bench.expiry_reclaim import run
from mini_redis.config import EXPIRE_MAX_PER_TICK


def test_expiry_reclaim_burst_over_tick_cap_wheel_drains_at_cap_lazy_keeps_all():
    n = EXPIRE_MAX_PER_TICK * 15  # 휠이 비우는 데 15 tick(1.5초)
    ttl_1s = lambda rng: 1000

    lazy, _ = run(n, ttl_1s, use_wheel=False, seconds=3, reads_per_tick=0)
    wheel, _ = run(n, ttl_1s, use_wheel=True, seconds=3, reads_per_tick=0)

    assert lazy == [n, n, n]  # 아무도 읽지 않으면 만료 키가 그대로 남는다
    assert wheel == [n, n - 10 * EXPIRE_MAX_PER_TICK, 0]  # 1초(tick 10번)에 상한만큼씩 지운다
