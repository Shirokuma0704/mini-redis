import random
import threading

from mini_redis.config import WHEEL_OVERFLOW_MOVE_PER_TICK
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
    # 500초 뒤 만료: overflow -> (2단 범위에 들어오는 89.7초에) 2단 -> (499.2초에) 1단 -> 500초에 삭제
    wheel, table, deleted_at = TimingWheel(0), {"k": 500_000}, []
    wheel.add("k", 500_000)
    for now in range(0, 600_001, 100):
        if wheel.expire(now, deleter(table)):
            deleted_at.append(now)
    assert deleted_at == [500_000]


def test_wheel_cascade_l1_holds_only_cascaded_ticks_deleted_at_exact_tick():
    # 2단 칸 i와 1단 칸 i가 같은 tick이어야 칸째 붙여도 맞다. tick마다 1단 맨 끝(64 tick 뒤)과
    # 2단(65, 127 tick 뒤)에 하나씩 넣으며 2단을 한 바퀴 넘게 돌린다.
    start = 1_762_934_399_900  # 64 tick 경계에 맞지 않는 시각
    wheel, table, deleted_at = TimingWheel(start), {}, {}
    now = start
    while now <= start + (4_096 + 200) * 100:  # 마지막 항목(127 tick 뒤)까지 다 지나간다
        log = []
        wheel.expire(now, deleter(table, log))
        for key in log:
            deleted_at[key] = now
        cur = now // 100
        # 불변식: 1단 칸 i에는 tick % 64 == i이고 커서 다음 64 tick 안의 항목만 있다
        for i, slot in enumerate(wheel._l1):
            assert all(t % 64 == i and cur < t <= cur + 64 for t in (exp // 100 for _, exp in slot))
        if now <= start + 4_096 * 100:
            for d in (64, 65, 127):
                table[(cur, d)] = (cur + d) * 100
                wheel.add((cur, d), (cur + d) * 100)
        now += 100
    assert table == {} and len(deleted_at) == 3 * 4_097
    assert all(at == (cur + d) * 100 for (cur, d), at in deleted_at.items())


def test_wheel_cascade_100k_in_one_l2_slot_no_add_per_item():
    # 10초 뒤 만료 10만 개는 2단 한 슬롯에 몰린다. 6.4초에 1단으로 내려갈 때 항목마다 add를 부르면 안 된다
    wheel, table = TimingWheel(0), {}
    for i in range(100_000):
        table[i] = 10_000
        wheel.add(i, 10_000)
    calls, real_add = [], wheel.add
    wheel.add = lambda key, exp: (calls.append(key), real_add(key, exp))

    deleted = [wheel.expire(now, deleter(table)) for now in range(100, 10_001, 100)]
    assert calls == []
    assert deleted == [0] * 99 + [1_000]  # 10초 tick에 처음 꺼내고, 상한만큼만 지운다


def test_wheel_overflow_100k_moves_at_most_cap_per_tick_all_before_expiry():
    # 500초 뒤 만료 10만 개가 overflow에 몰려도 tick당 상한만큼만 휠로 옮기고, 만료 전에 다 옮긴다
    wheel, table = TimingWheel(0), {}
    for i in range(100_000):
        table[i] = 500_000
        wheel.add(i, 500_000)
    assert len(wheel._overflow) == 100_000

    moved, deleted = [], 0
    for now in range(100, 500_001, 100):
        before = len(wheel._overflow)
        deleted += wheel.expire(now, deleter(table))
        moved.append(before - len(wheel._overflow))
        assert not wheel._overflow or wheel._overflow[0][0] > now  # 만료가 지난 항목이 힙에 남지 않음
    assert max(moved) == WHEEL_OVERFLOW_MOVE_PER_TICK and sum(moved) == 100_000
    assert deleted == 1_000  # 500초 tick에 처음 꺼내고, 상한만큼만 지운다


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
