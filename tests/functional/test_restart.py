"""앱 수명주기와 재시작: 복구가 끝난 뒤에 만료 스레드가 시작하고, 재시작해도 데이터가 남는다."""
import importlib
import os

from fastapi.testclient import TestClient

import mini_redis.api.app as app_module
from mini_redis.api.app import create_app
from mini_redis.clock import FakeClock
from mini_redis.persistence.manager import Persistence
from mini_redis.store import Store

T0 = 1_000_000
IDLE = 3600.0


def make_persistence(data_dir):
    return Persistence(data_dir, aof_interval_s=IDLE, snapshot_interval_s=IDLE, check_interval_s=IDLE)


def make_app(data_dir, clock):
    return create_app(Store(clock=clock), persistence=make_persistence(data_dir))


def test_lifespan_order(tmp_path, monkeypatch):
    calls = []
    store = Store(clock=FakeClock(T0))
    persistence = make_persistence(tmp_path)

    def wrap(obj, name, label):
        original = getattr(obj, name)

        def wrapper(*args, **kwargs):
            calls.append(label)
            return original(*args, **kwargs)

        monkeypatch.setattr(obj, name, wrapper)

    wrap(persistence, "start", "persistence.start")
    wrap(store, "start_expiry", "start_expiry")
    wrap(store, "stop_expiry", "stop_expiry")
    wrap(persistence, "stop", "persistence.stop")

    with TestClient(create_app(store, persistence=persistence)):
        assert calls == ["persistence.start", "start_expiry"]
    assert calls == ["persistence.start", "start_expiry", "stop_expiry", "persistence.stop"]


def test_http_restart_keeps_data(tmp_path):
    clock = FakeClock(T0)
    with TestClient(make_app(tmp_path, clock)) as c1:
        assert c1.put("/keys/a", json={"value": "1"}).status_code == 200
        assert c1.put("/keys/b", json={"value": "2", "ttl": 100}).status_code == 200
        assert c1.post("/keys/n/incr").status_code == 200
        assert c1.delete("/keys/b").status_code == 200

    with TestClient(make_app(tmp_path, clock)) as c2:
        assert c2.get("/keys/a").json()["value"] == "1"
        assert c2.get("/keys/b").status_code == 404
        assert c2.get("/keys/n").json()["value"] == "1"


def test_prefix_delete_and_flush_survive_restart(tmp_path):
    clock = FakeClock(T0)
    with TestClient(make_app(tmp_path, clock)) as c1:
        for key in ("u:1", "u:2", "x"):
            assert c1.put(f"/keys/{key}", json={"value": "v"}).status_code == 200
        assert c1.delete("/keys", params={"prefix": "u:"}).json() == {"deleted": 2}

    with TestClient(make_app(tmp_path, clock)) as c2:
        assert c2.get("/keys").json()["keys"] == ["x"]
        assert c2.post("/admin/flush", json={"confirm": True}).status_code == 200

    with TestClient(make_app(tmp_path, clock)) as c3:
        assert c3.get("/keys/x").status_code == 404
        assert c3.get("/keys").json()["keys"] == []


def test_importing_app_module_does_not_create_data_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    importlib.reload(app_module)
    assert not (tmp_path / "data").exists()
    assert os.listdir(tmp_path) == []


def test_create_app_without_persistence_writes_no_files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    store = Store(clock=FakeClock(T0))
    with TestClient(create_app(store)) as client:
        assert client.put("/keys/a", json={"value": "1"}).status_code == 200
        assert client.get("/keys/a").json()["value"] == "1"
    assert os.listdir(tmp_path) == []
