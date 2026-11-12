"""Lease 토큰 테이블 (Facebook memcache, NSDI 2013 방식).

키 -> (토큰, 만료 시각)을 직접 구현한 해시테이블에 둔다. 엔트리의 value가 토큰이고
expire_at_ms가 발급 시각 + LEASE_TTL_MS다. 락은 잡지 않는다 (Store가 전역 RLock으로 보호).
일회성 상태라 스냅샷에도 AOF에도 넣지 않는다.
"""
import secrets

from .config import LEASE_RETRY_MS, LEASE_TTL_MS
from .hashtable import HashTable


class LeaseTable:
    def __init__(self):
        # ponytail: 만료된 lease는 만날 때만 지운다(lazy). 보유자가 PUT 없이 죽고 아무도 그 키를 다시 찾지 않으면
        # FLUSH 전까지 남는다. 쌓이는 게 보이면 lease 전용 타이밍 휠을 expire_tick에 붙인다
        self._table = HashTable()

    def _live(self, key: str, now_ms: int):
        entry = self._table.get(key)
        if entry is not None and now_ms >= entry.expire_at_ms:
            self._table.delete(key)
            return None
        return entry

    def acquire(self, key: str, now_ms: int):
        """새 lease를 발급해 (토큰, None)을, 살아 있는 lease가 있으면 (None, retry_after_ms)를 돌려준다.

        retry_after_ms는 LEASE_RETRY_MS이고, lease가 그보다 먼저 끝나면 끝나는 시각까지만 기다리게 한다.
        """
        entry = self._live(key, now_ms)
        if entry is not None:
            return None, min(LEASE_RETRY_MS, entry.expire_at_ms - now_ms)
        token = secrets.token_hex(8)  # 64비트
        self._table.put(key, token, now_ms + LEASE_TTL_MS)
        return token, None

    def issued_at(self, key: str, now_ms: int):
        """살아 있는 lease의 발급 시각(ms), 없으면 None. XFetch가 재계산 시간(delta)을 재는 데 쓴다."""
        entry = self._live(key, now_ms)
        return None if entry is None else entry.expire_at_ms - LEASE_TTL_MS

    def consume(self, key: str, token: str, now_ms: int) -> bool:
        """token이 살아 있는 lease와 같으면 lease를 지우고 True. 없거나 만료됐거나 다르면 False."""
        entry = self._live(key, now_ms)
        if entry is None or entry.value != token:
            return False
        self._table.delete(key)
        return True

    def drop(self, key: str) -> None:
        self._table.delete(key)

    def drop_prefix(self, prefix: str) -> None:
        for entry in self._table.items():
            if entry.key.startswith(prefix):
                self._table.delete(entry.key)
