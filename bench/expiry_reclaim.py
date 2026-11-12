"""만료 회수 측정 (#21): 타이밍 휠 tick을 켠 것과 lazy 만료만 쓰는 것을 비교한다.

같은 키 집합(같은 시드)을 두 방식으로 돌리며 1초마다 "만료됐는데 아직 메모리에 남은 키 수"를 센다.
시간은 FakeClock으로 100ms씩 흘린다. 만료 스레드는 100ms마다 expire_tick()을 부를 뿐이고
무엇을 지울지는 주입한 시계 값으로만 정해지므로, 같은 호출 순서를 가짜 시계로 재현하면 결과가 결정적이다.

레포 루트에서:  python -m bench.expiry_reclaim [--keys 100000] [--seconds 70]
"""
import argparse
import bisect
import csv
import random
import time
from pathlib import Path

from mini_redis.clock import FakeClock
from mini_redis.config import EXPIRE_MAX_PER_TICK, EXPIRE_TICK_MS
from mini_redis.store import Store

T0 = 1_700_000_000_000
TICKS_PER_S = 1000 // EXPIRE_TICK_MS
OUT = Path(__file__).parent / "results"

# 시나리오별 TTL(ms). 모든 키는 t=0에 한꺼번에 저장한다.
SCENARIOS = {
    "spread": ("TTL 1~60초에 고르게", lambda rng: rng.randint(1_000, 60_000)),
    "burst": ("10초에 전부 만료 (tick 상한에 걸림)", lambda rng: 10_000),
}
MODES = (("lazy", "lazy만", False), ("wheel", "휠", True))


def run(n_keys, ttl_ms, use_wheel, seconds, hot_ratio=0.1, reads_per_tick=100, seed=21):
    """1초마다 남은 만료 키 수 목록과 expire_tick 한 번의 최대 소요 시간(ms)을 돌려준다.

    매 100ms: 시계를 민다 -> 핫 키(앞쪽 hot_ratio)를 무작위로 reads_per_tick번 EXISTS(lazy 회수)
    -> 1초 경계면 표본 -> use_wheel이면 expire_tick.
    표본을 tick 직전에 떠서 휠 쪽은 최근 100ms 동안 만료된 키까지 남은 것으로 센다(휠에 불리한 쪽).
    """
    rng = random.Random(seed)
    clock = FakeClock(T0)
    store = Store(clock=clock)
    expire_at = [T0 + ttl_ms(rng) for _ in range(n_keys)]
    for i, at in enumerate(expire_at):
        store.set(f"k{i}", "v", expire_at_ms=at)
    expire_at.sort()
    hot = max(1, int(n_keys * hot_ratio))
    remaining, max_tick_ms = [], 0.0
    for step in range(1, seconds * TICKS_PER_S + 1):
        clock.advance(EXPIRE_TICK_MS)
        for _ in range(reads_per_tick):
            store.exists(f"k{rng.randrange(hot)}")
        if step % TICKS_PER_S == 0:
            expired = bisect.bisect_right(expire_at, clock.now_ms)  # Store와 같은 기준: now >= expire_at
            live = n_keys - expired  # 읽기는 살아 있는 키를 지우지 않으므로 그대로 남아 있다
            remaining.append(store.size() - live)
        if use_wheel:
            start = time.perf_counter()
            store.expire_tick()
            max_tick_ms = max(max_tick_ms, (time.perf_counter() - start) * 1000)
    return remaining, max_tick_ms


def to_markdown(n_keys, seconds, cols, tick_ms):
    lines = [
        "# 만료 회수 측정 결과",
        "",
        f"키 {n_keys:,}개를 t=0에 저장, {seconds}초 동안 가짜 시계로 {EXPIRE_TICK_MS}ms씩 진행.",
        f"휠은 tick당 최대 {EXPIRE_MAX_PER_TICK:,}개. 두 방식 모두 핫 키 10%에 tick마다 EXISTS 100번(시드 21).",
        "표본은 1초마다 tick 직전에 뜬다. `python -m bench.expiry_reclaim`이 이 파일과 CSV를 다시 만든다.",
        "",
        f"| 시나리오 | 방식 | 최대 | 평균 | 끝({seconds}초) | tick 최대(ms) |",
        "| --- | --- | ---: | ---: | ---: | ---: |",
    ]
    for name in SCENARIOS:
        for mode, label, use_wheel in MODES:
            r = cols[f"{name}_{mode}"]
            tick = f"{tick_ms[f'{name}_{mode}']:.1f}" if use_wheel else "-"
            lines.append(f"| {name} | {label} | {max(r):,} | {sum(r) // len(r):,} | {r[-1]:,} | {tick} |")
    lines += ["", "tick 최대(ms)는 이 컴퓨터의 벽시계 값이라 돌릴 때마다 조금씩 다르다. 나머지 숫자는 결정적이다."]
    for name, (desc, _) in SCENARIOS.items():
        lazy, wheel = cols[f"{name}_lazy"], cols[f"{name}_wheel"]
        lines += [
            "",
            f"## {name}: {desc}",
            "",
            "```mermaid",
            '%%{init: {"themeVariables": {"xyChart": {"plotColorPalette": "#d62728, #1f77b4"}}}}%%',
            "xychart-beta",
            '  title "남은 만료 키 수 (빨강 lazy만, 파랑 휠)"',
            f'  x-axis "경과 시간(초)" 1 --> {seconds}',
            '  y-axis "남은 만료 키 수"',
            f"  line [{', '.join(map(str, lazy))}]",
            f"  line [{', '.join(map(str, wheel))}]",
            "```",
        ]
    return "\n".join(lines) + "\n"


def main():
    p = argparse.ArgumentParser(description="만료 회수 측정: 타이밍 휠 vs lazy만")
    p.add_argument("--keys", type=int, default=100_000)
    p.add_argument("--seconds", type=int, default=70)
    args = p.parse_args()

    cols, tick_ms = {}, {}
    for name, (_, ttl_ms) in SCENARIOS.items():
        for mode, _, use_wheel in MODES:
            cols[f"{name}_{mode}"], tick_ms[f"{name}_{mode}"] = run(args.keys, ttl_ms, use_wheel, args.seconds)

    OUT.mkdir(exist_ok=True)
    with open(OUT / "expiry_reclaim.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_s", *cols])
        for i, row in enumerate(zip(*cols.values())):
            w.writerow([i + 1, *row])
    md = to_markdown(args.keys, args.seconds, cols, tick_ms)
    (OUT / "expiry_reclaim.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
