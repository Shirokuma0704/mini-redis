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
