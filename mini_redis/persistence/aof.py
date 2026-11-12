"""AOF 한 줄 형식: 레코드(dict)를 만들고, 한 줄 JSON으로 바꾸고, 다시 읽어요.

한 줄에 JSON 하나를 적고, 만료 시각은 상대값이 아니라 절대 epoch ms로 적어요.
이 모듈은 줄 형식과 AofWriter(버퍼링, 파일 쓰기)를 맡아요. 스냅샷과 복구는 다른 모듈이 맡아요.
"""

import json
import os
import threading
import time
from collections.abc import Callable
from typing import Any

from mini_redis import config
from mini_redis.errors import PersistenceUnavailable


def set_record(key: str, value: str, expire_at_ms: int | None = None) -> dict[str, Any]:
    """SET 레코드를 만들어요. expire_at_ms가 None이면 만료 없음이에요."""
    return {"op": "set", "k": key, "v": value, "exp": expire_at_ms}


def del_record(key: str) -> dict[str, Any]:
    """DEL 레코드를 만들어요."""
    return {"op": "del", "k": key}


def expire_record(key: str, expire_at_ms: int) -> dict[str, Any]:
    """EXPIRE 레코드를 만들어요. 시각은 절대 epoch ms예요."""
    return {"op": "expire", "k": key, "exp": expire_at_ms}


def flush_record() -> dict[str, Any]:
    """FLUSH 레코드를 만들어요."""
    return {"op": "flush"}


def ns_version_record(ns: str, version: int) -> dict[str, Any]:
    """네임스페이스 버전 변경 레코드를 만들어요."""
    return {"op": "nsver", "ns": ns, "ver": version}


def encode_record(record: dict[str, Any]) -> str:
    """레코드를 줄바꿈 하나로 끝나는 한 줄 JSON 문자열로 바꿔요."""
    return json.dumps(record, separators=(",", ":"), ensure_ascii=False) + "\n"


def _is_str(value: Any) -> bool:
    return isinstance(value, str)


def _is_int(value: Any) -> bool:
    # bool은 int의 하위 타입이라 따로 걸러내요.
    return isinstance(value, int) and not isinstance(value, bool)


def _is_int_or_none(value: Any) -> bool:
    return value is None or _is_int(value)


# op별로 꼭 있어야 하는 필드와, 그 필드가 통과해야 하는 검사예요.
_REQUIRED_FIELDS = {
    "set": {"k": _is_str, "v": _is_str, "exp": _is_int_or_none},
    "del": {"k": _is_str},
    "expire": {"k": _is_str, "exp": _is_int},
    "flush": {},
    "nsver": {"ns": _is_str, "ver": _is_int},
}


def decode_line(line: str) -> dict[str, Any]:
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


class AofBrokenError(PersistenceUnavailable, RuntimeError):
    """롤백까지 실패해서 AOF 파일 끝이 깨졌을 수 있을 때 던져요. 더 적지 않고 멈춰요."""


class AofUnavailableError(PersistenceUnavailable):
    """디스크 쓰기가 reject_after_s초 넘게 계속 실패해서 새 쓰기를 받지 않을 때 던져요."""


class AofWriter:
    """AOF 줄을 메모리 버퍼에 모아 두었다가 파일에 적어요.

    append는 버퍼에 줄을 더하기만 해서 가벼워요. 디스크 I/O는 flush()에서만 일어나고,
    start()로 켠 스레드가 interval_s마다 flush()를 불러요.
    """

    def __init__(
        self,
        path: str | os.PathLike[str],
        interval_s: float = config.AOF_FSYNC_INTERVAL_S,
        reject_after_s: float = config.AOF_REJECT_AFTER_S,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.path = path
        self._interval_s = interval_s
        self._reject_after_s = reject_after_s
        self._monotonic = monotonic
        # flush가 연속으로 실패하기 시작한 시각(단조 시계)이에요. 오류 없이 flush가 끝나면 None으로 돌아가요.
        self.failing_since: float | None = None
        self._buffer: list[bytes] = []
        # rotate 전에 쌓였지만 아직 옛 파일에 못 적은 묶음이에요. 오래된 것부터 들어 있어요.
        self._pending: list[tuple[str | os.PathLike[str], list[bytes]]] = []
        # 지금 path에 fsync까지 끝난 바이트 수예요. 스냅샷 크기 기준이 이걸 읽고, 파일을 stat하지 않아요.
        self.current_bytes: int = 0
        self._buffer_lock = threading.Lock()  # 버퍼를 바꿔 끼울 때만 잡아요.
        self._write_lock = threading.RLock()  # flush끼리 순서가 섞이지 않게 해요.
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.last_error: BaseException | None = None  # 마지막 flush가 실패했다면 그 예외예요.
        # 롤백(truncate)까지 실패하면 파일 끝이 깨진 채 남아요. 이때부터는 더 적지 않아요.
        self.broken: bool = False
        self.rollback_error: BaseException | None = None  # 롤백이 실패했을 때 그 예외예요.

    def append(self, record: dict[str, Any]) -> None:
        """레코드를 한 줄 바이트로 바꿔 버퍼에 더해요. 파일은 건드리지 않아요.

        인코딩을 여기서 미리 해요. flush에서 실패하면 들고 나간 줄들이 통째로 사라질 수 있어서,
        못 쓰는 레코드는 append 단계에서 바로 예외로 돌려줘요.

        flush가 reject_after_s초 넘게 계속 실패 중이면 AofUnavailableError로 거절해요.
        알려진 한계: 저장소는 로그를 먼저 적고 나서 바꾸기 때문에 거절된 쓰기는 반영되지 않지만,
        lease를 걸고 하는 PUT이 로그에서 거절되면 그 lease는 이미 쓰인 뒤예요. 클라이언트는
        GET ?lease=1로 lease를 다시 받아야 해요.
        """
        if self.broken:
            raise AofBrokenError("AOF 파일이 깨진 상태라 더 적을 수 없어요")
        since = self.failing_since
        if since is not None and self._monotonic() - since >= self._reject_after_s:
            raise AofUnavailableError(
                f"AOF를 {self._reject_after_s:g}초 넘게 디스크에 쓰지 못해서 쓰기를 받지 않아요"
            )
        line = encode_record(record).encode("utf-8")
        with self._buffer_lock:
            self._buffer.append(line)

    def rotate(self, new_path: str | os.PathLike[str]) -> None:
        """이후 줄이 new_path로 가도록 바꿔요. 파일은 열지도 stat하지도 않고 버퍼만 만져요.

        스냅샷은 Store 락 안에서 AOF를 바꿔 끼워야 하는데, 락 안에서는 디스크 I/O를 못 해요.
        그래서 지금까지 쌓인 줄은 옛 경로 이름표를 붙여 _pending에 넘겨 두고, 실제 쓰기는 flush가 해요.
        _write_lock은 잡지 않아요. flush가 디스크에 쓰는 동안 락 안의 rotate가 기다리면 안 돼서예요.
        """
        if self.broken:
            raise AofBrokenError("AOF 파일이 깨진 상태라 더 적을 수 없어요")
        with self._buffer_lock:
            if self._buffer:
                self._pending.append((self.path, self._buffer))
            self._buffer = []
            self.path = new_path
            self.current_bytes = 0

    def drop_pending(self) -> int:
        """옛 파일로 가려고 기다리던 줄을 버리고 몇 줄이었는지 돌려줘요.

        스냅샷 교체가 끝난 뒤에 불러요. 그 줄들의 결과는 이미 새 스냅샷 안에 들어 있어서예요.
        """
        with self._buffer_lock:
            dropped = sum(len(lines) for _, lines in self._pending)
            self._pending = []
        return dropped

    def _write_batch(self, path: str | os.PathLike[str], data: bytes) -> tuple[BaseException | None, bool]:
        """한 파일에 data를 적고 fsync해요. (처음 에러 또는 None, durable 여부)를 돌려줘요."""
        # with 문을 쓰면 닫기 에러가 처음 에러를 덮어써서, 닫기를 직접 부르고 에러를 따로 모아요.
        first_error: BaseException | None = None
        durable = False  # write, flush, fsync가 모두 성공했는지예요.
        f = None
        try:
            try:
                f = open(path, "ab")
                start_size = f.tell()  # 이번 시도 전 파일 크기예요.
                try:
                    f.write(data)
                    f.flush()
                    os.fsync(f.fileno())
                except Exception:
                    # 반쯤 쓴 바이트가 남지 않게 원래 크기로 잘라 내요.
                    try:
                        f.truncate(start_size)
                    except Exception as truncate_error:
                        # 잘라 내지 못하면 다시 써도 안전하지 않아서 깨진 상태로 표시해요.
                        # 처음 에러가 더 중요해서 그대로 던지고, 이 에러는 따로 남겨 둬요.
                        self.rollback_error = truncate_error
                        self.broken = True
                    raise
                durable = True
            except Exception as error:
                first_error = error
        finally:
            # KeyboardInterrupt 같은 예외가 지나가는 중에도 파일은 꼭 닫아요.
            if f is not None:
                try:
                    f.close()
                except Exception as close_error:
                    # 이미 실패한 뒤의 닫기 에러는 삼켜서 처음 에러가 이기게 해요.
                    if first_error is None:
                        first_error = close_error
        return first_error, durable

    def flush(self) -> int:
        """버퍼를 파일에 적고 fsync해요. 적은 줄 수를 돌려줘요.

        rotate 때문에 옛 파일로 가야 할 묶음이 있으면 오래된 것부터 차례로 적어요.
        실패하면 failing_since에 첫 실패 시각을 남기고, 오류 없이 끝나면 지워요.
        """
        with self._write_lock:
            try:
                written = self._flush_locked()
            except BaseException:
                if self.failing_since is None:
                    self.failing_since = self._monotonic()
                raise
            self.failing_since = None
            return written

    def _flush_locked(self) -> int:
        """실제로 버퍼를 파일에 적어요. flush()가 쓰기 락을 잡고 불러요."""
        with self._write_lock:
            if self.broken:
                # 파일을 열지도, 버퍼를 건드리지도 않고 바로 멈춰요.
                raise AofBrokenError("AOF 파일이 깨진 상태라 더 적을 수 없어요")
            # 바꿔 끼우는 것도 쓰기 락 안에서 해야 나중 줄이 먼저 적히지 않아요.
            with self._buffer_lock:
                batches = [(p, lines) for p, lines in self._pending + [(self.path, self._buffer)] if lines]
                self._pending = []
                self._buffer = []
                current = os.fspath(self.path)
            # 각 묶음의 경로는 여기서 정해요. 적는 동안 rotate가 와도 self.path를 다시 읽지 않아요.
            total = 0
            for index, (path, lines) in enumerate(batches):
                data = b"".join(lines)
                first_error, durable = self._write_batch(path, data)
                if durable and os.fspath(path) == current:
                    with self._buffer_lock:
                        # 그 사이 rotate가 왔다면 이 바이트는 이미 옛 파일 몫이라 세지 않아요.
                        if os.fspath(self.path) == current:
                            self.current_bytes += len(data)
                if first_error is None:
                    total += len(lines)
                    continue
                # 실패한 묶음과 아직 안 적은 묶음을 되돌려요. 단, durable이면 닫기만 실패한 거라 그 묶음은 빼요.
                # 줄은 이미 fsync로 디스크에 있어서, 다시 적으면 앞부분만 적히다 끊겼을 때
                # 새 값 뒤에 옛 값이 남을 수 있어요.
                requeue = ([] if durable else [(path, lines)]) + batches[index + 1 :]
                self._requeue(requeue)
                self.last_error = first_error
                raise first_error
            self.last_error = None
            return total

    def _requeue(self, batches: list[tuple[str | os.PathLike[str], list[bytes]]]) -> None:
        """못 적은 묶음을 제자리로 되돌려요. 그동안 쌓인 줄보다 항상 앞에 놓여요."""
        with self._buffer_lock:
            current = os.fspath(self.path)
            front_buffer: list[bytes] = []
            front_pending: list[tuple[str | os.PathLike[str], list[bytes]]] = []
            for path, lines in batches:
                if os.fspath(path) == current:
                    front_buffer.extend(lines)
                else:
                    front_pending.append((path, lines))
            self._buffer = front_buffer + self._buffer
            self._pending = front_pending + self._pending

    def start(self) -> None:
        """1초마다 flush하는 데몬 스레드를 켜요. 이미 켜져 있으면 아무것도 안 해요."""
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """스레드를 멈추고 남은 줄을 마지막으로 flush해요. start 없이 불러도 돼요."""
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        self.flush()

    def _run(self) -> None:
        while not self._stop_event.wait(self._interval_s):
            try:
                self.flush()
            except Exception:
                pass  # last_error에 남아 있고, 다음 주기에 다시 써요.
