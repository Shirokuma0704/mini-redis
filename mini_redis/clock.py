"""시계 주입용. 벽시계 epoch ms를 돌려주는 함수를 Store에 넘긴다."""
import time


def system_clock_ms() -> int:
    return int(time.time() * 1000)


class FakeClock:
    """테스트용 가짜 시계 (sleep 대신 사용)."""

    def __init__(self, now_ms: int = 0):
        self.now_ms = now_ms

    def __call__(self) -> int:
        return self.now_ms

    def advance(self, ms: int) -> None:
        self.now_ms += ms
