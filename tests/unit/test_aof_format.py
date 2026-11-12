"""AOF 한 줄 형식(레코드 만들기, 인코딩, 디코딩) 단위 테스트.

파일도 시계도 쓰지 않고, 레코드(dict) <-> 한 줄 문자열 변환만 확인합니다.
"""

import pytest

from mini_redis.persistence.aof import (
    decode_line,
    del_record,
    encode_record,
    expire_record,
    flush_record,
    ns_version_record,
    set_record,
)


# 항목 1: 다섯 가지 레코드가 인코딩 후 디코딩해도 그대로 돌아온다
@pytest.mark.parametrize(
    "record",
    [
        set_record("a", "1", 5000),
        del_record("a"),
        expire_record("a", 5000),
        flush_record(),
        ns_version_record("user", 7),
    ],
    ids=["set", "del", "expire", "flush", "nsver"],
)
def test_record_encode_then_decode_round_trips_unchanged(record):
    line = encode_record(record)
    assert decode_line(line) == record


# 항목 2: 명세의 set 예시와 글자까지 똑같다
def test_encode_set_record_matches_spec_example_exactly():
    line = encode_record(set_record("user:v3:42", "...", 1762934400000))
    assert line == '{"op":"set","k":"user:v3:42","v":"...","exp":1762934400000}\n'


# 항목 3: 나머지 네 가지 모양의 정확한 텍스트
def test_encode_del_record_has_exact_text():
    assert encode_record(del_record("a")) == '{"op":"del","k":"a"}\n'


def test_encode_flush_record_has_exact_text():
    assert encode_record(flush_record()) == '{"op":"flush"}\n'


def test_encode_expire_record_has_exact_text():
    assert encode_record(expire_record("a", 5000)) == '{"op":"expire","k":"a","exp":5000}\n'


def test_encode_ns_version_record_has_exact_text():
    assert encode_record(ns_version_record("user", 7)) == '{"op":"nsver","ns":"user","ver":7}\n'


# 항목 4: TTL 없는 SET은 "exp":null 로 적히고 None 으로 돌아온다
def test_encode_set_record_without_ttl_writes_null_and_decodes_to_none():
    line = encode_record(set_record("a", "1"))
    assert '"exp":null' in line
    assert decode_line(line)["exp"] is None


# 항목 5: 줄바꿈, 따옴표, 역슬래시가 들어간 값도 정확히 한 줄이고 그대로 돌아온다
def test_encode_set_record_with_special_characters_stays_one_line_and_round_trips():
    value = 'a\nb\rc"d\\e'
    record = set_record("k", value, None)
    line = encode_record(record)
    assert line.endswith("\n")
    assert line.count("\n") == 1
    assert decode_line(line)["v"] == value
    assert decode_line(line) == record


# 항목 6: 한글 키와 값은 이스케이프 없이 그대로 적히고 그대로 돌아온다
def test_encode_set_record_with_korean_text_is_unescaped_and_round_trips():
    record = set_record("세션:상철", "안녕", None)
    line = encode_record(record)
    assert "세션:상철" in line
    assert "안녕" in line
    assert decode_line(line) == record


# 항목 7: 빈 문자열 값은 None 이 아니라 빈 문자열로 돌아온다
def test_set_record_with_empty_string_value_round_trips_as_empty_string():
    decoded = decode_line(encode_record(set_record("a", "", None)))
    assert decoded["v"] == ""
    assert decoded["v"] is not None


# 항목 8: 끝의 줄바꿈이 없어도, "\r\n" 이어도 읽힌다
def test_decode_line_without_trailing_newline_works():
    assert decode_line('{"op":"del","k":"a"}') == {"op": "del", "k": "a"}


def test_decode_line_with_crlf_ending_works():
    assert decode_line('{"op":"del","k":"a"}\r\n') == {"op": "del", "k": "a"}


# 항목 9: 깨졌거나 모양이 틀린 줄은 ValueError
@pytest.mark.parametrize(
    "bad_line",
    [
        '{"op":"set","k":"a","v":"1',
        "",
        "[]",
        '{"k":"a"}',
        '{"op":"get","k":"a"}',
        '{"op":"set","k":"a","v":"1"}',
        '{"op":"set","k":"a","v":"1","exp":"5000"}',
        '{"op":"nsver","ns":"u","ver":true}',
        # op 가 문자열이 아니면(해시 불가 값 포함) TypeError 가 아니라 ValueError 여야 한다
        '{"op":[1]}',
        '{"op":{"a":1}}',
    ],
    ids=[
        "truncated_in_the_middle",
        "empty_string",
        "json_array_not_object",
        "op_missing",
        "op_get_is_never_logged",
        "set_without_exp",
        "exp_is_a_string",
        "ver_is_a_bool",
        "op_is_a_list",
        "op_is_an_object",
    ],
)
def test_decode_line_with_bad_line_raises_value_error(bad_line):
    with pytest.raises(ValueError):
        decode_line(bad_line)


# 항목 10: TTL이 있는 SET의 exp 는 호출한 쪽이 계산한 절대 시각 그대로다
def test_set_record_with_ttl_keeps_absolute_expire_time_unchanged():
    now = 1_000_000
    ttl_seconds = 10
    record = set_record("b", "2", now + ttl_seconds * 1000)
    decoded = decode_line(encode_record(record))
    assert decoded["exp"] == 1_010_000
