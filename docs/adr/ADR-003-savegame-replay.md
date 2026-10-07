# ADR-003: SavegameV1 — server-side reveal-gated export projection + browser-local replay (Phase 32)

- **Status:** ACCEPTED
- **Date:** 2026-10-07
- **Supersedes:** nothing (additive Phase 32 feature; no REQUIREMENTS.md change)

## Context

A completed investigation must be downloadable as a portable `.pdcase` and a
loaded save must start a **fresh replay** of the same fixed case (Phase32-SALC-R
§1-§2). The `.pdcase` is hostile-environment transport (a user can edit
anything in it), so the document is the exact contract shared by a backend
export track and a frontend import/replay track.

The player never receives the full public case document during play. The
browser consumes only `InvestigationBootstrapResponse` (scene + world objects +
candidates + witnesses + playerKnowledge) and lazily-fetched
`EvidenceReadResultDTO` records, so a save can **never** be assembled
client-side — it must be a SERVER-SIDE PROJECTION of the frozen
`published_versions.payload_json` row. Save export is only legal after the
truth has been legitimately revealed (playthrough lifecycle `{ACCUSED,
REVEALED}`), so the export is a **reveal-gated** read.

The existing reveal DTO (`RevealTruthDTO`) deliberately omits the accusation
tolerance, but a replay must evaluate `timeCorrect` with the exact server
scoring time set (REQUIREMENTS 31.7 / DEC-003) — therefore the save MUST carry
`accusationToleranceSeconds` explicitly.

## Decision

### 1. Architecture: server-side export projection + browser-local replay

- **Export:** a pure, strict-allowlist projection
  (`backend/app/services/savegame.py::project_savegame_v1`) derives the
  `SavegameV1` document from the pinned published payload, reusing the EXISTING
  player-safe projection helpers (`publication.public_case_dict_from_payload`,
  `publication.project_world_objects`, `publication.project_witnesses`,
  `publication.project_read_content`, `publication.witness_statement_content_tags`,
  `reveal.candidate_block_of`, `reveal.truth_labels`). No projection logic is
  rebuilt; the exported shapes ARE the DTO shapes the frontend already parses
  (zero new parsing on the replay side).
- **Surface:** a dedicated REVEAL-GATED endpoint
  `GET /api/v1/playthroughs/{playthrough_id}/savegame`, authorized with the
  existing `require_playthrough` playthroughAccessToken dependency. It applies
  the SAME lifecycle gate as the reveal endpoint (`{ACCUSED, REVEALED}`, else
  `403 REVEAL_NOT_AVAILABLE`) and resolves the payload EXCLUSIVELY from the
  pinned `(caseId, caseVersion)` of the playthrough row (never "latest"). A
  missing/unpublished pinned version answers the generic 404.
- **Replay:** browser-LOCAL. No load endpoint, no replay session, no DB
  migration, no AI/quota consumption (Phase32 §17/§39). The frontend track
  owns `parse -> validate -> normalize -> SavedCaseDefinition + FreshReplayState`
  and reuses the existing case-player UI. The backend provides only the export.

Rejected alternatives:

- Extending the reveal DTO with a `savegame` field (too big, pollutes a frozen
  Phase 7 allowlist that QA locked).
- A `GET /cases/{case_id}/savegame` creator-scoped export: the playthrough
  route reuses the existing playthrough token, the lifecycle gate and the
  pinned-version resolution, and is the exact place the reveal already lives.
- Any load/import endpoint: replay is browser-local by design.

### 2. File format and constants (Phase32 §8, corrected from the illustrative shape)

- extension `.pdcase`, JSON document, `formatVersion: 1`
- MIME `application/vnd.procedural-detective.case+json`
- `MAX_EXPORT_BYTES = 5 MiB` (a realistic demo export is ~25–45 KiB; 5 MiB is
  a documented hard cap with three orders of magnitude headroom, below the
  source payload's own 8 MiB publication bound). The serialization always uses
  `sort_keys=True` compact separators, so equal inputs produce byte-identical
  exports.

### 3. SavegameV1 exact shape (strict allowlist)

```jsonc
{
  "format": "procedural-detective-case",
  "formatVersion": 1,
  "exportedAt": "<ISO-8601 UTC, e.g. 2026-10-06T20:00:00Z>",
  "case": {
    "metadata": {
      "title": "<string>",
      "difficulty": "<string|null>",
      "source": "generated" | "demo",
      "sourceCaseId": "<string; display-only, NEVER authority>",
      "environmentId": "<string|null>"
    },
    "publicCase": { /* EXACT PublicCaseResponse allowlist (publication.public_case_dict_from_payload) */ },
    "scene": {
      "location": { "locationId": "<string>", "name": "<string>" },
      "environmentId": "<string|null>",
      "environmentVersion": "<int|null>",
      "worldObjects": [ /* EXACT WorldObjectDTO[] with FULL knowledge (discovered/read=true, evidenceId exposed) */ ]
    },
    "candidates": { "suspects": [], "motives": [], "weapons": [] },
    "witnesses": [ /* EXACT WitnessListEntryDTO[] */ ],
    "evidence": [ /* one read-record enrichment per published fact */ ],
    "replayTruth": { /* ReplayTruthV1, see below */ }
  }
}
```

Field-by-field derivation and semantics:

- `metadata.source`: `"generated"` when the frozen payload recorded a real
  provider `model` (non-empty string); `"demo"` when `payload.model is null`
  (the deterministic fixture-based paths — the default fake provider and every
  demo fixture). This is the honest, payload-derivable distinction; it is
  display metadata only and never grants anything.
- `metadata.sourceCaseId`: the server case id, kept for display/debug ONLY.
  Never an owner/session/server-lookup authority (Phase32 §22).
- `publicCase`: the exact `PublicCaseResponse` dossier (scene, persons, motives,
  objects, locations, travelRules, worldGraph with the FULL placement
  `evidenceId`s, evidence summary list, compositionNotes). The save is an
  honest spoiler archive — full visibility is intentional and documented.
- `scene`: the exact bootstrap scene shape with the full world-object
  projection (`project_world_objects(discovered=ALL, read=ALL)`): every
  published evidence id is considered discovered/read so the immutable world
  mapping (object -> evidenceId, generated definitions, displayLabel) is
  complete. A replay runtime starts its OWN knowledge empty and re-projects as
  the player investigates.
- `candidates` / `witnesses`: the exact bootstrap blocks (never winner-marked).
- `evidence`: one record per published fact, each carrying exactly
  `{evidenceId, kind, reliability, title, description, content}` where
  `content` is the EXACT `EvidenceReadResultDTO.content` shape
  (`project_read_content` closed render payload: `renderType`/`summary`/
  `entries`/`comparison` + the kind-allowlisted keys) PLUS the deterministic
  Phase 23 interview-source tags (`questionType`/`witnessId`) for
  witness-statement records. `openedAt`/`readByPlayer` are playthrough state
  and are NOT exported (the replay runtime owns them).
- `replayTruth` — **ReplayTruthV1** (new explicit allowlist DTO, NOT the
  internal `CaseTruth`):

```jsonc
{
  "murdererId": "<string>",
  "motiveId": "<string>",
  "weaponId": "<string>",
  "crimeTime": "<canonical ISO-8601 string>",
  "accusationToleranceSeconds": "<int>",
  "murdererName": "<public label>",
  "motiveLabel": "<public label>",
  "weaponName": "<public label>"
}
```

  Derived ONLY from the already-revealed truth representation:
  `reveal.truth_labels(payload)` (canonical ids + public winner labels via the
  semantic-object rule, DEF-081) plus the published
  `truth.crime.crime_time.accusation_tolerance_seconds`. The internal
  `CaseTruth` object is never serialized.

- **Strictness:** every object in the document is allowlist-only. The schema
  consumed by the import path MUST use `additionalProperties: false` at every
  level (per Phase32 §19), `format`/`formatVersion` are required, and unknown
  future `formatVersion` values fail closed.

### 4. Trust model (Phase32 §18/§13)

- Live `CaseTruth` = trusted server-side truth.
- Imported `ReplayTruthV1` = untrusted, user-controlled **replay-scoped** data.
- Imported truth may ONLY drive the local replay (local evaluation with the
  existing normalization/comparison semantics); it is never canonical and never
  persists to browser storage (Phase32 §24). The solution is inside the file,
  so the UI may hide it during play but a user can read the JSON manually —
  documented honesty, no fake secrecy (Phase32 §7).

### 5. Secret exclusion (Phase32 §23/§34)

The projection is a strict allowlist over the frozen payload sections
(`draft` public material + `truth.crime` allowlisted fields). The following are
NEVER read for export (and never copied): `schemaVersion`,
`generationAttemptId`, `publishedAt`, `seed`, `prompt`, `locked`, `truth` (as an
object), `solverProof`, `universes` (as an object), `report`, evidence
`propositions` / `source_ref`, provider output, internal prompts, API keys,
session / playthrough / creator tokens, bridge tokens, server config and
filesystem paths. One deliberate exception: `payload.model` MAY be read as a
non-empty presence predicate to classify `metadata.source` (`"generated"` when
it is a non-empty string, else `"demo"`, ADR-003 §1/§3) — the boolean outcome
is ALL the projection derives from it. The projection NEVER exports the model
string/name, NEVER exports provider configuration or credentials, and NEVER
emits any model VALUE into the document (sentinel-proven by
`test_secret_sentinels_absent_from_exported_bytes`): the exported bytes contain
zero model and zero secret material. `json.dumps(payload)` (or any wholesale
serialization) is FORBIDDEN. The canonical fixture and the regression suite
assert zero occurrences of secret sentinels in the exported bytes.

### 6. Isolation and boundedness

- Read-only: the export performs ZERO writes, never mutates the payload/DB and
  needs no quota/AI/provider participation.
- Deterministic: the projection is a pure function of `(payload, difficulty,
  exportedAt)`; equal inputs yield byte-identical exports.
- Bounded: `serialize_savegame_v1` enforces `MAX_EXPORT_BYTES` and raises a
  sanitized 500 if the bound is ever hit.

## Consequences

- The frontend track consumes this ADR + the canonical fixture
  (`backend/tests/fixtures/savegame/v1_demo_apartment.pdcase.json`, ~25 KiB)
  as the exact contract; it implements `parse -> validate -> normalize ->
  fresh replay` with zero new server calls.
- No DB migration, no new state, no provider/quota code (Phase32 §17/§39).
- The exported document contains the full solution (documented spoiler limit).
- `metadata.source` distinguishes deterministic demo/fixture content from
  real-model generated content at export time; loaded saves always present as
  "Saved Case" in the player UI (Phase32 §36).

## Precedence

Additive Phase 32 feature. No REQUIREMENTS.md change; every existing player
flow keeps its favorite backend-gated semantics. The reveal endpoint (Phase 7
frozen allowlist) is UNCHANGED.