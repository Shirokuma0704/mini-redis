"""가짜 원본 API(sample_app.origin) 테스트. 실제로 sleep 하지 않고 가짜 sleep을 주입해요."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

import sample_app.origin as origin
from sample_app.origin import create_origin_app


def make_client(delay_ms=None):
    """가짜 sleep(호출된 초를 기록만 함)을 꽂은 앱과 클라이언트를 만들어요."""
    waited = []

    async def fake_sleep(seconds):
        waited.append(seconds)

    if delay_ms is None:
        app = create_origin_app(sleep=fake_sleep)
    else:
        app = create_origin_app(delay_ms=delay_ms, sleep=fake_sleep)
    return TestClient(app), waited


# 1
def test_stats_item_3번_호출_후_calls_3():
    client, _ = make_client()
    for item_id in ("a", "b", "c"):
        client.get(f"/items/{item_id}")
    response = client.get("/stats")
    assert response.status_code == 200
    assert response.json() == {"calls": 3}


# 2
def test_stats_연속_조회해도_카운트_안_변함():
    client, _ = make_client()
    assert client.get("/stats").json() == {"calls": 0}
    assert client.get("/stats").json() == {"calls": 0}


# 3
def test_items_같은_id_두번_같은_본문_카운트_2():
    client, _ = make_client()
    first = client.get("/items/7")
    second = client.get("/items/7")
    assert first.status_code == 200
    assert first.json() == second.json()
    assert client.get("/stats").json() == {"calls": 2}


# 4
def test_items_42_본문_모양_맞음():
    client, _ = make_client()
    response = client.get("/items/42")
    assert response.status_code == 200
    assert response.json() == {"id": "42", "value": "item-42"}


# 5
def test_items_기본_지연_200ms는_0점2초로_sleep_1번_호출():
    client, waited = make_client()
    client.get("/items/1")
    assert waited == [0.2]
    client.get("/items/2")
    assert waited == [0.2, 0.2]


# 6
def test_items_delay_50ms_주면_0점05초로_sleep():
    client, waited = make_client(delay_ms=50)
    client.get("/items/1")
    assert waited == [0.05]


# 7
def test_stats_와_reset은_sleep_호출_안_함():
    client, waited = make_client()
    client.get("/stats")
    client.post("/stats/reset")
    client.get("/stats")
    assert waited == []


# 8
def test_reset_2번_호출_후_0으로_돌아가고_다시_센다():
    client, _ = make_client()
    client.get("/items/1")
    client.get("/items/2")
    reset = client.post("/stats/reset")
    assert reset.status_code == 200
    assert reset.json() == {"calls": 0}
    assert client.get("/stats").json() == {"calls": 0}
    client.get("/items/3")
    assert client.get("/stats").json() == {"calls": 1}


# 9
def test_앱_두개_카운터_서로_분리():
    client_a, _ = make_client()
    client_b, _ = make_client()
    client_a.get("/items/1")
    client_a.get("/items/2")
    assert client_a.get("/stats").json() == {"calls": 2}
    assert client_b.get("/stats").json() == {"calls": 0}


# 10
def test_모듈_기본값_app_존재하고_지연_200ms():
    assert origin.DEFAULT_DELAY_MS == 200
    assert hasattr(origin, "app")
    assert isinstance(origin.app, FastAPI)
