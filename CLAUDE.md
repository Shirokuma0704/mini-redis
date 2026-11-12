# Mini Redis 작업 규칙

동작 명세는 `docs/spec.md`가 우선한다.

## 구조
- `mini_redis/hashtable.py` 직접 구현 해시테이블 (dict/set/collections 금지)
- `mini_redis/store.py` 전역 RLock, 모든 명령
- `mini_redis/api/` FastAPI 핸들러 (검증만)
- `mini_redis/persistence/` AOF, 스냅샷, 복구
- `tests/unit`, `tests/functional`, `bench/`, `sample_app/`

## 규칙
- 호출 방향: API -> Store -> HashTable
- 디스크 I/O는 락 밖에서
- 시간은 주입한 시계(epoch ms) 사용, 테스트는 FakeClock
- Uvicorn 워커 1개
- 테스트 이름: `test_<대상>_<상황>_<기대결과>`
- 에러 형식: `{"error":"CODE","message":"..."}`
- 브랜치: 이슈 단위 `feature/#번호-설명`, PR로 머지
