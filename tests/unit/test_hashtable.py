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
