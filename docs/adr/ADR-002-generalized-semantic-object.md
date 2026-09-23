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