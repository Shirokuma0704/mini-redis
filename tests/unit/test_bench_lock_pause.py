"""bench/lock_pause.py 스모크 테스트 (#20). 시간 값은 확인하지 않고 구조와 개수만 본다.

구현이 맞춰야 하는 이름:
- ROW_LABELS: {행 id: 표에 적는 이름} 7개. 순서는 아래 ROW_IDS와 같다.
- build_store(row_id, n_keys, prefix_share) -> Store: 그 행을 재기 직전 상태의 Store.
- run_row(row_id, n_keys, prefix_share, use_gc=False) -> dict: 한 번 재고 "victim_calls"(구간 안에서 시작한
  다른 요청 수)를 담는다. prefix_delete 행은 "removed"(지워진 키 수)도 담는다.
- main(argv): --keys, --repeat, --prefix-share, --gc, --out.
"""
from bench.lock_pause import ROW_LABELS, build_store, main, run_row
from bench.rehash_latency import resize_points
from mini_redis.config import REHASH_BUCKETS_PER_STEP

ROW_IDS = [
    "control_a", "control_b", "prefix_delete", "keys", "snapshot", "resize_incremental", "resize_bulk",
]
N = 2000
SHARE = 0.1
BULK = 1 << 62


def test_lock_pause_row_labels_cover_the_seven_rows_in_order():
    assert list(ROW_LABELS) == ROW_IDS
    assert len(set(ROW_LABELS.values())) == 7


def test_lock_pause_main_small_run_writes_results_with_every_row(tmp_path):
    out = tmp_path / "lock_pause.md"

    main(["--keys", str(N), "--repeat", "1", "--out", str(out)])

    md = out.read_text(encoding="utf-8")
    for label in ROW_LABELS.values():
        assert label in md
    assert f"{N:,}" in md  # 헤더에 키 수


def test_lock_pause_main_with_out_does_not_touch_default_results_dir(tmp_path):
    from pathlib import Path

    default = Path(__file__).resolve().parents[2] / "bench" / "results" / "lock_pause.md"
    before = default.read_bytes() if default.exists() else None

    main(["--keys", "500", "--repeat", "1", "--out", str(tmp_path / "x.md")])

    assert (default.read_bytes() if default.exists() else None) == before


def test_lock_pause_build_store_prefix_delete_has_share_of_keys_under_prefix():
    store = build_store("prefix_delete", N, SHARE)

    assert store.size() == N
    assert len(store.keys("user:")) == int(N * SHARE)
    assert len(store.keys("key:")) == N - int(N * SHARE)


def test_lock_pause_run_row_prefix_delete_removes_share_of_keys():
    r = run_row("prefix_delete", N, SHARE)

    assert r["removed"] == int(N * SHARE)


def test_lock_pause_build_store_resize_incremental_stops_just_below_last_threshold():
    store = build_store("resize_incremental", N, SHARE)
    last_put, _ = resize_points(N)[-1]

    assert store.size() == last_put - 1
    assert store._table._old is None  # 아직 리해싱을 시작하지 않았다
    assert store._table._rehash_buckets == REHASH_BUCKETS_PER_STEP
    assert store._max_keys > N * 4  # 리해싱이 끝날 때까지 CapacityExceeded가 나지 않을 만큼


def test_lock_pause_build_store_resize_bulk_swaps_in_bulk_rehash_table():
    store = build_store("resize_bulk", N, SHARE)
    last_put, _ = resize_points(N)[-1]

    assert store.size() == last_put - 1
    assert store._table._rehash_buckets == BULK
    assert store._max_keys > N * 4


def test_lock_pause_build_store_resize_bulk_next_put_finishes_resize_at_once():
    store = build_store("resize_bulk", N, SHARE)

    store.set("new:0", "v")

    assert store._table._old is None  # 한 put이 다 옮겼다


def test_lock_pause_build_store_resize_incremental_next_put_starts_rehash_and_keeps_it_pending():
    store = build_store("resize_incremental", N, SHARE)

    store.set("new:0", "v")

    assert store._table._old is not None  # 점진적이라 한 put으로는 안 끝난다


def test_lock_pause_run_row_every_row_records_victim_calls():
    for row_id in ROW_IDS:
        r = run_row(row_id, N, SHARE)
        assert r["victim_calls"] > 0, row_id
