# Case Quality Reference Corpus

Phase 35 (`Phase35-Fix-GCQ.md`) test/reference corpus for generated-case
quality: three known-good `Try Demo Case` exports (`golden/demo/`) and one real
generated Ollama export (`negative/generated/`) that failed the current quality
contract.

## Layout

```text
tests/fixtures/case_quality/
├── README.md
├── manifest.json
├── golden/
│   └── demo/
│       ├── procedural-detective-case-CASE-iA2tcy7PkB1P.ok.pdcase   (19254 bytes)
│       ├── procedural-detective-case-CASE-Yw0tGvxleJab.ok.pdcase   (18773 bytes)
│       └── procedural-detective-case-CASE-htbCd0X0mDkt.ok.pdcase   (25086 bytes)
└── negative/
    └── generated/
        └── procedural-detective-case-CASE-LZig0W97AIyW.nok.pdcase  (32597 bytes)
```

## What each file is

- **`golden/demo/*.ok.pdcase`** — SavegameV1 exports created after completing
  the `Try Demo Case` flow (demo source). They are known to play, solve,
  export and load successfully. They are the **golden reference cases**: every
  file must pass the deterministic case-quality validator.
- **`negative/generated/*.nok.pdcase`** — a real generated Ollama export
  (source `generated`). It is the **negative regression case**: it must
  trigger the confirmed quality issue codes and never publish through the
  current contract.

## Golden vs negative classification

| File                              | Classification | Source    |
|-----------------------------------|----------------|-----------|
| `CASE-iA2tcy7PkB1P.ok.pdcase`     | golden         | demo      |
| `CASE-Yw0tGvxleJab.ok.pdcase`     | golden         | demo      |
| `CASE-htbCd0X0mDkt.ok.pdcase`     | golden         | demo      |
| `CASE-LZig0W97AIyW.nok.pdcase`    | negative       | generated |

The golden cases are **regression evidence, not the source of truth**. The
authoritative contract hierarchy is: frozen `REQUIREMENTS.md`, the canonical
domain models, the strict parser, the deterministic validators, the solver /
truth invariants and the security boundaries — golden files rank below all of
them. If a golden file ever conflicts with the authoritative contract, the
conflict is investigated rather than weakening the contract to match the
fixture.

## Fingerprints

Byte lengths and SHA-256 hashes are recorded in `manifest.json` (formatVersion
1) for every file. A fixture whose bytes or hash no longer match the manifest
is a modified/regressed reference and will fail the corpus test.

## Immutability

The raw reference files are committed byte-for-byte and are **immutable**.
Tests read them directly, never rewrite them. `manifest.json` is test metadata
only and is not loaded by any production runtime.

## Prompts

The full `.pdcase` contents are **never injected into provider prompts**
(no golden/negative savegame is pasted into any generation or repair request,
for either provider). Only compact, canonical, sanitized quality contract text
is used as model guidance.

## Expected validator result

- All three `golden/demo/*` cases: **PASS** the hard case-quality validator.
- `negative/generated/*.nok.pdcase`: **FAILS** with the confirmed issue codes
  (public role truth leak, victim/witness in suspect candidates, missing
  usable witness statement for the remote witness).