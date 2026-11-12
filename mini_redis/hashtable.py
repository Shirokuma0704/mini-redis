"""직접 구현한 해시테이블 (체이닝, FNV-1a 64비트).

dict / set / collections.* 는 사용하지 않는다. 버킷은 고정 길이 list.
락은 잡지 않는다 (Store가 전역 RLock으로 보호).
"""
from .config import INITIAL_CAPACITY, LOAD_FACTOR

_FNV_OFFSET = 0xCBF29CE484222325
_FNV_PRIME = 0x100000001B3
_MASK64 = 0xFFFFFFFFFFFFFFFF


def fnv1a_64(key: str) -> int:
    """FNV-1a 64비트. Python 내장 hash()는 프로세스마다 달라 쓰지 않는다."""
    h = _FNV_OFFSET
    for byte in key.encode("utf-8"):
        h ^= byte
        h = (h * _FNV_PRIME) & _MASK64
    return h


class Entry:
    __slots__ = ("key", "value", "expire_at_ms", "delta_ms", "next")

    def __init__(self, key, value, expire_at_ms=None, next=None, delta_ms=None):
        self.key = key
        self.value = value
        self.expire_at_ms = expire_at_ms  # None이면 무기한
        self.delta_ms = delta_ms  # 값을 다시 만드는 데 걸린 시간(XFetch용 힌트). 영속화하지 않는다
        self.next = next


class HashTable:
    def __init__(self, capacity: int = INITIAL_CAPACITY, hash_fn=fnv1a_64):
        if capacity < 1 or capacity & (capacity - 1):
            raise ValueError("capacity must be a power of two")
        self._hash_fn = hash_fn  # 테스트에서 충돌을 강제하기 위해 주입 가능
        self._buckets = [None] * capacity
        self._size = 0

    @property
    def size(self) -> int:
        return self._size

    @property
    def capacity(self) -> int:
        return len(self._buckets)

    def _index(self, key: str) -> int:
        return self._hash_fn(key) & (len(self._buckets) - 1)

    def get(self, key):
        """엔트리를 반환, 없으면 None. 평균 O(1), 최악 O(n)."""
        node = self._buckets[self._index(key)]
        while node is not None:
            if node.key == key:
                return node
            node = node.next
        return None

    def put(self, key, value, expire_at_ms=None, delta_ms=None) -> bool:
        """있으면 값과 만료 시각을 교체, 없으면 체인 머리에 삽입.

        새 키가 들어갔으면 True, 덮어썼으면 False.
        """
        idx = self._index(key)
        node = self._buckets[idx]
        while node is not None:
            if node.key == key:
                node.value = value
                node.expire_at_ms = expire_at_ms
                node.delta_ms = delta_ms
                return False
            node = node.next
        self._buckets[idx] = Entry(key, value, expire_at_ms, self._buckets[idx], delta_ms)
        self._size += 1
        if self._size / len(self._buckets) > LOAD_FACTOR:
            self._resize()
        return True

    def delete(self, key) -> bool:
        """체인의 처음·중간·끝 어디서든 제거. 지웠으면 True."""
        idx = self._index(key)
        prev = None
        node = self._buckets[idx]
        while node is not None:
            if node.key == key:
                if prev is None:
                    self._buckets[idx] = node.next
                else:
                    prev.next = node.next
                node.next = None
                self._size -= 1
                return True
            prev, node = node, node.next
        return False

    def items(self):
        """모든 엔트리를 순회. O(capacity + n)."""
        for head in self._buckets:
            node = head
            while node is not None:
                nxt = node.next  # 순회 중 삭제에도 안전
                yield node
                node = nxt

    def _resize(self) -> None:
        """버킷을 2배로 늘리고 모든 엔트리를 한 번에 재배치. 축소는 없다."""
        old = self._buckets
        self._buckets = [None] * (len(old) * 2)
        mask = len(self._buckets) - 1
        for head in old:
            node = head
            while node is not None:
                nxt = node.next
                idx = self._hash_fn(node.key) & mask
                node.next = self._buckets[idx]
                self._buckets[idx] = node
                node = nxt
