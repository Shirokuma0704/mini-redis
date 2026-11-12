"""Store: 전역 RLock 하나로 모든 명령을 직렬화하는 계층.

API -> Store -> HashTable 순서로만 호출한다. 락을 잡는 곳은 Store뿐.
"""
import threading

from .clock import system_clock_ms


class Store:
    def __init__(self, clock=system_clock_ms):
        self._clock = clock
        self._lock = threading.RLock()
