from bench.lease_scenarios import EMPTY, FRESH, STALE, stale_set, stampede


def test_lease_scenarios_lease_off_vs_on_stampede_once_and_stale_only_in_overlap_without_lease():
    assert stampede(10, use_lease=False)["origin_calls"] == 10  # 다 같이 miss라 전부 원본을 부른다
    assert stampede(10, use_lease=True)["origin_calls"] == 1

    # 쓰는 쪽 시각: GET 직전, 원본 읽기와 같은 ms, 늦은 PUT과 같은 ms, PUT 직후 (읽는 쪽은 0ms에 읽고 200ms에 PUT)
    at = (-1, 0, 200, 201)
    assert [stale_set(t, use_lease=False) for t in at] == [FRESH, STALE, STALE, EMPTY]
    assert [stale_set(t, use_lease=True) for t in at] == [FRESH, EMPTY, EMPTY, EMPTY]  # 겹치면 늦은 PUT이 409
