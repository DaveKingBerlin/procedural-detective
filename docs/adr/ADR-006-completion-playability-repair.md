# ADR-006: Playability Reachability for Completion-Generated Evidence (Phase 45 Fix)

- Status: Accepted (Phase 45 Fix contract)
- Date: 2026-10-16
- Phase: 45 Fix
- Related: ADR-004 (Gate 3), ADR-005 (world-compose ownership), `Phase45-Fix.md`

## Context

Deterministic evidence completion (`evidence_algebra`) can turn an ambiguous case into a
solver-unique one by adding canonical `d_ev_*` facts. In the generic/Frontier path, completion
runs before the WORLD_GRAPH stage and the completed evidence is installed in the attempt, so
the provider is given the completed fact ids — but a provider-authored `world_graph` may link
no `evidence_id` at all. Phase 44 repaired exactly this by binding missing winning facts to
declared-location inspectables (`_ensure_dimension_clues`). Phase 45's Model B
**provider-authored skip returned before that repair**, so no completion fact became
player-reachable and the publication Gate 3 failed (`reachableCriticalDimensions=[]`), which
also mapped to a prompt-blaming user message.

Verified root cause (read-only investigation): the skipped call site is the broken link; the
WORLD_GRAPH context already includes the completed evidence (not a stale-context bug), and the
solver is legitimately unique.

## Decision

Restore and generalize deterministic, difficulty-aware **playability reachability repair**,
and guarantee the "unique ⇒ player-reachable" invariant at the publication boundary.

### D1. Core invariant

> A fact required for the unique solution must not exist only in solver state. It must be
> reachable by the player through a real, placement-linked inspectable (a declared object at a
> declared location) unless the domain model explicitly marks it derivable from reachable
> facts. `solverOutcome == unique` with required critical dimensions unreachable must never be
> accepted as a playable case.

### D2. Deterministic repair (no provider calls)

Extend the existing `_ensure_dimension_clues` into a single deterministic
`_ensure_playability_clues(draft, kit, difficulty)` repair that, using only the case's own
declared objects/locations and canonical evidence ids (no kit/golden id injection — ADR-005
Model B), binds:
- the **winning** fact for each missing required dimension (WHO/WHY/WEAPON/WHEN; suicide
  WHY/WEAPON/WHEN) to a new `INSPECTABLE` placement at a declared location;
- enough reachable **red-herring** facts (derived from evidence-backed EXCLUDED solver
  candidates) to meet the difficulty minimum;
- enough meaningful/source coverage to meet the difficulty minima.

It never mutates `CaseTruth`, the solver, or the canonical evidence; it only adds declared
objects/placements linking existing discloseable facts. It is **idempotent** (a no-op when the
candidate already satisfies Gate 3; no duplicate `dim_clue_*`/decoys) and **bounded** (≤ the
missing dimensions + the red-herring shortfall). It reuses canonical domain structures and
preserves the Phase 45 namespace guard.

### D3. Enforcement points

1. The provider-authored (and legacy/composer) composition branch applies the repair before
   the candidate is finalized.
2. As an authoritative provider-neutral safety net, the publication boundary
   (`_publish_if_ready`) applies the repair to the frozen candidate when Gate 3 fails; if the
   repaired candidate passes canonical validation + Gate 3, it becomes authoritative; if not,
   publication fails closed (no payload, no silent `published=true`). Any mutated candidate is
   re-validated (Gate 1/Gate 2) and re-scored (Gate 3) before it can be persisted
   (ADR-005 D3).

### D4. No weakening

Gate 3 thresholds, the solver uniqueness requirement, difficulty contracts, red-herring
requirements, and all validators are unchanged. Unreachable evidence is never merely relabelled
reachable — a real placement-linked player path is created. No budget/timeout/repair/
regeneration increase; repair is deterministic and provider-free.

### D5. Failure classification and user copy

A playability/Gate 3 failure is classified distinctly from a prompt/generation failure, and the
user-facing message must not blame the prompt for an internal playability failure (a
non-prompt-blaming message is preferred where the UI has only one safe generic string).

### D6. Preserved invariants

Phase 44 Gate 3 semantics; Phase 45 Model B ownership and namespace guard; evidence-completion
idempotence; solver/CaseTruth/Phase 41/41A/43A/43B/Fix1 semantics; savegame/accusation/reveal
compatibility; `REQUIREMENTS.md` unchanged.

## Phase 45 Fix findings and dispositions

| Finding | Severity | Disposition | Resolution |
|---|---|---|---|
| `Phase45-Fix` unique-but-unplayable regression | — | CLOSED | deterministic `_ensure_playability_clues` repair on the provider-authored/legacy/composer paths + publication-boundary safety net; red→green |
| `ADV-45FIX-01` (safety net did not pin the kit scene → latent null-`scene.environmentId` savegame rejection) | LOW | CLOSED | scene-only pin on the repaired candidate + fail-closed when unpinnable |
| `ADV-45FIX-02` (repair revalidation used the authoritative `generation.validation.*` prefix) | LOW | CLOSED | revalidation uses the non-authoritative `playability.repair.validation.*` prefix |

Outcome: backend full suite `3429 passed / 1 skipped / 1 known pre-existing failure`; frontend
typecheck 0 + `2009 passed` + build OK; Caddy-backed Playwright acceptance green for
Easy/Medium/Hard (no late `generation.validation.*` failure; non-prompt-blaming playability
copy verified); adversarial review found no HIGH/BLOCKER. `unique ⇒ player-reachable` now
holds; a solver-unique case cannot publish with unreachable critical dimensions.
