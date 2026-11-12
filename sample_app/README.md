# 샘플 앱

## 가짜 원본 API (200ms 지연)

cache-aside 성능 비교에서 "느린 원본" 역할을 하는 가짜 API예요.
호출 횟수를 세어 두니까, 캐시가 원본 호출을 얼마나 줄였는지 확인할 수 있어요.

실행해요.

    uvicorn sample_app.origin:app --port 8001

| 엔드포인트 | 설명 |
| --- | --- |
| `GET /items/{item_id}` | 200ms 기다린 뒤 `{"id": ..., "value": "item-<id>"}`를 돌려줘요. 호출 수가 1 올라가요. |
| `GET /stats` | 지금까지의 호출 수 `{"calls": n}`를 바로 돌려줘요. 호출로 세지 않아요. |
| `POST /stats/reset` | 호출 수를 0으로 되돌리고 `{"calls": 0}`를 돌려줘요. |

## cache-aside 샘플 앱

Mini Redis를 앞에 두고 가짜 원본을 감싸는 앱이에요. 캐시에 있으면 바로 돌려주고(HIT), 없으면 원본을 불러 60초 동안 캐시에 채워요(MISS).

프로세스 세 개를 띄워요.

    # 1) Mini Redis (:8000) - scripts/run.sh 또는 uvicorn
    # 2) 가짜 원본 (:8001)
    uvicorn sample_app.origin:app --port 8001
    # 3) 샘플 앱 (:8002)
    uvicorn sample_app.app:app --port 8002

환경 변수로 접속 주소를 바꿀 수 있어요.

| 변수 | 기본값 | 설명 |
| --- | --- | --- |
| `MINI_REDIS_URL` | `http://127.0.0.1:8000` | Mini Redis 주소 |
| `ORIGIN_URL` | `http://127.0.0.1:8001` | 가짜 원본 주소 |

| 엔드포인트 | 설명 |
| --- | --- |
| `GET /items/{item_id}` | 응답 헤더 `X-Cache`가 `HIT`(캐시에서)나 `MISS`(원본에서)예요. 캐시가 고장 나면 `502 CACHE_ERROR`, 원본이 고장 나면 `502 ORIGIN_ERROR`예요. 캐시가 죽었다고 원본으로 몰래 우회하지 않아요. 캐시 값이나 원본 응답이 JSON이 아니어도 500 대신 같은 502예요. `item_id`가 `.`나 `..`이면 호출 없이 `400 INVALID_ITEM_ID`예요. |
| `GET /stats` | `{"hits": h, "misses": m}`를 돌려줘요. |
| `POST /stats/reset` | 두 카운터를 0으로 되돌려요. |
