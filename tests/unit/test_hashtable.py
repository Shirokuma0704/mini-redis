import random

import pytest

from mini_redis.hashtable import HashTable, fnv1a_64


def same_bucket(_key):
    return 0  # 모든 키를 같은 버킷으로 (충돌 강제)


def chain_keys(table, idx=0):
    out, node = [], table._buckets[idx]
    while node is not None:
        out.append(node.key)
        node = node.next
    return out


def test_fnv1a_64_known_vectors_match_reference():
    assert fnv1a_64("") == 0xCBF29CE484222325
    assert fnv1a_64("a") == 0xAF63DC4C8601EC8C
    assert fnv1a_64("foobar") == 0x85944171F73967E8


def test_hashtable_init_non_power_of_two_raises():
    with pytest.raises(ValueError):
        HashTable(capacity=10)


def test_get_missing_key_returns_none():
    assert HashTable().get("nope") is None


def test_put_new_key_then_get_returns_value():
    t = HashTable()
    assert t.put("a", "1") is True
    assert t.get("a").value == "1"
    assert t.size == 1


def test_put_existing_key_replaces_value_and_expire():
    t = HashTable()
    t.put("a", "1", expire_at_ms=100)
    assert t.put("a", "2") is False
    e = t.get("a")
    assert (e.value, e.expire_at_ms, t.size) == ("2", None, 1)


def test_put_stores_expire_at_ms():
    t = HashTable()
    t.put("a", "1", expire_at_ms=500)
    assert t.get("a").expire_at_ms == 500


def test_collision_keys_in_same_bucket_all_retrievable():
    t = HashTable(capacity=1024, hash_fn=same_bucket)
    for k in "abc":
        t.put(k, k.upper())
    assert chain_keys(t) == ["c", "b", "a"]  # 머리 삽입
    assert [t.get(k).value for k in "abc"] == ["A", "B", "C"]


def test_delete_chain_head_unlinks_head():
    t = HashTable(capacity=1024, hash_fn=same_bucket)
    for k in "abc":
        t.put(k, k)
    assert t.delete("c") is True
    assert chain_keys(t) == ["b", "a"]


def test_delete_chain_middle_unlinks_middle():
    t = HashTable(capacity=1024, hash_fn=same_bucket)
    for k in "abc":
        t.put(k, k)
    assert t.delete("b") is True
    assert chain_keys(t) == ["c", "a"]


def test_delete_chain_tail_unlinks_tail():
    t = HashTable(capacity=1024, hash_fn=same_bucket)
    for k in "abc":
        t.put(k, k)
    assert t.delete("a") is True
    assert chain_keys(t) == ["c", "b"]
    assert t.size == 2


def test_delete_missing_key_returns_false_and_keeps_size():
    t = HashTable()
    t.put("a", "1")
    assert t.delete("x") is False
    assert t.size == 1


def test_resize_exceeding_load_factor_doubles_capacity():
    t = HashTable(capacity=16)
    for i in range(12):  # 12/16 = 0.75, 초과 아님
        t.put(f"k{i}", i)
    assert t.capacity == 16
    t.put("k12", 12)  # 13/16 > 0.75
    assert t.capacity == 32


def test_resize_all_keys_retrievable_before_and_after():
    t = HashTable()
    for i in range(5000):
        t.put(f"key:{i}", i)
    assert t.capacity > 16
    assert t.size == 5000
    assert all(t.get(f"key:{i}").value == i for i in range(5000))


def test_resize_never_shrinks_after_deletes():
    t = HashTable()
    for i in range(100):
        t.put(str(i), i)
    cap = t.capacity
    for i in range(100):
        t.delete(str(i))
    assert (t.capacity, t.size) == (cap, 0)


def test_items_yields_every_entry_once():
    t = HashTable()
    for i in range(300):
        t.put(f"k{i}", i)
    keys = [e.key for e in t.items()]
    assert len(keys) == 300 and len(set(keys)) == 300


def test_items_delete_during_iteration_is_safe():
    t = HashTable()
    for i in range(100):
        t.put(f"k{i}", i)
    for e in t.items():
        t.delete(e.key)
    assert t.size == 0


def test_put_delta_ms_stored_and_default_none():
    t = HashTable()
    t.put("a", "1")
    t.put("b", "2", delta_ms=150)
    assert (t.get("a").delta_ms, t.get("b").delta_ms) == (None, 150)


def test_put_overwrite_replaces_delta_ms():
    t = HashTable()
    t.put("a", "1", delta_ms=150)
    t.put("a", "2")
    assert t.get("a").delta_ms is None


def test_resize_keeps_delta_ms():
    t = HashTable()
    for i in range(200):
        t.put(f"k{i}", i, delta_ms=i)
    assert all(t.get(f"k{i}").delta_ms == i for i in range(200))


# --- 점진적 리해싱 (#24) ---


def fill_mid_rehash(n_old=1024, rehash_buckets=1):
    """옛 배열 n_old칸에서 리사이즈를 막 시작한 테이블과 넣은 키 목록."""
    t = HashTable(capacity=n_old, rehash_buckets=rehash_buckets)
    keys = [f"k{i}" for i in range(n_old * 3 // 4 + 1)]
    for k in keys:
        t.put(k, k)
    assert t._old is not None
    return t, keys


def test_put_crossing_load_factor_moves_only_rehash_buckets():
    t = HashTable(capacity=16, rehash_buckets=2)
    for i in range(13):  # 13/16 > 0.75
        t.put(f"k{i}", i)
    assert (t.capacity, t._rehash_idx) == (32, 2)  # 새 배열만 만들고 옛 버킷은 2개만 옮겼다
    for _ in range(6):
        t.get("nope")
    assert t._old is not None
    t.get("nope")  # 남은 14개 / 2 = 7번째 명령에서 끝난다
    assert t._old is None
    assert all(t.get(f"k{i}").value == i for i in range(13))


def test_rehash_buckets_larger_than_table_resizes_at_once():
    t = HashTable(capacity=16, rehash_buckets=1 << 62)  # 측정의 일괄 리사이즈 모드
    for i in range(13):
        t.put(f"k{i}", i)
    assert (t.capacity, t._old) == (32, None)


def test_hashtable_init_non_positive_rehash_buckets_raises():
    with pytest.raises(ValueError):
        HashTable(rehash_buckets=0)



def test_put_burst_outrunning_rehash_keeps_all_keys():
    t = HashTable(capacity=16, rehash_buckets=1)  # 버킷 1개씩이면 put만 이어질 때 새 배열이 옮기기 전에 다시 0.75를 넘는다
    for i in range(5000):
        t.put(f"k{i}", i)
    assert t.size == 5000
    assert all(t.get(f"k{i}").value == i for i in range(5000))

@pytest.mark.parametrize("seed", range(20))
def test_hashtable_random_ops_during_rehash_match_dict_model(seed):
    rng = random.Random(seed)
    weak = lambda k: fnv1a_64(k) & 0x3F  # 해시 64가지: 체인이 길어 체인 중간 삭제·이동이 자주 일어난다
    t = HashTable(capacity=1, hash_fn=weak if seed % 2 else fnv1a_64, rehash_buckets=1)
    model = {}
    checked_items_mid_rehash = 0
    for step in range(3000):
        key = f"k{rng.randrange(10 + step // 4)}"  # 키 공간을 넓혀 가며 리사이즈가 계속 일어나게
        r = rng.random()
        if r < 0.45:
            value = rng.random()
            assert t.put(key, value) is (key not in model)
            model[key] = value
        elif r < 0.7:
            assert t.delete(key) is (model.pop(key, None) is not None)
        elif r < 0.97:
            e = t.get(key)
            assert (e.value if e else None) == model.get(key)
        else:
            checked_items_mid_rehash += t._old is not None
            assert sorted((e.key, e.value) for e in t.items()) == sorted(model.items())
        assert t.size == len(model)
    assert checked_items_mid_rehash > 0


@pytest.mark.parametrize("pick", [lambda i: True, lambda i: i % 3 == 0])
def test_items_delete_during_rehash_yields_each_once_and_deletes(pick):
    t, keys = fill_mid_rehash()
    for _ in range(300):  # 옛 배열 일부만 옮긴 상태로
        t.get("nope")
    seen = []
    for e in t.items():
        seen.append(e.key)
        if pick(int(e.key[1:])):
            t.delete(e.key)  # prefix 삭제가 만료 키를 지우는 것과 같은 모양
    assert sorted(seen) == sorted(keys)  # 두 번 나오거나 빠진 키가 없다
    kept = [k for k in keys if not pick(int(k[1:]))]
    assert t.size == len(kept)
    assert sorted(e.key for e in t.items()) == sorted(kept)
    assert all(t.get(k) is not None for k in kept)


def test_items_finished_or_broken_resumes_rehash():
    t, _ = fill_mid_rehash(n_old=16)
    for _ in t.items():
        break  # 중간에 멈춘 순회도 닫히면 리해싱을 다시 허락한다
    list(t.items())
    for _ in range(16):
        t.get("nope")
    assert t._old is None


# --- SIEVE 큐와 hand (#25) ---

def queue_keys(t):
    """FIFO 큐를 꼬리(가장 오래된 것)부터 머리까지."""
    keys, node = [], t._oldest
    while node is not None:
        keys.append(node.key)
        node = node.newer
    return keys


def evict(t):
    victim = t.sieve_victim()
    t.delete(victim.key)
    return victim.key


def test_sieve_victim_empty_table_returns_none():
    assert HashTable().sieve_victim() is None


def test_sieve_queue_put_appends_and_overwrite_keeps_position():
    t = HashTable()
    for k in "abc":
        t.put(k, k)
    t.put("a", "again")  # 덮어쓰기는 자리를 옮기지 않는다
    assert queue_keys(t) == ["a", "b", "c"]


def test_sieve_victim_skips_visited_clears_bit_and_picks_oldest_unvisited():
    t = HashTable()
    for k in "abc":
        t.put(k, k)
    t.get("a").visited = True
    assert evict(t) == "b"
    assert t.get("a").visited is False  # 한 번 살아남고 비트는 꺼진다
    assert queue_keys(t) == ["a", "c"]  # 살아남은 엔트리도 자리를 옮기지 않는다


def test_sieve_hand_resumes_where_it_stopped_not_from_oldest():
    t = HashTable()
    for k in "abcd":
        t.put(k, k)
    t.get("a").visited = True
    assert evict(t) == "b"
    # a는 이제 꺼져 있고 가장 오래됐지만, hand는 b 다음(c)에서 이어간다 (FIFO와 다른 점)
    assert evict(t) == "c"
    assert evict(t) == "d"
    assert evict(t) == "a"  # 머리를 지나 꼬리로 돌아온다


def test_sieve_all_visited_one_lap_clears_all_and_evicts_oldest():
    t = HashTable()
    for k in "abc":
        t.put(k, k)
        t.get(k).visited = True
    assert evict(t) == "a"
    assert [t.get(k).visited for k in "bc"] == [False, False]


def test_sieve_delete_hand_entry_moves_hand_to_newer():
    t = HashTable()
    for k in "abc":
        t.put(k, k)
    t.get("a").visited = True
    assert t.sieve_victim().key == "b"  # hand가 b에 있다
    t.delete("b")  # DEL 같은 다른 경로로 지워도
    t.put("d", "d")
    assert evict(t) == "c"  # hand는 지운 엔트리 바로 머리 쪽에서 이어간다


def test_sieve_delete_hand_at_newest_wraps_to_oldest():
    t = HashTable()
    t.put("a", "a")
    t.put("b", "b")
    t.get("a").visited = True
    assert evict(t) == "b"  # 머리를 내보내면 hand는 None
    t.put("c", "c")
    assert evict(t) == "a"  # 다음은 꼬리부터


def test_sieve_victim_not_deleted_is_picked_again():
    t = HashTable()
    for k in "ab":
        t.put(k, k)
    assert t.sieve_victim().key == "a"
    assert t.sieve_victim().key == "a"  # 지우지 못했으면(AOF 실패) 같은 키를 다시 고른다


def test_sieve_queue_survives_rehash_and_matches_insertion_order():
    t = HashTable(rehash_buckets=1)
    keys = [f"k{i}" for i in range(200)]  # 리사이즈와 점진적 리해싱을 여러 번 거친다
    for k in keys:
        t.put(k, k)
    for k in keys[::3]:
        t.delete(k)
    kept = [k for k in keys if k not in keys[::3]]
    assert queue_keys(t) == kept
    assert [evict(t) for _ in kept] == kept
    assert (t.size, t._oldest, t._newest) == (0, None, None)
