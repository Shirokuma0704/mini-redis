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


def test_wheel_far_item_cascades_overflow_to_l2_to_l1():
    wheel, table = TimingWheel(0), {"k": 500_000}
    wheel.add("k", 500_000)
    assert wheel._overflow == [("k", 500_000)]

    wheel.expire(409_600, deleter(table))  # 2단 한 바퀴: overflow -> 2단
    assert wheel._overflow == [] and ("k", 500_000) in wheel._l2[500_000 // 6_400 % 64]

    wheel.expire(499_200, deleter(table))  # 2단 슬롯 차례: 2단 -> 1단
    assert ("k", 500_000) in wheel._l1[5_000 % 64]

    assert wheel.expire(499_999, deleter(table)) == 0
    assert wheel.expire(500_000, deleter(table)) == 1 and table == {}


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


def test_expiry_thread_running_calls_tick_until_stopped():
    called = threading.Event()
    stop = start_expiry_thread(called.set, interval_ms=1)
    try:
        assert called.wait(1)
    finally:
        stop.set()
