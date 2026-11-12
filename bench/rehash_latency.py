"""리사이즈 지연 측정 (#24): 일괄 리사이즈와 점진적 리해싱에서 put 한 번의 지연을 비교한다.

빈 HashTable에 키 N개를 하나씩 넣으며 put마다 perf_counter_ns로 잰다. Store는 전역 락을 잡은 채로
put을 부르므로, put 한 번이 걸린 시간이 곧 그동안 다른 요청이 기다리는 시간이다.
일괄 리사이즈는 rehash_buckets를 배열보다 크게 줘서 재현한다. 리사이즈를 일으킨 put이 전부 옮기므로 main의 _resize와 같은 일이다.

기본은 GC를 끄고 잰다. 세대별 GC가 엔트리 수십만 개를 훑는 멈춤이 두 방식에 똑같이 섞여 리사이즈 효과를 가리기 때문이다.
GC를 켠 결과는 따로 한 표로 남긴다.
벽시계 값이라 방식마다 --repeat번 돌려 통계마다 가장 작은 값을 쓴다(timeit과 같은 이유).
리사이즈 비용은 매번 나오고, 다른 프로세스의 간섭은 시간을 더하기만 하기 때문이다.

레포 루트에서:  python -m bench.rehash_latency [--keys 1000000] [--repeat 3]
"""
import argparse
import gc
import time
from pathlib import Path

from mini_redis.config import INITIAL_CAPACITY, LOAD_FACTOR, REHASH_BUCKETS_PER_STEP
from mini_redis.hashtable import HashTable, fnv1a_64

OUT = Path(__file__).parent / "results"
BULK = 1 << 62  # 배열보다 크면 리사이즈를 한 put이 끝낸다 = 일괄 리사이즈
STEPS = (1, 4, 16, 64)  # 명령마다 옮기는 버킷 수 후보. config 값을 고른 근거
MIN_WINDOW_CAP = 1 << 14  # 구간 표는 이 배열 크기부터


def run(n_keys, rehash_buckets, use_gc=False):
    """put마다 걸린 시간(ns) 목록."""
    keys = [f"key:{i}" for i in range(n_keys)]
    table = HashTable(rehash_buckets=rehash_buckets)
    lat = [0] * n_keys
    now = time.perf_counter_ns
    gc.collect()
    was_enabled = gc.isenabled()
    if not use_gc:
        gc.disable()
    try:
        for i, key in enumerate(keys):
            start = now()
            table.put(key, "v")
            lat[i] = now() - start
    finally:
        if was_enabled:
            gc.enable()
    return lat


def max_moved(n_keys, rehash_buckets):
    """put 한 번이 새 배열로 옮긴 엔트리 수의 최대. 시계를 쓰지 않아 매번 같다.

    put은 자기 키를 한 번 해시하고, 옮기는 엔트리마다 한 번씩 더 해시한다. 그래서 해시 호출 수 - 1이 옮긴 수다.
    """
    calls = [0]

    def counting_hash(key):
        calls[0] += 1
        return fnv1a_64(key)

    table = HashTable(hash_fn=counting_hash, rehash_buckets=rehash_buckets)
    most = 0
    for i in range(n_keys):
        before = calls[0]
        table.put(f"key:{i}", "v")
        most = max(most, calls[0] - before - 1)
    return most


def resize_points(n_keys):
    """[(리사이즈를 일으키는 put 번호(1부터), 그때 배열 크기)]. size/capacity > LOAD_FACTOR가 처음 되는 put."""
    out, cap = [], INITIAL_CAPACITY
    while int(cap * LOAD_FACTOR) + 1 <= n_keys:
        out.append((int(cap * LOAD_FACTOR) + 1, cap))
        cap *= 2
    return out


def summarize(lat, n_keys):
    s = sorted(lat)
    n = len(s)
    windows = []
    points = resize_points(n_keys)
    for j, (at, _) in enumerate(points):
        end = points[j + 1][0] - 1 if j + 1 < len(points) else n_keys
        windows.append(max(lat[at - 1:end]))
    return {
        "p50": s[n // 2], "p99": s[n * 99 // 100], "p999": s[n * 999 // 1000], "max": s[-1],
        "over_1ms": sum(x > 1_000_000 for x in lat), "total": sum(lat), "windows": windows,
    }


def best_of(runs):
    out = {k: min(r[k] for r in runs) for k in runs[0] if k != "windows"}
    out["windows"] = [min(w) for w in zip(*(r["windows"] for r in runs))]
    return out


def label(step):
    if step == BULK:
        return "일괄 리사이즈", "-"
    return ("점진적 리해싱 (config)" if step == REHASH_BUCKETS_PER_STEP else "점진적 리해싱"), str(step)


def to_markdown(n_keys, repeat, results, gc_results, moved):
    us, ms = 1e3, 1e6
    lines = [
        "# 리사이즈 지연 측정 결과",
        "",
        f"빈 HashTable(배열 {INITIAL_CAPACITY}칸)에 키 {n_keys:,}개(`key:0`부터)를 넣으며 put 한 번의 지연을 잰다.",
        f"방식을 번갈아 {repeat}번씩 돌려 통계마다 가장 작은 값을 적는다. 벽시계 값이라 돌릴 때마다 조금씩 다르다.",
        "\"put 하나가 옮긴 최대 엔트리\"만 시계를 쓰지 않는 값이라 매번 같다.",
        "`python -m bench.rehash_latency`가 이 파일을 다시 만든다.",
        "",
        "## put 한 번의 지연 (GC 끔)",
        "",
        "| 방식 | 버킷/명령 | put 하나가 옮긴 최대 엔트리 | p50(µs) | p99(µs) | p99.9(µs) | 최대(ms) | 1ms 넘은 put | 합계(s) |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for step, r in results.items():
        name, per = label(step)
        lines.append(
            f"| {name} | {per} | {moved[step]:,} | {r['p50'] / us:.1f} | {r['p99'] / us:.1f} | {r['p999'] / us:.1f} "
            f"| {r['max'] / ms:.2f} | {r['over_1ms']:,.0f} | {r['total'] / 1e9:.2f} |"
        )
    bulk, inc = results[BULK], results[REHASH_BUCKETS_PER_STEP]
    lines += [
        "",
        "## 리사이즈 구간별 최대 지연 (GC 끔)",
        "",
        "구간은 리사이즈를 일으킨 put부터 다음 리사이즈 직전 put까지다. 일괄은 그 첫 put이 옛 엔트리를 전부 옮긴다.",
        "",
        f"| 배열 크기 | 리사이즈를 일으킨 put | 일괄 최대(ms) | 점진적({REHASH_BUCKETS_PER_STEP}) 최대(ms) |",
        "| --- | ---: | ---: | ---: |",
    ]
    for (at, cap), b, i in zip(resize_points(n_keys), bulk["windows"], inc["windows"]):
        if cap >= MIN_WINDOW_CAP:
            lines.append(f"| {cap:,} → {cap * 2:,} | {at:,} | {b / ms:.2f} | {i / ms:.2f} |")
    lines += [
        "",
        "## GC를 켜면",
        "",
        "| 방식 | p99(µs) | 최대(ms) | 1ms 넘은 put |",
        "| --- | ---: | ---: | ---: |",
    ]
    for step, r in gc_results.items():
        lines.append(f"| {label(step)[0]} | {r['p99'] / us:.1f} | {r['max'] / ms:.2f} | {r['over_1ms']:,.0f} |")
    return "\n".join(lines) + "\n"


def main():
    p = argparse.ArgumentParser(description="리사이즈 지연 측정: 일괄 리사이즈 vs 점진적 리해싱")
    p.add_argument("--keys", type=int, default=1_000_000)
    p.add_argument("--repeat", type=int, default=3)
    args = p.parse_args()

    steps = (BULK, *sorted(set(STEPS) | {REHASH_BUCKETS_PER_STEP}))
    gc_steps = (BULK, REHASH_BUCKETS_PER_STEP)
    runs = {s: [] for s in steps}
    gc_runs = {s: [] for s in gc_steps}
    for _ in range(args.repeat):  # 방식을 번갈아 돌려 다른 프로세스의 간섭이 한 방식에 몰리지 않게 한다
        for s in steps:
            runs[s].append(summarize(run(args.keys, s), args.keys))
        for s in gc_steps:
            gc_runs[s].append(summarize(run(args.keys, s, use_gc=True), args.keys))
    results = {s: best_of(r) for s, r in runs.items()}
    gc_results = {s: best_of(r) for s, r in gc_runs.items()}
    moved = {s: max_moved(args.keys, s) for s in steps}
    md = to_markdown(args.keys, args.repeat, results, gc_results, moved)
    OUT.mkdir(exist_ok=True)
    (OUT / "rehash_latency.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
