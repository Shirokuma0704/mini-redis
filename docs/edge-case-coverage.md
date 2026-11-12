# 엣지 케이스 테스트 지도

명세 "제약과 엣지 케이스" 표의 모든 줄이 어떤 테스트로 확인되는지 적은 표예요. 이슈 #15에서 정리했어요. 테스트 이름은 `파일::함수` 형태이고, 매개변수를 쓰는 테스트는 기본 이름만 적었어요.

## 표 1. 명세 "제약과 엣지 케이스" 26줄

| 상황 | 처리 | 테스트 | 비고 |
| --- | --- | --- | --- |
| 빈 키, 256바이트 초과 키 | 400 `INVALID_KEY` | `tests/unit/test_edge_cases.py::test_store_set_invalid_key_raises_400_invalid_key_and_stores_nothing`<br>`tests/functional/test_api_keys.py::test_put_key_over_256_bytes_returns_400_invalid_key`<br>`tests/unit/test_store_keys.py::test_commands_empty_key_raise_invalid_key` | 빈 키는 HTTP 경로로 들어오지 못해요. `PUT /keys/`는 307로 `/keys`로 돌려보내져요. 그래서 빈 키의 400 `INVALID_KEY`는 Store 단계에서만 확인해요. |
| 키에 `/`, 공백, 한글 | 퍼센트 인코딩, 디코딩 후 길이 검사 | `tests/functional/test_api_keys.py::test_key_percent_encoding_roundtrips_through_all_key_endpoints`<br>`tests/functional/test_api_keys.py::test_put_hangul_key_over_256_bytes_checked_after_decoding`<br>`tests/unit/test_store_keys.py::test_set_key_with_slash_space_and_hangul_roundtrips` | |
| 값 1MB 초과 | 413 `VALUE_TOO_LARGE` | `tests/functional/test_api_keys.py::test_put_value_over_1mb_returns_413`<br>`tests/functional/test_api_keys.py::test_put_value_exactly_1mb_returns_200`<br>`tests/unit/test_store_keys.py::test_set_value_over_1mb_raises_413_and_stores_nothing` | |
| 빈 문자열 값 | 허용, 조회 시 `""` (없음과 구분) | `tests/functional/test_api_keys.py::test_put_empty_string_value_returns_empty_not_404`<br>`tests/unit/test_store_keys.py::test_set_empty_string_value_returns_empty_not_missing` | |
| TTL이 정수가 아님 (1.5, "abc") | 400 `INVALID_TTL` | `tests/functional/test_api_keys.py::test_put_invalid_ttl_returns_400_invalid_ttl_and_stores_nothing`<br>`tests/functional/test_api_keys.py::test_expire_invalid_or_missing_ttl_returns_400_invalid_ttl_and_keeps_key` | |
| SET에 0 이하 TTL | 400 `INVALID_TTL` | `tests/functional/test_api_keys.py::test_put_invalid_ttl_returns_400_invalid_ttl_and_stores_nothing` | |
| EXPIRE에 0 이하 TTL | 즉시 삭제 | `tests/functional/test_api_keys.py::test_expire_zero_or_negative_ttl_deletes_key`<br>`tests/unit/test_store_ttl.py::test_expire_zero_or_negative_deletes_now_returns_true` | |
| 없는 키에 EXPIRE, DEL | `false` (에러 아님, 키가 새로 생기지 않음) | `tests/functional/test_api_keys.py::test_expire_missing_key_returns_updated_false_and_creates_nothing`<br>`tests/functional/test_api_keys.py::test_delete_missing_returns_false_not_error`<br>`tests/unit/test_store_keys.py::test_delete_missing_key_returns_false_without_creating` | |
| TTL 없이 SET으로 덮어쓰기 | 기존 TTL 제거 | `tests/functional/test_api_keys.py::test_put_without_ttl_overwrite_removes_old_ttl`<br>`tests/unit/test_store_keys.py::test_set_without_expire_overwrite_removes_old_ttl`<br>`tests/unit/test_store_expiry.py::test_expire_tick_overwrite_without_ttl_survives_old_wheel_item` | |
| 만료 직후 GET | 404, 원래 없던 키와 구분하지 않음 | `tests/functional/test_api_keys.py::test_get_after_ttl_passed_returns_404`<br>`tests/unit/test_store_keys.py::test_get_at_expire_time_raises_404`<br>`tests/unit/test_store_keys.py::test_get_expired_key_is_removed_lazily` | HTTP 테스트는 상태 코드만 확인하고, 없던 키의 에러 코드와 직접 비교하지는 않아요. 단위 테스트는 둘 다 같은 `KeyNotFound`예요. |
| 만료 후 같은 키 재저장 | 새 키로 저장, 낡은 휠 항목은 무시됨 | `tests/unit/test_store_expiry.py::test_expire_tick_expired_then_resaved_with_ttl_uses_new_expiry`<br>`tests/unit/test_store_keys.py::test_set_after_expire_stores_as_new_key`<br>`tests/unit/test_store_expiry.py::test_expire_tick_deleted_key_resaved_without_ttl_survives` | HTTP로 확인하는 테스트는 없어요. |
| 정수가 아닌 값에 INCR | 409 `NOT_INTEGER` | `tests/functional/test_api_keys.py::test_incr_non_integer_value_returns_409_not_integer`<br>`tests/unit/test_store_incr.py::test_incr_non_integer_value_raises_409_and_keeps_value` | |
| 없는 키에 INCR | 0에서 시작해 결과 1 | `tests/functional/test_api_keys.py::test_incr_missing_key_returns_one`<br>`tests/unit/test_store_incr.py::test_incr_missing_key_starts_from_zero_returns_one` | |
| 여러 스레드 동시 INCR | 최종값 = 횟수 합 | `tests/unit/test_store_incr.py::test_incr_concurrent_10_threads_1000_times_final_equals_total`<br>`tests/unit/test_store_incr.py::test_incr_concurrent_mixed_amounts_final_equals_sum` | Store 단계에서만 확인해요. |
| 같은 키에 동시 PUT | 락을 잡은 순서대로 처리, 최종값은 보낸 값 중 하나 | `tests/unit/test_edge_cases.py::test_store_set_concurrent_same_key_final_value_is_one_sent_and_log_follows_lock_order` | Store 단계에서만 확인해요. |
| 빈 prefix로 삭제 | 400 `EMPTY_PREFIX` | `tests/functional/test_api_bulk.py::test_delete_prefix_empty_returns_400_empty_prefix`<br>`tests/unit/test_store_bulk.py::test_delete_prefix_empty_raises_400_and_deletes_nothing` | |
| confirm 없는 FLUSH | 400 `CONFIRM_REQUIRED` | `tests/functional/test_api_bulk.py::test_flush_without_confirm_returns_400_confirm_required`<br>`tests/unit/test_store_bulk.py::test_flush_without_true_confirm_raises_400_and_keeps_data` | |
| `ns`를 붙인 SET에 TTL 없음 | 400 `TTL_REQUIRED` | `tests/functional/test_api_namespace.py::test_namespace_put_without_ttl_returns_400_ttl_required`<br>`tests/unit/test_store_namespace.py::test_set_ns_without_ttl_raises_ttl_required` | |
| `ns`를 붙인 SET에 lease도 버전도 없음 | 400 `GUARD_REQUIRED` | `tests/functional/test_api_namespace.py::test_namespace_put_without_lease_or_version_returns_400_guard_required`<br>`tests/unit/test_store_namespace.py::test_set_ns_without_lease_or_version_raises_guard_required` | |
| `ns`를 붙인 SET의 버전이 현재와 다름 | 409 `VERSION_MISMATCH` | `tests/functional/test_api_namespace.py::test_namespace_slow_put_between_invalidate_returns_409_version_mismatch`<br>`tests/unit/test_store_namespace.py::test_set_ns_slow_put_between_invalidate_raises_version_mismatch` | |
| 무효화되거나 만료된 lease로 PUT | 409 `LEASE_INVALID` | `tests/functional/test_api_lease.py::test_lease_put_after_delete_returns_409_and_stores_nothing`<br>`tests/unit/test_store_lease.py::test_set_with_token_after_lease_ttl_raises_lease_invalid`<br>`tests/functional/test_api_namespace.py::test_namespace_lease_put_after_invalidate_returns_409_lease_invalid` | 만료된 토큰의 HTTP 테스트(`tests/functional/test_api_lease.py::test_lease_after_10s_reissued_and_old_token_rejected`)는 상태 409만 확인하고 에러 코드는 단위 테스트에서 확인해요. |
| 키 개수 상한 초과 쓰기 | 507 `CAPACITY_EXCEEDED` | `tests/functional/test_api_keys.py::test_put_capacity_exceeded_returns_507`<br>`tests/unit/test_store_keys.py::test_set_new_key_over_capacity_raises_507`<br>`tests/unit/test_store.py::test_check_capacity_new_key_at_limit_raises_507` | |
| AOF 마지막 줄이 잘림 | 그 줄만 버리고 기동 | `tests/unit/test_recovery.py::test_recover_truncated_last_line_is_dropped_and_counted`<br>`tests/unit/test_recovery.py::test_recover_truncated_tail_in_non_last_file_is_dropped_and_next_file_applied` | `recover()` 단계까지만 확인해요. 잘린 줄이 있는 채로 앱을 띄우는 테스트는 없어요. |
| 스냅샷 도중 죽음 | 이전 스냅샷 + 이전 AOF로 복구 | `tests/unit/test_recovery.py::test_recover_leftover_snapshot_tmp_is_ignored`<br>`tests/unit/test_recovery.py::test_recover_old_aofs_not_yet_deleted_after_snapshot_replace_are_ignored`<br>`tests/unit/test_snapshot.py::test_write_snapshot_fsync_failure_raises_and_keeps_existing_snapshot_untouched` | |
| 서버 재시작 | 만료된 키는 복구하지 않음, 버전 테이블은 유지 | `tests/unit/test_persistence_startup.py::test_key_expired_at_recovery_time_is_not_loaded`<br>`tests/unit/test_persistence_nsver.py::test_restart_keeps_namespace_version_and_versioned_key`<br>`tests/functional/test_restart.py::test_http_restart_keeps_data` | 두 가지(만료 키 제외, 버전 유지)는 서로 다른 테스트에서 확인해요. |
| 시스템 시계가 뒤로 감 | 만료가 늦어지거나 일찍 일어남 (알려진 한계) | `tests/unit/test_edge_cases.py::test_ttl_clock_went_back_expiry_is_late_known_limitation`<br>`tests/unit/test_edge_cases.py::test_ttl_clock_jumped_forward_expiry_is_early_known_limitation` | 알려진 한계를 그대로 고정한 테스트예요. |

## 표 2. #15 체크리스트 세부 항목

해시테이블, TTL, 타이밍 휠, INCR, 일괄 명령 다섯 줄만 나눴어요.

| 항목 | 테스트 | 비고 |
| --- | --- | --- |
| 해시테이블: 충돌 강제(같은 버킷) | `tests/unit/test_hashtable.py::test_collision_keys_in_same_bucket_all_retrievable` | |
| 해시테이블: 체인 처음 삭제 | `tests/unit/test_hashtable.py::test_delete_chain_head_unlinks_head` | |
| 해시테이블: 체인 중간 삭제 | `tests/unit/test_hashtable.py::test_delete_chain_middle_unlinks_middle` | |
| 해시테이블: 체인 끝 삭제 | `tests/unit/test_hashtable.py::test_delete_chain_tail_unlinks_tail` | |
| 해시테이블: 리사이즈 전후 전체 조회 | `tests/unit/test_edge_cases.py::test_hashtable_resize_all_keys_readable_just_before_and_just_after`<br>`tests/unit/test_hashtable.py::test_resize_all_keys_retrievable_before_and_after` | 새 테스트는 리사이즈 직전과 직후에 모든 키를 읽어요. 기본 해시와 한 버킷 체인 두 경우를 확인해요. |
| TTL: 만료 직전·직후 GET | `tests/unit/test_store_keys.py::test_get_before_expire_returns_value`<br>`tests/unit/test_store_keys.py::test_get_at_expire_time_raises_404`<br>`tests/unit/test_store_ttl.py::test_ttl_just_before_and_at_expiry_returns_1_then_minus_2` | 직전과 직후가 서로 다른 테스트에 나뉘어 있어요. |
| TTL: 덮어쓰기로 TTL 제거 | `tests/unit/test_store_keys.py::test_set_without_expire_overwrite_removes_old_ttl`<br>`tests/unit/test_store_ttl.py::test_ttl_overwrite_without_ttl_returns_minus_1` | |
| TTL: EXPIRE가 남은 시간에 더하지 않음 | `tests/unit/test_store_ttl.py::test_expire_recomputes_from_now_not_added_to_remaining` | |
| TTL: EXPIRE 0 이하 | `tests/unit/test_store_ttl.py::test_expire_zero_or_negative_deletes_now_returns_true`<br>`tests/unit/test_store_ttl.py::test_expire_zero_on_missing_key_returns_false` | |
| 타이밍 휠: 낡은 항목 무시 | `tests/unit/test_timing_wheel.py::test_wheel_stale_item_after_ttl_change_ignored`<br>`tests/unit/test_store_expiry.py::test_expire_tick_overwrite_without_ttl_survives_old_wheel_item`<br>`tests/unit/test_store_expiry.py::test_expire_tick_deleted_key_resaved_without_ttl_survives` | |
| 타이밍 휠: 단 간 내려보내기 | `tests/unit/test_timing_wheel.py::test_wheel_overflow_item_cascades_down_deleted_exactly_at_expiry`<br>`tests/unit/test_timing_wheel.py::test_wheel_cascade_l1_holds_only_cascaded_ticks_deleted_at_exact_tick`<br>`tests/unit/test_store_expiry.py::test_expire_tick_far_future_ttl_beyond_wheel_range_deleted_on_time` | |
| 타이밍 휠: tick당 삭제 상한 | `tests/unit/test_timing_wheel.py::test_wheel_tick_limit_reached_rest_deleted_next_tick`<br>`tests/unit/test_timing_wheel.py::test_wheel_tick_limit_counts_stale_items_too`<br>`tests/unit/test_store_expiry.py::test_expire_tick_over_limit_deletes_at_most_limit_per_tick` | |
| INCR: 없는 키 | `tests/unit/test_store_incr.py::test_incr_missing_key_starts_from_zero_returns_one`<br>`tests/unit/test_store_incr.py::test_incr_missing_key_has_no_ttl` | |
| INCR: 정수 아닌 값 | `tests/unit/test_store_incr.py::test_incr_non_integer_value_raises_409_and_keeps_value` | |
| INCR: TTL 유지 | `tests/unit/test_store_incr.py::test_incr_keeps_existing_ttl`<br>`tests/unit/test_store_expiry.py::test_expire_tick_incr_keeps_ttl_and_still_expires` | |
| INCR: 스레드 10개 × 1,000회 동시 INCR | `tests/unit/test_store_incr.py::test_incr_concurrent_10_threads_1000_times_final_equals_total`<br>`tests/unit/test_store_incr.py::test_incr_concurrent_mixed_amounts_final_equals_sum` | |
| 일괄 명령: KEYS가 만료 키 제외 | `tests/unit/test_store_bulk.py::test_keys_excludes_expired_keys`<br>`tests/unit/test_store_bulk.py::test_keys_removes_expired_keys_lazily` | |
| 일괄 명령: prefix 삭제 개수 | `tests/unit/test_store_bulk.py::test_delete_prefix_returns_deleted_count`<br>`tests/unit/test_store_bulk.py::test_delete_prefix_expired_keys_removed_but_not_counted`<br>`tests/unit/test_store_bulk.py::test_delete_prefix_exact_key_equal_to_prefix_is_deleted` | |
| 일괄 명령: FLUSH 후 빈 상태와 버전 유지 | `tests/unit/test_store_bulk.py::test_flush_confirm_true_empties_store`<br>`tests/unit/test_store_namespace.py::test_flush_keeps_version_table`<br>`tests/unit/test_store_aof_nsver.py::test_store_nsver_flush_logs_only_flush_record_and_keeps_versions` | 빈 상태와 버전 유지는 서로 다른 테스트에서 확인해요. |

## 확인해 볼 것

- 빈 키의 HTTP 동작: `PUT /keys/`는 307로 `/keys`로 돌려보내져서, 빈 키의 `INVALID_KEY`는 HTTP 단계에서 확인할 수 없어요. API(#37) 쪽 의견이 필요해요.
- `tests/unit/test_store_expiry.py` 155번째 줄 근처의 `time.sleep(0.005)`: `test_expiry_thread_started_removes_expired_keys_in_background`가 실제 만료 스레드를 기다리느라 최대 2초까지 sleep으로 확인해요. 명세는 시간을 다루는 테스트에 sleep 대신 가짜 시계를 쓰도록 적었어요.
- 이름 규칙 `test_<대상>_<상황>_<기대결과>`에서 벗어나 보이는 이름이에요. 기계로 센 게 아니라 읽고 판단한 목록이고, 이름을 바꿀지는 각 테스트 담당자에게 맡겨요.
  - `tests/unit/test_hashtable.py::test_put_stores_expire_at_ms`
  - `tests/unit/test_hashtable.py::test_items_yields_every_entry_once`
  - `tests/unit/test_hashtable.py::test_resize_never_shrinks_after_deletes`
  - `tests/unit/test_hashtable.py::test_put_delta_ms_stored_and_default_none`
  - `tests/unit/test_hashtable.py::test_resize_keeps_delta_ms`
  - `tests/unit/test_store_bulk.py::test_keys_prefix_is_case_sensitive`
  - `tests/unit/test_store_bulk.py::test_flush_swaps_table_object`
  - `tests/unit/test_store_ttl.py::test_expire_keeps_value`
  - `tests/unit/test_store_keys.py::test_exists_existing_missing_and_expired`
  - `tests/unit/test_store_incr.py::test_incr_negative_stored_value_works`

## Lease·네임스페이스·영속성

이 세 영역 체크리스트는 #16에서 다뤄요. 표 1의 해당 줄은 이미 있는 테스트로 채웠어요.
