"""네임스페이스 버전이 스냅샷을 거쳐도 남는지, 그리고 start가 중간에 실패하면 깨끗이 되돌리는지 확인한다."""
import pathlib

import pytest

from mini_redis.clock import FakeClock
from mini_redis.persistence import manager
from mini_redis.persistence.aof import AofWriter, decode_line
from mini_redis.persistence.layout import SNAPSHOT_NAME, aof_name
from mini_redis.persistence.manager import Persistence, Snapshotter
from mini_redis.store import Store

T0 = 1_000_000
IDLE = 3600.0  # 스레드가 테스트 중에 깨어나지 않게 아주 길게 잡는다
NSVER_USERS_7 = '{"op":"nsver","ns":"users","ver":7}\n'
SET_A = '{"op":"set","k":"a","v":"1","exp":null}\n'


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


def snapshot_records(data_dir):
    text = pathlib.Path(data_dir, SNAPSHOT_NAME).read_bytes().decode("utf-8")
    lines = text.splitlines(keepends=True)
    return [decode_line(line) for line in lines[1:]]  # 첫 줄은 헤더


def nsver_pairs(records):
    return [(r["ns"], r["ver"]) for r in records if r["op"] == "nsver"]


def test_snapshot_after_recovery_keeps_recovered_versions_and_next_start_sees_them(
    tmp_path, clock, make_persistence
):
    pathlib.Path(tmp_path, aof_name(1)).write_text(NSVER_USERS_7 + SET_A, encoding="utf-8")
    p1 = make_persistence(tmp_path)
    p1.start(Store(clock=clock))

    assert p1.snapshotter.snapshot() is True

    assert nsver_pairs(snapshot_records(tmp_path)) == [("users", 7)]
    assert not pathlib.Path(tmp_path, aof_name(1)).exists()
    p1.stop()

    p2 = make_persistence(tmp_path)
    state = p2.start(Store(clock=clock))
    assert [(e.key, e.value) for e in state.versions.items()] == [("users", 7)]


def test_snapshot_twice_in_a_row_still_holds_recovered_version(tmp_path, clock, make_persistence):
    pathlib.Path(tmp_path, aof_name(1)).write_text(NSVER_USERS_7 + SET_A, encoding="utf-8")
    p = make_persistence(tmp_path)
    p.start(Store(clock=clock))

    assert p.snapshotter.snapshot() is True
    assert p.snapshotter.snapshot() is True

    assert nsver_pairs(snapshot_records(tmp_path)) == [("users", 7)]


def test_versions_source_is_called_while_store_lock_is_held(tmp_path, clock):
    store = Store(clock=clock)
    aof = AofWriter(tmp_path / aof_name(1), interval_s=IDLE)
    store.attach_aof(aof)
    owned = []

    def source():
        owned.append(store._lock._is_owned())
        return [("users", 3)]

    snapshotter = Snapshotter(tmp_path, store, aof, 1, interval_s=IDLE, versions_source=source)
    assert snapshotter.snapshot() is True

    assert owned == [True]
    assert nsver_pairs(snapshot_records(tmp_path)) == [("users", 3)]


def test_snapshotter_without_versions_source_writes_no_nsver_records(tmp_path, clock):
    store = Store(clock=clock)
    store.set("a", "1")
    aof = AofWriter(tmp_path / aof_name(1), interval_s=IDLE)
    store.attach_aof(aof)

    snapshotter = Snapshotter(tmp_path, store, aof, 1, interval_s=IDLE)
    assert snapshotter.snapshot() is True

    records = snapshot_records(tmp_path)
    assert [r["op"] for r in records] == ["set"]
    assert nsver_pairs(records) == []


def test_start_failure_after_attach_rolls_back_and_second_start_succeeds(
    tmp_path, clock, make_persistence, monkeypatch
):
    writers = []

    class CapturingWriter(AofWriter):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            writers.append(self)

    def boom(self):
        raise RuntimeError("스냅샷 스레드를 못 켜요")

    monkeypatch.setattr(manager, "AofWriter", CapturingWriter)
    monkeypatch.setattr(manager.Snapshotter, "start", boom)
    store = Store(clock=clock)
    p = make_persistence(tmp_path)

    with pytest.raises(RuntimeError, match="스냅샷 스레드를 못 켜요"):
        p.start(store)

    assert store._aof is None
    assert p.aof is None
    assert p.snapshotter is None
    assert len(writers) == 1
    assert writers[0]._thread is None

    monkeypatch.undo()
    p.start(store)  # 롤백이 깨끗했다면 "이미 AOF가 붙어 있는" 오류 없이 시작돼요.
    assert store._aof is not None
    assert p.aof is not None and p.aof is not writers[0]
