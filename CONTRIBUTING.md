# 기여 가이드

Mini Redis 팀이 일하는 방법이다. 동작은 [docs/spec.md](docs/spec.md), 코드 규칙은 [CLAUDE.md](CLAUDE.md), 담당자는 [docs/assignees.md](docs/assignees.md)를 따른다.

## TL;DR

```
이슈 고르기 → main에서 feature/#번호-설명 브랜치 → 작게 커밋 → pytest 통과 → PR(Closes #번호) → 리뷰 → 머지 → 이슈에 "한 일 / 배운 것" 채우기
```

공부하려고 하는 프로젝트라서, 이슈와 PR은 **나중에 읽는 사람이 흐름을 이해할 수 있게** 쓴다. 무엇을 했는지보다 TL;DR과 흐름이 먼저다.

## 개발 환경

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'   # 앱 + pytest, httpx
pytest
./scripts/run.sh          # http://localhost:8000/health
```

## 1. 이슈

- 새 작업은 **작업** 템플릿으로 연다. TL;DR과 흐름을 먼저 쓰고, 할 일과 완료 조건을 적는다.
- 제목 앞에 영역 머리말을 붙인다: `[Core]`, `[TTL]`, `[API]`, `[Lease]`, `[Persistence]`, `[Test]`, `[Perf]`, `[Docs]`, `[Stretch]`
- 라벨은 기존 라벨에서 하나 고른다: `core`, `ttl`, `api`, `lease`, `persistence`, `test`, `stretch`, `setup`, `documentation`
- 담당자는 [docs/assignees.md](docs/assignees.md) 기준으로 지정한다.
- 작업이 끝나면 이슈의 **한 일 / 배운 것**을 채운다. 계획과 달라진 점이 있으면 그것도 적는다.

## 2. 브랜치

main에서 따고, 이슈 하나에 브랜치 하나다.

```
feature/#번호-짧은-영문-설명      예) feature/#8-timing-wheel
```

## 3. 커밋

```
접두사(범위): 요약
```

| 접두사 | 쓸 때 |
|---|---|
| `feat` | 기능 추가 |
| `fix` | 버그 수정 |
| `docs` | 문서만 바꿈 |
| `test` | 테스트만 바꿈 |
| `refactor` | 동작은 그대로, 구조만 바꿈 |
| `perf` | 성능 개선 |
| `chore` | 설정, 의존성 같은 잡일 |
| `ci` | CI 설정 |

- 범위는 라벨 이름을 쓴다(`core`, `ttl`, `api`, `lease`, `persistence`). 여러 곳에 걸치면 생략한다.
- 요약은 한국어, 50자 안쪽, 마침표 없이 쓴다.
- 본문에는 무엇보다 **왜** 바꿨는지 쓰고, 마지막 줄에 `Refs #번호`를 붙인다.

```
feat(ttl): 계층형 타이밍 휠 추가

접근하지 않는 만료 키도 제때 지우려고 100ms마다 도는 휠을 둔다.

Refs #8
```

## 4. PR

- main으로 연다. 제목은 커밋 형식과 같게 쓴다.
- [PR 템플릿](.github/pull_request_template.md)을 채우고, 본문에 `Closes #번호`를 넣는다.
- 올리기 전에 `pytest`를 통과시킨다. 시간이 들어가는 테스트는 `FakeClock`을 쓴다(sleep 금지).
- 아직 작업 중이면 초안(Draft) PR로 연다.

## 5. 리뷰

- 다른 팀원이 확인한 뒤 머지한다.
- 다른 사람 담당 파일은 그 PR이 머지되기 전에는 직접 고치지 않고, 리뷰 코멘트로 남긴다.
- 코멘트 앞에 종류를 붙이면 무엇부터 볼지 알기 쉽다: `[버그]`, `[테스트]`, `[성능]`, `[정리]`, `[질문]`
- 지적에는 **어떤 입력이나 상황에서 무엇이 틀리는지**를 함께 적는다.
