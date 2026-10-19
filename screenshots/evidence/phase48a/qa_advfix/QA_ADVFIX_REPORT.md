# Phase 48.1 — Adversarial Fix Batch — Independent QA Retest (Dev-Box only)

- Branch: `phase48a/ollama-structured-repair` (off `main` `c148b3a`; dev changes UNCOMMITTED)
- Scope: Dev-Box hermetic. **GAMING-PC real Ollama acceptance: NOT RUN** (no Ollama on this box).
- Verdict (Dev-Box): **PHASE 48.1 QA PASS — no new product defects.**
- Retested: ADV-48.1-01/02/04/05/06/07/08/09/10. ADV-48.1-03 intentionally
  unchanged (not in this batch).
- Zero network / zero Ollama / zero paid calls. No product source modified.

## Evidence artifacts (this directory)
- `qa_advfix_probe.py` — independent probe, **82/82 PASS**
- `qa_advfix_probe_output.txt`
- `qa_advfix_probe_results.json`
- `release_check.txt`, `release_check_allow_placeholders.txt`

## Per-finding retest
| Finding | Result | Key independent evidence |
| --- | --- | --- |
| ADV-48.1-01 fail-closed | PASS | Scenario A (executed plan = UNKNOWN, re-validation targeted WORLD_GRAPH, same codes) does NOT fail closed early → consumes pass 2 (`repair_passes==2`); Scenario C (targeted) fails closed after 1 pass; Scenario B (all UNKNOWN) → `REPAIR_BUDGET_EXHAUSTED` (3 passes); differential probe emulating the OLD current-report source fails closed after 1 pass → the plan source is decisive. |
| ADV-48.1-02 semantic inversion | PASS | `in_desk`/`inside_desk`/`in_wall`/`at_table`/`at_cabinet` → None; 28 documented aliases normalize to their declared canonical; `on_desk`≠`on_table`; alias-table image == exactly the six `RELATION_KINDS`; `RELATION_KINDS`/`RELATION_TO_ANCHOR_TYPES` exactly six; strict dataclass rejects `in_desk`. |
| ADV-48.1-04 multi-token | PASS | `on_on_table`/`inside_on_table` → None. |
| ADV-48.1-05 glyphs | PASS | `on\x00table`, `on\u200b_table`, `\u202e on_table \u202c`, `on\ufeff_table` → None; parse boundary rejects with `ValueError`; space/`-`/`_`/`.` separators still normalize. |
| ADV-48.1-06 deferrals | PASS | `world_requirements stage: unsupported environment` and `... stage produced no usable content` → WORLD_GRAPH; `world composition failed ...` / `world composition produced no usable result` / `world_graph produced no usable result` stay UNKNOWN. |
| ADV-48.1-07 composition | PASS | Test uses non-golden `brass candlestick`; alias→`on_table` → TABLE_PROP vs hint-ignored → GENERIC_PROP; neutralizing the alias mapper makes the assertion FAIL (non-vacuous). |
| ADV-48.1-08 routing | PASS | `case_people`/`case_people ...` → CASE_PEOPLE (not CASE_TRUTH); `case_truth` → CASE_TRUTH. |
| ADV-48.1-09 boundary | PASS | `world_requirementsX` and `world_requirements stage parse failedX` → None/UNKNOWN; exact `... stage parse failed` (+ `: detail`) → WORLD_GRAPH. |
| ADV-48.1-10 coverage | PASS | Rewritten CORE-exhaustion test genuinely runs the repair loop: `repair_passes==1`, `core_calls==10`, world re-ran ≥3×, `generation.repair.started` emitted. |

Exact observed message `world_requirements stage parse failed: ...` →
`(WORLD_GRAPH,)`, label `("world_requirements",)`, plan reruns world+recompose only.

## Regression (Dev-Box)
| Suite | Result |
| --- | --- |
| 6 named suites (`test_phase48a_structured_repair`, `test_phase38_repair_routing`, `test_phase26_c3_budget`, `test_phase19_budgets`, `test_world_requirements`, `test_ollama_driver`) | **233 passed** |
| `-k "repair or world_requirements or structured"` | **452 passed / 3125 deselected** |
| bridge `tests` | **351 passed** |
| frontend `npm test` | **98 files / 2103 passed** |
| frontend `npm run typecheck` | exit 0 |
| frontend `npm run build` (+`scan:bundle`) | exit 0; 37 text files, no private-endpoint evidence |
| Phase 44/45/47/48 + Demo subset | **296 passed / 1 failed** |
| known env failure | `test_phase21b_demo_truthfulness.py::test_ollama_probe_hang_dto_via_bounded_seam` — `assert elapsed < 1.0`; independently reproduced on a CLEAN HEAD worktree (`assert 2.766 < 1.0`) → PRE-EXISTING/ENVIRONMENT-ONLY |
| `python -m tools.release_check` | 20 checks, **2 FAILING** = README.md:415 `'lands here after'`, SUBMISSION.md:16 `'TBD'` (pre-existing) |
| `python -m tools.release_check --allow-hosted-placeholders` | **ALL 21 checks OK**, 6 REPORT-only |

## Invariants
- Only 9 tracked files differ (`observability.py`, `controller.py`, `pipeline.py`,
  `prompts.py`, `repair_routing.py`, `ollama_driver.py`, `requirements.py`,
  `test_phase19_budgets.py`, `test_phase26_c3_budget.py`) + new test + QA evidence.
- `RELATION_KINDS` byte-identical to `c148b3a`.
- No diff in `config.py`/`timeout_envelope.py`/`budgets.py`/`publish.py`/
  `state_machine.py`/`difficulty_policy.py`, `frontend/`, `bridge/`, `e2e/`,
  `assets/`, Demo Cases.

## New defects
**None.** Non-blocking residual (recorded, not filed): fullwidth/compatibility
homoglyphs still NFKC-fold to a canonical kind (output stays within the closed
six; strict dataclass intact); outside the four in-scope control/format payloads.

## Residual risk
- **BLOCKING (external, not a code defect):** Gaming-PC real Local Ollama
  `hermes3:8b` acceptance NOT RUN.
- **NON-BLOCKING:** fullwidth homoglyph NFKC-fold (documented rule; closed set).
- **PRE-EXISTING / ENVIRONMENT-ONLY:** `test_ollama_probe_hang_dto_via_bounded_seam`
  wall-clock timing; `tools.release_check` README/SUBMISSION placeholders.
