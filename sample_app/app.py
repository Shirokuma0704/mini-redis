"""cache-aside 샘플 앱이에요.

Mini Redis에 먼저 물어보고(HIT), 없으면 느린 원본을 불러서 캐시에 채워 둬요(MISS).
"""

import json
import logging
import os
import threading
from urllib.parse import quote

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from mini_redis import config

logger = logging.getLogger(__name__)


def create_sample_app(
    cache: httpx.Client,
    origin: httpx.Client,
    ttl_s: int = config.SAMPLE_CACHE_TTL_S,
) -> FastAPI:
    """샘플 앱을 만들어요. cache/origin 클라이언트는 밖에서 끼워 넣어요."""
    app = FastAPI()
    lock = threading.Lock()
    counters = {"hits": 0, "misses": 0}

    def error(message: str, code: str) -> JSONResponse:
        return JSONResponse(status_code=502, content={"error": code, "message": message})

    @app.get("/items/{item_id}")
    def get_item(item_id: str):
        path = "/keys/" + quote("item:" + item_id, safe="")
        try:
            cached = cache.get(path)
        except httpx.HTTPError as exc:
            return error(f"cache request failed: {exc}", "CACHE_ERROR")

        if cached.status_code == 200:
            body = json.loads(cached.json()["value"])
            with lock:
                counters["hits"] += 1
            return JSONResponse(content=body, headers={"X-Cache": "HIT"})
        if cached.status_code != 404:
            return error(f"cache returned {cached.status_code}", "CACHE_ERROR")

        try:
            fetched = origin.get("/items/" + quote(item_id, safe=""))
        except httpx.HTTPError as exc:
            return error(f"origin request failed: {exc}", "ORIGIN_ERROR")
        if fetched.status_code != 200:
            return error(f"origin returned {fetched.status_code}", "ORIGIN_ERROR")
        body = fetched.json()

        try:
            stored = cache.put(
                path,
                json={"value": json.dumps(body, ensure_ascii=False), "ttl": ttl_s},
            )
            if stored.status_code != 200:
                logger.warning("cache put returned %s for %s", stored.status_code, path)
        except httpx.HTTPError as exc:
            logger.warning("cache put failed for %s: %s", path, exc)

        with lock:
            counters["misses"] += 1
        return JSONResponse(content=body, headers={"X-Cache": "MISS"})

    @app.get("/stats")
    def get_stats() -> dict[str, int]:
        with lock:
            return dict(counters)

    @app.post("/stats/reset")
    def reset_stats() -> dict[str, int]:
        with lock:
            counters["hits"] = 0
            counters["misses"] = 0
            return dict(counters)

    return app


app = create_sample_app(
    httpx.Client(base_url=os.environ.get("MINI_REDIS_URL", "http://127.0.0.1:8000")),
    httpx.Client(base_url=os.environ.get("ORIGIN_URL", "http://127.0.0.1:8001")),
)
