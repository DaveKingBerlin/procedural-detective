# Asset Oracle Catalog

`catalog.json` is the single source of truth for the application-owned asset
catalog (Phase 10). The backend Asset Oracle
(`backend/app/assets/` — catalog loader, resolver, security validation) consumes
it to resolve semantic object requests into trusted logical `assetId`s, versioned
semantic placements, and resolution provenance; the frontend scene renderer will
consume the same manifest so both sides agree on the same logical ids and safe
render metadata. The catalog is deterministic and byte-stable: no timestamps, no
environment-specific values, no secrets — it carries only declarative geometry,
labels, vocabularies and anchor-type strings. Treat this file as the v1 contract;
a version bump (`catalogVersion`) is required before any breaking change.