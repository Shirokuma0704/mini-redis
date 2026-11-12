from fastapi.testclient import TestClient

from mini_redis.api.app import app


def test_health_get_returns_200():
    assert TestClient(app).get("/health").status_code == 200
