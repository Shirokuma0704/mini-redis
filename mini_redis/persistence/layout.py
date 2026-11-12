"""데이터 디렉터리 안의 파일 이름 규칙이에요.

스냅샷과 AOF 파일 이름을 한곳에서만 정해 두면, 쓰는 쪽과 복구하는 쪽이 이름을 따로 외우다가
어긋날 일이 없어요.
"""

import os
import pathlib
import re

SNAPSHOT_NAME = "snapshot.jsonl"
SNAPSHOT_TMP_NAME = "snapshot.jsonl.tmp"

# 이름 전체가 정확히 맞아야 해요. appendonly-x.aof나 .aof.bak 같은 건 우리 파일이 아니라서 무시해요.
# [0-9]를 쓰는 이유는 \d가 한글 환경 등의 다른 숫자 문자까지 받아 주기 때문이에요.
_AOF_PATTERN = re.compile(r"appendonly-([0-9]+)\.aof")


def aof_name(number: int) -> str:
    """AOF 번호를 파일 이름으로 바꿔요. 6자리까지 0으로 채우고, 더 크면 그냥 길어져요."""
    return f"appendonly-{number:06d}.aof"


def aof_numbers(data_dir: str | os.PathLike[str]) -> list[int]:
    """데이터 디렉터리의 AOF 파일 번호를 오름차순으로 돌려줘요. 디렉터리가 없으면 빈 리스트예요."""
    directory = pathlib.Path(data_dir)
    if not directory.is_dir():
        return []
    numbers: list[int] = []
    for entry in directory.iterdir():
        match = _AOF_PATTERN.fullmatch(entry.name)
        if match is not None:
            numbers.append(int(match.group(1)))
    return sorted(numbers)
