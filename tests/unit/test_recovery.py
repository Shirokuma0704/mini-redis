"""복구(recovery) 테스트: 스냅샷과 번호 붙은 AOF 파일에서 상태를 되살려요.

파일은 일부러 바이트 단위로 직접 써서, 잘린 줄이나 깨진 줄 같은 상황을 정확히 만들어요.
복구는 계산만 하고 파일을 건드리지 않아야 해서, 그 약속도 같이 확인해요.
"""

import pytest

from mini_redis.hashtable import HashTable
from mini_redis.persistence.aof import (
    del_record,
    encode_record,
    expire_record,
    flush_record,
    ns_version_record,
    set_record,
)
from mini_redis.persistence.layout import SNAPSHOT_TMP_NAME, aof_name
from mini_redis.persistence.recovery import CorruptAofError, recover
from mini_redis.persistence.snapshot import write_snapshot


def live(state):
    """살아 있는 항목을 (키, 값, 만료 시각)로 뽑아 정렬해요. 순회 순서에 기대지 않으려는 거예요."""
    return sorted((e.key, e.value, e.expire_at_ms) for e in state.table.items())


def write_aof(data_dir, number, *records, tail=b""):
    """레코드들을 AOF 파일 하나로 써요. tail은 줄바꿈 없는 잘린 꼬리를 흉내 낼 때 붙여요."""
    data = "".join(encode_record(r) for r in records).encode("utf-8") + tail
    (data_dir / aof_name(number)).write_bytes(data)


def test_recover_missing_dir_returns_empty_state(tmp_path):
    state = recover(tmp_path / "없는폴더", now_ms=1000)
    assert live(state) == []
    assert state.versions == {}
    assert state.next_aof == 1
    assert state.dropped_tails == 0


def test_recover_empty_dir_returns_empty_state(tmp_path):
    state = recover(tmp_path, now_ms=1000)
    assert live(state) == []
    assert state.versions == {}
    assert state.next_aof == 1
    assert state.dropped_tails == 0


def test_recover_snapshot_then_aof_applies_set_and_del_in_order(tmp_path):
    write_snapshot(tmp_path, [("a", "1", None)], [], next_aof=1)
    write_aof(tmp_path, 1, set_record("b", "2"), del_record("a"))
    state = recover(tmp_path, now_ms=1000)
    assert live(state) == [("b", "2", None)]


def test_recover_snapshot_entry_already_expired_is_skipped(tmp_path):
    write_snapshot(tmp_path, [("a", "1", 5000)], [], next_aof=1)
    state = recover(tmp_path, now_ms=6000)
    assert live(state) == []


def test_recover_truncated_last_line_is_dropped_and_counted(tmp_path):
    write_snapshot(tmp_path, [("a", "1", None)], [], next_aof=1)
    partial = encode_record(set_record("c", "3")).encode("utf-8")[:15]
    write_aof(tmp_path, 1, set_record("b", "2"), tail=partial)
    state = recover(tmp_path, now_ms=1000)
    assert live(state) == [("a", "1", None), ("b", "2", None)]
    assert state.dropped_tails == 1


def test_recover_corrupt_middle_line_raises_with_file_and_line_number(tmp_path):
    good = encode_record(set_record("a", "1")).encode("utf-8")
    (tmp_path / aof_name(1)).write_bytes(good + b"{broken\n" + good)
    with pytest.raises(CorruptAofError) as info:
        recover(tmp_path, now_ms=1000)
    assert "appendonly-000001.aof" in str(info.value)
    assert "2" in str(info.value)


def test_corrupt_aof_error_is_a_value_error():
    assert issubclass(CorruptAofError, ValueError)


def test_recover_bad_last_line_with_newline_raises_not_truncated_tail(tmp_path):
    # 줄바꿈까지 있는 줄은 끝까지 쓴 줄이라, 깨졌다면 잘린 꼬리가 아니라 진짜 손상이에요.
    good = encode_record(set_record("a", "1")).encode("utf-8")
    (tmp_path / aof_name(1)).write_bytes(good + b"{broken\n")
    with pytest.raises(CorruptAofError):
        recover(tmp_path, now_ms=1000)


def test_recover_last_line_without_newline_but_valid_is_applied(tmp_path):
    full = encode_record(set_record("b", "2")).encode("utf-8").rstrip(b"\n")
    write_aof(tmp_path, 1, set_record("a", "1"), tail=full)
    state = recover(tmp_path, now_ms=1000)
    assert live(state) == [("a", "1", None), ("b", "2", None)]
    assert state.dropped_tails == 0


def test_recover_truncated_tail_in_non_last_file_is_dropped_and_next_file_applied(tmp_path):
    partial = encode_record(set_record("x", "9")).encode("utf-8")[:15]
    write_aof(tmp_path, 1, set_record("a", "1"), tail=partial)
    write_aof(tmp_path, 2, set_record("c", "3"))
    state = recover(tmp_path, now_ms=1000)
    assert live(state) == [("a", "1", None), ("c", "3", None)]
    assert state.dropped_tails == 1


def test_recover_files_are_replayed_in_numeric_order_not_text_order(tmp_path):
    # 글자 순서로 정렬하면 "10"이 "2"보다 앞서요. 숫자 순서로 읽어야 new가 이겨요.
    write_aof(tmp_path, 2, set_record("k", "old"))
    write_aof(tmp_path, 10, set_record("k", "new"))
    state = recover(tmp_path, now_ms=1000)
    assert live(state) == [("k", "new", None)]


def test_recover_aof_numbers_below_snapshot_next_aof_are_ignored(tmp_path):
    write_snapshot(tmp_path, [], [], next_aof=3)
    write_aof(tmp_path, 1, set_record("old1", "x"))
    write_aof(tmp_path, 2, set_record("old2", "x"))
    write_aof(tmp_path, 3, set_record("new3", "y"))
    write_aof(tmp_path, 4, set_record("new4", "z"))
    state = recover(tmp_path, now_ms=1000)
    assert live(state) == [("new3", "y", None), ("new4", "z", None)]


def test_recover_leftover_snapshot_tmp_is_ignored(tmp_path):
    # 스냅샷을 쓰다 죽으면 tmp만 남아요. 이전 스냅샷과 AOF로 복구해야 해요.
    write_snapshot(tmp_path, [("a", "1", None)], [], next_aof=1)
    (tmp_path / SNAPSHOT_TMP_NAME).write_bytes(b"\x00garbage{{{\n")
    write_aof(tmp_path, 1, set_record("b", "2"))
    write_aof(tmp_path, 2, set_record("c", "3"))
    state = recover(tmp_path, now_ms=1000)
    assert live(state) == [("a", "1", None), ("b", "2", None), ("c", "3", None)]


def test_recover_old_aofs_not_yet_deleted_after_snapshot_replace_are_ignored(tmp_path):
    # 스냅샷 교체 직후 옛 AOF를 지우기 전에 죽은 상황이에요.
    write_snapshot(tmp_path, [("a", "1", None), ("b", "2", None)], [], next_aof=3)
    write_aof(tmp_path, 1, del_record("a"))
    write_aof(tmp_path, 2, del_record("a"))
    write_aof(tmp_path, 3, set_record("c", "3"))
    state = recover(tmp_path, now_ms=1000)
    assert live(state) == [("a", "1", None), ("b", "2", None), ("c", "3", None)]


def test_recover_aof_set_with_past_exp_removes_older_snapshot_value(tmp_path):
    write_snapshot(tmp_path, [("a", "1", None)], [], next_aof=1)
    write_aof(tmp_path, 1, set_record("a", "2", 500))
    state = recover(tmp_path, now_ms=1000)
    assert live(state) == []


def test_recover_expire_existing_key_with_future_exp_updates_exp_and_keeps_value(tmp_path):
    write_snapshot(tmp_path, [("a", "1", None)], [], next_aof=1)
    write_aof(tmp_path, 1, expire_record("a", 9000))
    state = recover(tmp_path, now_ms=1000)
    assert live(state) == [("a", "1", 9000)]


def test_recover_expire_existing_key_with_past_exp_removes_key(tmp_path):
    write_snapshot(tmp_path, [("a", "1", None)], [], next_aof=1)
    write_aof(tmp_path, 1, expire_record("a", 500))
    state = recover(tmp_path, now_ms=1000)
    assert live(state) == []


def test_recover_expire_missing_key_stays_missing_without_error(tmp_path):
    write_aof(tmp_path, 1, expire_record("ghost", 9000))
    state = recover(tmp_path, now_ms=1000)
    assert live(state) == []


def test_recover_flush_clears_earlier_keys_keeps_later_keys_and_versions(tmp_path):
    write_aof(
        tmp_path,
        1,
        set_record("a", "1"),
        ns_version_record("user", 2),
        flush_record(),
        set_record("b", "2"),
    )
    state = recover(tmp_path, now_ms=1000)
    assert live(state) == [("b", "2", None)]
    assert state.versions == {"user": 2}


def test_recover_nsver_last_record_wins_and_snapshot_only_namespace_stays(tmp_path):
    write_snapshot(tmp_path, [], [("user", 3), ("post", 8)], next_aof=1)
    write_aof(tmp_path, 1, ns_version_record("user", 4), ns_version_record("user", 5))
    state = recover(tmp_path, now_ms=1000)
    assert state.versions == {"user": 5, "post": 8}


def test_recover_exp_equal_to_now_is_expired(tmp_path):
    write_aof(tmp_path, 1, set_record("a", "1", 1000))
    state = recover(tmp_path, now_ms=1000)
    assert live(state) == []


def test_recover_exp_one_ms_after_now_is_alive(tmp_path):
    write_aof(tmp_path, 1, set_record("a", "1", 1001))
    state = recover(tmp_path, now_ms=1000)
    assert live(state) == [("a", "1", 1001)]


def test_recover_next_aof_nothing_on_disk_is_1(tmp_path):
    assert recover(tmp_path, now_ms=1000).next_aof == 1


def test_recover_next_aof_snapshot_only_equals_snapshot_next_aof(tmp_path):
    write_snapshot(tmp_path, [], [], next_aof=5)
    assert recover(tmp_path, now_ms=1000).next_aof == 5


def test_recover_next_aof_after_snapshot_and_files_5_6_is_7(tmp_path):
    write_snapshot(tmp_path, [], [], next_aof=5)
    write_aof(tmp_path, 5, set_record("a", "1"))
    write_aof(tmp_path, 6, set_record("b", "2"))
    assert recover(tmp_path, now_ms=1000).next_aof == 7


def test_recover_next_aof_without_snapshot_and_files_1_to_3_is_4(tmp_path):
    for n in (1, 2, 3):
        write_aof(tmp_path, n, set_record(f"k{n}", "v"))
    assert recover(tmp_path, now_ms=1000).next_aof == 4


def test_recover_table_is_team_hashtable_and_get_returns_entry(tmp_path):
    write_aof(tmp_path, 1, set_record("b", "2"))
    state = recover(tmp_path, now_ms=1000)
    assert isinstance(state.table, HashTable)
    assert state.table.get("b").value == "2"


def test_recover_does_not_modify_any_file_in_data_dir(tmp_path):
    # 복구는 읽기만 해야 해요. 잘린 꼬리가 있어도 파일을 고치지 않아요.
    write_snapshot(tmp_path, [("a", "1", None)], [("user", 1)], next_aof=2)
    partial = encode_record(set_record("c", "3")).encode("utf-8")[:15]
    write_aof(tmp_path, 1, set_record("old", "x"))
    write_aof(tmp_path, 2, set_record("b", "2"), tail=partial)
    (tmp_path / SNAPSHOT_TMP_NAME).write_bytes(b"garbage")

    def dump():
        return {p.name: p.read_bytes() for p in tmp_path.iterdir()}

    before = dump()
    recover(tmp_path, now_ms=1000)
    assert dump() == before


def test_recover_broken_snapshot_record_propagates_value_error(tmp_path):
    (tmp_path / "snapshot.jsonl").write_bytes(
        b'{"snapshot":1,"next_aof":1}\n{broken\n'
    )
    with pytest.raises(ValueError):
        recover(tmp_path, now_ms=1000)
