"""명세 "제약과 엣지 케이스" 표에서 아직 테스트가 없던 줄을 채우는 테스트예요.

빈 키/긴 키, 같은 키 동시 PUT, 시계가 뒤로 가는 경우, 해시테이블 리사이즈 전후 조회를 다뤄요.
시간은 FakeClock으로만 움직이고 sleep은 쓰지 않아요.
"""
import threading

import pytest

from mini_redis.clock import FakeClock
from mini_redis.config import LOAD_FACTOR
from mini_redis.errors import InvalidKey, KeyNotFound
from mini_redis.hashtable import HashTable
from mini_redis.store import Store

T0 = 1_000_000


class RecordingAof:
    def __init__(self):
        self.records = []

    def append(self, record):
        self.records.append(record)


# 명세 줄: 빈 키, 256바이트 초과 키 | 400 INVALID_KEY
@pytest.mark.parametrize("key", ["", "x" * 257, "한" * 86], ids=["empty", "257_bytes", "258_bytes_hangul"])
def test_store_set_invalid_key_raises_400_invalid_key_and_stores_nothing(key):
    store = Store(clock=FakeClock(T0))
    with pytest.raises(InvalidKey) as info:
        store.set(key, "v")
    assert info.value.status == 400
    assert info.value.code == "INVALID_KEY"
    assert store.size() == 0


def test_store_set_key_of_exactly_256_bytes_succeeds_and_reads_back():
    store = Store(clock=FakeClock(T0))
    key = "x" * 256
    store.set(key, "v")
    assert store.get(key).value == "v"


# 명세 줄: 같은 키에 동시 PUT | 락을 잡은 순서대로 처리, 최종값은 보낸 값 중 하나
def test_store_set_concurrent_same_key_final_value_is_one_sent_and_log_follows_lock_order():
    threads_n, rounds = 8, 200
    aof = RecordingAof()
    store = Store(clock=FakeClock(T0), aof=aof)
    barrier = threading.Barrier(threads_n)
    errors = []

    def worker(i):
        try:
            barrier.wait(timeout=10)
            for j in range(rounds):
                store.set("k", f"v{i}-{j}")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,), daemon=True) for i in range(threads_n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    hung = [t for t in threads if t.is_alive()]
    assert not hung, f"{len(hung)}개 스레드가 30초 안에 끝나지 않았어요"
    assert errors == []

    sent = {f"v{i}-{j}" for i in range(threads_n) for j in range(rounds)}
    final = store.get("k").value
    assert final in sent

    sets = [r for r in aof.records if r["op"] == "set" and r["k"] == "k"]
    assert len(sets) == threads_n * rounds
    assert sets[-1]["v"] == final  # 로그 순서 = 락 순서
    for i in range(threads_n):
        mine = [r["v"] for r in sets if r["v"].startswith(f"v{i}-")]
        assert mine == [f"v{i}-{j}" for j in range(rounds)]


# 명세 줄: 시스템 시계가 뒤로 감 | 만료가 늦어지거나 일찍 일어남 (알려진 한계)
# 아래 두 테스트는 명세가 적어 둔 알려진 한계를 그대로 고정해 두는 거예요. 버그가 아니에요.
def test_ttl_clock_went_back_expiry_is_late_known_limitation():
    clock = FakeClock(T0)
    store = Store(clock=clock)
    store.set("k", "v", expire_at_ms=T0 + 10_000)
    clock.now_ms -= 5_000
    clock.advance(10_000)  # SET 이후 실제로는 15초가 지났다
    assert store.get("k").value == "v"
    assert store.ttl("k") > 0


def test_ttl_clock_jumped_forward_expiry_is_early_known_limitation():
    clock = FakeClock(T0)
    store = Store(clock=clock)
    store.set("k", "v", expire_at_ms=T0 + 10_000)
    clock.advance(20_000)  # 실제 시간은 지나지 않았다
    with pytest.raises(KeyNotFound):
        store.get("k")


# 체크리스트: 해시테이블 리사이즈 전후 전체 조회
def _max_keys_before_resize(capacity):
    n = 0
    while (n + 1) / capacity <= LOAD_FACTOR:
        n += 1
    return n


def _same_bucket(_key):
    return 0


@pytest.mark.parametrize("hash_fn", [None, _same_bucket], ids=["default_hash", "single_bucket_chain"])
def test_hashtable_resize_all_keys_readable_just_before_and_just_after(hash_fn):
    table = HashTable() if hash_fn is None else HashTable(hash_fn=hash_fn)
    cap0 = table.capacity
    n = _max_keys_before_resize(cap0)
    for i in range(n):
        table.put(f"k{i}", f"v{i}")
    assert table.capacity == cap0
    for i in range(n):
        assert table.get(f"k{i}").value == f"v{i}"

    table.put(f"k{n}", f"v{n}")
    assert table.capacity == 2 * cap0
    assert table.size == n + 1
    for i in range(n + 1):
        assert table.get(f"k{i}").value == f"v{i}"
