# 이슈별 담당자

레포: https://github.com/rlatmdgus388/mini-redis/issues

## rlatmdgus388 — 코어, API, 문서

| 이슈 | 제목 |
| --- | --- |
| #1 | [Setup] 프로젝트 골격과 개발 환경 구성 |
| #2 | [Core] 해시테이블 직접 구현 (체이닝, FNV-1a, 리사이즈) |
| #3 | [Core] Store 계층과 전역 RLock, 시계 주입 |
| #4 | [Core] 키 명령: SET / GET / DEL / EXISTS |
| #5 | [Core] INCR 구현 |
| #6 | [Core] 일괄 명령: KEYS(prefix), prefix 삭제, FLUSH |
| #9 | [API] FastAPI 키 엔드포인트와 에러 형식 |
| #22 | [Docs] README와 발표 자료 (알려진 한계 포함) |
| #23 | [Stretch] XFetch 확률적 조기 갱신 |

## Pancheble — 만료, Lease, 무효화

| 이슈 | 제목 |
| --- | --- |
| #7 | [TTL] Lazy 만료와 TTL/EXPIRE 규칙 |
| #8 | [TTL] 계층형 타이밍 휠과 백그라운드 만료 스레드 |
| #10 | [Lease] Lease 토큰 발급과 PUT 검증 |
| #11 | [Lease] 네임스페이스 버전 키와 invalidate |
| #16 | [Test] Lease·네임스페이스·영속성 단위 테스트 |
| #19 | [Perf] 스탬피드와 Stale set 시나리오 (lease 유무 비교) |
| #21 | [Perf] 만료 회수 측정 (타이밍 휠 vs lazy만) |
| #24 | [Stretch] 점진적 리해싱 |
| #25 | [Stretch] SIEVE 축출 |

## Shirokuma0704 — 영속성, 테스트, 성능

| 이슈 | 제목 |
| --- | --- |
| #12 | [Persistence] AOF 기록과 1초 fsync 스레드 |
| #13 | [Persistence] 스냅샷과 AOF 전환/정리 |
| #14 | [Persistence] 기동 시 복구 |
| #15 | [Test] 해시테이블·Store 단위 테스트 모음 |
| #17 | [Test] 200ms 지연 가짜 원본 API와 cache-aside 샘플 앱 |
| #18 | [Perf] 캐시 효과 측정 (캐시 없음 vs 있음) |
| #20 | [Perf] 락 멈춤 측정 (리사이즈, prefix 삭제, 스냅샷 복사) |
| #26 | [Stretch] prefix 삭제 배치화 |
| #27 | [Stretch] 전역 락 vs 버킷 락 처리량 비교 |
