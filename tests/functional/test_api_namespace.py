import pytest

T0 = 1_000_000  # conftest의 FakeClock 시작 시각


def err(resp):
    body = resp.json()
    assert set(body) >= {"error", "message"}
    return body["error"]


def read_version(client, key="k", ns="user"):
    r = client.get(f"/keys/{key}?ns={ns}")
    assert r.status_code == 404
    return r.json()["version"]


def test_namespace_cache_aside_miss_put_with_version_then_hit(client):
    r = client.get("/keys/42?ns=user")
    assert (r.status_code, err(r), r.json()["version"]) == (404, "KEY_NOT_FOUND", T0)
    r = client.put(f"/keys/42?ns=user&v={T0}", json={"value": "kim", "ttl": 60})
    assert (r.status_code, r.json()) == (200, {"ok": True})
    r = client.get("/keys/42?ns=user")
    assert (r.status_code, r.json()) == (200, {"value": "kim", "ttl": 60, "version": T0})
    assert client.get("/keys?prefix=user:").json() == {"keys": [f"user:v{T0}:42"]}


def test_namespace_invalidate_then_get_hides_old_value(client):
    client.put(f"/keys/k?ns=user&v={read_version(client)}", json={"value": "old", "ttl": 60})
    r = client.post("/namespaces/user/invalidate")
    assert (r.status_code, r.json()) == (200, {"version": T0 + 1})
    r = client.get("/keys/k?ns=user")
    assert (r.status_code, r.json()["version"]) == (404, T0 + 1)


def test_namespace_slow_put_between_invalidate_returns_409_version_mismatch(client):
    v = read_version(client)
    client.post("/namespaces/user/invalidate")
    r = client.put(f"/keys/k?ns=user&v={v}", json={"value": "stale", "ttl": 60})
    assert (r.status_code, err(r)) == (409, "VERSION_MISMATCH")
    assert "version" not in r.json()
    assert client.get("/keys?prefix=user:").json() == {"keys": []}


def test_namespace_lease_put_after_invalidate_returns_409_lease_invalid(client):
    token = client.get("/keys/k?ns=user&lease=1").json()["lease"]
    client.post("/namespaces/user/invalidate")
    r = client.put("/keys/k?ns=user", json={"value": "stale", "ttl": 60, "lease": token})
    assert (r.status_code, err(r)) == (409, "LEASE_INVALID")


def test_namespace_put_without_ttl_returns_400_ttl_required(client):
    r = client.put(f"/keys/k?ns=user&v={T0}", json={"value": "v"})
    assert (r.status_code, err(r)) == (400, "TTL_REQUIRED")


def test_namespace_put_without_lease_or_version_returns_400_guard_required(client):
    r = client.put("/keys/k?ns=user", json={"value": "v", "ttl": 60})
    assert (r.status_code, err(r)) == (400, "GUARD_REQUIRED")


@pytest.mark.parametrize("url", ["/keys/k?ns=user&v=abc", "/keys/k?ns=user&v=1.5", "/keys/k?v=7"])
def test_namespace_put_bad_or_orphan_version_returns_400(client, store, url):
    assert client.put(url, json={"value": "v", "ttl": 60}).status_code == 400
    assert store.size() == 0


def test_namespace_empty_ns_returns_400_invalid_key(client):
    r = client.get("/keys/k?ns=")
    assert (r.status_code, err(r)) == (400, "INVALID_KEY")


def test_namespace_query_value_is_decoded_once(client):
    """%25는 서버가 한 번 풀어 '%'가 된다. 핸들러가 또 풀면 'a%41'이 'aA'가 된다."""
    v = read_version(client, ns="a%2541")
    client.put(f"/keys/k?ns=a%2541&v={v}", json={"value": "v", "ttl": 60})
    assert client.get("/keys?prefix=a%2541:").json() == {"keys": [f"a%41:v{T0}:k"]}


def test_namespace_invalidate_percent_encoded_ns_matches_query_ns(client):
    v = read_version(client, ns="a/b")
    client.put(f"/keys/k?ns=a/b&v={v}", json={"value": "v", "ttl": 60})
    assert client.post("/namespaces/a%2Fb/invalidate").json() == {"version": T0 + 1}
    assert client.get("/keys/k?ns=a/b").status_code == 404


def test_namespace_other_key_commands_use_versioned_key(client):
    client.put(f"/keys/k?ns=user&v={read_version(client)}", json={"value": "1", "ttl": 60})
    assert client.get("/keys/k/exists?ns=user").json() == {"exists": True}
    assert client.post("/keys/k/incr?ns=user", json={}).json() == {"value": 2}
    assert client.get("/keys/k/ttl?ns=user").json() == {"ttl": 60}
    assert client.post("/keys/k/expire?ns=user", json={"ttl": 30}).json() == {"updated": True}
    assert client.delete("/keys/k?ns=user").json() == {"deleted": True}


def test_namespace_incr_missing_key_returns_400_ttl_required(client):
    r = client.post("/keys/k/incr?ns=user", json={})
    assert (r.status_code, err(r)) == (400, "TTL_REQUIRED")


def test_namespace_flush_keeps_version(client):
    client.post("/namespaces/user/invalidate")
    client.post("/admin/flush", json={"confirm": True})
    assert read_version(client) == T0 + 1
