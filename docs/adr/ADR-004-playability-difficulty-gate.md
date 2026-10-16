# ADR-004: Gate 3 — Playability & Difficulty Quality Gate

- Status: Accepted (Phase 44 contract)
- Date: 2026-10-16
- Phase: 44
- Supersedes: none
- Related: ADR-001 (hierarchical provider budget), ADR-002 (generalized semantic object),
  ADR-003 (savegame replay), `Phase44.md`

## Context

Phase 41–43 established that the merged system is canonically correct, solver-correct,
publication-safe, security-sound and provider-independent. The `ADV-INV-01` investigation
proved a missing product boundary: **a schema-valid, solver-valid case can still be
unplayable or too sparse for the selected difficulty**. In the extreme, a valid
`worldGraph` with `placements: []` publishes with zero inspectable objects and every
evidence record undiscoverable, while `validate_draft` reports `VALID`.

The current publication pipeline enforces only:

- **Gate 1 — canonical correctness** (`validate_draft` structural/safety/universe/quality/locked),
- **Gate 2 — solver correctness** (`ValidationReport.outcome` solver/truth branches).

Difficulty is a **stored label only**; nothing consumes it. There is no playability gate.

## Decision

Introduce a deterministic **Gate 3 — Playability & Difficulty Quality Gate** with a single
authoritative validator and a single authoritative difficulty policy.

### D1. One authoritative difficulty policy module

`backend/app/generation/difficulty_policy.py` is the ONLY place difficulty thresholds live.
It exposes a closed `easy|medium|hard` vocabulary (aligned with
`services.savegame.SAVEGAME_DIFFICULTIES`), a `normalize_difficulty()` function, per-difficulty
`DifficultyPolicy` values, and a `difficulty_requirements_block()` used to render provider
prompt guidance. Prompts, the validator and observability all derive from this module
(AC-13/AC-14).

Enforced thresholds (minimums are **hard** publish-blocking; maximums are guidance and
observability only — they never block publication, so a legitimately richer case is never
rejected):

| Property | Easy | Medium | Hard |
|---|---:|---:|---:|
| Inspectable objects (min) | 3 | 5 | 7 |
| Meaningful inspectables (min) | 2 | 3 | 5 |
| Red herrings (min) | 0 | 1 | 2 |
| Distinct meaningful interaction sources (min) | 2 | 3 | 4 |
| Solver-critical dimensions reachable | all applicable | all applicable | all applicable |

Target (guidance) maxima: inspectables 5/7/10; meaningful 3/5/7; red herrings 1/2/3.

**Default difficulty.** A missing/empty/out-of-vocabulary difficulty normalizes to
`medium` — the same default the product UI selects (`frontend/src/routes/new.tsx`). This
prevents an omitted or malformed difficulty from being used to *lower* the playability
contract (bypass attempt → rejected by design).

**Applicable dimensions.** `WHO` is required only for `HOMICIDE`; `SUICIDE` requires
`WHY`, `WEAPON`, `WHEN` (mirrors `ValidationReport.outcome`). This preserves Phase 41
suicide semantics (AC-18).

### D2. One authoritative deterministic validator

`backend/app/generation/playability.py` owns Gate 3. It exposes:

- `PlayabilityReport` — frozen dataclass carrying deterministic, non-secret metrics
  (`inspectable_count`, `meaningful_inspectable_count`, `distinct_meaningful_source_count`,
  `red_herring_count`, `reachable_critical_dimensions`, `unreachable_critical_dimensions`,
  `issue_codes`) and a `valid` property.
- `validate_playability_quality(...)` — ZERO provider/network/render/browser work; pure
  function of the assembled candidate (`draft`, `public`, `evidence`, `universes`, `truth`)
  plus the normalized difficulty. **It never mutates `CaseTruth`** (AC-04) and never emits
  hidden truth (AC/§41).
- **Reachability (strict):** a discoverable evidence fact is player-reachable iff an
  interactive world placement's `evidence_id` equals the fact id, OR it is a witness-kind
  fact attributed to a published witness with usable content. (No person/motive/location
  "entity surface" — the runtime reveals evidence only through those two paths.)
- **Winner-support requirement:** each required dimension must be supported by the
  **winning** clue, not merely any fact of that type: WHO → a reachable observation /
  fingerprint / usable witness for `proof.who.winner`; WHY → a reachable
  `MOTIVE_LINKED_TO_PERSON(motive_id == proof.why.winner)`; WEAPON → a reachable positive
  weapon fact on `proof.weapon.winner`; WHEN → a reachable fact in
  `proof.when.critical_evidence_ids`. A missing winner clue ⇒
  `PLAYABILITY_CRITICAL_DIMENSION_UNREACHABLE`; `PLAYABILITY_EVIDENCE_UNREACHABLE` also
  fires when no proof-used fact is reachable.
- **Distribution accounting (record-based):** a distinct evidence record counts once no
  matter how many objects link it; a no-link object counts only when its derived roles are
  EVIDENCE_RELEVANT/WEAPON_CANDIDATE. Red herrings count only subjects of placement-linked
  reachable facts.
- **Fail-closed:** the publication guard treats any unexpected Gate 3 exception as a
  failure (never publishes); the stage gate fails open only for parser-owned assembly shape
  errors and is fail-closed on a validator exception.
- Closed issue codes: `PLAYABILITY_EMPTY_WORLD`, `PLAYABILITY_TOO_FEW_INSPECTABLES`,
  `PLAYABILITY_TOO_FEW_MEANINGFUL_INSPECTABLES`, `PLAYABILITY_EVIDENCE_UNREACHABLE`,
  `PLAYABILITY_CRITICAL_DIMENSION_UNREACHABLE`, `PLAYABILITY_EVIDENCE_OVERCONCENTRATED`,
  `PLAYABILITY_RED_HERRING_REQUIREMENT_UNMET`, `PLAYABILITY_WITNESS_CONTENT_UNREACHABLE`,
  `PLAYABILITY_DIFFICULTY_CONTRACT_UNMET`.

Gate 3 is a **separate observable result** from Gate 1/Gate 2 (own events, own report); it
does not modify `ValidationReport.outcome` semantics.

### D3. Classification

Gate 3 does not invent new state-machine semantics. It is evaluated over a candidate that
has already passed Gate 1/Gate 2 at the publication boundary; a failure means **the
candidate is not publishable**. The existing bounded controller repair/regeneration remains
the recovery mechanism, fed by a WORLD_GRAPH stage-acceptance gate (D5).

### D4. Hard publication guard (authoritative)

`services/generation.py::_publish_if_ready` is the single place
`PublicationService.publish_transactionally` is called, so it is the authoritative
chokepoint (AC-05). Immediately before publication, Gate 3 runs over the frozen
`PublishedCaseVersion` (the exact candidate that would be persisted). On failure:

- emit `playability.validation.failed`,
- mark the attempt/version FAILED (no payload persisted),
- never call `publish_transactionally`.

There must be no path where `Gate 3 fails -> published=true` for the same candidate (AC-29).

### D5. Recovery via WORLD_GRAPH stage acceptance + deterministic supplementation

To give the existing bounded repair/regeneration a chance without new budgets, a
`world_graph_playability_issues(attempt)` stage-local gate is added in
`pipeline.apply_stage_output`, mirroring the Phase 43B `public_world_semantic_breadth_issues`
pattern. A structurally valid but playability-sparse WORLD_GRAPH (including
`placements: []`) becomes NOT trusted and routes through the existing VALIDATING recovery
classification. No repair/regeneration/deadline budget is increased (AC-22).

Because the game model surfaces evidence through world objects (and the certification path
must also place driver/composer-authored worlds), the deterministic composer preserves
placement linkage for every evidence-bearing object, and the service applies a bounded
deterministic supplementation (`_ensure_dimension_clues`) that binds an EXISTING discoverable
fact to a NEW inspectable placement at a **declared** world-graph location when a required
dimension's winning clue is not yet placed. It never adds evidence facts, never mutates
`CaseTruth`/the solver, and is bounded to ≤ the number of missing dimensions. (Supplementation
can raise the inspectable/meaningful counts above a provider-authored world — accepted as
legitimate deterministic enrichment; it never creates a required-dimension bypass because it
binds only winner-supporting facts.)

### D6. Observability

Emit `playability.validation.started|complete|failed` with only allowlisted, non-secret
fields (caseId, generationAttemptId, difficulty, inspectable/meaningful/source/red-herring
counts, reachable & unreachable critical dimension tuples, validationOutcome/issueCodes).
Add required field names to `core/observability.py::_SAFE_FIELDS`. No secrets, no hidden
truth, no prompts.

### D7. Red-herring classification (smallest deterministic extension)

The generation `EvidenceSpec` has no `is_red_herring` field and no red-herring model exists
today (documented gap). Gate 3 uses a deterministic extension that does not require new
provider-authored fields: a red herring is a **placement-linked, reachable, meaningful**
interaction whose subject is a solver candidate that is evidence-backed EXCLUDED by the
solver proof (a plausible-but-innocent lead). Pure decoration never counts. Red herrings
must remain non-premises of the winning solution (the existing solver gate already
guarantees a second solution cannot exist).

### D8. Non-goals / preserved invariants

No change to the hidden truth model, homicide/suicide semantics, final-target semantics,
witness-murderer semantics, `WITNESS_CLAIMS` premise rules, the solver, canonical
validators, Phase 43A evidence algebra, Phase 43B structured-output enforcement, or
Phase43-Fix1 request-body semantics. No budget/timeout/repair/regeneration increase. No
savegame-format change. `REQUIREMENTS.md` unchanged.

## Consequences

- A published case now satisfies Gate 1 + Gate 2 + Gate 3.
- `ADV-INV-01` is closed: an empty/sparse world cannot publish.
- Difficulty becomes an enforced product contract sourced from one module.
- Additional deterministic validation cost is negligible (no provider/network/render).

## Phase 44 findings and dispositions

Findings raised during independent QA and the adversarial pass, and their final disposition:

| Finding | Severity | Disposition | Resolution |
|---|---|---|---|
| `ADV-INV-01` (empty world publishes) | HIGH | CLOSED | Gate 3 + publication guard; red→green regression |
| `ADV-44-01` (entity-surface reachability bypass) | HIGH | CLOSED | strict placement/witness reachability |
| `ADV-44-07` / `DEF-106` (decoy clue covers a dimension) | HIGH | CLOSED | winner-support per dimension |
| `ADV-44-08` / `DEF-107` (supplementation used undeclared location) | MEDIUM | CLOSED | declared-location + winner-fact binding |
| `ADV-44-03` (record-share count inflation) | MEDIUM | CLOSED | record-based accounting |
| `DEF-105` (demo/fake fixture not Hard-capable) | MEDIUM | CLOSED | demo fixture enriched (Hard-capable) |
| `DEF-108` (CI fake-world drift after enrichment) | MEDIUM | CLOSED | deterministic fixture regeneration |
| `ADV-44-06` (stage gate fail-open) | LOW | CLOSED | fail-closed guard + stage fail-closed on validator error |
| `ADV-44-09` (supplementation can raise counts across difficulty) | LOW | ACCEPTED (documented) | deterministic winner-fact enrichment only; not a bypass |
| `ADV-44-04` (inert interactive objects raise inspectable count) | LOW | REJECTED | inspectable ≠ meaningful; meaningful/source/red-herring minimums still bind |
| `ADV-44-05` (stage gate generic-path only) | LOW | REJECTED | the publication guard is provider-uniform and authoritative; no bypass |

Merge-gate evidence (Phase 44 §44): backend `backend/tests` 1 known pre-existing failure /
3362 passed / 1 skipped; root `tests` 2 environmental failures / 248 passed / 2 skipped;
frontend typecheck/build green and vitest 91 files / 2002 passed; Caddy-backed Playwright
acceptance PASS for Medium and Hard; `tools/generate_ci_fake_world --check` OK;
`tools.showcase_matrix` rc=0; `tools.catalog_report` 0 issues; `REQUIREMENTS.md` hash
unchanged; no budgets/timeouts increased.
