import random
import threading

from mini_redis.timing_wheel import TimingWheel, start_expiry_thread


def deleter(table, log=None):
    """테스트용 Store 흉내: 엔트리 만료 시각이 같을 때만 지운다."""

    def delete_if_current(key, expire_at_ms):
        if table.get(key) != expire_at_ms:
            return False
        del table[key]
        if log is not None:
            log.append(key)
        return True

    return delete_if_current


def test_wheel_random_expiries_deleted_within_one_tick_after_expiry():
    rng = random.Random(8)
    for start in (0, 123_456, 1_762_934_399_950):
        wheel, table, deleted_at = TimingWheel(start), {}, {}
        base = start - start % 100
        # 단 경계 바로 앞뒤 tick (1단 64, 2단 64x64)
        edges = [base + n * 100 + d for n in (0, 1, 63, 64, 65, 128, 4032, 4095, 4096, 4097, 4160, 4161, 8192) for d in (-1, 0, 1)]
        # 이미 만료, 1단, 2단, overflow 모두 섞임
        exps = edges + [start + rng.randint(-500, 1_000_000) for _ in range(300)]
        for i, exp in enumerate(exps):
            table[i] = exp
            wheel.add(i, exp)
        expected = dict(table)
        now = start
        while now <= start + 1_000_200:  # 만료 스레드처럼 100ms씩 진행
            log = []
            wheel.expire(now, deleter(table, log))
            for key in log:
                deleted_at[key] = now
            now += 100
        assert table == {}
        for key, exp in expected.items():
            due = max(exp, start)
            assert due <= deleted_at[key] < due + 200


def test_wheel_overflow_item_cascades_down_deleted_exactly_at_expiry():
    # 500초 뒤 만료: overflow -> (409.6초에) 2단 -> (499.2초에) 1단 -> 500초에 삭제
    wheel, table, deleted_at = TimingWheel(0), {"k": 500_000}, []
    wheel.add("k", 500_000)
    for now in range(0, 600_001, 100):
        if wheel.expire(now, deleter(table)):
            deleted_at.append(now)
    assert deleted_at == [500_000]


def test_wheel_extended_ttl_readded_by_callback_deleted_at_new_expiry():
    wheel, table = TimingWheel(0), {"a": 500}
    wheel.add("a", 500)
    table["a"] = 900  # 만료를 늦추는 EXPIRE: add하지 않는다

    def delete_or_readd(key, expire_at_ms):  # Store가 할 일 (모듈 docstring)
        current = table.get(key)
        if current is not None and current > expire_at_ms:
            wheel.add(key, current)
            return False
        return deleter(table)(key, expire_at_ms)

    assert wheel.expire(500, delete_or_readd) == 0 and table == {"a": 900}
    assert wheel.expire(899, delete_or_readd) == 0
    assert wheel.expire(900, delete_or_readd) == 1 and table == {}


def test_wheel_stale_item_after_ttl_change_ignored():
    wheel, table = TimingWheel(0), {"a": 500, "b": 500}
    wheel.add("a", 500)
    wheel.add("b", 500)
    table["a"] = 900  # EXPIRE로 갱신: 옛 휠 항목은 그대로 둔다
    wheel.add("a", 900)
    del table["b"]  # DEL: 휠 항목은 그대로 둔다

    assert wheel.expire(500, deleter(table)) == 0
    assert table == {"a": 900}
    assert wheel.expire(900, deleter(table)) == 1
    assert table == {}


def test_wheel_tick_limit_reached_rest_deleted_next_tick():
    wheel, table = TimingWheel(0), {}
    for i in range(2_500):
        table[i] = 100
        wheel.add(i, 100)

    assert [wheel.expire(100, deleter(table)) for _ in range(4)] == [1_000, 1_000, 500, 0]
    assert table == {}


def test_wheel_tick_limit_counts_stale_items_too():
    wheel = TimingWheel(0)
    for i in range(2_500):
        wheel.add(i, 100)  # 엔트리가 없는 낡은 항목뿐

    seen = []
    for _ in range(4):
        calls = []
        wheel.expire(100, lambda key, exp: calls.append(key))  # None: 지우지 않음
        seen.append(len(calls))
    assert seen == [1_000, 1_000, 500, 0]


def test_expiry_thread_running_calls_tick_until_stopped():
    called = threading.Event()
    stop = start_expiry_thread(called.set, interval_ms=1)
    try:
        assert called.wait(1)
    finally:
        stop.set()


def test_expiry_thread_tick_raises_keeps_running():
    calls, recovered = [], threading.Event()

    def tick():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        recovered.set()

    stop = start_expiry_thread(tick, interval_ms=1)
    try:
        assert recovered.wait(1)
    finally:
        stop.set()
