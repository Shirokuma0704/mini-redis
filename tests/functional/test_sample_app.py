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


def test_sample_app_캐시에_JSON이_아닌_값이_있으면_502_CACHE_ERROR이고_원본은_안_부름(app, cache, origin):
    cache.put("/keys/item%3A1", json={"value": "not-json", "ttl": 60})
    response = app.get("/items/1")
    assert response.status_code == 502
    assert response.json()["error"] == "CACHE_ERROR"
    assert origin_calls(origin) == 0


def test_sample_app_원본이_JSON_아닌_200이면_502_ORIGIN_ERROR_캐시에_안_씀(cache):
    origin = mock_client(lambda request: httpx.Response(200, text="oops", headers={"content-type": "text/plain"}))
    app = TestClient(create_sample_app(cache, origin, ttl_s=60))
    response = app.get("/items/1")
    assert response.status_code == 502
    assert response.json()["error"] == "ORIGIN_ERROR"
    assert cache.get("/keys/item%3A1").status_code == 404


@pytest.mark.parametrize("item_id", ["%2E", "%2E%2E"])
def test_sample_app_점_id는_400_INVALID_ITEM_ID이고_원본_호출도_통계도_0(app, origin, item_id):
    response = app.get("/items/" + item_id)
    assert response.status_code == 400
    assert response.json()["error"] == "INVALID_ITEM_ID"
    assert origin_calls(origin) == 0
    assert app.get("/stats").json() == {"hits": 0, "misses": 0}


@pytest.mark.parametrize("stored_value", ["NaN", "1e999"])
def test_sample_app_캐시_값이_JSON이지만_렌더할_수_없으면_502_CACHE_ERROR이고_hits는_0(app, cache, stored_value):
    # NaN/Infinity는 파싱은 되지만 응답으로 내보낼 수 없어서, 500 대신 502로 알려요.
    cache.put("/keys/item%3A1", json={"value": stored_value, "ttl": 60})
    response = app.get("/items/1")
    assert response.status_code == 502
    assert response.json()["error"] == "CACHE_ERROR"
    assert app.get("/stats").json() == {"hits": 0, "misses": 0}


def test_sample_app_원본이_NaN_본문을_주면_502_ORIGIN_ERROR_캐시에_안_쓰고_misses는_0(cache):
    origin = mock_client(
        lambda request: httpx.Response(200, content=b"NaN", headers={"content-type": "application/json"})
    )
    app = TestClient(create_sample_app(cache, origin, ttl_s=60))
    response = app.get("/items/1")
    assert response.status_code == 502
    assert response.json()["error"] == "ORIGIN_ERROR"
    assert cache.get("/keys/item%3A1").status_code == 404
    assert app.get("/stats").json() == {"hits": 0, "misses": 0}


# --- 캐시 끄기 모드 (use_cache=False, #18) ---


def failing_cache():
    """호출되면 바로 실패하는 캐시 클라이언트. 끄기 모드가 캐시를 건드리지 않는지 확인하는 데 써요."""

    def fail(request):
        raise AssertionError(f"캐시를 건드렸어요: {request.method} {request.url}")

    return mock_client(fail)


def test_sample_app_캐시_끄기_같은_id_두번_모두_200_BYPASS_원본_2번_stats는_misses_2(origin):
    app = TestClient(create_sample_app(failing_cache(), origin, ttl_s=60, use_cache=False))
    first = app.get("/items/1")
    second = app.get("/items/1")
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.headers["X-Cache"] == "BYPASS"
    assert second.headers["X-Cache"] == "BYPASS"
    assert first.json() == {"id": "1", "value": "item-1"}
    assert origin_calls(origin) == 2
    assert app.get("/stats").json() == {"hits": 0, "misses": 2}


def test_sample_app_캐시_끄기_cache가_None이어도_똑같이_동작해요(origin):
    app = TestClient(create_sample_app(None, origin, ttl_s=60, use_cache=False))
    first = app.get("/items/1")
    second = app.get("/items/1")
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.headers["X-Cache"] == "BYPASS"
    assert second.headers["X-Cache"] == "BYPASS"
    assert origin_calls(origin) == 2
    assert app.get("/stats").json() == {"hits": 0, "misses": 2}


def test_sample_app_캐시_끄기_원본이_500이면_502_ORIGIN_ERROR이고_misses는_0():
    origin = mock_client(lambda request: httpx.Response(500, json={"error": "BOOM"}))
    app = TestClient(create_sample_app(failing_cache(), origin, ttl_s=60, use_cache=False))
    response = app.get("/items/1")
    assert response.status_code == 502
    assert response.json()["error"] == "ORIGIN_ERROR"
    assert app.get("/stats").json() == {"hits": 0, "misses": 0}


def test_sample_app_캐시_끄기_원본_전송_오류도_502_ORIGIN_ERROR이고_misses는_0():
    def broken(request):
        raise httpx.ConnectError("origin down", request=request)

    app = TestClient(create_sample_app(failing_cache(), mock_client(broken), ttl_s=60, use_cache=False))
    response = app.get("/items/1")
    assert response.status_code == 502
    assert response.json()["error"] == "ORIGIN_ERROR"
    assert app.get("/stats").json() == {"hits": 0, "misses": 0}


def test_sample_app_캐시_끄기_원본이_NaN_본문을_주면_502_ORIGIN_ERROR이고_misses는_0():
    origin = mock_client(
        lambda request: httpx.Response(200, content=b"NaN", headers={"content-type": "application/json"})
    )
    app = TestClient(create_sample_app(failing_cache(), origin, ttl_s=60, use_cache=False))
    response = app.get("/items/1")
    assert response.status_code == 502
    assert response.json()["error"] == "ORIGIN_ERROR"
    assert app.get("/stats").json() == {"hits": 0, "misses": 0}


def test_sample_app_캐시_끄기_원본이_JSON_아닌_200이면_502_ORIGIN_ERROR():
    origin = mock_client(lambda request: httpx.Response(200, text="oops", headers={"content-type": "text/plain"}))
    app = TestClient(create_sample_app(failing_cache(), origin, ttl_s=60, use_cache=False))
    response = app.get("/items/1")
    assert response.status_code == 502
    assert response.json()["error"] == "ORIGIN_ERROR"


@pytest.mark.parametrize("item_id", ["%2E", "%2E%2E"])
def test_sample_app_캐시_끄기_점_id는_400_INVALID_ITEM_ID이고_원본_호출도_통계도_0(origin, item_id):
    app = TestClient(create_sample_app(failing_cache(), origin, ttl_s=60, use_cache=False))
    response = app.get("/items/" + item_id)
    assert response.status_code == 400
    assert response.json()["error"] == "INVALID_ITEM_ID"
    assert origin_calls(origin) == 0
    assert app.get("/stats").json() == {"hits": 0, "misses": 0}


def test_sample_app_캐시_끄기_stats_reset하면_misses가_0으로_돌아와요(origin):
    app = TestClient(create_sample_app(None, origin, ttl_s=60, use_cache=False))
    app.get("/items/1")
    assert app.post("/stats/reset").json() == {"hits": 0, "misses": 0}
    assert app.get("/stats").json() == {"hits": 0, "misses": 0}


def test_sample_app_use_cache를_안_주면_기본은_켜짐이라_두번째가_HIT(cache, origin):
    app = TestClient(create_sample_app(cache, origin, ttl_s=60))
    app.get("/items/1")
    assert app.get("/items/1").headers["X-Cache"] == "HIT"



# --- 모듈 수준 app의 환경변수 SAMPLE_USE_CACHE ---


@pytest.mark.parametrize("value, expected", [("0", False), ("1", True), ("", True), ("false", True), ("00", True)])
def test_use_cache_from_env_0만_False이고_나머지_값은_True(value, expected):
    from sample_app.app import _use_cache_from_env

    assert _use_cache_from_env({"SAMPLE_USE_CACHE": value}) is expected


def test_use_cache_from_env_환경변수가_없으면_True():
    from sample_app.app import _use_cache_from_env

    assert _use_cache_from_env({}) is True


@pytest.fixture
def reload_sample_module(monkeypatch):
    """httpx.Client를 가짜 전송으로 바꾼 채 sample_app.app을 다시 불러와요. 끝나면 원래 환경으로 되돌려 한 번 더 불러와요.

    cache.test로 가는 요청은 기록하고 404를 주고, origin.test로 가는 요청은 200 JSON을 줘요.
    """
    import importlib

    import sample_app.app as module

    real_client = httpx.Client
    cache_requests = []

    def handler(request):
        if request.url.host == "cache.test":
            cache_requests.append(str(request.url))
            return httpx.Response(404, json={"error": "KEY_NOT_FOUND"})
        return httpx.Response(200, json={"id": "1", "value": "item-1"})

    def fake_client(*args, **kwargs):
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    def reload_with(value):
        monkeypatch.setenv("MINI_REDIS_URL", "http://cache.test")
        monkeypatch.setenv("ORIGIN_URL", "http://origin.test")
        if value is None:
            monkeypatch.delenv("SAMPLE_USE_CACHE", raising=False)
        else:
            monkeypatch.setenv("SAMPLE_USE_CACHE", value)
        monkeypatch.setattr(httpx, "Client", fake_client)
        return importlib.reload(module), cache_requests

    yield reload_with
    monkeypatch.undo()
    importlib.reload(module)


def test_sample_app_환경변수_SAMPLE_USE_CACHE_0이면_모듈_app이_BYPASS이고_캐시는_안_건드려요(reload_sample_module):
    module, cache_requests = reload_sample_module("0")
    response = TestClient(module.app).get("/items/1")
    assert response.status_code == 200
    assert response.headers["X-Cache"] == "BYPASS"
    assert cache_requests == []


@pytest.mark.parametrize("value", ["1", None])
def test_sample_app_환경변수가_1이거나_없으면_모듈_app은_캐시를_먼저_물어봐요(reload_sample_module, value):
    module, cache_requests = reload_sample_module(value)
    response = TestClient(module.app).get("/items/1")
    assert response.headers["X-Cache"] == "MISS"
    assert len(cache_requests) >= 1
