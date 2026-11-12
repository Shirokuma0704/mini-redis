"""Snapshotter: 락 안에서는 복사와 AOF 전환만, 파일 쓰기와 지우기는 락 밖에서 하는지 확인해요.

time.sleep은 쓰지 않아요. 시간은 가짜 monotonic과 FakeClock으로 움직이고, 스레드 테스트는 Event.wait(5)를 써요.
아직 없는 모듈(mini_redis.persistence.manager)은 파일 맨 위에서 import해요.
"""

import builtins
import os
import pathlib
import threading

import pytest

import mini_redis.persistence.manager as manager
from mini_redis.clock import FakeClock
from mini_redis.persistence.aof import AofWriter, decode_line, encode_record, set_record
from mini_redis.persistence.layout import SNAPSHOT_NAME, aof_name, aof_numbers
from mini_redis.persistence.manager import Snapshotter
from mini_redis.persistence.snapshot import load_snapshot
from mini_redis.store import Store

T0 = 1_000_000
HUGE_INTERVAL_S = 3600


class FakeMonotonic:
    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class RecordingAof:
    def __init__(self):
        self.records = []

    def append(self, record):
        self.records.append(record)


def line(record):
    return encode_record(record).encode("utf-8")


def read_bytes(path):
    return path.read_bytes() if path.exists() else b""


@pytest.fixture
def clock():
    return FakeClock(T0)


@pytest.fixture
def mono():
    return FakeMonotonic()


def make_setup(tmp_path, clock, mono, *, with_line=True, **kwargs):
    """store + 파일 1번에 적는 writer + Snapshotter를 만들어요. with_line이면 파일 1에 한 줄이 이미 있어요."""
    writer = AofWriter(tmp_path / aof_name(1), interval_s=HUGE_INTERVAL_S)
    if with_line:
        writer.append(set_record("old", "x", None))
        writer.flush()
    store = Store(clock=clock)
    kwargs.setdefault("interval_s", 300)
    kwargs.setdefault("max_aof_bytes", 10 * 1024 * 1024)
    snap = Snapshotter(tmp_path, store, writer, 1, monotonic=mono, **kwargs)
    return store, writer, snap


def test_snapshot은_만료되지_않은_키만_스냅샷에_적고_옛_AOF를_지우고_새_AOF로_바꾼다(tmp_path, clock, mono):
    store, writer, snap = make_setup(tmp_path, clock, mono)
    store.set("a", "1")
    store.set("b", "2", T0 + 100_000)
    store.set("c", "3", T0 + 1000)
    clock.advance(2000)  # c만 만료돼요.
    assert snap.snapshot() is True
    next_aof, records = load_snapshot(tmp_path / SNAPSHOT_NAME)
    assert next_aof == 2
    assert {r["op"] for r in records} == {"set"}
    assert len(records) == 2
    assert {(r["k"], r["v"], r["exp"]) for r in records} == {("a", "1", None), ("b", "2", T0 + 100_000)}
    assert not (tmp_path / aof_name(1)).exists()
    assert os.fspath(writer.path) == os.fspath(tmp_path / aof_name(2))
    assert snap.aof_number == 2
    assert store.size() == 3  # 복사가 만료된 c를 지우지 않았어요.


def test_복사_뒤에_적은_줄은_새_AOF_파일로_간다(tmp_path, clock, mono):
    store, writer, snap = make_setup(tmp_path, clock, mono)
    store.attach_aof(writer)
    store.set("a", "1")
    assert snap.snapshot() is True
    store.set("d", "4")
    writer.flush()
    assert read_bytes(tmp_path / aof_name(2)) == line(set_record("d", "4", None))


def test_snapshot_중_락을_잡은_동안에는_파일_IO가_없다(tmp_path, clock, mono, monkeypatch):
    store, writer, snap = make_setup(tmp_path, clock, mono)
    store.attach_aof(writer)
    store.set("a", "1")
    calls = []

    def guard(name, real):
        def wrapper(*args, **kwargs):
            assert not store._lock._is_owned(), f"{name}이(가) Store 락 안에서 불렸어요"
            calls.append(name)
            return real(*args, **kwargs)

        return wrapper

    real_unlink = pathlib.Path.unlink
    monkeypatch.setattr(builtins, "open", guard("open", builtins.open))
    monkeypatch.setattr(os, "replace", guard("os.replace", os.replace))
    monkeypatch.setattr(os, "fsync", guard("os.fsync", os.fsync))
    monkeypatch.setattr(os, "unlink", guard("os.unlink", os.unlink))
    monkeypatch.setattr(os, "remove", guard("os.remove", os.remove))
    monkeypatch.setattr(
        pathlib.Path,
        "unlink",
        lambda self, *a, **k: guard("Path.unlink", real_unlink)(self, *a, **k),
    )
    assert snap.snapshot() is True
    assert "open" in calls and "os.replace" in calls  # 감시가 실제로 동작했는지 확인해요.


def test_copy_for_snapshot은_락_안에서_during_lock을_부르고_AOF에_아무것도_적지_않는다(clock):
    recorder = RecordingAof()
    store = Store(clock=clock, aof=recorder)
    store.set("a", "1")
    store.set("b", "2", T0 + 5000)
    before = list(recorder.records)
    owned = []
    entries = store.copy_for_snapshot(during_lock=lambda: owned.append(store._lock._is_owned()))
    assert owned == [True]
    assert sorted(entries) == [("a", "1", None), ("b", "2", T0 + 5000)]
    assert recorder.records == before
    assert store._lock._is_owned() is False


def test_maybe_snapshot은_주기가_차거나_AOF가_커지면_True를_돌려준다(tmp_path, clock, mono):
    # 1) 시간 기준
    store, writer, snap = make_setup(tmp_path, clock, mono, interval_s=300)
    assert snap.maybe_snapshot() is False
    mono.advance(299)
    assert snap.maybe_snapshot() is False
    mono.advance(1)
    assert snap.maybe_snapshot() is True
    assert snap.maybe_snapshot() is False  # 방금 찍어서 다시 처음부터 세요.

    # 2) 크기 기준: 시간은 흐르지 않아요.
    other = tmp_path / "size"
    other.mkdir()
    _store2, writer2, snap2 = make_setup(other, clock, mono, with_line=False, interval_s=300, max_aof_bytes=10)
    assert snap2.maybe_snapshot() is False
    writer2.append(set_record("k", "value-longer-than-ten-bytes", None))
    assert snap2.maybe_snapshot() is False  # flush 전이라 current_bytes는 아직 0이에요.
    writer2.flush()
    assert writer2.current_bytes > 10
    assert snap2.maybe_snapshot() is True


def test_스냅샷_쓰기가_실패하면_False를_돌려주고_옛_AOF는_남고_새_AOF로_이어서_적는다(tmp_path, clock, mono, monkeypatch):
    _store, writer, snap = make_setup(tmp_path, clock, mono)
    old_bytes = read_bytes(tmp_path / aof_name(1))
    assert old_bytes != b""

    def boom(*args, **kwargs):
        raise OSError("스냅샷 쓰기 실패")

    monkeypatch.setattr(manager, "write_snapshot", boom)
    assert snap.snapshot() is False
    assert read_bytes(tmp_path / aof_name(1)) == old_bytes
    assert os.fspath(writer.path) == os.fspath(tmp_path / aof_name(2))
    assert not (tmp_path / SNAPSHOT_NAME).exists()


def test_AOF가_깨져_회전이_실패하면_False를_돌려주고_아무것도_바뀌지_않는다(tmp_path, clock, mono):
    _store, writer, snap = make_setup(tmp_path, clock, mono)
    writer.broken = True
    assert snap.snapshot() is False
    assert snap.aof_number == 1
    assert os.fspath(writer.path) == os.fspath(tmp_path / aof_name(1))
    assert not (tmp_path / SNAPSHOT_NAME).exists()
    assert (tmp_path / aof_name(1)).exists()


def replay(data_dir):
    """스냅샷을 읽고 next_aof 이상 번호의 AOF 줄을 순서대로 적용해 {key: (value, exp)}를 만들어요."""
    directory = pathlib.Path(data_dir)
    next_aof, records = load_snapshot(directory / SNAPSHOT_NAME)
    for number in aof_numbers(directory):
        if number >= next_aof:
            text = (directory / aof_name(number)).read_bytes().decode("utf-8")
            records = records + [decode_line(ln) for ln in text.splitlines()]
    state = {}
    for r in records:
        op = r["op"]
        if op == "set":
            state[r["k"]] = (r["v"], r["exp"])
        elif op == "del":
            state.pop(r["k"], None)
        elif op == "expire":
            if r["k"] in state:
                state[r["k"]] = (state[r["k"]][0], r["exp"])
        elif op == "flush":
            state.clear()
    return state


def test_스냅샷_뒤_쓰기까지_스냅샷과_새_AOF를_재생하면_저장소의_살아있는_키와_같다(tmp_path, clock, mono):
    store, writer, snap = make_setup(tmp_path, clock, mono)
    store.attach_aof(writer)
    store.set("a", "1")
    store.set("b", "2")
    store.set("c", "3", T0 + 50_000)
    assert snap.snapshot() is True
    store.set("e", "5")
    store.delete("a")
    store.expire("b", 100)
    writer.flush()
    expected = {k: (store.get(k).value, store.get(k).expire_at_ms) for k in store.keys()}
    assert set(expected) == {"b", "c", "e"}
    assert replay(tmp_path) == expected


def test_start와_stop은_백그라운드에서_스냅샷을_찍고_stop을_두_번_불러도_괜찮다(tmp_path, clock, monkeypatch):
    writer = AofWriter(tmp_path / aof_name(1), interval_s=HUGE_INTERVAL_S)
    store = Store(clock=clock)
    store.set("a", "1")
    snap = Snapshotter(tmp_path, store, writer, 1, interval_s=0, check_interval_s=0.001)
    done = threading.Event()
    real_snapshot = Snapshotter.snapshot

    def wrapper(self):
        result = real_snapshot(self)
        done.set()
        return result

    monkeypatch.setattr(Snapshotter, "snapshot", wrapper)
    snap.start()
    snap.start()  # 이미 켜져 있으면 아무 일도 없어요.
    assert done.wait(5)
    snap.stop()
    snap.stop()
    assert snap.aof_number >= 2
    assert (tmp_path / SNAPSHOT_NAME).exists()
