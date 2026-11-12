"""직접 구현한 해시테이블 (체이닝, FNV-1a 64비트, 점진적 리해싱).

dict / set / collections.* 는 사용하지 않는다. 버킷은 고정 길이 list.
락은 잡지 않는다 (Store가 전역 RLock으로 보호).

리사이즈는 Redis처럼 점진적으로 한다(#24). load factor를 넘으면 2배 크기 새 배열만 만들고,
그 뒤 get/put/delete가 불릴 때마다 옛 배열의 버킷을 앞에서부터 rehash_buckets개씩 새 배열로 옮긴다.
옮기는 동안 조회·삭제는 두 배열을 다 보고, 새 키는 새 배열에만 넣는다. 다 옮기면 옛 배열을 버린다.
"""
from .config import INITIAL_CAPACITY, LOAD_FACTOR, REHASH_BUCKETS_PER_STEP

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


def _find(node, key):
    while node is not None and node.key != key:
        node = node.next
    return node


def _unlink(buckets, idx, key) -> bool:
    """체인의 처음·중간·끝 어디서든 key를 떼어낸다. 떼었으면 True."""
    prev, node = None, buckets[idx]
    while node is not None:
        if node.key == key:
            if prev is None:
                buckets[idx] = node.next
            else:
                prev.next = node.next
            node.next = None
            return True
        prev, node = node, node.next
    return False


class HashTable:
    def __init__(self, capacity: int = INITIAL_CAPACITY, hash_fn=fnv1a_64, rehash_buckets: int = REHASH_BUCKETS_PER_STEP):
        if capacity < 1 or capacity & (capacity - 1):
            raise ValueError("capacity must be a power of two")
        if rehash_buckets < 1:
            raise ValueError("rehash_buckets must be positive")
        self._hash_fn = hash_fn  # 테스트에서 충돌을 강제하기 위해 주입 가능
        self._rehash_buckets = rehash_buckets  # 배열보다 크면 리사이즈를 한 번에 끝낸다(예전 일괄 리사이즈, 측정용)
        self._buckets = [None] * capacity  # 새 키가 들어가는 배열. 리해싱 중에는 새 배열
        self._old = None  # 리해싱 중에만: 비우고 있는 옛 배열
        self._rehash_idx = 0  # _old에서 다음에 옮길 버킷. 그 앞은 이미 비었다
        self._iterating = 0  # 돌고 있는 items() 수. 0이 아니면 버킷을 옮기지 않는다
        self._size = 0  # 두 배열을 합친 엔트리 수

    @property
    def size(self) -> int:
        return self._size

    @property
    def capacity(self) -> int:
        return len(self._buckets)

    def _lookup(self, h, key):
        node = _find(self._buckets[h & (len(self._buckets) - 1)], key)
        if node is None and self._old is not None:
            node = _find(self._old[h & (len(self._old) - 1)], key)
        return node

    def get(self, key):
        """엔트리를 반환, 없으면 None. 평균 O(1), 최악 O(n)."""
        self._rehash_step()
        return self._lookup(self._hash_fn(key), key)

    def put(self, key, value, expire_at_ms=None, delta_ms=None) -> bool:
        """있으면 값과 만료 시각을 교체, 없으면 체인 머리에 삽입.

        새 키가 들어갔으면 True, 덮어썼으면 False.
        """
        self._rehash_step()
        h = self._hash_fn(key)
        node = self._lookup(h, key)
        if node is not None:  # 옛 배열에 있으면 그 자리에서 고친다. 나중에 버킷째 옮겨진다
            node.value = value
            node.expire_at_ms = expire_at_ms
            node.delta_ms = delta_ms
            return False
        idx = h & (len(self._buckets) - 1)
        self._buckets[idx] = Entry(key, value, expire_at_ms, self._buckets[idx], delta_ms)
        self._size += 1
        if self._old is None and self._size / len(self._buckets) > LOAD_FACTOR:
            self._old, self._buckets, self._rehash_idx = self._buckets, [None] * (len(self._buckets) * 2), 0
            self._rehash_step()
        return True

    def delete(self, key) -> bool:
        """두 배열 중 있는 쪽에서 떼어낸다. 지웠으면 True."""
        self._rehash_step()
        h = self._hash_fn(key)
        for buckets in (self._buckets, self._old):
            if buckets is not None and _unlink(buckets, h & (len(buckets) - 1), key):
                self._size -= 1
                return True
        return False

    def items(self):
        """모든 엔트리를 한 번씩 순회. O(capacity + n). 순회 중 지금 엔트리를 지워도 안전하다.

        도는 동안에는 버킷을 옮기지 않는다(Redis의 safe iterator와 같은 생각). 옮기면 지금 보는 체인이
        새 배열 체인에 붙어, 이미 내보낸 엔트리를 새 배열에서 다시 내보낸다.
        ponytail: 끝까지 돌지도 닫히지도 않은 제너레이터를 붙들고 있으면 그동안 리해싱이 멈춘다
        (조회는 맞고 새 배열 체인만 길어진다). CPython은 for 루프가 끝나거나 break하면 바로 닫는다.
        """
        self._iterating += 1
        try:
            for buckets in (self._old, self._buckets):  # 시작할 때 두 배열을 잡아 둔다
                if buckets is None:
                    continue
                for head in buckets:
                    node = head
                    while node is not None:
                        nxt = node.next  # 순회 중 삭제에도 안전
                        yield node
                        node = nxt
        finally:
            self._iterating -= 1

    def _rehash_step(self) -> None:
        """옛 배열의 버킷을 앞에서부터 최대 rehash_buckets개(빈 버킷 포함) 새 배열로 옮긴다. 축소는 없다."""
        old = self._old
        if old is None or self._iterating:
            return
        new, hash_fn = self._buckets, self._hash_fn
        mask = len(new) - 1
        end = min(self._rehash_idx + self._rehash_buckets, len(old))
        for i in range(self._rehash_idx, end):
            node, old[i] = old[i], None
            while node is not None:
                nxt = node.next
                idx = hash_fn(node.key) & mask
                node.next = new[idx]
                new[idx] = node
                node = nxt
        self._rehash_idx = end
        if end == len(old):
            self._old = None
