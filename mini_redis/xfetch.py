"""XFetch: 확률적 조기 갱신 (Vattani, Chierichetti, Lowenstein, VLDB 2015).

캐시 값이 만료되기 직전에 모든 클라이언트가 한꺼번에 miss를 만나 몰리는 일(스탬피드)을
lease만으로는 "한 명만 원본을 부른다"까지만 막는다. 그 한 명이 원본을 부르는 동안(delta) 다른
클라이언트는 기다려야 하므로, 만료 전에 미리 한 명이 갱신을 시작하면 기다림 자체가 없어진다.

갱신 시점은 요청마다 독립적으로 다음 식이 참일 때다.

    now - delta * beta * ln(u)  >=  expire_at        (u는 (0, 1]의 난수, ln(u) <= 0)

남은 시간이 delta보다 훨씬 길면 거의 참이 되지 않고, 만료에 가까울수록 확률이 급격히 커진다.
확률은 exp(-남은시간 / (delta * beta))다. beta를 키우면 더 일찍 갱신한다.
delta는 서버가 lease 발급부터 PUT까지 걸린 시간으로 직접 잰다 (Store.set).

이 모듈은 순수 함수만 둔다. 락도 시계도 난수 공급도 Store가 맡는다.
"""
import math


def should_refresh_early(now_ms: int, expire_at_ms: int, delta_ms: float, beta: float, u: float) -> bool:
    """지금 이 요청이 조기 갱신을 맡아야 하면 True.

    u는 (0, 1] 범위여야 한다 (0이면 ln이 발산한다).
    delta_ms가 0 이하이면(측정값이 없음) 조기 갱신은 하지 않는다.
    """
    if delta_ms <= 0:
        return False
    return now_ms - delta_ms * beta * math.log(u) >= expire_at_ms
