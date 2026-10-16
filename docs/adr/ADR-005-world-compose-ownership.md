# ADR-005: World-Composition Ownership Boundary (Phase 45 / DEF-076)

- Status: Accepted (Phase 45 contract)
- Date: 2026-10-16
- Phase: 45
- Supersedes: none
- Related: ADR-004 (Gate 3 playability), `Phase45.md`

## Context

Phase 41–43 proved that after canonical generation succeeds and the controller reaches
`generation.published=true`, the service runs `_apply_kit_composition`
(`services/generation.py`). For fake/live/frontier paths this rebuilds the world from the
static golden kit (`world/composer.py::KIT_BASE_OBJECT_IDS` + `_GOLDEN_OBJECT_FACTS`) and
then **replaces the entire `world_graph`**. When the provider authored its own world with a
disjoint ID namespace, the composed candidate references golden object/evidence IDs that are
absent from the draft's declared namespace. A second `pipeline.validate_draft` then reports
`RECOVERABLE_REPAIR` (`unknown objectId` / `unknown evidenceId`) through the ordinary
`generation.validation.*` channel *after* `generation.published=true`, and publication
silently falls back to the canonical aggregate.

The safety fallback is correct; the ownership model is not. `DEF-076` is a representation/
ownership defect, not a validator or publication-safety defect. Reproduced hermetically
(scratch `pd45inv`): canonical `VALID` → composed `RECOVERABLE_REPAIR` with all 9 golden
object IDs and 5 golden evidence IDs foreign to the provider namespace → fallback to
canonical; event sequence `generation.published` → `asset.resolve.*` →
`generation.validation.complete=RECOVERABLE_REPAIR` → `publication.complete`.

## Decision

Adopt **Model B — explicit, observable composition skip for already-authored provider
worlds** (with a canonical-ID-preserving guard), not Model A or C.

Rationale:
- The canonical provider `world_graph` is **authoritative** in the current architecture: it
  is what Gate 1/Gate 2/Gate 3 validate, what is persisted on fallback, and what every
  downstream reader consumes. The composer is the only component that treats it as a
  pre-composition input and then replaces it — that half-ownership is the defect (Phase45
  §20/§53).
- **Model A** (canonical-ID-preserving composition) would require the composer's base set to
  become provider-driven rather than the static golden kit, a broader change to the composer
  input contract that risks the golden/demo path — more invasive than the defect warrants
  (Phase45 §46 "avoid overengineering").
- **Model C** (canonical materialization of golden objects) would inject golden
  `POTENTIAL_WEAPON`/evidence objects into the canonical draft, changing the solver candidate
  universe and Gate 3 accounting — semantically unsafe (§7/§46).

### D1. Canonical namespace invariant

A composition step may not introduce a placement reference to an `objectId`, `locationId`,
or `evidenceId` absent from the candidate's declared canonical namespace. No golden/kit or
provider exception.

### D2. Skip rule (deterministic, pre-composition)

Kit composition applies only when the canonical draft declares the selected kit's
`KIT_BASE_OBJECT_IDS` (i.e. `kit_base ⊆ declared_object_ids`). Otherwise the world is
provider-authored and the kit composer is inapplicable: **skip explicitly** (no `compose_world`
call, no revalidation, no foreign IDs) and keep the canonical world graph. A namespace guard
after composition (defense-in-depth) verifies every composed placement object/evidence/
location id is declared; a mismatch triggers an explicit skip before any candidate could
become authoritative.

On every skip (provider-authored, namespace mismatch) and fallback, the resolved kit **scene**
is still applied scene-only — `scene_for_kit(kit, draft.scene)` — exactly as the legacy
byte-identity path does, so `scene.environment_id` / `environment_version` remain non-null.
This is environment selection, not object/evidence-ID injection, and it preserves savegame
export/import compatibility (a null `scene.environmentId` is rejected by `parseSavegameV1`)
and the requested environment. All non-success paths restore the canonical attempt state
(D5) with the scene applied.

### D3. Publication authority

Composition is **optional enrichment**. The authoritative published candidate is always a
candidate that passes Gate 1 + Gate 2 + Gate 3 (and, if composition applied, the
composition validation). If composition is skipped or fails, the canonical aggregate remains
authoritative and publication proceeds; if the final authoritative candidate fails Gate 3,
publication fails. No invalid intermediate ever becomes authoritative.

### D4. Observability separation

Composition lifecycle is logged under a dedicated `world_compose.*` namespace
(`started`, `skipped`, `completed`, `validation.started`, `validation.complete`, `fallback`,
`failed`) with bounded, non-secret fields, and **never** reuses
`generation.validation.complete` for a non-authoritative composition candidate. The
composed-candidate validation emits `world_compose.validation.*`.

### D5. State consistency

On any non-success composition path (skip/fallback/failure) the attempt is restored to its
canonical state — `draft`, `last_validation`, `solver_proof`, `_phase3_cache`, `published` —
so the canonical validation remains authoritative and no discarded composed candidate leaks
into the attempt.

### D6. Preserved invariants

No change to the solver, CaseTruth, suicide/homicide/witness semantics, `WITNESS_CLAIMS`
rules, Gate 1/2/3, Phase 43A evidence algebra, Phase 43B structured-output enforcement,
Phase43-Fix1 ASGI, savegame format, or `REQUIREMENTS.md`. No budget/timeout/repair/
regeneration increase. Semantic object IDs remain distinct from visual asset IDs; category
asset fallback stays visual-only. Golden/demo/apartment byte-identity and the Ollama driver
path are preserved.

## Phase 45 findings and dispositions

| Finding | Severity | Disposition | Resolution |
|---|---|---|---|
| `DEF-076` (foreign kit IDs in a provider-authored draft) | — | CLOSED | Model B explicit skip + namespace guard; red→green regression |
| `ADV-45-01` / `DEF-109` (skip dropped the kit scene pin → null `scene.environmentId`; real savegame importer rejected it) | HIGH | CLOSED | scene-only pin on every canonical-keeping skip/fallback path |
| `ADV-45-02` (provider-authored apartment world mislabelled `APARTMENT_BYTE_IDENTITY`) | LOW | CLOSED | representation-first classification |
| `ADV-45-03` (`COMPOSE_EXCEPTION` for an unsupported kit leaves `scene.environmentId=None`) | LOW | REJECTED (documented) | not production-reachable: environment resolution only yields supported kits; latent defense-in-depth residual |

Merge-gate evidence (Phase 45 §52): DEF-076 red→green proven; no foreign object/evidence/
location IDs introduced (namespace guard); no late `generation.validation.*` failure for a
discarded composition candidate; golden/demo/world-matrix/world-integration green; Ollama
driver untouched; Phase 44 Gate 3 suites green; savegame/reveal/accusation unchanged and the
real `parseSavegameV1` accepts skipped/composed exports; Caddy-backed Playwright acceptance
green (Medium + Hard, clean logs); adversarial review found no HIGH/BLOCKER; backend suite
`3397 passed / 1 skipped / 1 known pre-existing failure`; `REQUIREMENTS.md` unchanged; no
budgets/timeouts increased. The primary defect id `DEF-076` is reused from the Phase 45
document; the repo ledger already carried an unrelated `DEF-076` (asset part-id) — a
governance numbering note only.
