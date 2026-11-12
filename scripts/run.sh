#!/usr/bin/env bash
# Uvicorn 워커는 반드시 1개 (프로세스마다 저장소가 갈라지므로)
exec uvicorn mini_redis.api.app:app --host 0.0.0.0 --port 8000 --workers 1
