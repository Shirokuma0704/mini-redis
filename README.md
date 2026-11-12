# Mini Redis

해시테이블을 직접 구현한 인메모리 키-값 저장소. FastAPI HTTP API로 노출하고 cache-aside 캐시로 성능을 측정한다.
설계 명세: [docs/spec.md](docs/spec.md)

## 시작하기
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
./scripts/run.sh        # http://localhost:8000/health
pytest
```
