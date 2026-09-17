"""Phase 13 — bounded, versioned cache of generated asset definitions.

``GeneratedAssetCache`` stores validated, IMMUTABLE ``GeneratedAssetDefinition``
objects plus bounded metadata so repeated unknown-asset prompts never call the
spec provider twice (cache hit -> zero provider calls, proven by tests with a
counting provider).

Properties:

- **Keying**: the cache key is the version-free content hash of a normalized
  spec (``spec_hash_for_cache`` = sha256 of ``normalize_spec`` output). The
  same normalized spec ALWAYS maps to the same key (deterministic).
- **Bounded**: at most ``max_entries`` entries (LRU eviction) and a per-entry
  byte cap (~32 KiB) on the normalized spec text that produced the entry
  (refused, never cached, when exceeded).
- **Versioned**: every entry records the (compilerVersion, schemaVersion) that
  produced it; ``get`` ONLY returns entries matching the CURRENT
  ``(COMPILER_VERSION, SCHEMA_VERSION)`` — an entry from an older compiler is
  never reused for a newer compiler (the content-addressed assetId also embeds
  the versions, so the key would differ anyway; this is the explicit guard).
- **Immutable**: the stored definition is never mutated (frozen by ``compile``);
  ``get`` returns the SAME frozen instance shared with the published payload.
- **Thread-safe**: all mutations run under one lock (used by tests with
  threads); ``get`` is a read under the same lock so eviction orderings stay
  deterministic.
- **Metadata**: entries carry ONLY ``{canonicalName, category,
  compilerVersion, schemaVersion, createdAt (epoch), usedCount}`` — the raw
  prompt/spec text is NEVER stored (no diagnostics leakage).
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

from app.assets.compiler import (
    COMPILER_VERSION,
    SCHEMA_VERSION,
    GeneratedAssetDefinition,
)

# Default entry count cap (LRU) and per-entry byte cap on the normalized spec.
DEFAULT_MAX_ENTRIES = 128
DEFAULT_MAX_ENTRY_BYTES = 32 * 1024  # ~32 KiB per entry on the normalized spec


class CacheBoundError(ValueError):
    """A cache entry exceeded a documented bound (refused, never cached)."""


@dataclass(frozen=True)
class CacheEntryMetadata:
    """Bounded per-entry metadata (NO raw prompt/spec material)."""

    canonical_name: str | None = None
    category: str | None = None
    compiler_version: int = COMPILER_VERSION
    schema_version: int = SCHEMA_VERSION
    created_at: float = 0.0
    used_count: int = 0


@dataclass(frozen=True)
class CacheEntry:
    """One immutable cache entry (frozen definition + bounded metadata)."""

    definition: GeneratedAssetDefinition
    metadata: CacheEntryMetadata


class GeneratedAssetCache:
    """Thread-safe, bounded, versioned LRU cache keyed by spec content hash."""

    def __init__(
        self,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        max_entry_bytes: int = DEFAULT_MAX_ENTRY_BYTES,
        clock: Any = None,
    ) -> None:
        if isinstance(max_entries, bool) or not isinstance(max_entries, int):
            raise TypeError("GeneratedAssetCache.max_entries must be an int")
        if max_entries <= 0:
            raise ValueError("GeneratedAssetCache.max_entries must be > 0")
        self._max_entries = int(max_entries)
        if isinstance(max_entry_bytes, bool) or not isinstance(max_entry_bytes, int):
            raise TypeError("GeneratedAssetCache.max_entry_bytes must be an int")
        self._max_entry_bytes = int(max_entry_bytes)
        self._clock = clock if clock is not None else time.time
        self._lock = threading.RLock()
        self._entries: "OrderedDict[str, CacheEntry]" = OrderedDict()

    # -- public surface -----------------------------------------------------

    @property
    def max_entries(self) -> int:
        return self._max_entries

    @property
    def max_entry_bytes(self) -> int:
        return self._max_entry_bytes

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def get(self, key: str) -> tuple[GeneratedAssetDefinition, CacheEntryMetadata] | None:
        """Return the cached (frozen definition, metadata) for ``key``.

        Version guard: an entry whose (compilerVersion, schemaVersion) does NOT
        match the CURRENT constants is treated as a miss (an old-compiler entry
        is never reused for a newer compiler). A hit refreshes the LRU order and
        increments ``usedCount``.
        """
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            if (
                entry.metadata.compiler_version != COMPILER_VERSION
                or entry.metadata.schema_version != SCHEMA_VERSION
            ):
                return None  # stale version -> never reused
            self._entries.move_to_end(key)
            updated_metadata = CacheEntryMetadata(
                canonical_name=entry.metadata.canonical_name,
                category=entry.metadata.category,
                compiler_version=entry.metadata.compiler_version,
                schema_version=entry.metadata.schema_version,
                created_at=entry.metadata.created_at,
                used_count=entry.metadata.used_count + 1,
            )
            self._entries[key] = CacheEntry(definition=entry.definition, metadata=updated_metadata)
            return entry.definition, updated_metadata

    def put(
        self,
        key: str,
        definition: GeneratedAssetDefinition,
        *,
        spec_bytes: bytes | None = None,
        canonical_name: str | None = None,
        category: str | None = None,
        created_at: float | None = None,
    ) -> None:
        """Cache one immutable definition under ``key`` (idempotent).

        ``spec_bytes`` (the normalized spec text UTF-8) is length-capped at
        ``max_entry_bytes``; an oversized spec raises ``CacheBoundError`` and is
        NEVER cached. ``canonicalName``/``category`` are the ONLY metadata
        fields recorded (never the raw prompt/spec). Repeated ``put`` of the
        same key is idempotent: the existing entry is replaced in place.
        """
        if not isinstance(key, str) or not key:
            raise ValueError("cache key must be a non-empty string")
        if not isinstance(definition, GeneratedAssetDefinition):
            raise TypeError("cache put requires a GeneratedAssetDefinition")
        if spec_bytes is not None:
            if not isinstance(spec_bytes, (bytes, bytearray)):
                raise TypeError("spec_bytes must be bytes or None")
            if len(spec_bytes) > self._max_entry_bytes:
                raise CacheBoundError(
                    f"normalized spec exceeds the {self._max_entry_bytes} byte "
                    "per-entry cap"
                )
        now = float(self._clock()) if created_at is None else float(created_at)
        with self._lock:
            # Version guard: never cache under a stale version marker.
            if (
                definition.compiler_version != COMPILER_VERSION
                or definition.schema_version != SCHEMA_VERSION
            ):
                raise CacheBoundError(
                    "refusing to cache a definition from non-current versions"
                )
            if key in self._entries:
                old = self._entries[key].metadata
                self._entries.move_to_end(key)
                self._entries[key] = CacheEntry(
                    definition=definition,
                    metadata=CacheEntryMetadata(
                        canonical_name=canonical_name if canonical_name is not None else old.canonical_name,
                        category=category if category is not None else old.category,
                        compiler_version=definition.compiler_version,
                        schema_version=definition.schema_version,
                        created_at=old.created_at if old.created_at else now,
                        used_count=old.used_count,
                    ),
                )
                return
            self._entries[key] = CacheEntry(
                definition=definition,
                metadata=CacheEntryMetadata(
                    canonical_name=canonical_name,
                    category=category,
                    compiler_version=definition.compiler_version,
                    schema_version=definition.schema_version,
                    created_at=now,
                    used_count=0,
                ),
            )
            self._evict_locked()

    def contains(self, key: str) -> bool:
        with self._lock:
            entry = self._entries.get(key)
            return entry is not None and (
                entry.metadata.compiler_version == COMPILER_VERSION
                and entry.metadata.schema_version == SCHEMA_VERSION
            )

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __contains__(self, key: object) -> bool:
        return self.contains(str(key)) if isinstance(key, str) else False

    # -- internals -----------------------------------------------------------

    def _evict_locked(self) -> None:
        """LRU eviction: drop the least-recently-used entries over the cap."""
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)


__all__ = [
    "CacheBoundError",
    "CacheEntry",
    "CacheEntryMetadata",
    "DEFAULT_MAX_ENTRIES",
    "DEFAULT_MAX_ENTRY_BYTES",
    "GeneratedAssetCache",
]