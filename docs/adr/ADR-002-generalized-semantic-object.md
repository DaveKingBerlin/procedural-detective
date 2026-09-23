# ADR-002: Generalized Semantic World Objects (Phase 19E)

- **Status:** ACCEPTED
- **Date:** 2026-09-23
- **Supersedes:** the Phase 19 semantic-object-id rule for the enumerated items
  only (see **Precedence**); extends ADR-001's world/asset ceilings with one new
  bounded setting.

## Context

An arbitrary but physically plausible murder weapon such as `fork` could not
travel the whole pipeline. The root cause (Phase 19D, authoritative): the
CaseTruth-declared weapon was NEVER materialized as a REQUIRED semantic world
object — the world-requirements stage had no app-owned deterministic injection
of the locked weapon, and the deterministic extractor could not even emit
`fork` as an `ObjectRequest` (head word outside the noun lexicon; not in the
known-object table; the locked-weapon surface only matched table triggers). The
run died at the driver's fail-closed object-presence guard.
A second gap: a variant weapon (`antique brass letter opener`) whose RENDER
resolves to a KIT-BASE asset was DEDUPED against the base placement, so its
semantic id unmaterialized.

## Decision — "semantic role over catalog membership"

1. **Closed semantic role model.** New `app/world/roles.py` defines the closed
   role vocabulary — `STRUCTURAL`, `DECORATIVE`, `INTERACTIVE`,
   `EVIDENCE_RELEVANT`, `WEAPON_CANDIDATE` — mapped to the existing project
   terms (`criticality` required/decorative, `evidence_id`,
   `requiredInteraction`, `INSPECTABLE`, `POTENTIAL_WEAPON`). Roles are DERIVED
   deterministically from validated traits; no arbitrary LLM-defined role
   strings exist. **Solver participation is derived only from validated
   semantics**: only objects whose published affordances carry
   `POTENTIAL_WEAPON` enter the weapon candidate universe / evidence graph /
   accusation / solver. Decorative objects can never become solver candidates.

2. **Deterministic weapon-lock injection (the general `fork` fix).** Both the
   Ollama driver (`inject_locked_weapon_request` after the world stage parse)
   and the deterministic extractor (`extract_world_requirements` with the
   locked constraints) MERGE the CaseTruth-declared weapon as a REQUIRED
   semantic `ObjectRequest` when no request with the same semantic id already
   exists (an existing request is upgraded to REQUIRED). The semantic id (slug
   of the locked name, e.g. `fork`) is preserved; the RENDER representation
   resolves independently (catalog exact / catalog alias / validated
   procedural AssetSpec). No fork/whitelist special-casing — this is the rule
   for ANY locked weapon.

3. **Render resolution lane.** For every semantic world object:
   (1) exact known asset -> (2) compatible catalog variant/alias ->
   (3) validated declarative AssetSpec through the EXISTING Phase 13 + Phase 17
   gates. The procedural lane is unchanged (proved by ice-pick/fork). The
   locked weapon reliably takes the procedural lane when no catalog/variant
   matches.

4. **Base-dedup collision fix.** The composer's dedupe is now SEMANTIC, never
   by render asset: a request dedupes against the kit base only when its
   semantic id equals an already-materialized base object id (the golden
   kitchen knife). A REQUIRED semantic weapon whose render is a KIT-BASE asset
   still materializes as its OWN semantic object (a distinct placement reusing
   the render asset visually), never a duplicate semantic placement, and
   base-object behavior for the golden case is unchanged.

5. **Scene enrichment + bounds.** Richer world requests are allowed while the
   provider budgets are UNCHANGED. A new configurable TOTAL VISIBLE OBJECT
   bound — `MAX_WORLD_OBJECTS_PER_KIT` (default 32, measured safe against the
   shipped kits whose anchor capacity validates at max 40) — caps the total
   PLACED object count. DECORATIVE objects beyond the bound (or that cannot be
   placed safely after the deterministic priority retry) are dropped with
   player-safe composition notes — optional decoration NEVER fails a case.
   REQUIRED / evidence-relevant objects get placement priority and are never
   dropped for the bound; a set that stays over-bound after every decorative
   drop FAILS CLOSED (`world.object-count-bound`).

6. **Plan-graph stability.** A non-recursive topological order
   (`app/world/graph.py`) orders placement plans with no recursion
   (RecursionError-hardened).

## Precedence / bounds declared

- `MAX_WORLD_OBJECTS_PER_KIT` = 32 is the new canonical setting (documented in
  `.env.example` / `.env.production.example`); it does NOT change
  `MAX_LLM_CALLS_PER_GENERATION`, `MAX_CORE_LLM_CALLS_PER_GENERATION`,
  `MAX_PROCEDURAL_ASSETS_PER_GENERATION` or `MAX_FAILED_ASSETS_PER_GENERATION`.
- Phase 19's semantic-object-id-as-authoritative-identity rule is retained and
  generalized to every REQUIRED CaseTruth weapon and every generated object.
- `REQUIREMENTS.md` is untouched (byte-identical).

## Amendment — Phase 19E adversarial close-out (ADV-235..240)

Follow-up decisions recorded after the Phase 19D/E adversarial review (the
weapon-lock injection and composition semantics above are refined, not
reversed):

- **ADV-235 (HIGH, fixed) — unsafe weapons FAIL CLOSED.** The weapon-lock
  injection in BOTH lanes (deterministic extractor and
  `ollama_driver.inject_locked_weapon_request`) and the driver's world-stage
  parser respect `app.world.extract.UNSAFE_OBJECT_TERMS` with the
  extractor's own matching (NFKC-casefold word-boundary, shared helper
  `unsafe_object_match`) BEFORE composing: a locked `gun`/`bomb`/`rifle`/
  `pistol`/`grenade`/... is recorded as a sanitized
  `unsafeUnsupported` safe-fail note and NEVER materialized/upgraded. The
  attempt then fails closed (`VALIDATION_FAILED`, nothing published) through
  the object-presence guard (Ollama) or the locked-constraint check
  (deterministic). Safe arbitrary weapons (fork/hammer/...) are unchanged.
- **ADV-236 (MEDIUM, fixed) — `with`/`used` ordinary prose is DECORATIVE.**
  The weapon-arc REQUIRED classification is narrowed to genuinely
  weapon-adjacent strong signals (`tool|weapon|killed|killer|murder|stabbed|
  ...`); the universally-common `with`/`used`/`use`/`uses`/`using` were
  removed from `UNSEEN_WEAPON_CONTEXT_WORDS`. "with a tray", "with a cup of
  coffee", "used a spatula" are DECORATIVE (never case-failing). The locked
  weapon stays REQUIRED via the injection; the bronze-ice-pick precedent
  survives via the strong `killer` signal.
- **ADV-237 (MEDIUM, fixed) — no slug divergence for >40-char weapons.** The
  weapon line of the driver's locked identity sheet uses
  `semantic_object_id` directly (the SAME 40-char-bounded slug the composer
  materializes and the injection uses); `_identity_slug` truncates to the
  same bound; `_weapon_evidence_id`/`_enhance_weapon` match against the
  single slug source. The injected request id ALWAYS equals the resolved
  semantic object id (one weapon per case — no prefix-collision is possible
  inside one case).
- **ADV-238 (MEDIUM, accepted + documented — demo truthfulness).** The
  deterministic (fake/demo) provider pins the GOLDEN case, which is
  immutable. A prompt substituting a different weapon ("Weapon: fork") is NOT
  the demo case: pre-19E (commit `0081baa`) the fake path ALREADY failed it
  with a terminal locked-weapon-vs-golden mismatch (verified by running the
  old checkout), and post-19E it fails identically at the same locked-
  constraint gate (the controller validates BEFORE `_apply_kit_composition`).
  The demo path therefore never silently ignores a weapon substitution and
  never publishes a non-validated world; the 19E generalized success
  criterion is achieved on the Ollama/procedural lane (which renders any
  locked weapon).
- **ADV-239 (LOW, accepted + documented — attempt-level budget).**
  `MAX_PROCEDURAL_ASSETS_PER_GENERATION` is a PER-ATTEMPT distinct-procedural
  richness bound shared across repair/regeneration passes (REQUIREMENTS says
  "per generation attempt"; ADR-001 documents it) — behavior is correct and
  unchanged. Within one composition the per-composition provider budget plus
  decorative drops guarantee a decorative-only over-ceiling never fails a
  case that would otherwise publish (regression test added).
- **ADV-240 (LOW, fixed — cache-independent decorative drops).** The
  per-composition procedural generation budget is allocated by a
  deterministic pre-pass FROM THE REQUEST ALONE (request order, then semantic
  id; `SPEC_PROVIDER_CALL_LIMIT` candidates). A warm generated-asset cache
  only memoizes (same definitions) and can never change which decorative
  objects are placed/dropped: empty-cache and warm-cache compositions of the
  same input are byte-identical.