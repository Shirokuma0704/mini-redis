"""Lease 시나리오 측정 (#19): 스탬피드와 stale set을 lease 없음과 있음으로 비교한다.

Store는 전역 락 하나로 명령을 하나씩 실행하므로, 경합의 결과는 명령이 Store에 닿은 순서로만 갈린다.
그래서 실제 스레드 대신 가짜 시계 위의 이벤트 시뮬레이션으로 그 순서를 정확히 만든다.
클라이언트는 제너레이터다. yield한 ms(원본 지연, retry_after_ms)만큼 가짜 시계가 흐른 뒤 이어서 돈다.
캐시 요청은 TestClient로 실제 HTTP API(라우트, 404 본문의 lease, 409)를 거치고, 요청 자체는 0ms로 친다.
같은 ms에 일이 겹치면 먼저 예약된 쪽이 먼저 돈다.

레포 루트에서:  python -m bench.lease_scenarios [--clients 100]
"""
import argparse
import heapq
import itertools
from pathlib import Path

from fastapi.testclient import TestClient

from mini_redis.api.app import create_app
from mini_redis.clock import FakeClock
from mini_redis.config import LEASE_RETRY_MS, ORIGIN_DELAY_MS, SAMPLE_CACHE_TTL_S
from mini_redis.store import Store

T0 = 1_700_000_000_000
KEY = "/keys/hot"
OUT = Path(__file__).parent / "results"
FRESH, EMPTY, STALE = "최신", "비어 있음", "낡음"
# stale set: 쓰는 쪽 시각(읽는 쪽 GET 기준 ms). 읽는 쪽은 0ms에 원본을 읽고 ORIGIN_DELAY_MS에 PUT한다
SWEEP = range(-100, ORIGIN_DELAY_MS + 101)
REGIONS = (
    ("읽는 쪽 GET 전", lambda t: t < 0),
    ("겹침: 원본 읽기 ~ 늦은 PUT", lambda t: 0 <= t <= ORIGIN_DELAY_MS),
    ("늦은 PUT 뒤", lambda t: t > ORIGIN_DELAY_MS),
)


class Origin:
    """느린 원본. 부른 순간의 값을 돌려주고(응답은 ORIGIN_DELAY_MS 뒤 도착), 호출 수를 센다."""

    def __init__(self):
        self.value, self.calls = "v1", 0

    def read(self):
        self.calls += 1
        return self.value


def cache_aside(cache, origin):
    """lease 없음: sample_app과 같은 순서. GET -> miss면 원본 -> PUT."""
    r = cache.get(KEY)
    if r.status_code == 200:
        return
    value = origin.read()
    yield ORIGIN_DELAY_MS
    cache.put(KEY, json={"value": value, "ttl": SAMPLE_CACHE_TTL_S})


def cache_aside_lease(cache, origin):
    """lease 있음: 토큰을 받은 하나만 원본을 부르고, 나머지는 retry_after_ms 뒤 다시 GET한다."""
    while True:
        r = cache.get(KEY, params={"lease": 1})
        if r.status_code == 200:
            return
        body = r.json()
        if "lease" in body:
            break
        yield body["retry_after_ms"]
    value = origin.read()
    yield ORIGIN_DELAY_MS
    # 그 사이 DEL이 lease를 지웠으면 409이고 캐시에 아무것도 쓰지 않는다
    cache.put(KEY, json={"value": value, "ttl": SAMPLE_CACHE_TTL_S, "lease": body["lease"]})


def update_then_delete(cache, origin):
    """쓰는 쪽: 원본을 바꾸고 캐시 키를 지운다(원본 갱신 뒤 무효화)."""
    origin.value = "v2"
    cache.delete(KEY)
    return
    yield  # 기다리는 일이 없지만 시뮬레이터가 다루게 제너레이터로 둔다


def simulate(clock, procs):
    """procs: [(시작 ms, 제너레이터)]. 각 제너레이터가 끝난 시각(ms) 목록을 돌려준다."""
    base, seq = clock.now_ms, itertools.count()
    heap = [(at, next(seq), i, gen) for i, (at, gen) in enumerate(procs)]
    heapq.heapify(heap)
    done = [None] * len(procs)
    while heap:
        at, _, i, gen = heapq.heappop(heap)
        clock.now_ms = base + at
        try:
            wait = next(gen)
        except StopIteration:
            done[i] = at
        else:
            heapq.heappush(heap, (at + wait, next(seq), i, gen))
    return done


def new_cache():
    clock = FakeClock(T0)
    cache = TestClient(create_app(Store(clock=clock)))
    sent = []
    cache.event_hooks["request"].append(sent.append)  # Mini Redis가 받은 요청 수
    return cache, clock, sent


def stampede(n_clients, use_lease):
    """인기 키가 만료되는 순간 n_clients개가 동시에 읽는다. 원본 호출 수와 캐시 요청 수, 가장 늦은 응답 시각."""
    cache, clock, sent = new_cache()
    cache.put(KEY, json={"value": "v1", "ttl": SAMPLE_CACHE_TTL_S})
    clock.advance(SAMPLE_CACHE_TTL_S * 1000)  # 지금 막 만료됐다
    sent.clear()
    origin = Origin()
    client = cache_aside_lease if use_lease else cache_aside
    done = simulate(clock, [(0, client(cache, origin)) for _ in range(n_clients)])
    return {"origin_calls": origin.calls, "cache_requests": len(sent), "max_ms": max(done)}


def stale_set(writer_at, use_lease):
    """읽는 쪽이 miss -> 원본 읽기 -> PUT 하는 동안, writer_at ms에 쓰는 쪽이 원본을 바꾸고 DEL한다.

    끝난 뒤 캐시가 원본과 같으면 FRESH, 비었으면 EMPTY(다음 읽기가 최신을 채움), 옛 값이면 STALE.
    읽는 쪽을 먼저 예약하므로 같은 ms면 읽는 쪽이 먼저 돈다.
    """
    cache, clock, _ = new_cache()
    origin = Origin()
    reader = (cache_aside_lease if use_lease else cache_aside)(cache, origin)
    shift = max(0, -writer_at)  # 시작 시각이 음수가 되지 않게 둘 다 민다
    simulate(clock, [(shift, reader), (shift + writer_at, update_then_delete(cache, origin))])
    r = cache.get(KEY)
    if r.status_code == 404:
        return EMPTY
    return FRESH if r.json()["value"] == origin.value else STALE


def to_markdown(n_clients, storm, sweep):
    lines = [
        "# Lease 시나리오 측정 결과",
        "",
        f"원본 지연 {ORIGIN_DELAY_MS}ms, lease 대기 간격(retry_after_ms) {LEASE_RETRY_MS}ms, 캐시 TTL {SAMPLE_CACHE_TTL_S}초.",
        "가짜 시계 위 이벤트 시뮬레이션이라 매번 같은 숫자가 나온다. 캐시 요청은 0ms로 친다.",
        "`python -m bench.lease_scenarios`가 이 파일을 다시 만든다.",
        "",
        f"## 스탬피드: 인기 키가 만료되는 순간 동시 요청 {n_clients}개",
        "",
        "| 방식 | 원본 호출 | Mini Redis 요청 | 가장 늦은 응답(ms) |",
        "| --- | ---: | ---: | ---: |",
    ]
    for label, r in (("lease 없음", storm[False]), ("lease 있음", storm[True])):
        lines.append(f"| {label} | {r['origin_calls']:,} | {r['cache_requests']:,} | {r['max_ms']} |")
    lines += [
        "",
        f"lease 있음의 대기자는 {LEASE_RETRY_MS}ms마다 다시 묻는다. 같은 ms면 먼저 예약된 보유자의 PUT이 먼저라 여기서는",
        f"{ORIGIN_DELAY_MS}ms에 값을 받는다. 재시도가 PUT보다 조금이라도 먼저 닿으면 한 번 더 기다려 최대 {ORIGIN_DELAY_MS + LEASE_RETRY_MS}ms다.",
        "",
        "## Stale set: 읽는 쪽의 원본 읽기와 늦은 PUT 사이에 원본 갱신 + DEL",
        "",
        f"쓰는 쪽 시각을 읽는 쪽 GET 기준 {SWEEP.start}ms부터 {SWEEP.stop - 1}ms까지 1ms씩 옮기며 한 번씩 돌린다.",
        f"읽는 쪽은 0ms에 원본을 읽고 {ORIGIN_DELAY_MS}ms에 PUT한다. 칸은 끝난 뒤 캐시 상태의 실행 수다.",
        "",
        f"| 쓰는 쪽 시각 | 실행 | lease 없음: {FRESH} / {EMPTY} / {STALE} | lease 있음: {FRESH} / {EMPTY} / {STALE} | 낡음 비율 (없음 → 있음) |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for label, in_region in (*REGIONS, ("전체", lambda t: True)):
        ts = [t for t in SWEEP if in_region(t)]
        cells, ratios = [], []
        for use_lease in (False, True):
            got = [sweep[use_lease][t] for t in ts]
            cells.append(" / ".join(str(got.count(o)) for o in (FRESH, EMPTY, STALE)))
            ratios.append(f"{got.count(STALE) / len(ts):.1%}")
        span = f"{ts[0]}~{ts[-1]}ms"
        lines.append(f"| {label} ({span}) | {len(ts)} | {cells[0]} | {cells[1]} | {ratios[0]} → {ratios[1]} |")
    return "\n".join(lines) + "\n"


def main():
    p = argparse.ArgumentParser(description="lease 없음과 있음에서 스탬피드와 stale set 비교")
    p.add_argument("--clients", type=int, default=100)
    args = p.parse_args()

    storm = {use_lease: stampede(args.clients, use_lease) for use_lease in (False, True)}
    sweep = {use_lease: {t: stale_set(t, use_lease) for t in SWEEP} for use_lease in (False, True)}
    OUT.mkdir(exist_ok=True)
    md = to_markdown(args.clients, storm, sweep)
    (OUT / "lease_scenarios.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
