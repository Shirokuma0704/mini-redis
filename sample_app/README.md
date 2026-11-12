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
