"""스냅샷 파일 쓰기와 읽기, 그리고 지난 AOF 파일 지우기예요.

스냅샷은 AOF와 같은 JSON 줄을 쓰는 "압축된 AOF"예요. 그래서 복구할 때 AOF를 읽던 decode_line을
그대로 써요. 첫 줄은 헤더이고, 여기에 "어느 AOF 번호부터 이어서 재생할지"를 적어 둬요.
락을 잡고 복사하거나 AOF를 새 파일로 바꾸는 일은 이 모듈이 아니라 다른 기능이 맡아요.
"""

import json
import os
import pathlib
from collections.abc import Iterable
from typing import Any

from mini_redis.persistence.aof import decode_line, encode_record, ns_version_record, set_record
from mini_redis.persistence.layout import (
    SNAPSHOT_NAME,
    SNAPSHOT_TMP_NAME,
    aof_name,
    aof_numbers,
)

SNAPSHOT_FORMAT_VERSION = 1


def _header_line(next_aof: int) -> bytes:
    header = {"snapshot": SNAPSHOT_FORMAT_VERSION, "next_aof": next_aof}
    return (json.dumps(header, separators=(",", ":")) + "\n").encode("utf-8")


def write_snapshot(
    data_dir: str | os.PathLike[str],
    entries: Iterable[tuple[str, str, int | None]],
    versions: Iterable[tuple[str, int]],
    next_aof: int,
) -> pathlib.Path:
    """스냅샷을 임시 파일에 다 쓰고 fsync한 뒤, 이름을 바꿔서 한 번에 교체해요.

    중간에 죽거나 실패해도 교체 전까지는 이전 스냅샷을 건드리지 않아서, 파일이 반쯤 쓰인 채
    보이는 일이 없어요. data_dir은 미리 만들어져 있어야 해요.
    """
    directory = pathlib.Path(data_dir)
    tmp_path = directory / SNAPSHOT_TMP_NAME
    final_path = directory / SNAPSHOT_NAME

    # "wb"는 이전에 죽으면서 남은 임시 파일도 비우고 새로 시작해요.
    with open(tmp_path, "wb") as f:
        f.write(_header_line(next_aof))
        for key, value, expire_at_ms in entries:
            f.write(encode_record(set_record(key, value, expire_at_ms)).encode("utf-8"))
        for namespace, version in versions:
            f.write(encode_record(ns_version_record(namespace, version)).encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())

    # 같은 파일 시스템 안에서의 replace는 원자적이라, 읽는 쪽은 옛 파일이나 새 파일 중 하나만 봐요.
    os.replace(tmp_path, final_path)

    # 이름 바꾸기 자체를 디스크에 남기려면 디렉터리를 fsync해야 해요. Windows는 디렉터리를 못 열어서 건너뛰어요.
    if os.name != "nt":
        dir_fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    return final_path


def _parse_header(line: str) -> int:
    """헤더 줄을 검사하고 next_aof를 돌려줘요. 모양이 다르면 ValueError예요."""
    header: Any = json.loads(line)
    if not isinstance(header, dict) or set(header) != {"snapshot", "next_aof"}:
        raise ValueError("스냅샷 헤더는 snapshot과 next_aof 두 키만 가져야 해요")
    version = header["snapshot"]
    next_aof = header["next_aof"]
    # bool은 int의 하위 타입이라 True가 1로 통과하지 않게 따로 걸러내요.
    if isinstance(version, bool) or not isinstance(version, int) or version != SNAPSHOT_FORMAT_VERSION:
        raise ValueError(f"지원하지 않는 스냅샷 버전이에요: {version!r}")
    if isinstance(next_aof, bool) or not isinstance(next_aof, int) or next_aof < 1:
        raise ValueError(f"next_aof는 1 이상의 정수여야 해요: {next_aof!r}")
    return next_aof


def load_snapshot(path: str | os.PathLike[str]) -> tuple[int, list[dict[str, Any]]]:
    """스냅샷을 읽어 (next_aof, 레코드 목록)으로 돌려줘요.

    스냅샷은 replace로만 보이게 되니 중간에 끊긴 파일이 정상일 수 없어요. 그래서 끝 줄이
    잘렸더라도 조용히 넘기지 않고 어떤 나쁜 줄이든 ValueError로 알려요.
    """
    data = pathlib.Path(path).read_bytes()
    # splitlines()는 \u2028 같은 문자에서도 끊어서 쓰지 않고, 줄바꿈 \n 하나로만 나눠요.
    text = data.decode("utf-8")  # 잘못된 UTF-8은 UnicodeDecodeError(ValueError의 하위)예요.
    if text == "":
        raise ValueError("스냅샷 파일이 비어 있어요")
    if not text.endswith("\n"):
        raise ValueError("스냅샷의 마지막 줄이 줄바꿈 없이 끝나요(잘린 파일이에요)")
    lines = text[:-1].split("\n")

    next_aof = _parse_header(lines[0])
    records: list[dict[str, Any]] = []
    for line in lines[1:]:
        record = decode_line(line)
        if record["op"] not in ("set", "nsver"):
            raise ValueError(f"스냅샷에는 set과 nsver만 있어야 해요: {record['op']!r}")
        records.append(record)
    return next_aof, records


def delete_old_aofs(data_dir: str | os.PathLike[str], before: int) -> list[int]:
    """번호가 before보다 작은 AOF 파일을 지우고, 지운 번호를 오름차순으로 돌려줘요."""
    directory = pathlib.Path(data_dir)
    deleted: list[int] = []
    for number in aof_numbers(directory):
        if number < before:
            (directory / aof_name(number)).unlink()
            deleted.append(number)
    return deleted
