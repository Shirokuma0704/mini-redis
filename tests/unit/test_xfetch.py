import math
import random

import pytest

from mini_redis.xfetch import should_refresh_early

EXPIRE = 1_000_000


def test_should_refresh_early_u_one_means_only_at_or_after_expiry():
    # u=1이면 ln(u)=0이라 식이 now >= expire가 된다
    assert should_refresh_early(EXPIRE - 1, EXPIRE, 200, 1.0, 1.0) is False
    assert should_refresh_early(EXPIRE, EXPIRE, 200, 1.0, 1.0) is True


def test_should_refresh_early_small_u_pushes_decision_earlier():
    # u=e^-5 -> 200 * 5 = 1000ms 앞당김
    u = math.exp(-5)
    assert should_refresh_early(EXPIRE - 1000, EXPIRE, 200, 1.0, u) is True
    assert should_refresh_early(EXPIRE - 1001, EXPIRE, 200, 1.0, u) is False


def test_should_refresh_early_zero_or_negative_delta_never_early():
    assert should_refresh_early(EXPIRE - 1, EXPIRE, 0, 1.0, 1e-9) is False
    assert should_refresh_early(EXPIRE - 1, EXPIRE, -5, 1.0, 1e-9) is False


def test_should_refresh_early_beta_scales_how_early():
    u = math.exp(-5)
    assert should_refresh_early(EXPIRE - 1500, EXPIRE, 200, 1.0, u) is False
    assert should_refresh_early(EXPIRE - 1500, EXPIRE, 200, 2.0, u) is True  # 200*2*5 = 2000


@pytest.mark.parametrize("remaining_ms, delta_ms", [(200, 200), (400, 200), (1000, 200), (50, 100)])
def test_should_refresh_early_probability_matches_exp_of_remaining_over_delta(remaining_ms, delta_ms):
    rng = random.Random(42)
    n = 40_000
    hits = sum(
        should_refresh_early(EXPIRE - remaining_ms, EXPIRE, delta_ms, 1.0, 1.0 - rng.random())
        for _ in range(n)
    )
    expected = math.exp(-remaining_ms / delta_ms)
    assert hits / n == pytest.approx(expected, abs=0.01)


def test_should_refresh_early_far_from_expiry_almost_never_fires():
    rng = random.Random(7)
    hits = sum(
        should_refresh_early(EXPIRE - 60_000, EXPIRE, 200, 1.0, 1.0 - rng.random()) for _ in range(50_000)
    )
    assert hits == 0  # exp(-300) ~ 0
