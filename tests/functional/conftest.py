import pytest
from fastapi.testclient import TestClient

from mini_redis.api.app import create_app
from mini_redis.clock import FakeClock
from mini_redis.store import Store


@pytest.fixture
def clock():
    return FakeClock(1_000_000)


@pytest.fixture
def store(clock):
    return Store(clock=clock)


@pytest.fixture
def client(store):
    # raise_server_exceptions=False: 500 응답 형식도 검증할 수 있게
    return TestClient(create_app(store), raise_server_exceptions=False)
