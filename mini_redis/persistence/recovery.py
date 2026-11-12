"""영속성: 스냅샷과 번호 붙은 AOF 파일들을 읽어서 해시테이블 상태를 되살려요.

이 모듈은 파일에서 상태를 계산하기만 해요. 파일을 쓰거나 지우거나 이름을 바꾸지 않아요.
Store에 넘기고 서버를 띄우는 일은 다른 기능이 맡아요.
"""

import dataclasses
import os
import pathlib
from typing import Any

from mini_redis.hashtable import HashTable
from mini_redis.persistence.aof import decode_line
from mini_redis.persistence.layout import SNAPSHOT_NAME, aof_name, aof_numbers
from mini_redis.persistence.snapshot import load_snapshot


@dataclasses.dataclass
class RecoveredState:
    """복구 결과예요."""

    table: HashTable  # 만료되지 않은 살아 있는 키만 들어 있어요.
    versions: dict[str, int]  # 네임스페이스 -> 버전
    next_aof: int  # AOF 쓰기 쪽이 다음에 만들 파일 번호예요.
    dropped_tails: int  # 버린, 끝이 잘린 마지막 줄의 개수예요.


class CorruptAofError(ValueError):
    """끝이 잘린 줄로 볼 수 없는, 진짜로 깨진 AOF 줄을 만났을 때 던져요."""


def _apply(state: RecoveredState, record: dict[str, Any], now_ms: int) -> None:
    """레코드 하나를 반영해요. 모두 대입 연산이라 같은 레코드를 또 적용해도 결과가 같아요."""
    op = record["op"]
    if op == "set":
        exp = record["exp"]
        if exp is not None and now_ms >= exp:
            state.table.delete(record["k"])  # 이미 만료된 값은 예전 값도 같이 지워요.
        else:
            state.table.put(record["k"], record["v"], exp)
    elif op == "del":
        state.table.delete(record["k"])
    elif op == "expire":
        entry = state.table.get(record["k"])
        if entry is None:
            return
        exp = record["exp"]
        if now_ms >= exp:
            state.table.delete(record["k"])
        else:
            state.table.put(record["k"], entry.value, exp)
    elif op == "flush":
        state.table = HashTable()  # 버전은 그대로 두고 키만 비워요.
    elif op == "nsver":
        state.versions[record["ns"]] = record["ver"]


def _replay_file(state: RecoveredState, path: pathlib.Path, now_ms: int) -> None:
    """AOF 파일 하나를 줄 단위로 재생해요. 끝이 잘린 마지막 줄만 버려요."""
    lines = _split_on_newline(path.read_bytes())
    for index, line in enumerate(lines):
        try:
            record = decode_line(line.decode("utf-8"))
        except ValueError as exc:  # UnicodeDecodeError도 ValueError의 하위 클래스예요.
            is_last = index == len(lines) - 1
            if is_last and not line.endswith(b"\n"):
                state.dropped_tails += 1  # 쓰다가 죽은 흔적이라 그 줄만 버려요.
                return
            raise CorruptAofError(f"{path.name} {index + 1}번째 줄이 깨졌어요: {exc}") from exc
        _apply(state, record, now_ms)


def _split_on_newline(data: bytes) -> list[bytes]:
    """\n으로만 줄을 나누고 줄 끝은 남겨 둬요. 마지막 줄에 \n이 없는지 확인해야 해서예요."""
    lines: list[bytes] = []
    start = 0
    while start < len(data):
        end = data.find(b"\n", start)
        if end == -1:
            lines.append(data[start:])
            break
        lines.append(data[start : end + 1])
        start = end + 1
    return lines


def recover(data_dir: str | os.PathLike[str], now_ms: int) -> RecoveredState:
    """스냅샷을 읽고, 그 뒤 번호의 AOF를 순서대로 재생해서 상태를 돌려줘요.

    스냅샷 임시 파일은 스냅샷을 쓰다 죽은 흔적일 수 있어서 읽지 않아요.
    """
    directory = pathlib.Path(data_dir)
    state = RecoveredState(table=HashTable(), versions={}, next_aof=1, dropped_tails=0)

    next_aof_start = 1
    snapshot_path = directory / SNAPSHOT_NAME
    if snapshot_path.is_file():
        # 스냅샷이 깨졌으면 조용히 넘기지 않고 ValueError를 그대로 올려요.
        next_aof_start, records = load_snapshot(snapshot_path)
        for record in records:
            _apply(state, record, now_ms)

    numbers = aof_numbers(directory)
    for number in numbers:
        if number >= next_aof_start:  # 더 작은 번호는 이미 스냅샷 안에 들어 있어요.
            _replay_file(state, directory / aof_name(number), now_ms)

    state.next_aof = max([next_aof_start - 1] + numbers) + 1
    return state
