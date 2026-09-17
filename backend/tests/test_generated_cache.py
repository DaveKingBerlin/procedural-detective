"""Phase 13 — bounded, versioned generated-definition cache tests.

Proves: deterministic keying, idempotent put, LRU eviction, per-entry byte
cap, version-version filtering (an entry from older compiler/schema versions is
never reused), thread safety and metadata that stores ONLY canonical metadata
metadata (never the raw prompt/spec).
"""

from __future__ import annotations

import threading
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.assets.compiler import (
    COMPILER_VERSION,
    SCHEMA_VERSION,
    compile_asset_spec,
    spec_hash_for_cache,
)
from app.assets.generated_cache import (
    CacheBoundError,
    CacheEntry,
    CacheEntryMetadata,
    GeneratedAssetCache,
)
from app.assets.specs import parse_asset_spec


def _spec(canonical_name="Cache Prop", **overrides):
    base = {
        "canonicalName": canonical_name,
        "category": "decor",
        "subtype": None,
        "dimensions": {"x": 0.2, "y": 0.2, "z": 0.2},
        "parts": [
            {
                "id": "part_00",
                "role": "base",
                "primitive": "box",
                "transform": {
                    "position": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "rotation": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "scale": {"x": 0.2, "y": 0.2, "z": 0.2},
                },
                "material": "plastic",
            }
        ],
    }
    base.update(overrides)
    return base


def _definition(canonical_name="Cache Prop"):
    return compile_asset_spec(parse_asset_spec(_spec(canonical_name), non_throwing=False))


def test_put_get_round_trip_and_deterministic_key():
    cache = GeneratedAssetCache(max_entries=16)
    definition = _definition()
    key = spec_hash_for_cache(parse_asset_spec(_spec(), non_throwing=False))
    cache.put(key, definition, canonical_name="Cache Prop", category="decor")
    hit = cache.get(key)
    assert hit is not None
    cached_def, metadata = hit
    assert cached_def is definition
    assert metadata.canonical_name == "Cache Prop"
    assert metadata.category == "decor"
    assert metadata.compiler_version == COMPILER_VERSION
    assert metadata.schema_version == SCHEMA_VERSION
    assert metadata.created_at > 0
    assert metadata.used_count == 1  # the get itself counted
    assert key in cache
    assert len(cache) == 1
    # same input -> same key (a second spec parse yields the same hash)
    assert spec_hash_for_cache(parse_asset_spec(_spec(), non_throwing=False)) == key


def test_get_unknown_returns_none():
    cache = GeneratedAssetCache(max_entries=8)
    assert cache.get("missing-key") is None
    assert "missing-key" not in cache


def test_put_is_idempotent():
    cache = GeneratedAssetCache(max_entries=8)
    definition = _definition()
    key = spec_hash_for_cache(parse_asset_spec(_spec(), non_throwing=False))
    cache.put(key, definition, canonical_name="A", category="decor")
    cache.put(key, definition, canonical_name="A", category="decor")
    cache.put(key, definition, canonical_name="A", category="decor")
    assert len(cache) == 1


def test_lru_eviction_bounds_entries():
    cache = GeneratedAssetCache(max_entries=4)
    for i in range(6):
        definition = _definition(f"Prop {i}")
        key = spec_hash_for_cache(parse_asset_spec(_spec(f"Prop {i}"), non_throwing=False))
        cache.put(key, definition)
    assert len(cache) == 4
    # the two OLDEST entries were evicted
    for i in (0, 1):
        key = spec_hash_for_cache(parse_asset_spec(_spec(f"Prop {i}"), non_throwing=False))
        assert key not in cache
        assert cache.get(key) is None
    # the newest survive
    for i in (2, 3, 4, 5):
        key = spec_hash_for_cache(parse_asset_spec(_spec(f"Prop {i}"), non_throwing=False))
        assert cache.get(key) is not None


def test_get_refreshes_lru_order():
    cache = GeneratedAssetCache(max_entries=2)
    keys = []
    for i in range(2):
        definition = _definition(f"Prop {i}")
        key = spec_hash_for_cache(parse_asset_spec(_spec(f"Prop {i}"), non_throwing=False))
        keys.append(key)
        cache.put(key, definition)
    # touch the OLDEST entry, then insert a third -> the LRU must evict the
    # NOT-touched one.
    assert cache.get(keys[0]) is not None
    d3 = _definition("Prop 2")
    k3 = spec_hash_for_cache(parse_asset_spec(_spec("Prop 2"), non_throwing=False))
    cache.put(k3, d3)
    assert keys[1] not in cache
    assert cache.get(keys[0]) is not None


def test_per_entry_byte_cap_refuses_oversized_spec():
    cache = GeneratedAssetCache(max_entries=8, max_entry_bytes=64)
    definition = _definition()
    key = "some-hash"
    with pytest.raises(CacheBoundError):
        cache.put(key, definition, spec_bytes=b"x" * 65)
    assert key not in cache
    # In-bounds spec bytes are accepted.
    cache.put(key, definition, spec_bytes=b"small")
    assert key in cache


def test_put_refuses_stale_version_definition():
    from dataclasses import replace

    cache = GeneratedAssetCache(max_entries=8)
    definition = _definition()
    stale = replace(definition, compiler_version=999, schema_version=999)
    with pytest.raises(CacheBoundError):
        cache.put("hash", stale)


def test_get_ignores_stale_version_entries():
    cache = GeneratedAssetCache(max_entries=8)
    definition = _definition()
    stale = CacheEntry(
        definition=definition,
        metadata=CacheEntryMetadata(
            canonical_name="Old",
            category="decor",
            compiler_version=999,
            schema_version=999,
            created_at=1.0,
            used_count=0,
        ),
    )
    # Direct insert bypasses put() on purpose: this test exercises the version
    # FILTER on reads (an entry from compilerVersion 1 is never reused for v2).
    cache._entries["stale-key"] = stale
    assert cache.get("stale-key") is None
    assert "stale-key" not in cache


def test_metadata_never_stores_raw_spec():
    cache = GeneratedAssetCache(max_entries=8)
    definition = _definition()
    key = spec_hash_for_cache(parse_asset_spec(_spec("Secret Name"), non_throwing=False))
    cache.put(
        key, definition,
        spec_bytes=parse_asset_spec(_spec("Secret Name"), non_throwing=False).canonical_name.encode(),
        canonical_name="Secret Name",
        category="decor",
    )
    hit = cache.get(key)
    assert hit is not None
    _cached_def, metadata = hit
    # Only the documented metadata fields exist; NO raw spec / prompt fields.
    fields = {name for name in metadata.__dataclass_fields__}
    assert fields == {
        "canonical_name", "category", "compiler_version",
        "schema_version", "created_at", "used_count",
    }
    assert metadata.canonical_name == "Secret Name"


def test_thread_safety_many_puts_and_gets():
    cache = GeneratedAssetCache(max_entries=256)
    errors: list[Exception] = []
    barrier = threading.Barrier(8)

    def worker(worker_id: int) -> None:
        try:
            barrier.wait()
            for i in range(40):
                name = f"Thread{worker_id}-Prop{i}"
                definition = _definition(name)
                key = spec_hash_for_cache(parse_asset_spec(_spec(name), non_throwing=False))
                cache.put(key, definition, canonical_name=name, category="decor")
                got = cache.get(key)
                assert got is not None
        except Exception as exc:  # noqa: BLE001 - collected for the assertion
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert not errors
    assert len(cache) <= 256  # bounded under concurrency


def test_clear_empties_cache():
    cache = GeneratedAssetCache(max_entries=8)
    definition = _definition()
    key = spec_hash_for_cache(parse_asset_spec(_spec(), non_throwing=False))
    cache.put(key, definition)
    assert len(cache) == 1
    cache.clear()
    assert len(cache) == 0
    assert cache.get(key) is None