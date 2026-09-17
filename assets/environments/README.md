# Environment Kits (Phase 11 — Five Environment Kits & Semantic Anchors)

This directory is the **single source of truth** for the five Phase 11
environment kits: `apartment.json`, `office.json`, `hotel_suite.json`,
`warehouse.json` and `mansion.json`. Each file is a typed, versioned,
load-time-validated descriptor (see `backend/app/environments/manifests.py`
for the validator) declaring zones, semantic anchors, the player spawn,
lighting, structural catalog assets, style hints and the default anchor
coverage per semantic anchor type. The backend placer
(`backend/app/environments/placer.py`) composes world-graph placements from a
kit + catalog asset requests, the resolver
(`backend/app/environments/resolver.py`) maps environment hints (exact id,
canonical name, alias, semantic tags, or the explicit fallback) to a kit with
provenance, and the frontend kit builders consume these same descriptors to
render recognizable local scenes with deterministic geometry. All positions
live in a bounded 12x12x4 local space (x/z horizontal, y vertical), all
rotations are deterministic local-space transforms, and no secrets or remote
resources are ever referenced by any kit.