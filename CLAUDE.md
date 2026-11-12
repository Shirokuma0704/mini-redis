# Mini Redis 작업 규칙

동작 명세는 `docs/spec.md`가 우선한다. 이 문서와 명세가 다르면 명세를 따른다.
이슈별 담당자는 `docs/assignees.md` 참고.

## 아키텍처

### 기술 스택
| 영역 | 선택 |
| --- | --- |
| 언어 | Python 3.11+ (개발 환경은 3.13도 가능) |
| 웹 | FastAPI + Uvicorn (워커 **1개** 고정) |
| 통신 | HTTP + JSON, 경로 `/keys/...` |
| 저장소 | 직접 구현한 해시테이블 (체이닝, FNV-1a 64, load factor 0.75) |
| 동시성 | `threading.RLock` 전역 락 1개 |
| 만료 | lazy + 계층형 타이밍 휠, 백그라운드 스레드 |
| 영속성 | 스냅샷 + AOF (JSON Lines, 1초 fsync) |
| 테스트 | pytest, httpx(TestClient), 가짜 시계 |

### 폴더 구조
```
mini_redis/
├── mini_redis/
│   ├── hashtable.py       # 직접 구현 해시테이블 (dict/set/collections 금지)
│   ├── store.py           # 전역 RLock, 모든 명령의 진입점
│   ├── timing_wheel.py    # 계층형 타이밍 휠
│   ├── lease.py           # Lease 토큰 테이블
│   ├── namespace.py       # 네임스페이스 버전 테이블
│   ├── clock.py           # 시계 주입, FakeClock
│   ├── config.py          # 한도·주기 상수
│   ├── errors.py          # 에러 코드
│   ├── api/               # FastAPI 핸들러 (검증만)
│   └── persistence/       # aof.py, snapshot.py, recovery.py
├── tests/unit/            # 단위 테스트
├── tests/functional/      # 실제 HTTP 기능 테스트
├── bench/                 # 성능 측정 스크립트
├── sample_app/            # 200ms 지연 가짜 API + cache-aside 샘플
├── scripts/               # 실행 스크립트
└── docs/
    ├── spec.md            # 기초 명세서
    ├── assignees.md       # 이슈별 담당자
    └── features/          # 기능별 기술·흐름 설명 (아래 참고)
```

### 계층 규칙
- 호출 방향은 **API → Store → HashTable** 한 방향뿐이다.
- 락을 잡는 곳은 `Store`뿐이다. API 핸들러는 입력 검증만 하고 상태를 직접 만지지 않는다.
- 디스크 I/O(AOF fsync, 스냅샷 쓰기)는 **락 밖**에서 한다.
- 시간은 주입된 시계(벽시계 epoch ms)만 쓴다. `time.time()`을 로직에서 직접 부르지 않는다.
- Uvicorn 워커는 1개로 고정한다.

## 컨벤션

### 코드
- PEP 8, 들여쓰기 4칸, 함수·변수 `snake_case`, 클래스 `PascalCase`, 상수 `UPPER_SNAKE`.
- 공개 함수에는 타입 힌트를 붙인다.
- 저장소 본체(`hashtable.py`, `store.py` 등)에서 `dict`, `set`, `collections.*`를 쓰지 않는다. 테스트 코드는 예외.
- 상수·한도 값은 `config.py`에 모으고 코드에 숫자를 직접 쓰지 않는다.
- 주석과 docstring은 한국어로, "무엇"보다 "왜"를 적는다.
- 에러는 `errors.py`의 예외로 던지고, 응답은 `{"error":"CODE","message":"..."}` 형식이다.
- 상태코드: 400 잘못된 입력, 404 없음·만료, 409 상태 충돌, 413 값 초과, 507 키 상한, 500 내부 오류.

### 테스트
- 테스트 이름은 `test_<대상>_<상황>_<기대결과>`.
- 시간이 걸린 테스트는 `sleep` 대신 `FakeClock`을 쓴다.
- 새 기능에는 단위 테스트를 같이 넣는다. 엣지 케이스 표(`docs/spec.md`)의 해당 행은 모두 테스트가 되어야 한다.
- PR 전에 `pytest`가 전부 통과해야 한다.

### Git
- 브랜치: 이슈 단위로 `feature/#번호-설명` (예: `feature/#2-hashtable`). `main`에 직접 커밋하지 않고 PR로 머지한다. (문서 전용 변경은 예외 허용)
- 커밋 메시지: `type: 한글 요약 #이슈번호`
  - type: `feat`(기능), `fix`(버그), `test`(테스트), `docs`(문서), `refactor`, `chore`(설정)
- PR: `.github/pull_request_template.md`를 따르고 본문에 `Closes #번호`를 넣는다. 다른 팀원 1명 이상 리뷰 후 머지.
- 이슈 제목은 `[영역] 요약` 형식, 라벨은 영역별(`core`, `ttl`, `api`, `lease`, `persistence`, `test`, `stretch`).

## 기능 설명 문서 (필수)

메인 기능을 구현할 때마다 **같은 PR에** `docs/features/<기능명>.md`를 추가한다.
기능 PR에 이 문서가 없으면 머지하지 않는다. 대상은 이슈 단위의 메인 기능이다
(해시테이블, Store와 락, 키 명령, INCR, 일괄 명령, TTL 만료, 타이밍 휠, API, Lease, 네임스페이스, AOF, 스냅샷, 복구 등).

문서 구성:
1. **개요**: 이 기능이 무엇이고 왜 필요한가 (1~3문장)
2. **사용 기술**: 쓰인 자료구조·알고리즘·라이브러리와 선택 이유 (대안과의 비교 포함)
3. **동작 흐름**: 요청부터 응답까지(또는 데이터가 흐르는 순서)를 단계별로 설명. 가능하면 텍스트 다이어그램이나 mermaid를 쓴다.
4. **관련 파일**: 구현·테스트 파일 경로
5. **엣지 케이스와 한계**: 처리 방식과 알려진 제약
6. **관련 이슈·명세**: 이슈 번호, `docs/spec.md`의 해당 절

파일명은 소문자 `kebab-case` (예: `hashtable.md`, `timing-wheel.md`). 템플릿은 `docs/features/_template.md`.
