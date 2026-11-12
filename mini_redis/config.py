"""설정 상수. 값은 docs/spec.md 기준."""

MAX_KEY_BYTES = 256
MAX_VALUE_BYTES = 1024 * 1024
MAX_KEYS = 1_000_000

INITIAL_CAPACITY = 16
LOAD_FACTOR = 0.75
# 점진적 리해싱(#24): get/put/delete 한 번마다 옛 배열에서 새 배열로 옮기는 버킷 수(빈 버킷 포함)
REHASH_BUCKETS_PER_STEP = 4

EXPIRE_TICK_MS = 100
EXPIRE_MAX_PER_TICK = 1000
WHEEL_L1_SLOTS, WHEEL_L1_MS = 64, 100
WHEEL_L2_SLOTS, WHEEL_L2_MS = 64, 6400
# overflow 힙에서 휠로 tick당 옮기는 최대 개수. 삭제 상한과 같은 크기로 락 점유를 묶는다.
# 1,000 x 4,096 tick(2단 한 바퀴) = 409만 개 > MAX_KEYS라 키 100만 개가 한 구간에 몰려도 100초면 다 옮긴다.
WHEEL_OVERFLOW_MOVE_PER_TICK = 1000

LEASE_TTL_MS = 10_000
LEASE_RETRY_MS = 100  # lease 대기자에게 주는 retry_after_ms. 보유자가 원본(200ms)을 부르는 동안 두세 번 다시 묻는 간격

XFETCH_BETA = 1.0  # 클수록 만료 전에 더 일찍 갱신을 시작한다 (논문 기본값 1)

AOF_FSYNC_INTERVAL_S = 1
AOF_REJECT_AFTER_S = 2  # flush가 이만큼 계속 실패하면 쓰기를 거절한다
SNAPSHOT_INTERVAL_S = 300
SNAPSHOT_AOF_MAX_BYTES = 10 * 1024 * 1024
DATA_DIR = "data"

# 가짜 원본 API의 고정 지연(ms). 캐시 성능 비교에서 "느린 원본"을 흉내 내요.
ORIGIN_DELAY_MS = 200

# 샘플 앱이 Mini Redis에 넣는 항목의 TTL(초).
SAMPLE_CACHE_TTL_S = 60
