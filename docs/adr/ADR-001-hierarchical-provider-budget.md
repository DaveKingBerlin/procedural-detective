# ADR-001: Hierarchical provider-call budget replaces the single generation ceiling (Phase 19 amendment)

- **Title:** Hierarchical provider-call budget replaces the single generation ceiling
- **Status:** ACCEPTED
- **Date:** 2026-09-21
- **Supersedes:** REQUIREMENTS.md §32.7 (Model-Call Budget) for the enumerated Phase 19 settings only (see **Precedence**)

## Context

REQUIREMENTS.md §32.7 freezes a single canonical default:

```text
MAX_LLM_CALLS_PER_GENERATION = 8
```

During Phase 19, this single ceiling became a reliability blocker, not a
safeguard. Generation is a pipeline of heterogeneous stages with very
different call profiles:

- core stages (`case_truth`, `evidence`, `world_requirements`) plus global
  repair and regeneration passes,
- per-object procedural `ASSET_SPEC` generation, which itself includes initial
  generation, bounded repair, and geometry repair,
- a bounded number of distinct procedural assets and a bounded number of
  failed assets per generation.

With `Easy` and `Medium` reliability targets, repairs and retries need room
inside a per-stage allowance. A single shared ceiling of 8 forces any repair
to steal calls from another stage, so one noisy stage exhausts the budget for
the whole attempt and the attempt fails with a coarse code. It also caps room
richness: larger rooms (more procedural assets) must stay artificially small
to fit inside 8 total calls.

The local provider (Ollama) has no per-call cost and no per-call API charge,
so the only real constraint is that an attempt must stay finite and bounded.
The original number conflated "no charge" with "must be tiny"; the Phase 19
model decouples those concerns.

Phase 19 (commit `96f261f`) intentionally replaced the single ceiling with a
hierarchical provider-budget model. Per the post-freeze governance rule, this
intentional change is recorded as an amendment (ADV-215, Phase 19B) rather
than edited into the frozen REQUIREMENTS.md.

## Decision

Adopt a hierarchical provider-call budget for generation. One global ceiling
remains as a safety cap but is explicitly **never a target**; each stage gets
its own hard ceiling (numbers are canonical defaults):

| Setting | Default | Meaning |
| --- | --- | --- |
| `MAX_LLM_CALLS_PER_GENERATION` | 128 | Global safety ceiling for the whole attempt; NOT a target |
| `MAX_CORE_LLM_CALLS_PER_GENERATION` | 12 | Core stages (`case_truth`, `evidence`, `world_requirements`) plus global repair/regeneration calls |
| `MAX_LLM_CALLS_PER_PROCEDURAL_ASSET` | 5 | Per asset: initial `AssetSpec` + repairs + geometry repair + bounded retry |
| `MAX_PROCEDURAL_ASSETS_PER_GENERATION` | 20 | Richness bound: distinct procedural assets per generation (256 total calls are explicitly future-only) |
| `MAX_FAILED_ASSETS_PER_GENERATION` | 3 | Failed procedural assets tolerated before the attempt fails |
| `MAX_PARALLEL_ASSET_GENERATIONS` | 2 | Recorded for forward compatibility; effective asset concurrency stays 1 — no threading |

Hierarchy: per-asset calls sit inside the core serial budget; core calls sit
inside the global ceiling; the plural-asset bounds cap the number of assets
(knowable before a full call count). Failure attribution uses the narrowest
known code.

**New canonical failure codes** (all existing codes preserved):

- `CORE_PROVIDER_CALL_BUDGET_EXHAUSTED` — core stage budget hit
- `ASSET_PROVIDER_CALL_BUDGET_EXHAUSTED` — per-asset budget hit for one object
- `MAX_PROCEDURAL_ASSETS_EXCEEDED` — asset richness bound hit
- `MAX_FAILED_ASSETS_EXCEEDED` — failed-asset threshold hit

The generic `PROVIDER_CALL_BUDGET_EXHAUSTED` remains for a global-ceiling hit
with no narrower attribution.

**Canonical environment ids** (`environmentHint` is a closed enum with five
values): `apartment`, `office`, `hotel_suite`, `warehouse`, `mansion`.

**Semantic object id vs render asset id rule.** The SEMANTIC requested-name
slug is the authoritative public / evidence identity (for example a request
`antique brass letter opener` becomes the semantic object id
`antique_brass_letter_opener`). The render representation may resolve to a
catalog asset or alias (for example `letter_opener` /
`PROP_LETTER_OPENER_01`) through the Asset Oracle. Render ids never leak into
answer identity: `CaseTruth`, evidence, solver, and accusation all reference
the semantic object id.

## Consequences

- REQUIREMENTS.md §32.7 is superseded by this amendment for the enumerated
  settings only; the rest of REQUIREMENTS.md is untouched (see **Precedence**).
- Minimum sufficient architecture is retained: no threading, no async worker;
  `MAX_PARALLEL_ASSET_GENERATIONS` is accepted but effective asset concurrency
  is always 1.
- `MAX_LLM_CALLS_PER_GENERATION = 128` is a safety ceiling, never a target.
  A theoretical 20 × 5 = 100 asset calls plus core calls (~12) plus slots for
  repair/regeneration reaches ~128, so 256 total calls remain **future-only**
  until measurements show 128 is genuinely insufficient.
- Richer rooms are possible within `MAX_PROCEDURAL_ASSETS_PER_GENERATION = 20`.
- Failures report the narrowest cause code, improving diagnostics for
  `Easy`/`Medium` reliability debugging.

## Precedence

POST-FREEZE amendments approved for Phase 19 take precedence over the frozen
REQUIREMENTS.md value **for the enumerated settings only**:

`MAX_LLM_CALLS_PER_GENERATION` (8 -> 128 as ceiling), `MAX_CORE_LLM_CALLS_PER_GENERATION`
(12), `MAX_LLM_CALLS_PER_PROCEDURAL_ASSET` (5), `MAX_PROCEDURAL_ASSETS_PER_GENERATION`
(20), `MAX_FAILED_ASSETS_PER_GENERATION` (3), `MAX_PARALLEL_ASSET_GENERATIONS`
(2), the four new failure codes, the five canonical environment ids, and the
semantic-object-id-as-authoritative-identity rule.

For everything else, REQUIREMENTS.md remains the immutable product contract:
this amendment does not modify, narrow, or relax any other requirement.