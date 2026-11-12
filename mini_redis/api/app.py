"""FastAPI 앱. 핸들러는 검증만 하고 상태는 Store를 통해서만 만진다."""
from fastapi import FastAPI

app = FastAPI(title="Mini Redis")


@app.get("/health")
def health():
    return {"status": "ok"}
