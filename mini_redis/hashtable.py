"""직접 구현한 해시테이블 (체이닝, FNV-1a 64비트).

dict / set / collections.* 사용 금지. 구현: Issue 'Core: 해시테이블'.
"""


class Entry:
    __slots__ = ("key", "value", "expire_at_ms", "next")

    def __init__(self, key, value, expire_at_ms=None, next=None):
        self.key = key
        self.value = value
        self.expire_at_ms = expire_at_ms
        self.next = next


class HashTable:
    def get(self, key):
        raise NotImplementedError

    def put(self, key, value, expire_at_ms=None):
        raise NotImplementedError

    def delete(self, key):
        raise NotImplementedError

    def items(self):
        raise NotImplementedError
