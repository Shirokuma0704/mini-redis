"""Lease 흐름을 HTTP로: 발급 -> 대기 -> PUT, 삭제 뒤 늦은 PUT 409, 10초 뒤 재발급."""
import re

import pytest

from mini_redis.config import LEASE_RETRY_MS, LEASE_TTL_MS


def lease_get(client, key="k"):
    return client.get(f"/keys/{key}?lease=1")


def test_lease_flow_issue_wait_put_then_hit(client):
    r = lease_get(client)
    assert r.status_code == 404
    body = r.json()
    assert body["error"] == "KEY_NOT_FOUND" and "message" in body
    token = body["lease"]
    assert re.fullmatch(r"[0-9a-f]{16}", token) and "retry_after_ms" not in body

    r = lease_get(client)  # 다른 클라이언트: 원본을 부르지 말고 기다린다
    assert r.status_code == 404
    assert r.json()["retry_after_ms"] == LEASE_RETRY_MS and "lease" not in r.json()

    r = client.put("/keys/k", json={"value": "v", "ttl": 60, "lease": token})
    assert (r.status_code, r.json()) == (200, {"ok": True})
    r = lease_get(client)
    assert (r.status_code, r.json()) == (200, {"value": "v", "ttl": 60})


@pytest.mark.parametrize(
    "invalidate",
    [
        lambda c: c.delete("/keys/k"),
        lambda c: c.delete("/keys?prefix=k"),
        lambda c: c.post("/admin/flush", json={"confirm": True}),
    ],
    ids=["del", "prefix", "flush"],
)
def test_lease_put_after_delete_returns_409_and_stores_nothing(client, invalidate):
    token = lease_get(client).json()["lease"]
    assert invalidate(client).status_code == 200
    r = client.put("/keys/k", json={"value": "stale", "lease": token})
    assert r.status_code == 409
    assert r.json()["error"] == "LEASE_INVALID"
    assert client.get("/keys/k").status_code == 404


def test_lease_after_10s_reissued_and_old_token_rejected(client, clock):
    old = lease_get(client).json()["lease"]
    clock.advance(LEASE_TTL_MS)
    new = lease_get(client).json()["lease"]
    assert new != old
    assert client.put("/keys/k", json={"value": "v", "lease": old}).status_code == 409
    assert client.put("/keys/k", json={"value": "v", "lease": new}).status_code == 200


def test_put_with_wrong_token_returns_409_lease_invalid(client):
    lease_get(client)
    r = client.put("/keys/k", json={"value": "v", "lease": "0" * 16})
    assert (r.status_code, r.json()["error"]) == (409, "LEASE_INVALID")


def test_put_with_non_string_token_returns_400(client):
    lease_get(client)
    r = client.put("/keys/k", json={"value": "v", "lease": 123})
    assert (r.status_code, r.json()["error"]) == (400, "INVALID_VALUE")


def test_put_with_null_token_is_plain_write(client):
    r = client.put("/keys/k", json={"value": "v", "lease": None})
    assert r.status_code == 200
    assert client.get("/keys/k").json()["value"] == "v"


def test_get_without_lease_param_miss_has_no_lease_fields(client):
    body = client.get("/keys/k").json()
    assert set(body) == {"error", "message"}


@pytest.mark.parametrize("q", ["lease=0", "lease=false"])
def test_get_lease_param_false_miss_issues_nothing(client, q):
    assert set(client.get(f"/keys/k?{q}").json()) == {"error", "message"}
    assert "lease" in lease_get(client).json()


def test_get_lease_param_not_boolean_returns_400(client):
    r = client.get("/keys/k?lease=abc")
    assert r.status_code == 400


# --- XFetch: 만료 직전 히트 하나에 조기 갱신용 lease ---

def _api_with_forced_draw(clock, value):
    from fastapi.testclient import TestClient

    from mini_redis.api.app import create_app
    from mini_redis.store import Store

    rng = type("Rng", (), {"value": value, "__call__": lambda self: self.value})()
    store = Store(clock=clock, random_fn=rng)
    return TestClient(create_app(store), raise_server_exceptions=False), rng


def _fill(client, clock, key="hot", ttl=1, work_ms=200):
    miss = client.get(f"/keys/{key}", params={"lease": 1})
    assert miss.status_code == 404
    clock.advance(work_ms)
    r = client.put(f"/keys/{key}", json={"value": "v1", "ttl": ttl, "lease": miss.json()["lease"]})
    assert r.status_code == 200


def test_get_hit_lease_near_expiry_returns_200_with_value_and_lease(clock):
    client, rng = _api_with_forced_draw(clock, 0.999999)
    _fill(client, clock)
    clock.advance(500)
    r = client.get("/keys/hot", params={"lease": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["value"] == "v1"
    assert isinstance(body["lease"], str) and len(body["lease"]) == 16
    assert client.get("/keys/hot", params={"lease": 1}).json().get("lease") is None  # 한 명만


def test_get_hit_lease_with_losing_draw_has_no_lease_field(clock):
    client, rng = _api_with_forced_draw(clock, 0.0)
    _fill(client, clock)
    clock.advance(500)
    assert "lease" not in client.get("/keys/hot", params={"lease": 1}).json()


def test_get_hit_without_lease_param_has_no_lease_field_even_if_forced(clock):
    client, rng = _api_with_forced_draw(clock, 0.999999)
    _fill(client, clock)
    clock.advance(500)
    assert "lease" not in client.get("/keys/hot").json()


def test_early_refresh_put_over_http_extends_ttl_and_old_token_is_spent(clock):
    client, rng = _api_with_forced_draw(clock, 0.999999)
    _fill(client, clock)
    clock.advance(500)
    token = client.get("/keys/hot", params={"lease": 1}).json()["lease"]
    clock.advance(150)
    assert client.put("/keys/hot", json={"value": "v2", "ttl": 60, "lease": token}).status_code == 200
    assert client.get("/keys/hot").json() == {"value": "v2", "ttl": 60}
    r = client.put("/keys/hot", json={"value": "v3", "ttl": 60, "lease": token})
    assert (r.status_code, r.json()["error"]) == (409, "LEASE_INVALID")
