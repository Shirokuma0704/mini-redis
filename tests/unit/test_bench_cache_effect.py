"""bench/cache_effect.py 테스트. 순수 함수는 값으로, 전체 실행은 작은 크기로 한 번만 돌려요(실제 HTTP, 몇 초).

기대하는 인터페이스(구현이 맞춰야 해요):
- percentile(values, pct): 최근접 순위(nearest-rank). 정렬 안 된 목록도 받고, 순위 = ceil(pct/100 * n)(최소 1)번째 값. 빈 목록은 ValueError.
- summarize(latencies_ms, errors, hits, misses, wall_s) -> dict: count, errors, p50, p95, max, throughput, hit_ratio.
  count는 성공한 요청 수(= len(latencies_ms)), throughput = count / wall_s, hit_ratio = hits / (hits + misses)(둘 다 0이면 0.0).
- server_command(target, port) -> list[str]: uvicorn 실행 명령. "--workers"와 바로 뒤 값 "1"이 들어 있어야 해요(서버마다 프로세스 하나).
- server_env(base_env, extra) -> dict: 자식 프로세스 환경. base_env에서 WEB_CONCURRENCY를 빼고 extra를 얹어요(base_env는 안 바꿔요).
- phase_wall_seconds(started, completions) -> float: 단계 벽시계 시간 = max(completions) - started. completions는 워커별 마지막 요청 완료 시각(perf_counter)이에요.
- main(argv): 결과를 --out 경로에 쓰고, 표는 "첫 요청"과 "이후 요청" 제목 아래 하나씩이에요.
"""

import pytest

from bench.cache_effect import main, percentile, phase_wall_seconds, server_command, server_env, summarize


def test_percentile_원소_하나면_어떤_pct든_그_값():
    assert percentile([7.0], 50) == 7.0
    assert percentile([7.0], 95) == 7.0
    assert percentile([7.0], 100) == 7.0


def test_percentile_1부터_20의_p95는_19():
    assert percentile(list(range(1, 21)), 95) == 19  # ceil(0.95 * 20) = 19번째


def test_percentile_1부터_20의_p50은_10():
    assert percentile(list(range(1, 21)), 50) == 10  # ceil(0.5 * 20) = 10번째


def test_percentile_p100은_최댓값_정렬_안_된_목록도_받아요():
    assert percentile([5, 1, 9, 3], 100) == 9
    assert percentile([5, 1, 9, 3], 50) == 3  # 정렬하면 1 3 5 9, ceil(0.5 * 4) = 2번째


def test_percentile_빈_목록이면_ValueError():
    with pytest.raises(ValueError):
        percentile([], 50)


def test_summarize_합성_목록의_count_에러_hit_ratio():
    result = summarize([float(i) for i in range(1, 21)], errors=2, hits=15, misses=5, wall_s=4.0)
    assert result["count"] == 20
    assert result["errors"] == 2
    assert result["hit_ratio"] == pytest.approx(0.75)
    assert result["p50"] == 10
    assert result["p95"] == 19
    assert result["max"] == 20
    assert result["throughput"] == pytest.approx(5.0)  # 20 / 4.0


def test_summarize_hits와_misses가_모두_0이면_hit_ratio는_0():
    result = summarize([1.0, 2.0], errors=0, hits=0, misses=0, wall_s=1.0)
    assert result["hit_ratio"] == 0.0


def test_server_command_workers_1이_들어_있어요():
    command = server_command("sample_app.app:app", 8123)
    assert "--workers" in command
    assert command[command.index("--workers") + 1] == "1"


def test_server_command_대상과_포트와_uvicorn이_들어_있어요():
    command = server_command("sample_app.app:app", 8123)
    assert "uvicorn" in command
    assert "sample_app.app:app" in command
    assert command[command.index("--port") + 1] == "8123"


def test_server_env_WEB_CONCURRENCY를_지워요():
    base = {"PATH": "x", "WEB_CONCURRENCY": "4"}
    env = server_env(base, {"SAMPLE_USE_CACHE": "1"})
    assert "WEB_CONCURRENCY" not in env
    assert env["PATH"] == "x"
    assert env["SAMPLE_USE_CACHE"] == "1"
    assert base == {"PATH": "x", "WEB_CONCURRENCY": "4"}  # 원본은 그대로


def test_phase_wall_seconds_마지막_완료_시각의_최댓값_빼기_시작():
    assert phase_wall_seconds(10.0, [11.0, 13.5, 12.0]) == pytest.approx(3.5)


def test_phase_wall_seconds_완료_시각이_하나여도_계산해요():
    assert phase_wall_seconds(2.0, [2.5]) == pytest.approx(0.5)


def phase_rows(text, heading):
    """results.md에서 heading이 든 제목 아래 표의 데이터 행을 셀 목록으로 돌려줘요."""
    lines = text.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("#") and heading in line)
    rows = []
    for line in lines[start + 1 :]:
        if line.startswith("#"):
            break
        if line.startswith("|"):
            cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
            if cells[0].startswith("모드") or set(cells[0]) <= set("-: "):
                continue
            rows.append(cells)
    return rows


def test_cache_effect_전체_실행_두_모드의_행과_hit_ratio_원본_호출이_맞아요(tmp_path):
    out = tmp_path / "cache_effect.md"
    main(["--items", "3", "--requests", "6", "--concurrency", "2", "--out", str(out)])

    assert out.exists()
    text = out.read_text(encoding="utf-8")
    cold = phase_rows(text, "첫 요청")
    warm = phase_rows(text, "이후 요청")
    # 열: 모드 | 반복 | 요청 | 에러 | p50 | p95 | 최대 | 처리량 | hit ratio | 원본 호출
    assert len(cold) == 2 and len(warm) == 2
    cold_by_mode = {row[0]: row for row in cold}
    warm_by_mode = {row[0]: row for row in warm}
    assert set(cold_by_mode) == set(warm_by_mode) and len(cold_by_mode) == 2
    off = next(mode for mode in cold_by_mode if "끔" in mode or "off" in mode.lower())
    on = next(mode for mode in cold_by_mode if mode != off)

    # 요청 수와 에러
    assert (cold_by_mode[off][2], warm_by_mode[off][2]) == ("3", "6")
    assert (cold_by_mode[on][2], warm_by_mode[on][2]) == ("3", "6")
    assert all(row[3] == "0" for row in cold + warm)

    # hit ratio: 끄기는 항상 0, 켜기는 첫 요청 0(전부 miss), 이후 요청 1.0(3개가 이미 캐시에 있어요)
    assert float(cold_by_mode[off][8]) == 0.0
    assert float(warm_by_mode[off][8]) == 0.0
    assert float(cold_by_mode[on][8]) == 0.0
    assert float(warm_by_mode[on][8]) == 1.0

    # 원본 호출: 끄기는 3 + 6 = 9번, 켜기는 첫 요청 3번뿐
    assert int(cold_by_mode[off][9]) + int(warm_by_mode[off][9]) == 9
    assert int(cold_by_mode[on][9]) + int(warm_by_mode[on][9]) == 3
    assert int(warm_by_mode[on][9]) == 0


def test_cache_effect_결과_파일에_캐시_켬_TTL_구간이_기록돼요(tmp_path):
    out = tmp_path / "cache_effect.md"
    main(["--items", "3", "--requests", "6", "--concurrency", "2", "--out", str(out)])

    # 구현이 맞출 약속: 캐시 켬 실행마다 "TTL 구간"이 든 줄이 하나씩 있고, 끔 실행에는 없어요.
    lines = [line for line in out.read_text(encoding="utf-8").splitlines() if "TTL 구간" in line]
    assert len(lines) == 1
    assert "켬" in lines[0] and "끔" not in lines[0]
