"""네임스페이스 버전이 재시작·스냅샷·FLUSH를 거쳐도 Store에 남는지 확인한다 (#14, 명세 "버전 테이블은 유지")."""
import pathlib

import pytest

from mini_redis.clock import FakeClock
from mini_redis.errors import KeyNotFound
from mini_redis.persistence.aof import decode_line
from mini_redis.persistence.layout import SNAPSHOT_NAME, aof_name
from mini_redis.persistence.manager import Persistence
from mini_redis.store import Store

T0 = 1_000_000
EXP = T0 + 3_600_000  # 시계가 멈춰 있어서 이 키는 테스트 내내 살아 있다
IDLE = 3600.0  # 스레드가 테스트 중에 깨어나지 않게 아주 길게 잡는다


@pytest.fixture
def clock():
    return FakeClock(T0)


@pytest.fixture
def make_persistence():
    """Persistence를 만들어 주고, 테스트가 끝나면 전부 stop()한다 (stop은 여러 번 불러도 된다)."""
    created = []

    def factory(data_dir):
        p = Persistence(data_dir, aof_interval_s=IDLE, snapshot_interval_s=IDLE, check_interval_s=IDLE)
        created.append(p)
        return p

    yield factory
    for p in created:
        p.stop()


def snapshot_nsver_pairs(data_dir):
    text = pathlib.Path(data_dir, SNAPSHOT_NAME).read_bytes().decode("utf-8")
    records = [decode_line(line) for line in text.splitlines(keepends=True)[1:]]  # 첫 줄은 헤더
    return [(r["ns"], r["ver"]) for r in records if r["op"] == "nsver"]


def first_run_with_versioned_key(data_dir, clock, make_persistence):
    """첫 실행: user를 invalidate하고 그 버전으로 ns 키 k를 쓴 뒤 stop한다. 버전을 돌려준다."""
    p = make_persistence(data_dir)
    store = Store(clock=clock)
    p.start(store)
    v = store.invalidate("user")
    store.set("k", "val", EXP, ns="user", v=v)
    p.stop()
    return v


def restart(data_dir, clock, make_persistence):
    p = make_persistence(data_dir)
    store = Store(clock=clock)
    p.start(store)
    return p, store


def test_restart_keeps_namespace_version_and_versioned_key(tmp_path, clock, make_persistence):
    v = first_run_with_versioned_key(tmp_path, clock, make_persistence)

    _, store2 = restart(tmp_path, clock, make_persistence)

    item = store2.get("k", ns="user")
    assert item.value == "val"
    assert item.version == v


def test_restart_without_new_writes_does_not_log_restored_versions(tmp_path, clock, make_persistence):
    first_run_with_versioned_key(tmp_path, clock, make_persistence)

    p2, _ = restart(tmp_path, clock, make_persistence)
    p2.stop()

    new_aof = pathlib.Path(tmp_path, aof_name(2))  # 첫 실행이 1번을 썼으니 두 번째 실행의 새 AOF는 2번이다
    assert not new_aof.exists() or new_aof.stat().st_size == 0


def test_snapshot_after_restart_writes_nsver_and_third_start_keeps_version(tmp_path, clock, make_persistence):
    v = first_run_with_versioned_key(tmp_path, clock, make_persistence)
    p2, _ = restart(tmp_path, clock, make_persistence)

    assert p2.snapshotter.snapshot() is True

    assert ("user", v) in snapshot_nsver_pairs(tmp_path)
    assert not pathlib.Path(tmp_path, aof_name(1)).exists()
    p2.stop()

    _, store3 = restart(tmp_path, clock, make_persistence)
    assert store3.get("k", ns="user").version == v


def test_restore_versions_with_aof_attached_raises_runtime_error(tmp_path, clock, make_persistence):
    store = Store(clock=clock)
    make_persistence(tmp_path).start(store)  # start가 AOF를 붙인다

    with pytest.raises(RuntimeError):
        store.restore_versions([("user", 5)])


def test_flush_then_restart_keeps_namespace_version(tmp_path, clock, make_persistence):
    p1 = make_persistence(tmp_path)
    store1 = Store(clock=clock)
    p1.start(store1)
    v = store1.invalidate("user")
    store1.set("k", "val", EXP, ns="user", v=v)
    store1.flush(confirm=True)
    p1.stop()

    _, store2 = restart(tmp_path, clock, make_persistence)

    with pytest.raises(KeyNotFound) as info:
        store2.get("k", ns="user")
    assert info.value.extra["version"] == v
