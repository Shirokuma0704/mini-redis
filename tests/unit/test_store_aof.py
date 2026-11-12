"""Store가 모든 상태 변화를 AOF에 먼저 적는지 확인한다 (기록 먼저, 변경은 그다음)."""
import os

import pytest

from mini_redis.clock import FakeClock
from mini_redis.errors import (
    CapacityExceeded,
    ConfirmRequired,
    KeyNotFound,
    NotInteger,
)
from mini_redis.persistence import aof as aof_module
from mini_redis.persistence.aof import (
    AofBrokenError,
    AofWriter,
    decode_line,
    del_record,
    expire_record,
    flush_record,
    set_record,
)
from mini_redis.store import Store

T0 = 1_000_000


class RecordingAof:
    def __init__(self):
        self.records = []

    def append(self, record):
        self.records.append(record)


class BrokenAof:
    def append(self, record):
        raise AofBrokenError("깨진 AOF")


@pytest.fixture
def clock():
    return FakeClock(T0)


@pytest.fixture
def fake():
    return RecordingAof()


@pytest.fixture
def store(clock, fake):
    return Store(clock=clock, aof=fake)


def test_store_aof_set_without_ttl_logs_set_record(store, fake):
    store.set("a", "1")
    assert fake.records == [set_record("a", "1", None)]


def test_store_aof_set_with_ttl_logs_absolute_exp(store, fake):
    store.set("b", "2", expire_at_ms=1_010_000)
    assert fake.records == [set_record("b", "2", 1_010_000)]


def test_store_aof_delete_live_key_logs_del_and_returns_true(store, fake):
    store.set("a", "1")
    assert store.delete("a") is True
    assert fake.records == [set_record("a", "1", None), del_record("a")]


def test_store_aof_delete_missing_key_logs_nothing(store, fake):
    assert store.delete("nope") is False
    assert fake.records == []


def test_store_aof_delete_expired_key_logs_one_del_and_returns_false(store, fake, clock):
    store.set("a", "1", expire_at_ms=T0 + 1000)
    clock.advance(2000)
    fake.records.clear()
    assert store.delete("a") is False
    assert fake.records == [del_record("a")]


def test_store_aof_incr_new_key_logs_set_without_ttl(store, fake):
    assert store.incr("n") == 1
    assert store.incr("n", 5) == 6
    assert fake.records == [set_record("n", "1", None), set_record("n", "6", None)]


def test_store_aof_incr_keeps_ttl_in_record(store, fake):
    exp = T0 + 60_000
    store.set("k", "1", expire_at_ms=exp)
    store.incr("k")
    assert fake.records == [set_record("k", "1", exp), set_record("k", "2", exp)]


def test_store_aof_incr_non_integer_raises_and_logs_nothing_new(store, fake):
    store.set("s", "abc")
    before = list(fake.records)
    with pytest.raises(NotInteger):
        store.incr("s")
    assert fake.records == before


def test_store_aof_expire_positive_ttl_logs_absolute_expire(store, fake):
    store.set("k", "v")
    fake.records.clear()
    assert store.expire("k", 10) is True
    assert fake.records == [expire_record("k", 1_010_000)]


def test_store_aof_expire_zero_or_negative_ttl_logs_del_and_missing_logs_nothing(store, fake):
    store.set("a", "1")
    store.set("b", "2")
    fake.records.clear()
    assert store.expire("a", 0) is True
    assert store.expire("b", -1) is True
    assert fake.records == [del_record("a"), del_record("b")]
    fake.records.clear()
    assert store.expire("missing", 10) is False
    assert fake.records == []


def test_store_aof_delete_prefix_logs_one_del_per_victim(store, fake):
    store.set("user:1", "a")
    store.set("user:2", "b")
    store.set("item:1", "c")
    fake.records.clear()
    assert store.delete_prefix("user:") == 2
    assert len(fake.records) == 2
    assert all(r["op"] == "del" for r in fake.records)
    assert {r["k"] for r in fake.records} == {"user:1", "user:2"}


def test_store_aof_flush_logs_flush_record_and_unconfirmed_logs_nothing(store, fake):
    store.set("a", "1")
    fake.records.clear()
    with pytest.raises(ConfirmRequired):
        store.flush()
    assert fake.records == []
    store.flush(confirm=True)
    assert fake.records == [flush_record()]


def test_store_aof_lazy_expiry_on_get_exists_ttl_logs_single_del(store, fake, clock):
    store.set("live", "1")
    store.get("live")
    assert fake.records == [set_record("live", "1", None)]  # 살아 있는 키 GET은 기록이 없다
    store.set("x", "1", expire_at_ms=T0 + 1000)
    clock.advance(2000)
    fake.records.clear()
    with pytest.raises(KeyNotFound):
        store.get("x")
    assert store.exists("x") is False
    assert store.ttl("x") == -2
    assert fake.records == [del_record("x")]


def test_store_aof_keys_over_expired_entries_logs_two_dels(store, fake, clock):
    store.set("e1", "1", expire_at_ms=T0 + 1000)
    store.set("e2", "2", expire_at_ms=T0 + 1000)
    store.set("live", "3")
    clock.advance(2000)
    fake.records.clear()
    assert store.keys("") == ["live"]
    assert len(fake.records) == 2
    assert all(r["op"] == "del" for r in fake.records)
    assert sorted(r["k"] for r in fake.records) == ["e1", "e2"]


def test_store_aof_timing_wheel_expire_tick_logs_one_del(store, fake, clock):
    store.set("w", "1", expire_at_ms=T0 + 1000)
    clock.advance(2000)
    fake.records.clear()
    assert store.expire_tick() == 1
    assert fake.records == [del_record("w")]


def test_store_aof_set_over_expired_entry_logs_del_then_set(store, fake, clock):
    store.set("a", "old", expire_at_ms=T0 + 1000)
    clock.advance(2000)
    fake.records.clear()
    store.set("a", "new")
    assert fake.records == [del_record("a"), set_record("a", "new", None)]


def test_store_aof_capacity_exceeded_logs_nothing_for_rejected_set(clock, fake):
    s = Store(clock, max_keys=1, aof=fake)
    s.set("a", "1")
    with pytest.raises(CapacityExceeded):
        s.set("b", "2")
    assert fake.records == [set_record("a", "1", None)]


def test_store_aof_broken_append_blocks_set_and_delete(clock):
    s = Store(clock=clock, aof=BrokenAof())
    with pytest.raises(AofBrokenError):
        s.set("a", "1")
    s.attach_aof(None)
    with pytest.raises(KeyNotFound):
        s.get("a")  # 기록이 실패했으니 저장되지 않았다

    s.set("b", "2")  # 로거 없이 저장
    s.attach_aof(BrokenAof())
    with pytest.raises(AofBrokenError):
        s.delete("b")
    s.attach_aof(None)
    assert s.get("b").value == "2"  # 지워지지 않았다


def test_store_aof_attach_none_stops_logging_and_new_aof_gets_later_records(store, fake):
    store.set("a", "1")
    store.attach_aof(None)
    store.set("b", "2")
    assert fake.records == [set_record("a", "1", None)]
    fake2 = RecordingAof()
    store.attach_aof(fake2)
    store.set("c", "3")
    assert fake.records == [set_record("a", "1", None)]
    assert fake2.records == [set_record("c", "3", None)]


def test_store_aof_real_writer_does_no_file_io_under_lock(clock, tmp_path, monkeypatch):
    path = tmp_path / "a.aof"
    writer = AofWriter(path)
    s = Store(clock=clock)
    s.attach_aof(writer)

    def forbidden(*args, **kwargs):
        raise AssertionError("락 안에서 파일 I/O가 일어났다")

    monkeypatch.setattr(aof_module, "open", forbidden, raising=False)
    monkeypatch.setattr(os, "fsync", forbidden)
    s.set("a", "1")
    s.incr("n")
    s.delete("a")
    s.flush(confirm=True)
    monkeypatch.undo()

    assert writer.flush() == 4
    lines = path.read_bytes().decode("utf-8").splitlines()
    assert [decode_line(line) for line in lines] == [
        set_record("a", "1", None),
        set_record("n", "1", None),
        del_record("a"),
        flush_record(),
    ]


def test_store_aof_default_without_aof_round_trips(clock):
    s = Store(clock=clock)
    s.set("a", "1")
    assert s.get("a").value == "1"
    assert s.delete("a") is True
