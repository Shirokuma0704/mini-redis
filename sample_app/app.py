"""cache-aside 샘플 앱이에요.

Mini Redis에 먼저 물어보고(HIT), 없으면 느린 원본을 불러서 캐시에 채워 둬요(MISS).
"""

import json
import logging
import os
import threading
from collections.abc import Mapping
from typing import Optional
from urllib.parse import quote

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from mini_redis import config

logger = logging.getLogger(__name__)


def create_sample_app(
    cache: Optional[httpx.Client],
    origin: httpx.Client,
    ttl_s: int = config.SAMPLE_CACHE_TTL_S,
    use_cache: bool = True,
) -> FastAPI:
    """샘플 앱을 만들어요. cache/origin 클라이언트는 밖에서 끼워 넣어요.

    use_cache=False면 캐시를 전혀 건드리지 않고 매번 원본을 불러요(X-Cache: BYPASS, miss로 세요).
    이때 cache는 None이어도 돼요. 캐시 효과를 재는 벤치마크에서 "캐시 없는 쪽" 기준선으로 써요.
    """
    app = FastAPI()
    lock = threading.Lock()
    counters = {"hits": 0, "misses": 0}

    def error(message: str, code: str, status: int = 502) -> JSONResponse:
        return JSONResponse(status_code=status, content={"error": code, "message": message})

    def fetch_from_origin(item_id: str, label: str, cache_path: Optional[str] = None):
        """원본을 불러 miss로 세고 돌려줘요. cache_path가 있을 때만 캐시에 채워요."""
        try:
            fetched = origin.get("/items/" + quote(item_id, safe=""))
        except httpx.HTTPError as exc:
            return error(f"origin request failed: {exc}", "ORIGIN_ERROR")
        if fetched.status_code != 200:
            return error(f"origin returned {fetched.status_code}", "ORIGIN_ERROR")
        try:
            body = fetched.json()
            # 렌더할 수 없는 값(NaN 등)은 캐시에 쓰거나 miss로 세기 전에 막아요.
            json.dumps(body, allow_nan=False)
        except ValueError:
            return error("origin returned a non-JSON or unrenderable body", "ORIGIN_ERROR")

        if cache_path is not None:
            try:
                stored = cache.put(
                    cache_path,
                    json={"value": json.dumps(body, ensure_ascii=False), "ttl": ttl_s},
                )
                if stored.status_code != 200:
                    logger.warning("cache put returned %s for %s", stored.status_code, cache_path)
            except httpx.HTTPError as exc:
                logger.warning("cache put failed for %s: %s", cache_path, exc)

        with lock:
            counters["misses"] += 1
        return JSONResponse(content=body, headers={"X-Cache": label})

    @app.get("/items/{item_id}")
    def get_item(item_id: str):
        # "."와 ".."는 httpx가 URL을 합치면서 지워 버려서, 어떤 호출도 하기 전에 막아요.
        if item_id in (".", ".."):
            return error("item_id는 '.'나 '..'일 수 없어요", "INVALID_ITEM_ID", 400)
        if not use_cache:
            return fetch_from_origin(item_id, "BYPASS")
        path = "/keys/" + quote("item:" + item_id, safe="")
        try:
            cached = cache.get(path)
        except httpx.HTTPError as exc:
            return error(f"cache request failed: {exc}", "CACHE_ERROR")

        if cached.status_code == 200:
            try:
                body = json.loads(cached.json()["value"])
                # NaN/Infinity/1e999는 파싱은 되지만 응답으로 렌더할 수 없어서 같이 막아요.
                json.dumps(body, allow_nan=False)
            except (ValueError, KeyError, TypeError):
                # 캐시 본문이 JSON이 아니거나 "value"가 없거나 렌더할 수 없으면 500 대신 502로 알려요.
                return error("cache returned an invalid value", "CACHE_ERROR")
            with lock:
                counters["hits"] += 1
            return JSONResponse(content=body, headers={"X-Cache": "HIT"})
        if cached.status_code != 404:
            return error(f"cache returned {cached.status_code}", "CACHE_ERROR")

        return fetch_from_origin(item_id, "MISS", path)

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


def _use_cache_from_env(env: Mapping[str, str]) -> bool:
    """SAMPLE_USE_CACHE가 "0"일 때만 캐시를 꺼요. 없거나 다른 값이면 켜요."""
    return env.get("SAMPLE_USE_CACHE") != "0"


app = create_sample_app(
    httpx.Client(base_url=os.environ.get("MINI_REDIS_URL", "http://127.0.0.1:8000")),
    httpx.Client(base_url=os.environ.get("ORIGIN_URL", "http://127.0.0.1:8001")),
    use_cache=_use_cache_from_env(os.environ),
)
