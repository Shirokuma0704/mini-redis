"""cache-aside 샘플 앱(sample_app.app) 테스트. 소켓도 sleep도 쓰지 않고 TestClient/MockTransport로 이어 붙여요."""

import json
from urllib.parse import quote

import httpx
import pytest
from fastapi.testclient import TestClient

from mini_redis.api.app import create_app
from mini_redis.clock import FakeClock
from mini_redis.store import Store
from sample_app.app import create_sample_app
from sample_app.origin import create_origin_app


@pytest.fixture
def clock():
    return FakeClock(1_000_000)


@pytest.fixture
def slept():
    return []


@pytest.fixture
def cache(clock):
    return TestClient(create_app(Store(clock=clock)))


@pytest.fixture
def origin(slept):
    async def recording_fake_sleep(seconds):
        slept.append(seconds)

    return TestClient(create_origin_app(delay_ms=200, sleep=recording_fake_sleep))


@pytest.fixture
def app(cache, origin):
    return TestClient(create_sample_app(cache, origin, ttl_s=60))


def origin_calls(origin):
    return origin.get("/stats").json()["calls"]


def mock_client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler), base_url="http://mock")


def test_sample_app_첫_요청_MISS이고_원본_body_그대로_원본_1번_호출(app, origin):
    response = app.get("/items/1")
    assert response.status_code == 200
    assert response.json() == {"id": "1", "value": "item-1"}
    assert response.headers["X-Cache"] == "MISS"
    assert origin_calls(origin) == 1


def test_sample_app_TTL_안_두번째_요청_HIT이고_원본_호출_그대로_1(app, origin):
    first = app.get("/items/1")
    second = app.get("/items/1")
    assert second.status_code == 200
    assert second.headers["X-Cache"] == "HIT"
    assert second.json() == first.json()
    assert origin_calls(origin) == 1


def test_sample_app_캐시에_저장된_값은_body의_JSON_문자열이고_ttl_60(app, cache):
    body = app.get("/items/1").json()
    stored = cache.get("/keys/item%3A1")
    assert stored.status_code == 200
    assert json.loads(stored.json()["value"]) == body
    assert stored.json()["ttl"] == 60


def test_sample_app_60000ms_지나면_MISS이고_원본_호출_2(app, origin, clock):
    app.get("/items/1")
    clock.advance(60_000)
    response = app.get("/items/1")
    assert response.headers["X-Cache"] == "MISS"
    assert origin_calls(origin) == 2


def test_sample_app_59999ms_지나도_여전히_HIT(app, origin, clock):
    app.get("/items/1")
    clock.advance(59_999)
    response = app.get("/items/1")
    assert response.headers["X-Cache"] == "HIT"
    assert origin_calls(origin) == 1


def test_sample_app_서로_다른_id_둘은_각각_MISS_원본_2번_그다음_각각_HIT(app, origin):
    assert app.get("/items/1").headers["X-Cache"] == "MISS"
    assert app.get("/items/2").headers["X-Cache"] == "MISS"
    assert origin_calls(origin) == 2
    assert app.get("/items/1").headers["X-Cache"] == "HIT"
    assert app.get("/items/2").headers["X-Cache"] == "HIT"
    assert origin_calls(origin) == 2


def test_sample_app_공백과_한글_id도_MISS_다음_HIT_body_id는_원래_문자열(app):
    item_id = "한 글"
    path = "/items/" + quote(item_id, safe="")
    first = app.get(path)
    second = app.get(path)
    assert first.status_code == 200
    assert first.headers["X-Cache"] == "MISS"
    assert first.json()["id"] == item_id
    assert second.headers["X-Cache"] == "HIT"
    assert second.json()["id"] == item_id


def test_sample_app_stats는_hits_1_misses_1_reset하면_0(app):
    app.get("/items/1")
    app.get("/items/1")
    assert app.get("/stats").json() == {"hits": 1, "misses": 1}
    reset = app.post("/stats/reset")
    assert reset.json() == {"hits": 0, "misses": 0}
    assert app.get("/stats").json() == {"hits": 0, "misses": 0}


def test_sample_app_캐시가_500이면_502_CACHE_ERROR이고_원본은_안_부름(origin):
    cache = mock_client(lambda request: httpx.Response(500, json={"error": "INTERNAL"}))
    app = TestClient(create_sample_app(cache, origin, ttl_s=60))
    response = app.get("/items/1")
    assert response.status_code == 502
    assert response.json()["error"] == "CACHE_ERROR"
    assert origin_calls(origin) == 0


def test_sample_app_원본이_500이면_502_ORIGIN_ERROR_캐시에_안_쓰고_카운터_그대로(cache):
    origin = mock_client(lambda request: httpx.Response(500, json={"error": "BOOM"}))
    app = TestClient(create_sample_app(cache, origin, ttl_s=60))
    response = app.get("/items/1")
    assert response.status_code == 502
    assert response.json()["error"] == "ORIGIN_ERROR"
    assert cache.get("/keys/item%3A1").status_code == 404
    assert app.get("/stats").json() == {"hits": 0, "misses": 0}


def test_sample_app_캐시_PUT이_실패해도_200_MISS로_원본_body를_돌려줘요(origin):
    def handler(request):
        if request.method == "GET":
            return httpx.Response(404, json={"error": "KEY_NOT_FOUND"})
        return httpx.Response(500, json={"error": "INTERNAL"})

    cache = mock_client(handler)
    app = TestClient(create_sample_app(cache, origin, ttl_s=60))
    response = app.get("/items/1")
    assert response.status_code == 200
    assert response.headers["X-Cache"] == "MISS"
    assert response.json() == {"id": "1", "value": "item-1"}


def test_sample_app_원본_호출마다_가짜_sleep이_0점2초로_기록돼요(app, slept):
    app.get("/items/1")
    app.get("/items/2")
    app.get("/items/1")  # HIT이라 원본을 안 불러요
    assert slept == [pytest.approx(0.2), pytest.approx(0.2)]
