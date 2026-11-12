"""AOF 한 줄 형식: 레코드(dict)를 만들고, 한 줄 JSON으로 바꾸고, 다시 읽어요.

한 줄에 JSON 하나를 적고, 만료 시각은 상대값이 아니라 절대 epoch ms로 적어요.
파일 쓰기와 복구는 이 모듈의 다른 기능이 맡아요.
"""

import json
import os
import threading

from mini_redis import config


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


class AofWriter:
    """AOF 줄을 메모리 버퍼에 모아 두었다가 파일에 적어요.

    append는 버퍼에 줄을 더하기만 해서 가벼워요. 디스크 I/O는 flush()에서만 일어나고,
    start()로 켠 스레드가 interval_s마다 flush()를 불러요.
    """

    def __init__(self, path, interval_s=config.AOF_FSYNC_INTERVAL_S):
        self.path = path
        self._interval_s = interval_s
        self._buffer = []
        self._buffer_lock = threading.Lock()  # 버퍼를 바꿔 끼울 때만 잡아요.
        self._write_lock = threading.Lock()  # flush끼리 순서가 섞이지 않게 해요.
        self._stop_event = threading.Event()
        self._thread = None
        self.last_error = None  # 마지막 flush가 실패했다면 그 예외예요.

    def append(self, record):
        """레코드를 한 줄로 바꿔 버퍼에 더해요. 파일은 건드리지 않아요."""
        line = encode_record(record)
        with self._buffer_lock:
            self._buffer.append(line)

    def flush(self):
        """버퍼를 파일에 적고 fsync해요. 적은 줄 수를 돌려줘요."""
        with self._write_lock:
            # 바꿔 끼우는 것도 쓰기 락 안에서 해야 나중 줄이 먼저 적히지 않아요.
            with self._buffer_lock:
                lines, self._buffer = self._buffer, []
            if not lines:
                return 0
            data = "".join(lines).encode("utf-8")
            try:
                with open(self.path, "ab") as f:
                    start_size = f.tell()  # 이번 시도 전 파일 크기예요.
                    try:
                        f.write(data)
                        f.flush()
                        os.fsync(f.fileno())
                    except Exception:
                        # 반쯤 쓴 바이트가 남지 않게 원래 크기로 잘라 내요.
                        try:
                            f.truncate(start_size)
                        except Exception:
                            pass  # 처음 에러가 더 중요해서 이 에러는 무시해요.
                        raise
            except Exception as error:
                # 들고 나간 줄을 버퍼 맨 앞에 되돌려서 다음 flush가 먼저 적게 해요.
                with self._buffer_lock:
                    self._buffer = lines + self._buffer
                self.last_error = error
                raise
            self.last_error = None
            return len(lines)

    def start(self):
        """1초마다 flush하는 데몬 스레드를 켜요. 이미 켜져 있으면 아무것도 안 해요."""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """스레드를 멈추고 남은 줄을 마지막으로 flush해요. start 없이 불러도 돼요."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        self.flush()

    def _run(self):
        while not self._stop_event.wait(self._interval_s):
            try:
                self.flush()
            except Exception:
                pass  # last_error에 남아 있고, 다음 주기에 다시 써요.
