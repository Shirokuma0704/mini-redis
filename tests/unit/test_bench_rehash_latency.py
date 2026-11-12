from bench.rehash_latency import BULK, max_moved, resize_points, run, summarize


def test_rehash_latency_resize_points_match_load_factor_thresholds():
    assert resize_points(100) == [(13, 16), (25, 32), (49, 64), (97, 128)]


def test_rehash_latency_small_run_times_every_put_per_window():
    lat = run(100, BULK)
    r = summarize(lat, 100)
    assert len(lat) == 100 and len(r["windows"]) == 4
    assert r["max"] == max(lat) and r["windows"][-1] == max(lat[96:])


def test_rehash_latency_max_moved_bulk_moves_whole_table_incremental_few():
    assert max_moved(100, BULK) == 97  # 97번째 put이 방금 넣은 자기 키까지 97개를 한 번에 옮긴다
    assert max_moved(100, 1) < 10  # 버킷 하나의 체인만 옮긴다
