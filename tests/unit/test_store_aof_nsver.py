"""네임스페이스 버전 변경(nsver)도 AOF에 먼저 적히는지 확인한다 (기록 먼저, 버전 변경은 그다음)."""
import pytest

from mini_redis.clock import FakeClock
from mini_redis.errors import KeyNotFound
from mini_redis.persistence.aof import flush_record, ns_version_record, set_record
from mini_redis.store import Store

T0 = 1_000_000


class RecordingAof:
    def __init__(self):
        self.records = []

    def append(self, record):
        self.records.append(record)


class NsverFailingAof:
    """nsver 레코드만 실패하는 기록기."""

    def append(self, record):
        if record["op"] == "nsver":
            raise OSError("nsver 기록 실패")


@pytest.fixture
def clock():
    return FakeClock(T0)


@pytest.fixture
def fake():
    return RecordingAof()


@pytest.fixture
def store(clock, fake):
    return Store(clock=clock, aof=fake)


def _miss_version(store, key, ns):
    """ns를 붙여 miss를 읽고 404에 실린 현재 버전을 돌려준다."""
    with pytest.raises(KeyNotFound) as info:
        store.get(key, ns=ns)
    return info.value.extra["version"]


def test_store_nsver_first_get_with_ns_logs_creation_once(store, fake):
    _miss_version(store, "k", "user")
    assert fake.records == [ns_version_record("user", T0)]
    fake.records.clear()
    _miss_version(store, "k", "user")
    assert fake.records == []


def test_store_nsver_invalidate_logs_returned_version(store, fake):
    _miss_version(store, "k", "user")
    fake.records.clear()
    v = store.invalidate("user")
    assert fake.records == [ns_version_record("user", v)]


def test_store_nsver_invalidate_twice_in_same_ms_logs_v_then_v_plus_one(store, fake):
    _miss_version(store, "k", "user")
    fake.records.clear()
    v1 = store.invalidate("user")
    v2 = store.invalidate("user")
    assert v2 == v1 + 1
    assert fake.records == [ns_version_record("user", v1), ns_version_record("user", v2)]


def test_store_nsver_invalidate_of_unseen_ns_logs_creation_then_bump(store, fake):
    v = store.invalidate("user")
    assert fake.records == [ns_version_record("user", T0), ns_version_record("user", v)]
    assert v == T0 + 1


def test_store_nsver_set_with_ns_logs_creation_then_internal_key_set(store, fake):
    store.set("k", "1", expire_at_ms=T0 + 10_000, ns="user", v=T0)
    assert fake.records == [
        ns_version_record("user", T0),
        set_record(f"user:v{T0}:k", "1", T0 + 10_000),
    ]


def test_store_nsver_invalidate_with_failing_aof_raises_and_keeps_old_version(store, fake):
    assert _miss_version(store, "k", "user") == T0
    store.attach_aof(NsverFailingAof())
    with pytest.raises(OSError):
        store.invalidate("user")
    store.attach_aof(None)
    assert _miss_version(store, "k", "user") == T0


def test_store_nsver_creation_with_failing_aof_raises_and_does_not_create_ns(store, fake):
    store.attach_aof(NsverFailingAof())
    with pytest.raises(OSError):
        store.get("k", ns="new")
    store.attach_aof(fake)
    _miss_version(store, "k", "new")
    assert fake.records == [ns_version_record("new", T0)]


def test_store_nsver_flush_logs_only_flush_record_and_keeps_versions(store, fake):
    v = store.invalidate("user")
    fake.records.clear()
    store.flush(confirm=True)
    assert fake.records == [flush_record()]
    fake.records.clear()
    assert _miss_version(store, "k", "user") == v
    assert fake.records == []
