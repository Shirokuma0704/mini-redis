"""계층형 타이밍 휠과 백그라운드 만료 스레드.

1단 64슬롯 x 100ms, 2단 64슬롯 x 6.4초, 그보다 먼 만료는 overflow 최소 힙.
2단 슬롯 하나는 1단 한 바퀴(64 tick)를 덮으므로 슬롯 안을 tick별 64칸으로 나눠 두고,
내려보낼 때 같은 번호 칸끼리 list.extend로 붙인다. overflow는 휠 범위에 들어온 항목을
tick마다 최대 WHEEL_OVERFLOW_MOVE_PER_TICK개씩 옮긴다. 둘 다 tick 하나의 락 점유를 묶으려는 것이다.
휠 항목은 (key, expire_at_ms) 쌍이다. 꺼낼 때 엔트리의 현재 만료 시각과 다르면
낡은 항목으로 보고 무시하므로, TTL 갱신, 덮어쓰기, 삭제 때 휠에서 지울 필요가 없다.

휠은 락을 잡지 않는다. Store가 전역 락 안에서 add/expire를 부른다.

Store가 지킬 것 (낡은 항목이 쌓이지 않게):
- 만료가 생기거나 앞당겨질 때만 add한다. 만료를 늦추는 EXPIRE에서는 add하지 않는다.
- expire 콜백이 낡은 항목을 받았을 때, 엔트리가 남아 있고 만료가 그보다 늦으면
  그 시각으로 add한다. 그러면 키마다 휠 항목이 대개 하나로 유지된다.
"""
import heapq
import logging
import threading

from .config import (
    EXPIRE_MAX_PER_TICK,
    EXPIRE_TICK_MS,
    WHEEL_L1_MS,
    WHEEL_L1_SLOTS,
    WHEEL_L2_MS,
    WHEEL_L2_SLOTS,
    WHEEL_OVERFLOW_MOVE_PER_TICK,
)

L2_SPAN = WHEEL_L2_MS // WHEEL_L1_MS  # 2단 슬롯 하나가 덮는 tick 수 (64)
# 2단 슬롯의 칸 번호(t % L2_SPAN)를 1단 칸 번호(t % WHEEL_L1_SLOTS)로 그대로 쓰려면 둘이 같아야 한다
assert L2_SPAN == WHEEL_L1_SLOTS

log = logging.getLogger(__name__)


def _tick_of(expire_at_ms: int) -> int:
    # 올림: 만료 시각보다 일찍 꺼내지 않는다 (최대 100ms 늦음)
    return -(-expire_at_ms // WHEEL_L1_MS)


def _fits_l2(t: int, cur: int) -> bool:
    return t // L2_SPAN - cur // L2_SPAN <= WHEEL_L2_SLOTS


class TimingWheel:
    def __init__(self, now_ms: int):
        self._tick = now_ms // WHEEL_L1_MS  # 마지막으로 처리한 tick
        self._l1 = [[] for _ in range(WHEEL_L1_SLOTS)]
        self._l2 = [[[] for _ in range(L2_SPAN)] for _ in range(WHEEL_L2_SLOTS)]  # [슬롯][t % 64]
        self._overflow = []  # (expire_at_ms, key) 최소 힙
        self._due = []  # 만료됐지만 tick당 상한 때문에 아직 못 꺼낸 항목

    def add(self, key, expire_at_ms: int) -> None:
        t = _tick_of(expire_at_ms)
        cur = self._tick
        if t <= cur:
            self._due.append((key, expire_at_ms))
        elif t - cur <= WHEEL_L1_SLOTS:
            self._l1[t % WHEEL_L1_SLOTS].append((key, expire_at_ms))
        elif _fits_l2(t, cur):
            self._l2[t // L2_SPAN % WHEEL_L2_SLOTS][t % L2_SPAN].append((key, expire_at_ms))
        else:
            heapq.heappush(self._overflow, (expire_at_ms, key))

    def expire(self, now_ms: int, delete_if_current, limit: int = EXPIRE_MAX_PER_TICK) -> int:
        """now_ms까지 만료된 항목을 최대 limit개 꺼내 delete_if_current(key, expire_at_ms)를 부른다.

        콜백은 엔트리의 만료 시각이 같을 때만 지우고 True를 돌려준다(낡은 항목이면 False).
        낡은 항목도 limit에 세어 한 번에 락을 잡는 시간을 묶는다. 남은 항목은 다음 호출로
        넘긴다. 지운 개수를 돌려준다.
        """
        self._advance(now_ms // WHEEL_L1_MS)
        deleted = 0
        for _ in range(min(limit, len(self._due))):
            key, expire_at_ms = self._due.pop()
            if delete_if_current(key, expire_at_ms):
                deleted += 1
        return deleted

    def _advance(self, target: int) -> None:
        # ponytail: 빈 tick도 하나씩 돈다. 시계가 며칠씩 앞으로 뛰면 그만큼 락을 오래 잡음, 문제되면 빈 구간 건너뛰기
        while self._tick < target:
            t = self._tick + 1
            # 옮기기와 내려보내기는 커서가 t-1인 상태에서 한다. t에 만료될 항목은 아래에서 바로 꺼낸다.
            # overflow: 힙이라 맨 앞이 가장 이른 만료다. 휠 범위에 들어온 것만 tick당 상한만큼 옮긴다.
            # 항목은 만료 409.6초 전에 범위에 들어오므로 상한 때문에 늦어져도 만료 전에 휠에 들어간다.
            for _ in range(WHEEL_OVERFLOW_MOVE_PER_TICK):
                if not (self._overflow and _fits_l2(_tick_of(self._overflow[0][0]), t - 1)):
                    break
                expire_at_ms, key = heapq.heappop(self._overflow)
                self.add(key, expire_at_ms)
            if t % L2_SPAN == 0:
                # 불변식: 커서가 t-1이면 1단에는 tick [t, t+63] 항목만 있고(add가 커서+64까지만 1단에 넣음),
                # 2단 슬롯 s에도 같은 64 tick 항목만 있다(_fits_l2가 다음 64구간만 받아 슬롯이 겹치지 않음).
                # 그래서 칸 i끼리는 둘 다 tick t+i라 그대로 붙인다.
                # 항목 수와 상관없이 파이썬 루프는 64번이다.
                sub = self._l2[t // L2_SPAN % WHEEL_L2_SLOTS]
                for i in range(L2_SPAN):
                    self._l1[i].extend(sub[i])
                    sub[i] = []
            s = t % WHEEL_L1_SLOTS
            self._due.extend(self._l1[s])
            self._l1[s] = []
            self._tick = t


def start_expiry_thread(tick, interval_ms: int = EXPIRE_TICK_MS) -> threading.Event:
    """interval_ms마다 tick()을 부르는 데몬 스레드를 띄운다. 돌려준 Event를 set하면 멈춘다.

    tick은 Store가 넘기는 함수로, 전역 락을 잡고 wheel.expire(now, ...)를 부른다.
    tick이 예외를 내도 기록만 하고 다음 tick을 계속 돈다.
    """
    stop = threading.Event()

    def loop():
        while not stop.wait(interval_ms / 1000):
            try:
                tick()
            except Exception:
                log.exception("만료 tick 실패, 다음 tick에서 다시 시도")

    threading.Thread(target=loop, name="expiry", daemon=True).start()
    return stop
