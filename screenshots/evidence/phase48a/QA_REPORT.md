# Phase 48.1 — Independent QA Report (Dev-Box only)

- Branch: `phase48a/ollama-structured-repair` (off `main` `c148b3a`; dev changes UNCOMMITTED)
- Scope: Dev-Box hermetic. **GAMING-PC live Ollama acceptance: NOT RUN** (no Ollama on this box; not installed per instruction).
- Verdict (Dev-Box): **PHASE 48.1 QA PASS — no new product defects.** Phase 49 gating still requires the Gaming-PC real `hermes3:8b` acceptance (not run here).

## Evidence artifacts
- Independent probe: `qa_phase48a_probe.py` (36/36 PASS) + `qa_phase48a_probe_results.json` + `qa_phase48a_probe_output.txt`
- `release_check.txt`, `release_check_allow_placeholders.txt`

## Changed surface (verified, not trusted)
`git diff --name-only` = only:
`backend/app/core/observability.py`, `backend/app/generation/controller.py`,
`backend/app/generation/pipeline.py`, `backend/app/generation/prompts.py`,
`backend/app/generation/repair_routing.py`, `backend/app/services/ollama_driver.py`,
`backend/app/world/requirements.py`, `backend/tests/test_phase19_budgets.py`,
`backend/tests/test_phase26_c3_budget.py` (+ new `backend/tests/test_phase48a_structured_repair.py`).
Zero diff in `frontend/`, `bridge/`, `e2e/`, `assets/`, `backend/app/core/config.py`,
`backend/app/core/timeout_envelope.py`, `backend/app/generation/publish.py`,
`state_machine.py`, `difficulty_policy.py`, `budgets.py`.

## Duty results

### 1. Hermetic reproduction — PASS
- BEFORE-equivalent (module-global normalizer swapped back to the pre-fix
  `kind in RELATION_KINDS` check, source untouched): a world with
  `relations[0].kind="on_kitchen_counter"` raises
  `world_requirements.relations[0]: kind 'on_kitchen_counter' not in [...]`.
- AFTER: the same payload parses to `on_table`; the driven case PUBLISHES
  (`record.last_validation.valid is True`, `deferred_structural == ()`).
- Unsupported `teleported_inside` still raises strict `ValueError`; raw
  `PlacementRelation(kind="on_kitchen_counter")` still raises (dataclass strict).
- Style: a provider fixture emitting the alias on the FIRST attempt publishes in
  the clean 9-call shape (case=1, evidence=1, activity logs=5, world=1, asset=1)
  and emits exactly one `world.relation_kind.canonicalized` event, zero
  `generation.repair.started`.
- Transport-mode parity (the Local-Ollama/bridge structured-output mode): with
  `structured_output=True` the alias still publishes (9 calls, world=1) and the
  repeated unsupported alias still fails closed (`STRUCTURED_OUTPUT_INVALID`,
  `deadline_passed() is False`, `repair_passes == 1`) — identical to
  `structured_output=False`.

### 2. Normalization contract — PASS (one non-blocking observation)
- Every mapped alias -> correct canonical kind (20/20 sampled incl.
  `on_kitchen_counter`, `on_counter`, `On Kitchen Counter`, `on-kitchen-counter`,
  `desk`, `shelf`, `floor`, `wall`, `beside`, idempotent canonicals).
- `on_desk` vs `on_table` semantic distinction preserved.
- 13 unsupported/arbitrary/oversized/non-string values rejected
  (`teleported_inside`, `behind_the_sofa`, `on_bed`, `on_chair`,
  `on_floor_near_window`, empty, `on_`, None, int, `on_table`×40, 200-char
  garbage, `on_table_extra_words`).
- `RELATION_KINDS` is exactly the six; `RELATION_TO_ANCHOR_TYPES` keys == exactly
  those six; no alias leaked into the canonical vocabulary.
- The normalized target really composes: `on_kitchen_counter` -> `on_table`
  places the kitchen knife in `apartment` AND `office` kits with `issues == ()`.
- OBSERVATION (NON-BLOCKING, no defect filed): control characters used as
  *separators* around a canonical token are folded per the documented
  non-alphanumeric rule: `normalize_relation_kind("\x01on_table") == "on_table"`,
  `"on_table\x01"`, `"on\x00table"` likewise. A control-char-ONLY value
  (`"\x00"`) is rejected. Because the output is always one of the closed six and
  the strict dataclass is never bypassed, this is judged working-as-intended
  (documented `str.isalnum()`-else-`_` folding). It does not expand the enum or
  weaken validation.

### 3. Owner routing — PASS
- The exact observed message
  `"world_requirements stage parse failed: world_requirements.relations[0]: kind
  'on_kitchen_counter' not in [...]"` -> `owner_for_report == (WORLD_GRAPH,)`
  (NOT UNKNOWN); `stage_labels_for_report == ("world_requirements",)`; the plan
  reruns world+recompose only (case/evidence/logs False).
- Unprefixed structural -> `UNKNOWN` + `is_default_full_rerun`.
- Bounded/closed: hostile free text (`world_requirements_bogus:`, `prefix
  world_requirements:`, `notworld_graph:`, `world_requirementsX`, missing
  separator) yields NO owner. Closed labels (`world_graph`, `evidence`,
  `case_truth`, `asset_spec`, `activity_log`) route to their owners. `public_world`
  stays UNKNOWN by design. No regex over content.

### 4. Repair scope + fingerprint — PASS
- World-only defect (invalid world -> repair); real driver-generated deferred
  message: PUBLISHES; evidence prompt count == 1, activity-log count == 5
  (NOT re-called), case == 1, world == 3.
- Repeated UNSUPPORTED alias: FAILED with `STRUCTURED_OUTPUT_INVALID`; reason
  `repair made no progress: failure fingerprint <hex> unchanged; owner=WORLD_GRAPH;
  failingStage=world_requirements`; no "deadline" in reason; `repair_passes == 1`;
  `deadline_passed() is False`; evidence==1, logs==5, world==4, asset==0.

### 5. Scrutiny of the two modified existing tests — LEGITIMATE, NOT WEAKENED
**`test_phase26_c3_budget.py::test_f1b_repair_pass_reruns_logs_inside_derived_budget`
-> renamed `..._reruns_world_only_...`**
- BEFORE asserted (on the OLD broad replay): `transport.call_count == 20`,
  `budget.core_calls == 20`, `budget.repair_passes == 1`,
  `core_calls <= derive_core_call_budget_default()`, state PUBLISHED.
  The 20 encoded the BUG (pass-2 evidence + 7 logs + world re-burn).
- AFTER asserts: PUBLISHED, `repair_passes == 1`, `call_count == 12`,
  `core_calls == 12`, `core_calls <= derived`, PLUS stronger stage-level
  assertions (`evidence_v1`==1, `Canonical evidence time:`==7,
  `world_requirements_v1`==3).
- Judgement: the retained invariant ("2-pass repair chain publishes and fits the
  derived budget") is preserved and strengthened; only the expectation that
  encoded the buggy broad replay changed. No legitimate invariant dropped.

**`test_phase19_budgets.py::test_driver_core_budget_exhaustion_narrows_code`**
- BEFORE: a pathological endless-repair fixture drove CORE to 12 via broad
  replay; asserted FAILED, `CORE_PROVIDER_CALL_BUDGET_EXHAUSTED`,
  `core_calls >= 12`, `calls <= 60`, `calls == 12`.
- AFTER: `_staged()` clean valid pass with `max_core_llm_calls=4`; asserts FAILED,
  same narrow code, `core_calls == 4`, `calls <= 60`, `calls == 4`.
- Judgement: the invariant ("CORE exhausted before the global ceiling yields the
  narrow CORE code") is preserved and still genuinely exercised (core cap 4 < the
  9-call full pass, global 60). The old fixture's mechanism (broad replay) is
  intentionally removed by the fix, so it had to change. Not a dropped invariant.

### 6. Regression (Dev-Box)
| Suite | Result |
| --- | --- |
| `backend/tests` full (`python -m pytest tests -q`) | **3533 passed, 1 skipped, 1 failed** (18m10s) |
| known env failure | `test_phase21b_demo_truthfulness.py::test_ollama_probe_hang_dto_via_bounded_seam` — `assert elapsed < 1.0` (2.688s), pre-existing/environmental |
| `bridge/tests` full | **351 passed** |
| frontend `npm test` (vitest) | **98 files / 2103 passed** |
| frontend `npm run typecheck` | exit 0 |
| frontend `npm run build` (+`scan:bundle`) | exit 0; 37 text files, no private-endpoint evidence |
| Phase 44/45/47/48 + Demo Cases | **248 passed** |
| repair/budget/driver focus (`phase26_c3`, `phase19`, `phase38_*`, `test_ollama_driver`) | **132 passed** |
| new `test_phase48a_structured_repair.py` | **54 passed** (54 collected) |
| root `tests` (`python -m pytest tests -q`) | 294 passed, 2 skipped, **2 failed** |
| root failures cause | local untracked root `.env` (git-ignored) re-injects via `env_file: .env`; with `.env` hidden the file is **35 passed**; `.env` restored byte-identical (sha256 unchanged) -> ENVIRONMENT-ONLY, not Phase 48.1 |
| `python -m tools.release_check` | 20 checks, **2 FAILING** = README.md:415 `'lands here after'`, SUBMISSION.md:16 `'TBD'` (both pre-existing; files last touched `802cc5f`, untouched here) |
| `python -m tools.release_check --allow-hosted-placeholders` | **ALL 21 checks OK**, 6 REPORT-only |

### 7. Invariants unchanged — CONFIRMED
- `RELATION_KINDS` tuple byte-identical to `c148b3a` (six tokens).
- `config.py` (deadline default / `max_repair_passes`=2 / `max_full_regenerations`=1),
  `timeout_envelope.py` (300s envelope), `budgets.py`, `publish.py`,
  `state_machine.py`, `difficulty_policy.py`: **0 diff lines**.
- `frontend/`, `bridge/`, `e2e/`, `assets/` and Demo Case fixtures: **0 diff**.
- 300s deadline, repair budgets, publication gates, difficulty behavior unchanged.

## New defects
**None.** No new product defect filed. One NON-BLOCKING residual observation
(control-char separators) recorded in `DEFECTS.md`.

## Residual risks
- **BLOCKING (external, not a code defect):** Gaming-PC real Local Ollama
  `hermes3:8b` acceptance NOT RUN — Phase 48.1 is not fully accepted for Phase 49
  until it passes.
- **NON-BLOCKING:** control-char separator folding (documented rule; output stays
  within the closed six; strict dataclass intact).
- **PRE-EXISTING / ENVIRONMENT-ONLY:** `test_ollama_probe_hang_dto_via_bounded_seam`
  wall-clock timing failure; root `test_phase24a_fp_docker_fixes.py` two failures
  caused by the local untracked `.env`; `tools.release_check` README/SUBMISSION
  placeholders.
