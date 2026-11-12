"""200ms 지연 가짜 원본 API예요.

cache-aside 성능 비교에서 "느린 원본" 역할을 하고, 몇 번 불려 갔는지 세어 줘요.
"""

import asyncio
import threading

from fastapi import FastAPI

DEFAULT_DELAY_MS = 200


def create_origin_app(delay_ms=DEFAULT_DELAY_MS, sleep=asyncio.sleep) -> FastAPI:
    """가짜 원본 앱을 만들어요. sleep은 테스트에서 가짜로 바꿔 끼울 수 있어요."""
    app = FastAPI()
    app.state.calls = 0
    app.state.lock = threading.Lock()

    @app.get("/items/{item_id}")
    async def get_item(item_id: str):
        await sleep(delay_ms / 1000)
        with app.state.lock:
            app.state.calls += 1
        return {"id": item_id, "value": f"item-{item_id}"}

    @app.get("/stats")
    async def get_stats():
        with app.state.lock:
            return {"calls": app.state.calls}

    @app.post("/stats/reset")
    async def reset_stats():
        with app.state.lock:
            app.state.calls = 0
        return {"calls": 0}

    return app


app = create_origin_app()
