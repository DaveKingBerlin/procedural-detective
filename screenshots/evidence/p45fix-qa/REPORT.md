# Phase 45 Fix — Independent QA Report

- Branch: `phase45-fix/evidence-completion-playability` (implementation **uncommitted**)
- Repo: `C:\Users\Fujitsu\coding\procedural-detective`
- Date: 2026-10-16
- QA: independent verification only. **No product source was edited by QA.**
- Canonical refs: `Phase45-Fix.md`, `docs/adr/ADR-006-completion-playability-repair.md`,
  `.rad/roles/qa.md`, `.rad/policies/defect-lifecycle.md`.

## Verdict

**PASS.** The Phase 45 Fix reproduces the reported RED state and repairs it to GREEN
through the real controller/service and the production Caddy topology. No product
defect was found; no defect filed. The only failing backend test is a pre-existing,
environment/timing-sensitive Ollama-probe test, reproduced identically on the clean
pre-fix HEAD worktree.

---

## 1. RED → GREEN (real controller + service, provider-authored no-link world)

Harness: `e2e/probes/qa-p45fix-redgreen.py` — the SAME file run against the pre-fix
HEAD worktree (`C:\Temp\pd-prefix`, `a34a31c`) and the post-fix working tree. Script:
golden fixtures renamed to a disjoint `prov_*` namespace, **empty provider evidence**
(so deterministic completion fires), and a world graph that links **no `evidenceId`**
with an inspectable interaction on every placement.

### PRE-FIX (`FIX_PRESENT: False`)

```
gate12_outcome: VALID            solver: who/why/weapon unique, when not ambiguous
evidence_completed: True         d_ev_facts: 17
world_graph_stage_done: False
RAW_GATE3_BEFORE: valid=false
  reachableCriticalDimensions=[]  unreachableCriticalDimensions=[WHO,WHY,WEAPON,WHEN]
  redHerringCount=0  issueCodes=[CRITICAL_DIMENSION_UNREACHABLE, DIFFICULTY_CONTRACT_UNMET,
                                 EVIDENCE_UNREACHABLE, RED_HERRING_REQUIREMENT_UNMET]
guard_status: None   publication_calls: 0   record_state_after_guard: FAILED
guard_events: [playability.validation.started, playability.validation.failed]
PUBLISHED_GATE3_AFTER: null
```

### POST-FIX (`FIX_PRESENT: True`)

```
RAW_GATE3_BEFORE: identical RED report (raw candidate is STILL rejected)
guard_status: PUBLISHED   publication_calls: 1   record_state_after_guard: PUBLISHED
guard_events: [playability.validation.started, generation.validation.started,
  generation.validation.complete, playability.validation.repaired,
  playability.validation.complete, publication.started, publication.complete]
PUBLISHED_GATE3_AFTER: valid=true
  reachableCriticalDimensions=[WHO,WHY,WEAPON,WHEN]  unreachable=[]  redHerringCount=1
  inspectableCount=16  meaningful=8  distinctSources=9  issueCodes=[]
```

Exact artifacts: `redgreen-prefix-medium.txt`, `redgreen-postfix-medium.txt`.

## 2. Difficulty matrix

Same probe, `easy` / `medium` / `hard` on both trees (prefix = FAIL, postfix = PASS).

| difficulty | pre-fix raw | post-fix published | reachable dims | red herrings | meaningful | sources | inspectables |
|---|---|---|---|---|---|---|---|
| easy   | FAILED (published=null, state FAILED) | PUBLISHED | WHO,WHY,WEAPON,WHEN | 0 (min 0) | 7 (min 2) | 8 (min 2) | 15 (min 3) |
| medium | FAILED (published=null, state FAILED) | PUBLISHED | WHO,WHY,WEAPON,WHEN | 1 (min 1) | 8 (min 3) | 9 (min 3) | 16 (min 5) |
| hard   | FAILED (published=null, state FAILED) | PUBLISHED | WHO,WHY,WEAPON,WHEN | 2 (min 2) | 9 (min 5) | 10 (min 4) | 17 (min 7) |

**Easy: PASS · Medium: PASS · Hard: PASS** (each meets its own `difficulty_policy` contract).

## 3. Repair-semantics verification (independent)

`e2e/probes/qa-p45fix-semantics.py` — **27/27 checks PASS** (`repair-semantics.txt`):

- raw candidate Gate-3-rejected; `reachableCriticalDimensions == ()`.
- **completion facts reachable**: added placements link `d_ev_*` canonical facts
  (sample: `d_ev_motive_link`, `d_ev_opp_*`, `d_ev_presence`).
- **red herrings reachable and do not change the solution**: hard count 2 ≥ min 2;
  every herring subject is an EXCLUDED, non-winning candidate; solver stays unique.
- **semantic / record-based meaningful classification (not raw counts)**: adding a
  SECOND object linking an already-linked evidence record leaves the meaningful count
  unchanged (base 8 = dup 8).
- **idempotence**: `_ensure_playability_clues` twice → identical (no duplicate object
  or placement ids); evidence-algebra completion idempotent (first-added 17,
  second-added 0, same id tuple).
- **boundedness**: added placements = easy 4 / medium 5 / hard 6, each ≤ 4 + min_red_herrings.
- **no CaseTruth / solver / evidence mutation**: repaired draft is a NEW draft sharing
  the unchanged `crime`/`evidence` refs; published truth identity + crime identity +
  evidence content unchanged; evidence propositions byte-identical; solver winners
  unchanged (`thomas_reed` / `cover_up_embezzlement` / `prov_obj_kitchen_knife`).
- **namespace integrity**: `_world_graph_namespace_violations == ()` for all
  difficulties; every added placement references a declared object/location/evidence id.
- **empty/placement-less world is NOT conjured** (fail-closed preserved).

## 4. Phase 44 non-weakening + Gate 3 immutability

- `git diff main` for `playability.py`, `difficulty_policy.py`, `safety.py`,
  `domain/solver.py`, `domain/evidence.py` → **EMPTY**. Gate 3 thresholds/semantics
  and the solver are unchanged.
- The two modified Phase44 tests still assert the RAW candidate is Gate-3-rejected
  (specific `PLAYABILITY_*` codes + `WHY` unreachable) and that the PUBLISHED candidate
  is Gate-3-valid with `unreachable_critical_dimensions == ()`:
  `test_adv44_01_..._unrepaired`, `test_adv44_07_..._unrepaired` → **2 passed**.
- Fail-closed is preserved by `test_3_unrepairable_candidate_fails_closed` (empty
  placements → `None`, `calls == 0`, state FAILED) and independently by the real-API
  probe (§7).

## 5. Test evidence (exact)

- New Phase45-Fix suite: `pytest backend/tests/test_phase45_fix_completion_playability.py`
  → **27 passed**.
- Focused suites (**270 passed**), per file:
  phase45_fix 27 · phase44_gate 47 · phase44_pipeline 13 · phase45_ownership 35 ·
  phase43a_evidence_algebra 31 · world_composer 17 · world_matrix 43 · world_integration 13 ·
  phase41_savegame 6 · phase32_savegame_export 24 · publication_gate 7 · phase5_publication_tx 7.
- FULL backend suite
  `python -m pytest backend/tests -q --basetemp=C:\Temp\pd-p45fix-qa`
  → **1 failed, 3424 passed, 1 skipped** (1064 s).
  - The 1 failure is **pre-existing and unrelated**:
    `test_phase21b_demo_truthfulness.py::test_ollama_probe_hang_dto_via_bounded_seam`
    (`assert 2.75 < 1.0`, a probe-hang timing bound). It fails **identically** on the
    clean pre-fix HEAD worktree (`assert 2.734 < 1.0`). No generation.py involvement.
  - No flakes observed in the full run.
- Full log: `backend-full-run.log`.

## 6. Budget / config / hash

- `git diff main -- backend/app/core/config.py backend/app/generation/budgets.py`
  → **EMPTY** (no budget/timeout/repair/regeneration change).
- `REQUIREMENTS.md` SHA-256 =
  `BA3DFEC2CA08069BEF21137286DEF9E6E9C45AAAB80CDEA424400C522A909678` — matches the
  required pinned hash exactly.

## 7. Caddy production-topology acceptance

Stack rebuilt from the working tree (`docker compose -f docker-compose.prod.yml -f
docker-compose.lan.yml -f docker-compose.debug.yml up --build -d`,
`CADDY_DOMAIN=enshrouded-server`, `GENERATION_PROVIDER=fake`). Confirmed the running
container contains the fix (`grep -c _repair_playability_candidate` = 2);
`GET https://enshrouded-server/api/v1/health` → 200.

Playwright spec `e2e/p45/phase45-caddy-acceptance.spec.ts` (Easy added by QA):
**3 passed** (50.8 s) — `acceptance/caddy-acceptance.log`.

Per-difficulty evidence (`acceptance/p45-*-acceptance-evidence.json`):

| item | easy | medium | hard |
|---|---|---|---|
| create status | PUBLISHED | PUBLISHED | PUBLISHED |
| interactive inspectables | 7 (≥3) | 7 (≥5) | 7 (≥7) |
| scene canvas | 830×497, 1215 colors | same | same |
| object inspection | 7/7 discovery=true | 7/7 | 7/7 |
| witness answer | 162 chars | 162 | 162 |
| meaningful sources | 8 (≥2) | 8 (≥3) | 8 (≥4) |
| /health | 200 | 200 | 200 |
| frontend page errors | [] | [] | [] |
| accusation/reveal | CASE SOLVED, 4/4 | CASE SOLVED, 4/4 | CASE SOLVED, 4/4 |

Container logs (`container-post-acceptance.log`): every case emitted
`generation.validation.complete {validationOutcome: VALID}` and
`playability.validation.complete {playabilityOutcome: VALID, reachableCriticalDimensions:
[WHO,WHY,WEAPON,WHEN], unreachableCriticalDimensions: [], playabilityIssueCodes: []}`.
**Zero** `generation.validation.failed`, `playability.validation.failed`,
`playability.validation.repaired`, or `publication.failed` events for the accepted cases
(the golden default world is already Gate-3-valid, so the repair is a no-op — as intended).

### User-facing copy (production topology)

- Independent Playwright spec `e2e/p45/phase45fix-publication-copy.spec.ts` intercepts
  `POST /api/v1/cases` through the real SPA served by Caddy: **2 passed**.
  - `PUBLICATION_FAILED` → renders exactly
    "The generated case did not meet the playability requirements. Please try again."
    (never "Adjust the prompt" / "solvable case" / raw `PUBLICATION_FAILED`/`PLAYABILITY`).
  - control `VALIDATION_FAILED` → keeps the unchanged generic prompt-blaming copy.
- Independent real-API probe `e2e/probes/qa-p45fix-api-failure.py`: a genuine
  unrepairable playability failure (empty world) through the real app returns HTTP 201
  `{status: "FAILED", failureCode: "PUBLICATION_FAILED"}` with a
  `playability.validation.failed` event — i.e. the copy mapping matches reality.

## 8. Frontend suites

- `npm run typecheck` → **0 errors**.
- `npm test` (vitest) → **91 files / 2009 tests passed**.
- `npm run build` (tsc + vite + scan:bundle) → **exit 0**; bundle scan OK.

## 9. Acceptance criteria (Phase45-Fix.md)

- [x] Unique-but-unplayable regression reproduced by an automated test — MET
      (`test_red_to_green_observation_shape` + QA `redgreen-*`).
- [x] Root cause identified in code — MET (Phase45 Model B skip returned before the
      Phase44 repair; ADR-006). Verified the WORLD_GRAPH context already carries the
      completed `d_ev_*` evidence (no stale context).
- [x] Evidence completion remains idempotent — MET (probe + tests).
- [x] Completion-generated critical evidence player-reachable — MET.
- [x] No stale pre-completion playability/world state — MET
      (`test_4_world_graph_context_consumes_completed_evidence`).
- [x] Solver-unique case cannot silently proceed with all critical dims unreachable — MET
      (raw rejected; guard repairs+revalidates or fails closed).
- [x] Meaningful inspectable classification works for completion evidence — MET.
- [x] Existing difficulty contracts intact — MET (policy module unchanged).
- [x] Easy passes its playability contract — MET.
- [x] Medium passes its playability contract — MET.
- [x] Hard passes its playability contract — MET.
- [x] Required red-herring contracts satisfied — MET.
- [x] Red herrings do not alter solution uniqueness — MET.
- [x] Repair does not create duplicate evidence/inspectables — MET (idempotence).
- [x] Repair/regeneration loops remain bounded — MET (no provider passes consumed;
      bounded additions).
- [x] Existing schema/domain validation continues to pass — MET.
- [x] Existing relevant test suite passes — MET.
- [x] Full project test suite passes, or unrelated pre-existing failures documented — MET
      (1 pre-existing failure documented & independently reproduced on clean HEAD).
- [x] User-facing failure wording no longer blames the prompt for playability failures — MET.

## 10. Defects

None. No product defect was observed; `DEFECTS.md` unchanged by this pass.

## 11. Notes / residual risks

- The reported failure log's exact world (`inspectableCount=6, meaningful=1`) is not
  byte-reproduced by the golden fixture (my reproduction: inspectable 11, meaningful 3),
  but the invariant (unique solver + `reachableCriticalDimensions == []` + Gate 3 FAILED)
  is reproduced exactly and repaired.
- `_pick_free_evidence_anchor` now falls back to the global anchor allowlist with an
  empty zone id when a kit's evidence anchors are exhausted; namespace/validation checks
  and Gate 1/2 revalidation pass, so this is an acceptable mechanism (documented in ADR-006).
- The production stack was rebuilt to the post-fix image and left running/healthy
  (Caddy + backend). The pre-fix git worktree `C:\Temp\pd-prefix` is temporary.
