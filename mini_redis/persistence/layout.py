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
# 숫자만 맞다고 끝이 아니에요. 아래 aof_numbers에서 aof_name(n)과 똑같은 이름인지도 다시 확인해요.
_AOF_PATTERN = re.compile(r"appendonly-([0-9]+)\.aof")


def aof_name(number: int) -> str:
    """AOF 번호를 파일 이름으로 바꿔요. 6자리까지 0으로 채우고, 더 크면 그냥 길어져요."""
    return f"appendonly-{number:06d}.aof"


def aof_numbers(data_dir: str | os.PathLike[str]) -> list[int]:
    """데이터 디렉터리의 AOF 파일 번호를 오름차순으로 돌려줘요. 디렉터리가 없으면 빈 리스트예요.

    우리 쓰기 쪽이 만드는 정식 이름(aof_name)과 똑같은 일반 파일만 세어요.
    appendonly-1.aof 같은 다른 자릿수 표기나 디렉터리를 세면, 읽는 쪽이 aof_name(n)으로
    경로를 다시 만들 때 없는 파일을 찾거나 같은 파일을 두 번 읽게 되기 때문이에요.
    """
    directory = pathlib.Path(data_dir)
    if not directory.is_dir():
        return []
    numbers: list[int] = []
    for entry in directory.iterdir():
        match = _AOF_PATTERN.fullmatch(entry.name)
        if match is None:
            continue
        number = int(match.group(1))
        if entry.name == aof_name(number) and entry.is_file():
            numbers.append(number)
    return sorted(numbers)
