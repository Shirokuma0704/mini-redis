"""AOF 한 줄 형식: 레코드(dict)를 만들고, 한 줄 JSON으로 바꾸고, 다시 읽어요.

한 줄에 JSON 하나를 적고, 만료 시각은 상대값이 아니라 절대 epoch ms로 적어요.
파일 쓰기와 복구는 이 모듈의 다른 기능이 맡아요.
"""

import json


def set_record(key, value, expire_at_ms=None):
    """SET 레코드를 만들어요. expire_at_ms가 None이면 만료 없음이에요."""
    return {"op": "set", "k": key, "v": value, "exp": expire_at_ms}


def del_record(key):
    """DEL 레코드를 만들어요."""
    return {"op": "del", "k": key}


def expire_record(key, expire_at_ms):
    """EXPIRE 레코드를 만들어요. 시각은 절대 epoch ms예요."""
    return {"op": "expire", "k": key, "exp": expire_at_ms}


def flush_record():
    """FLUSH 레코드를 만들어요."""
    return {"op": "flush"}


def ns_version_record(ns, version):
    """네임스페이스 버전 변경 레코드를 만들어요."""
    return {"op": "nsver", "ns": ns, "ver": version}


def encode_record(record):
    """레코드를 줄바꿈 하나로 끝나는 한 줄 JSON 문자열로 바꿔요."""
    return json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n"


def _is_str(value):
    return isinstance(value, str)


def _is_int(value):
    # bool은 int의 하위 타입이라 따로 걸러내요.
    return isinstance(value, int) and not isinstance(value, bool)


def _is_int_or_none(value):
    return value is None or _is_int(value)


# op별로 꼭 있어야 하는 필드와, 그 필드가 통과해야 하는 검사예요.
_REQUIRED_FIELDS = {
    "set": {"k": _is_str, "v": _is_str, "exp": _is_int_or_none},
    "del": {"k": _is_str},
    "expire": {"k": _is_str, "exp": _is_int},
    "flush": {},
    "nsver": {"ns": _is_str, "ver": _is_int},
}


def decode_line(line):
    """한 줄을 레코드(dict)로 되돌려요. 깨졌거나 모양이 틀리면 ValueError예요."""
    record = json.loads(line.rstrip("\r\n"))
    if not isinstance(record, dict):
        raise ValueError("레코드는 JSON 객체여야 해요")
    op = record.get("op")
    if not isinstance(op, str) or op not in _REQUIRED_FIELDS:
        raise ValueError(f"알 수 없는 op예요: {op!r}")
    for field, is_valid in _REQUIRED_FIELDS[op].items():
        if field not in record or not is_valid(record[field]):
            raise ValueError(f"{op} 레코드의 {field} 필드가 없거나 타입이 틀려요")
    return record
