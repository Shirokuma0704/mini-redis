"""Store: 전역 RLock 하나로 모든 명령을 직렬화하는 계층.

API -> Store -> HashTable 순서로만 호출한다. 락을 잡는 곳은 Store뿐이다.
공개 메서드는 모두 @locked 로 감싸 명령 하나하나가 원자적이 되게 한다
(Redis의 명령 직렬 실행과 같은 효과). 디스크 I/O는 절대 락 안에서 하지 않는다.
"""
import functools
import re
import threading
from dataclasses import dataclass

from .clock import system_clock_ms
from .config import MAX_KEY_BYTES, MAX_KEYS, MAX_VALUE_BYTES
from .errors import (
    CapacityExceeded,
    InvalidKey,
    InvalidValue,
    KeyNotFound,
    NotInteger,
    ValueTooLarge,
)
from .hashtable import HashTable


def locked(method):
    """공개 메서드가 Store의 전역 RLock을 잡고 실행되게 한다.

    RLock이라 Store 내부에서 다른 공개 메서드를 불러도 교착되지 않는다.
    """

    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)

    return wrapper


def validate_key(key) -> None:
    """키는 비어 있지 않은 문자열이고 UTF-8로 1~256바이트여야 한다."""
    if not isinstance(key, str):
        raise InvalidKey("키는 문자열이어야 합니다")
    size = len(key.encode("utf-8", errors="surrogatepass"))
    if size < 1:
        raise InvalidKey("키는 비어 있을 수 없습니다")
    if size > MAX_KEY_BYTES:
        raise InvalidKey(f"키는 {MAX_KEY_BYTES}바이트 이하여야 합니다")


def validate_value(value) -> None:
    """값은 문자열이고 1MB 이하여야 한다. 빈 문자열은 허용한다."""
    if not isinstance(value, str):
        raise InvalidValue("값은 문자열이어야 합니다")
    if len(value.encode("utf-8", errors="surrogatepass")) > MAX_VALUE_BYTES:
        raise ValueTooLarge(f"값은 {MAX_VALUE_BYTES}바이트 이하여야 합니다")


INT64_MIN = -(2**63)
INT64_MAX = 2**63 - 1
_INT_RE = re.compile(r"-?[0-9]{1,20}")  # ASCII 10진수만. int()는 공백·밑줄·전각숫자도 받아서 쓰지 않는다


def parse_int64(text: str) -> int:
    """10진 정수 문자열을 int로. 아니거나 64비트 범위 밖이면 NotInteger(409)."""
    if not _INT_RE.fullmatch(text):
        raise NotInteger("값이 10진 정수가 아닙니다")
    n = int(text)
    if not INT64_MIN <= n <= INT64_MAX:
        raise NotInteger("값이 64비트 정수 범위를 벗어납니다")
    return n


@dataclass(frozen=True)
class Item:
    """GET 결과. 엔트리 자체(가변)를 락 밖으로 내보내지 않으려고 복사해서 돌려준다."""

    value: str
    expire_at_ms: int | None  # None이면 무기한


class Store:
    def __init__(
        self,
        clock=system_clock_ms,
        max_keys: int = MAX_KEYS,
    ):
        """clock: epoch ms를 돌려주는 함수. 테스트는 FakeClock을 넣는다."""
        self._clock = clock
        self._max_keys = max_keys
        self._lock = threading.RLock()
        self._table = HashTable()

    # --- 내부 도우미 (호출하는 공개 메서드가 락을 이미 잡고 있다) ---

    def _now_ms(self) -> int:
        return self._clock()

    def _check_capacity(self, key: str) -> None:
        """새 키를 넣을 때만 상한을 검사한다 (덮어쓰기는 키 수가 늘지 않는다)."""
        if self._table.get(key) is None and self._table.size >= self._max_keys:
            raise CapacityExceeded(f"키 개수 상한({self._max_keys})을 초과했습니다")

    def _get_live(self, key: str):
        """살아 있는 엔트리를 돌려준다. 만료됐으면 그 자리에서 지우고 None (lazy 만료).

        "만료됨"과 "원래 없음"은 구분하지 않는다.
        """
        entry = self._table.get(key)
        if entry is None:
            return None
        if entry.expire_at_ms is not None and self._now_ms() >= entry.expire_at_ms:
            self._table.delete(key)
            return None
        return entry

    # --- 공개 메서드 ---

    @locked
    def now_ms(self) -> int:
        return self._now_ms()

    @locked
    def size(self) -> int:
        """저장된 엔트리 수 (만료됐지만 아직 회수되지 않은 키 포함)."""
        return self._table.size

    @locked
    def set(self, key: str, value: str, expire_at_ms: int | None = None) -> None:
        """키를 저장한다. 이미 있으면 값과 만료 시각을 통째로 교체한다.

        expire_at_ms를 안 주면 기존 TTL도 사라진다 (Redis와 동일).
        TTL(초) -> 절대 시각 변환과 INVALID_TTL 검증은 TTL 이슈(#7)에서 한다.
        """
        validate_key(key)
        validate_value(value)
        self._get_live(key)  # 같은 키의 만료된 옛 엔트리는 새 키로 취급하려고 먼저 지운다
        self._check_capacity(key)
        self._table.put(key, value, expire_at_ms)

    @locked
    def get(self, key: str) -> Item:
        """없거나 만료됐으면 KeyNotFound(404)."""
        validate_key(key)
        entry = self._get_live(key)
        if entry is None:
            raise KeyNotFound(f"키 '{key}'가 없습니다")
        return Item(entry.value, entry.expire_at_ms)

    @locked
    def delete(self, key: str) -> bool:
        """지웠으면 True, 원래 없었으면 False (에러 아님, 키가 새로 생기지 않는다)."""
        validate_key(key)
        if self._get_live(key) is None:
            return False
        return self._table.delete(key)

    @locked
    def exists(self, key: str) -> bool:
        validate_key(key)
        return self._get_live(key) is not None

    @locked
    def incr(self, key: str, amount: int = 1) -> int:
        """값을 10진 정수로 보고 amount만큼 더한 결과를 돌려준다.

        - 없는(또는 만료된) 키는 0에서 시작하고 TTL 없는 새 키가 된다.
        - 기존 키의 TTL은 그대로 유지한다 (Redis와 동일).
        - 정수가 아니거나 결과가 64비트를 넘으면 NotInteger(409), 값은 바뀌지 않는다.
        읽기와 쓰기가 한 번의 락 안에서 일어나서 동시 호출에도 합이 맞는다.
        """
        validate_key(key)
        if isinstance(amount, bool) or not isinstance(amount, int):
            raise InvalidValue("amount는 정수여야 합니다")
        entry = self._get_live(key)
        if entry is None:
            self._check_capacity(key)
            current, expire_at_ms = 0, None
        else:
            current, expire_at_ms = parse_int64(entry.value), entry.expire_at_ms
        result = current + amount
        if not INT64_MIN <= result <= INT64_MAX:
            raise NotInteger("결과가 64비트 정수 범위를 벗어납니다")
        self._table.put(key, str(result), expire_at_ms)
        return result
