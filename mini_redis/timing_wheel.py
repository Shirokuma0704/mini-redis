"""계층형 타이밍 휠과 백그라운드 만료 스레드.

1단 64슬롯 x 100ms, 2단 64슬롯 x 6.4초, 그보다 먼 만료는 overflow 목록.
휠 항목은 (key, expire_at_ms) 쌍이다. 꺼낼 때 엔트리의 현재 만료 시각과 다르면
낡은 항목으로 보고 무시하므로, TTL 갱신, 덮어쓰기, 삭제 때 휠에서 지울 필요가 없다.

휠은 락을 잡지 않는다. Store가 전역 락 안에서 add/expire를 부른다.
"""
import threading

from .config import (
    EXPIRE_MAX_PER_TICK,
    EXPIRE_TICK_MS,
    WHEEL_L1_MS,
    WHEEL_L1_SLOTS,
    WHEEL_L2_MS,
    WHEEL_L2_SLOTS,
)

L2_SPAN = WHEEL_L2_MS // WHEEL_L1_MS  # 2단 슬롯 하나가 덮는 tick 수 (64)
L2_ROUND = L2_SPAN * WHEEL_L2_SLOTS  # 2단 한 바퀴의 tick 수 (4096)


class TimingWheel:
    def __init__(self, now_ms: int):
        self._tick = now_ms // WHEEL_L1_MS  # 마지막으로 처리한 tick
        self._l1 = [[] for _ in range(WHEEL_L1_SLOTS)]
        self._l2 = [[] for _ in range(WHEEL_L2_SLOTS)]
        self._overflow = []
        self._due = []  # 만료됐지만 tick당 상한 때문에 아직 못 지운 항목

    def add(self, key, expire_at_ms: int) -> None:
        # 올림한 tick에 넣어 만료 시각보다 일찍 꺼내지 않는다 (최대 100ms 늦음)
        t = -(-expire_at_ms // WHEEL_L1_MS)
        cur = self._tick
        if t <= cur:
            self._due.append((key, expire_at_ms))
        elif t - cur <= WHEEL_L1_SLOTS:
            self._l1[t % WHEEL_L1_SLOTS].append((key, expire_at_ms))
        elif t // L2_SPAN - cur // L2_SPAN <= WHEEL_L2_SLOTS:
            self._l2[t // L2_SPAN % WHEEL_L2_SLOTS].append((key, expire_at_ms))
        else:
            self._overflow.append((key, expire_at_ms))

    def expire(self, now_ms: int, delete_if_current, limit: int = EXPIRE_MAX_PER_TICK) -> int:
        """now_ms까지 만료된 항목마다 delete_if_current(key, expire_at_ms)를 부른다.

        콜백은 엔트리의 만료 시각이 같을 때만 지우고 True를 돌려준다(낡은 항목이면 False).
        지운 개수가 limit에 닿으면 멈추고 남은 항목은 다음 호출로 넘긴다. 지운 개수를 돌려준다.
        """
        self._advance(now_ms // WHEEL_L1_MS)
        deleted = 0
        while self._due and deleted < limit:
            key, expire_at_ms = self._due.pop()
            if delete_if_current(key, expire_at_ms):
                deleted += 1
        return deleted

    def _advance(self, target: int) -> None:
        # ponytail: 빈 tick도 하나씩 돈다. 시계가 며칠씩 앞으로 뛰면 그만큼 락을 오래 잡음, 문제되면 빈 구간 건너뛰기
        while self._tick < target:
            t = self._tick + 1
            # 내려보낼 항목은 커서가 t-1인 상태에서 다시 넣는다. t에 만료될 항목은 아래에서 바로 꺼낸다.
            if t % L2_ROUND == 0:
                items, self._overflow = self._overflow, []
                self._readd(items)
            if t % L2_SPAN == 0:
                s = t // L2_SPAN % WHEEL_L2_SLOTS
                items, self._l2[s] = self._l2[s], []
                self._readd(items)
            s = t % WHEEL_L1_SLOTS
            self._due.extend(self._l1[s])
            self._l1[s] = []
            self._tick = t

    def _readd(self, items) -> None:
        for key, expire_at_ms in items:
            self.add(key, expire_at_ms)


def start_expiry_thread(tick, interval_ms: int = EXPIRE_TICK_MS) -> threading.Event:
    """interval_ms마다 tick()을 부르는 데몬 스레드를 띄운다. 돌려준 Event를 set하면 멈춘다.

    tick은 Store가 넘기는 함수로, 전역 락을 잡고 wheel.expire(now, ...)를 부른다.
    """
    stop = threading.Event()

    def loop():
        while not stop.wait(interval_ms / 1000):
            tick()

    threading.Thread(target=loop, name="expiry", daemon=True).start()
    return stop
