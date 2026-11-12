"""스냅샷 파일 쓰기/읽기와 AOF 파일 이름 규칙을 확인해요.

스냅샷은 AOF와 같은 JSON 줄을 쓰는 "압축된 AOF"라서, 줄 바이트까지 정확히 맞는지 봐요.
디스크가 중간에 실패해도 이전 스냅샷이 그대로 남는지도 함께 확인해요.
"""

import os
import pathlib

import pytest

from mini_redis.persistence import layout, snapshot as snapshot_module
from mini_redis.persistence.aof import ns_version_record, set_record
from mini_redis.persistence.layout import SNAPSHOT_NAME, SNAPSHOT_TMP_NAME, aof_name, aof_numbers
from mini_redis.persistence.snapshot import delete_old_aofs, load_snapshot, write_snapshot

HEADER_3 = b'{"snapshot":1,"next_aof":3}\n'
SAMPLE_ENTRIES = [("a", "1", None), ("b", "2", 5000)]
SAMPLE_VERSIONS = [("user", 7)]


def _touch(directory: pathlib.Path, *names: str) -> None:
    """빈 파일을 여러 개 만들어요."""
    for name in names:
        (directory / name).write_bytes(b"")


def _write_raw(directory: pathlib.Path, data: bytes) -> pathlib.Path:
    """검증할 스냅샷 바이트를 그대로 파일로 적어요."""
    path = directory / SNAPSHOT_NAME
    path.write_bytes(data)
    return path


# ---- layout ----


def test_aof_name_small_number_is_zero_padded_to_six_digits():
    assert aof_name(1) == "appendonly-000001.aof"
    assert aof_name(42) == "appendonly-000042.aof"


def test_aof_name_wide_number_grows_past_six_digits():
    assert aof_name(1234567) == "appendonly-1234567.aof"


def test_aof_numbers_mixed_directory_returns_only_exact_matches_ascending(tmp_path):
    _touch(
        tmp_path,
        "appendonly-000003.aof",
        "appendonly-000001.aof",
        "appendonly-000010.aof",
        "snapshot.jsonl",
        "snapshot.jsonl.tmp",
        "appendonly-x.aof",
        "appendonly-000002.aof.bak",
        "notes.txt",
    )

    assert aof_numbers(tmp_path) == [1, 3, 10]


def test_aof_numbers_missing_directory_returns_empty_list(tmp_path):
    assert aof_numbers(tmp_path / "no-such-dir") == []


# ---- write_snapshot ----


def test_write_snapshot_normal_call_returns_final_path_and_removes_tmp(tmp_path):
    path = write_snapshot(tmp_path, SAMPLE_ENTRIES, SAMPLE_VERSIONS, 3)

    assert path == tmp_path / "snapshot.jsonl"
    assert path.exists()
    assert not (tmp_path / "snapshot.jsonl.tmp").exists()


def test_write_snapshot_sample_data_writes_exact_bytes_without_crlf(tmp_path):
    path = write_snapshot(tmp_path, SAMPLE_ENTRIES, SAMPLE_VERSIONS, 3)

    # 줄바꿈이 \n 하나뿐인지(\r\n이 아닌지)도 이 비교로 함께 확인돼요.
    assert path.read_bytes() == (
        b'{"snapshot":1,"next_aof":3}\n'
        b'{"op":"set","k":"a","v":"1","exp":null}\n'
        b'{"op":"set","k":"b","v":"2","exp":5000}\n'
        b'{"op":"nsver","ns":"user","ver":7}\n'
    )


def test_load_snapshot_written_sample_round_trips_to_same_records(tmp_path):
    path = write_snapshot(tmp_path, SAMPLE_ENTRIES, SAMPLE_VERSIONS, 3)

    assert load_snapshot(path) == (
        3,
        [
            set_record("a", "1", None),
            set_record("b", "2", 5000),
            ns_version_record("user", 7),
        ],
    )


def test_write_snapshot_newline_quote_and_korean_values_round_trip_unchanged(tmp_path):
    entries = [
        ("줄\n바꿈", "첫째 줄\n둘째 줄", None),
        ('따옴표"키', 'He said "hi"', 1234),
        ("한글키", "안녕하세요 세계", 99),
    ]

    path = write_snapshot(tmp_path, entries, [], 1)
    next_aof, records = load_snapshot(path)

    assert next_aof == 1
    assert records == [set_record(k, v, exp) for k, v, exp in entries]
    # 값 안의 줄바꿈은 \n 이스케이프로 적혀서, 줄 수는 헤더 1 + 레코드 수 그대로여야 해요.
    assert len(path.read_bytes().split(b"\n")) - 1 == 1 + len(entries)


def test_write_snapshot_empty_input_writes_header_line_only(tmp_path):
    path = write_snapshot(tmp_path, [], [], 5)

    assert path.read_bytes() == b'{"snapshot":1,"next_aof":5}\n'
    assert load_snapshot(path) == (5, [])


def test_write_snapshot_called_twice_second_content_fully_replaces_first(tmp_path):
    write_snapshot(tmp_path, [("old", "x", None), ("old2", "y", 1)], [("ns", 1)], 2)

    path = write_snapshot(tmp_path, [("new", "z", None)], [("ns2", 9)], 4)

    assert load_snapshot(path) == (
        4,
        [set_record("new", "z", None), ns_version_record("ns2", 9)],
    )


def test_write_snapshot_leftover_tmp_with_garbage_is_overwritten_not_appended(tmp_path):
    # 이전에 죽으면서 남은 임시 파일이에요. 이어 붙이면 안 되고 새로 써야 해요.
    (tmp_path / SNAPSHOT_TMP_NAME).write_bytes(b"\xff\xfegarbage garbage garbage\n" * 20)

    path = write_snapshot(tmp_path, SAMPLE_ENTRIES, SAMPLE_VERSIONS, 3)

    assert path.read_bytes().startswith(HEADER_3)
    assert b"garbage" not in path.read_bytes()
    assert not (tmp_path / SNAPSHOT_TMP_NAME).exists()


def test_write_snapshot_fsync_failure_raises_and_keeps_existing_snapshot_untouched(
    tmp_path, monkeypatch
):
    good_path = write_snapshot(tmp_path, [("keep", "me", None)], [("ns", 1)], 2)
    before = good_path.read_bytes()

    def failing_fsync(fd):
        raise OSError("디스크가 가득 찼다고 가정해요")

    monkeypatch.setattr(snapshot_module.os, "fsync", failing_fsync)

    with pytest.raises(OSError):
        write_snapshot(tmp_path, [("new", "data", None)], [], 9)

    assert good_path.read_bytes() == before


def test_write_snapshot_normal_call_fsyncs_tmp_before_replace_with_tmp_and_final_paths(
    tmp_path, monkeypatch
):
    events: list[tuple] = []
    real_fsync = os.fsync
    real_replace = os.replace

    def recording_fsync(fd):
        events.append(("fsync", fd))
        return real_fsync(fd)

    def recording_replace(src, dst, *args, **kwargs):
        events.append(("replace", pathlib.Path(src), pathlib.Path(dst)))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(snapshot_module.os, "fsync", recording_fsync)
    monkeypatch.setattr(snapshot_module.os, "replace", recording_replace)

    write_snapshot(tmp_path, SAMPLE_ENTRIES, SAMPLE_VERSIONS, 3)

    replace_indexes = [i for i, e in enumerate(events) if e[0] == "replace"]
    assert len(replace_indexes) == 1
    replace_index = replace_indexes[0]
    assert events[replace_index][1:] == (
        tmp_path / SNAPSHOT_TMP_NAME,
        tmp_path / SNAPSHOT_NAME,
    )
    # replace 앞에 fsync가 하나 이상 있어야 해요. (POSIX의 디렉터리 fsync는 replace 뒤에 올 수 있어서 보지 않아요.)
    assert any(e[0] == "fsync" for e in events[:replace_index])


# ---- load_snapshot ----


@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"", id="empty-file"),
        pytest.param(b'{"op":"set","k":"a","v":"1","exp":null}\n', id="header-missing"),
        pytest.param(b'{"snapshot":2,"next_aof":3}\n', id="header-version-2"),
        pytest.param(b'{"snapshot":1,"next_aof":0}\n', id="next-aof-zero"),
        pytest.param(b'{"snapshot":1,"next_aof":"3"}\n', id="next-aof-string"),
        pytest.param(
            HEADER_3
            + b'{"op":"set","k":"a","v":"1","exp":null}\n'
            + b'{"op":"set","k":\n'
            + b'{"op":"set","k":"b","v":"2","exp":null}\n',
            id="broken-json-in-the-middle",
        ),
        pytest.param(
            HEADER_3
            + b'{"op":"set","k":"a","v":"1","exp":null}\n'
            + b'{"op":"set","k":"b","v":',
            id="truncated-last-line-without-newline",
        ),
        pytest.param(HEADER_3 + b'{"op":"del","k":"a"}\n', id="del-record"),
    ],
)
def test_load_snapshot_malformed_file_raises_value_error(tmp_path, data):
    path = _write_raw(tmp_path, data)

    with pytest.raises(ValueError):
        load_snapshot(path)


# ---- delete_old_aofs ----


def test_delete_old_aofs_some_older_files_deletes_only_those_and_returns_numbers(tmp_path):
    _touch(
        tmp_path,
        aof_name(1),
        aof_name(2),
        aof_name(3),
        aof_name(4),
        SNAPSHOT_NAME,
    )

    deleted = delete_old_aofs(tmp_path, 3)

    assert deleted == [1, 2]
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        [aof_name(3), aof_name(4), SNAPSHOT_NAME]
    )


def test_delete_old_aofs_nothing_older_returns_empty_list_and_removes_nothing(tmp_path):
    _touch(tmp_path, aof_name(3), aof_name(4), SNAPSHOT_NAME)

    deleted = delete_old_aofs(tmp_path, 3)

    assert deleted == []
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        [aof_name(3), aof_name(4), SNAPSHOT_NAME]
    )


# ---- layout: 정식 이름만 AOF로 인정해요 ----
# 우리 writer는 aof_name(n) 이름만 만들어요. 숫자 표기가 다른 별명(appendonly-1.aof)을 받아 주면
# 읽는 쪽이 aof_name(n)으로 경로를 다시 만들 때 없는 파일을 찾거나 같은 파일을 두 번 읽게 돼요.


def test_aof_numbers_alias_spellings_alone_return_empty_list(tmp_path):
    _touch(
        tmp_path,
        "appendonly-1.aof",
        "appendonly-0000001.aof",
        "appendonly-000001.aof.tmp",
    )

    assert aof_numbers(tmp_path) == []


def test_aof_numbers_alias_next_to_canonical_returns_number_once(tmp_path):
    _touch(tmp_path, "appendonly-1.aof", "appendonly-000001.aof")

    assert aof_numbers(tmp_path) == [1]


def test_aof_numbers_directory_with_aof_name_is_ignored(tmp_path):
    (tmp_path / "appendonly-000002.aof").mkdir()
    _touch(tmp_path, "appendonly-000003.aof")

    assert aof_numbers(tmp_path) == [3]


def test_aof_numbers_seven_digit_canonical_name_is_accepted(tmp_path):
    _touch(tmp_path, aof_name(1000000))

    assert aof_numbers(tmp_path) == [1000000]


def test_delete_old_aofs_alias_file_is_left_untouched_without_error(tmp_path):
    _touch(tmp_path, "appendonly-000001.aof", "appendonly-2.aof")

    assert delete_old_aofs(tmp_path, 5) == [1]
    assert (tmp_path / "appendonly-2.aof").exists()
    assert not (tmp_path / "appendonly-000001.aof").exists()
