"""네임스페이스 버전 테이블 ns -> version (docs/spec.md "네임스페이스 버전 키").

직접 구현한 HashTable에 둔다. 엔트리의 value가 버전(int)이고 만료는 없다.
락은 잡지 않는다 (Store가 전역 RLock으로 보호). FLUSH에도 유지한다.
"""
from .hashtable import HashTable


class VersionTable:
    def __init__(self):
        self._table = HashTable()

    def current(self, ns: str, now_ms: int) -> int:
        """ns의 현재 버전. 처음 보는 ns면 지금 시각(ms)을 초기 버전으로 기록한다."""
        entry = self._table.get(ns)
        if entry is not None:
            return entry.value
        self._table.put(ns, now_ms)  # AOF(#12): nsver로 기록
        return now_ms

    def invalidate(self, ns: str, now_ms: int) -> int:
        """버전을 max(현재 + 1, now_ms)로 올리고 돌려준다.

        +1은 같은 ms에 두 번 불러도, 시계가 뒤로 가도 버전이 되돌아가지 않게 한다 (낡은 키가 되살아나지 않게).
        now_ms는 재시작 뒤 버전 테이블을 잃어도 새 버전이 옛 버전보다 크게 한다.
        """
        version = max(self.current(ns, now_ms) + 1, now_ms)
        self._table.put(ns, version)  # AOF(#12): nsver로 기록
        return version
