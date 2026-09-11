# Procedural Detective — REQUIREMENTS v6

## 1. Purpose

**Procedural Detective** is a browser-native AI application for the Victoria VR AI Builder Hackathon 2026.

The system transforms a natural-language crime prompt into a **complete, interactive, logically consistent 3D detective scenario**.

The generated experience must include:

- a hidden immutable ground truth,
- suspects and witnesses,
- relationships,
- a coherent timeline,
- financial transactions,
- emails,
- chat conversations,
- phone logs,
- CCTV events,
- alibis,
- witness statements,
- physical evidence,
- red herrings,
- interactive 3D locations,
- playable investigation logic,
- accusation and solution reveal.

The central product principle is:

> **AI can generate a world. Procedural Detective makes sure that world has a truth.**

---

# 2. Hackathon Goal

The application should demonstrate the following transformation:

```text
Natural Language
      ↓
Ground Truth
      ↓
Story Graph
      ↓
Evidence Graph
      ↓
World Graph
      ↓
Interactive 3D Scene
      ↓
Playable Investigation
```

The implementation must prioritize:

1. browser compatibility,
2. natural-language input,
3. AI-generated 3D scene composition,
4. AI-generated working game logic,
5. consistency and solvability,
6. short generation time,
7. a strong demo experience.

---

# 3. MVP Product Flow

## 3.1 Start Screen

The user sees:

```text
PROCEDURAL DETECTIVE

Describe your mystery:

[ multiline prompt input ]

[ GENERATE CASE ]
```

Example input:

```text
Victim: Sarah Miller
Murderer: Thomas Reed
Motive: €240,000 embezzlement
Weapon: Kitchen knife
Time: 22:17
Witness: Emily Reed
```

The system must also support less structured prompts, for example:

```text
Create a murder mystery in a luxury hotel.

There should be five suspects.
The murderer is the hotel manager.
The motive is blackmail.
There is no direct eyewitness.
Make the case difficult but logically solvable.
```

---

## 3.2 Generation Screen

During generation the UI should expose the pipeline.

Example:

```text
Generating case...

✓ Ground truth created
✓ Timeline generated
✓ Characters generated
✓ Relationships generated
✓ Evidence generated
✓ Red herrings generated
✓ World graph generated
✓ Interaction logic generated
✓ Consistency validated

Characters: 6
Locations: 5
Evidence items: 31
Consistency: 96%
Solvability: 92%

[ ENTER INVESTIGATION ]
```

The values may be approximate or derived from validation scores.

---

## 3.3 Investigation

The generated world must be explorable in a browser-based 3D view.

MVP interactions:

- move through the scene,
- inspect objects,
- collect evidence,
- open/read emails,
- open/read chat histories,
- inspect financial records,
- inspect phone logs,
- inspect CCTV records,
- read witness statements,
- inspect suspect profiles,
- review collected evidence,
- submit an accusation.

VR/WebXR support is desirable but not required for the first playable MVP.

---

## 3.4 Accusation

The player must be able to choose:

- murderer,
- motive,
- weapon,
- approximate crime time.

Optional later:

- accomplice,
- crime location,
- sequence of events.

Example:

```text
WHO?
Thomas Reed

WHY?
Embezzlement cover-up

WEAPON?
Kitchen knife

TIME?
22:17

[ SUBMIT CASE ]
```

---

## 3.5 Truth Reveal

After submission, reveal the immutable ground truth.

Minimum reveal:

- correct murderer,
- motive,
- weapon,
- time,
- key timeline,
- evidence that proves the solution,
- player score.

Preferred presentation:

```text
THE TRUTH

21:38 — Thomas enters the apartment
21:51 — Sarah confronts Thomas
22:11 — Argument begins
22:17 — Thomas kills Sarah
22:19 — Emily sees Thomas leaving the kitchen
22:23 — Thomas leaves the building
```

Stretch goal:

- animated or ghost-style 3D reconstruction.

---

# 4. Core Architecture

Recommended architecture:

```text
                    Browser
                       │
                       ▼
              React / TypeScript
                       │
                       ▼
              Babylon.js / WebXR
                       │
                       │ REST / WebSocket
                       ▼
                 FastAPI Backend
                       │
          ┌────────────┼────────────┐
          ▼            ▼            ▼
     Case Director   Validator   Persistence
          │
          ▼
        LLM API
          │
          ▼
       CaseTruth
          │
      ┌───┼──────────────┐
      ▼   ▼              ▼
   Story Evidence      World
   Graph  Graph        Graph
      │      │            │
      └──────┴──────┬─────┘
                    ▼
             Playable Case
```

Alternative frontend engines are acceptable if browser-native.

---

# 5. Technology Requirements

## 5.1 Frontend

Preferred:

- React
- TypeScript
- Vite
- Babylon.js
- WebGL/WebGPU
- optional WebXR

Required:

- browser execution,
- no mandatory desktop installation,
- responsive UI,
- 3D scene rendered in-browser.

---

## 5.2 Backend

Preferred:

- Python 3.12+
- FastAPI
- Pydantic
- async HTTP client
- JSON-based API

Responsibilities:

- prompt parsing,
- LLM orchestration,
- case generation,
- evidence generation,
- validation,
- persistence,
- case loading.

---

## 5.3 AI Provider

The LLM provider must be abstracted behind an interface.

Example:

```python
class LLMProvider:
    async def generate_json(self, prompt, schema):
        ...
```

Do not tightly couple business logic to a specific provider.

Support for multiple providers is a later goal.

---

# 6. Data Model and Generated-Content Safety

All generated game content must be represented as structured data before rendering.

The 3D world must be generated from structured JSON, not from uncontrolled free-form LLM responses.

Structured JSON alone is NOT considered safe. All generated content must pass schema,
identifier, rendering, and loading validation before it can affect the client.

## 6.1 Text and Markup Rendering

All LLM-generated text MUST be rendered as escaped plain text by default.

Examples:

- emails,
- chats,
- witness statements,
- bank descriptions,
- document bodies,
- suspect notes.

Generated strings MUST NOT be inserted as executable HTML.

A sanitization exception may apply ONLY to non-executable markup rendering, for example
an explicitly supported Markdown subset.

If markup rendering is supported:

```text
generated text
    ↓
strict allowlisted markup parser
    ↓
sanitized non-executable markup
    ↓
render
```

The sanitizer/renderer must remove or reject at least:

- `<script>` elements,
- event-handler attributes such as `onclick`,
- `javascript:` URLs,
- embedded executable content,
- unsafe iframe/object/embed content,
- arbitrary external resource loading.

The following are UNCONDITIONALLY FORBIDDEN for model-generated content and are NOT made
permissible by any sanitizer:

```text
dynamic script injection
eval(...)
new Function(...)
generated JavaScript execution
generated TypeScript execution
generated shader execution
generated backend code execution
generated event-handler code execution
```

Application-owned framework code may use normal framework mechanisms, but model-generated
strings must never become executable instructions.

## 6.2 No Generated Code Execution

The LLM must never be allowed to generate executable runtime code for the browser,
backend, Babylon.js, shaders, scripts, or interaction handlers.

Generated game logic must select from predefined interaction types and parameters.

Allowed example:

```json
{
  "interactionType": "inspect",
  "targetEvidenceId": "EV-004"
}
```

Forbidden example:

```json
{
  "javascript": "fetch('https://example.com')"
}
```

## 6.3 Asset Identifier Allowlist

The model may reference only approved logical asset IDs.

Example:

```json
{
  "assetId": "PROP_KITCHEN_KNIFE_01"
}
```

The application resolves this through a server/client-owned registry:

```text
PROP_KITCHEN_KNIFE_01
    ↓
/assets/props/kitchen_knife_01.glb
```

The model MUST NOT supply:

- arbitrary asset URLs,
- arbitrary filesystem paths,
- `file://` paths,
- remote GLB/GLTF locations,
- untrusted texture URLs,
- script URLs,
- shader URLs.

Unknown asset identifiers must fail validation or map to a safe fallback asset.

## 6.4 Interaction Identifier Allowlist

Generated interaction logic may use only approved identifiers.

MVP allowlist:

```text
inspect
collect
open
read
activate
talk
view_record
add_to_evidence_board
```

Any unknown interaction identifier must be rejected.

## 6.5 External Resource Loading

The browser must not fetch arbitrary model-generated URLs.

Any remote resource support added later must use:

- an explicit allowlist,
- HTTPS,
- expected content type,
- size limits,
- timeout limits,
- origin validation.

MVP should prefer repository-hosted/local approved assets.

## 6.6 Malicious-Content Acceptance Tests

Tests must include generated strings such as:

```text
<script>alert(1)</script>
<img src=x onerror=alert(1)>
javascript:alert(1)
../../../../windows/system32
file:///etc/passwd
https://attacker.example/evil.glb
```

Expected behavior:

- rendered as inert text where applicable,
- rejected as asset/URL/path identifiers,
- never executed,
- never fetched.

# 7. CaseTruth, CaseVersion Lifecycle, and Publication

`CaseTruth` is the canonical truth of one immutable published `CaseVersion`.

A `Case` may have multiple versions over time. A published version never transitions into
playthrough states.

## 7.1 Case and CaseVersion Identity

Use:

```text
caseId
caseVersion
```

Example:

```json
{
  "caseId": "CASE-001",
  "caseVersion": 1
}
```

A playthrough must always be pinned to the exact tuple:

```text
(caseId, caseVersion)
```

Publishing version 2 MUST NOT invalidate or alter an existing playthrough bound to version 1.

## 7.2 User Constraint Lock

Explicit user-specified facts are locked from the beginning of generation.

Example:

```text
Victim: Sarah Miller
Murderer: Thomas Reed
Motive: €240,000 embezzlement
Weapon: Kitchen knife
Time: 22:17
Witness: Emily Reed
```

These constraints must not be silently changed by repair or regeneration.

If explicit constraints are internally impossible, generation must fail with a sanitized
terminal error rather than overwrite them.

## 7.3 CaseVersion Generation State Machine

The `CaseVersion` generation/publication state machine is:

```text
DRAFT
  ↓
GENERATING
  ↓
VALIDATING
  ├── valid
  │      ↓
  │   PUBLISHED
  │
  ├── recoverable validation failure
  │   AND repair budget remains
  │      ↓
  │   REPAIRING
  │      ↓
  │   VALIDATING
  │
  ├── regeneration required
  │   AND regeneration budget remains
  │      ↓
  │   GENERATING
  │
  └── terminal validation failure
      OR deadline exhausted
      OR model-call budget exhausted
      OR repair/regeneration budget exhausted
         ↓
       FAILED
```

Optional future transition:

```text
PUBLISHED → RETIRED
```

`FAILED` is terminal.

Forbidden transitions include:

```text
FAILED → GENERATING
FAILED → REPAIRING
FAILED → VALIDATING
FAILED → PUBLISHED
```

A new user retry after terminal failure creates a new generation attempt/draft rather than
resurrecting the failed attempt.

A `CaseVersion` remains `PUBLISHED` while one or many playthroughs use it.

`PLAYING`, `ACCUSED`, and `REVEALED` are NOT CaseVersion states.

## 7.4 Draft vs Published Truth

`DraftCaseTruth` may change during:

```text
DRAFT
GENERATING
REPAIRING
VALIDATING
```

subject to locked user constraints.

At:

```text
VALIDATING → PUBLISHED
```

the following are version-locked atomically:

- `CaseTruth`,
- discoverable evidence set,
- World Graph,
- solution proof,
- candidate universes,
- deduction-rule version,
- generation metadata,
- asset-resolution version if applicable.

After publication, these components MUST NOT mutate for that `caseVersion`.

## 7.5 Publication Invariant

A CaseVersion may be published only when one coherent validated version exists:

```text
CaseTruth
+ Evidence
+ WorldGraph
+ SolutionProof
+ CandidateUniverses
+ ReachabilityValidation
```

Failed or partially repaired drafts MUST NOT become playable.

## 7.6 Canonical CaseTruth Example

```json
{
  "caseId": "CASE-001",
  "caseVersion": 1,
  "title": "The Missing €240,000",
  "crime": {
    "type": "murder",
    "victimId": "sarah_miller",
    "murdererId": "thomas_reed",
    "motiveId": "cover_up_embezzlement",
    "weaponId": "kitchen_knife",
    "locationId": "miller_apartment_kitchen",
    "crimeTime": {
      "canonical": "2026-09-11T22:17:00+02:00",
      "accusationToleranceSeconds": 120
    }
  },
  "timeline": [],
  "persons": [],
  "relationships": [],
  "facts": []
}
```

Requirements:

- generated/validated before publication,
- immutable after publication,
- server-side authoritative,
- hidden from players until reveal,
- never used as a premise by the deduction engine.

# 8. Person Model

Each person must include:

```json
{
  "id": "thomas_reed",
  "name": "Thomas Reed",
  "role": "suspect",
  "age": 42,
  "occupation": "Finance Director",
  "personality": {},
  "relationships": [],
  "knowledge": [],
  "beliefs": [],
  "secrets": [],
  "lies": [],
  "alibi": {}
}
```

Supported roles:

- victim,
- murderer,
- suspect,
- witness,
- police,
- colleague,
- family,
- friend,
- other.

---

# 9. Relationship Model

Example:

```json
{
  "sourcePersonId": "sarah_miller",
  "targetPersonId": "thomas_reed",
  "type": "business_partner",
  "trust": 0.2,
  "conflict": 0.9,
  "description": "Sarah suspected Thomas of financial fraud."
}
```

Relationship types should include:

- family,
- spouse,
- affair,
- friend,
- colleague,
- business_partner,
- debtor,
- creditor,
- rival,
- manager,
- employee,
- unknown.

---

# 10. Event / Timeline Model and Time Semantics

Every relevant event must be represented explicitly using an absolute canonical timestamp.

Do NOT use clock-only values such as `"22:17"` internally.

Canonical representation:

```json
{
  "id": "evt_017",
  "occurredAt": "2026-09-11T22:17:00+02:00",
  "durationSeconds": 30,
  "locationId": "kitchen",
  "participants": [
    "sarah_miller",
    "thomas_reed"
  ],
  "type": "murder",
  "description": "Thomas kills Sarah using the kitchen knife.",
  "hidden": true
}
```

## 10.1 Canonical Timestamp

Use ISO-8601 timestamps including timezone offset:

```text
YYYY-MM-DDTHH:MM:SS±HH:MM
```

Example:

```text
2026-09-11T22:17:00+02:00
```

The backend may normalize internally to UTC, but the original case timezone must be
preserved.

## 10.2 Midnight Crossings

Events spanning midnight must remain unambiguous through full timestamps.

Example:

```text
2026-09-11T23:58:00+02:00
2026-09-12T00:07:00+02:00
```

Do not rely on implicit `dayOffset` when an absolute timestamp is available.

A derived `dayOffset` may be used for UI/debugging only.

## 10.3 Event Duration

Events that have duration must specify:

```json
{
  "durationSeconds": 420
}
```

or explicit:

```json
{
  "startedAt": "...",
  "endedAt": "..."
}
```

Choose one canonical representation project-wide and use it consistently.

Preferred MVP representation:

```text
occurredAt + durationSeconds
```

## 10.4 Travel-Time Validation

Opportunity validation must account for time required to move between locations.

The validator must reject timelines that require impossible travel.

Example:

```text
Person observed at Office at 22:14
Crime occurs at Apartment at 22:17
Minimum travel time = 18 minutes
→ opportunity contradicted
```

## 10.5 Crime-Time Truth

`CaseTruth` must store:

```json
{
  "crimeTime": {
    "canonical": "2026-09-11T22:17:00+02:00",
    "accusationToleranceSeconds": 120
  }
}
```

## 10.6 Accepted Crime-Time Scoring Interval

For validation and player scoring, derive:

```text
acceptedFrom = canonical - tolerance
acceptedTo   = canonical + tolerance
```

Example:

```text
Canonical: 22:17:00
Tolerance: ±120 seconds

Accepted scoring interval:
22:15:00–22:19:00
```

The exact boundary convention is inclusive:

```text
acceptedFrom <= submittedTime <= acceptedTo
```

## 10.7 Evidence-Derived Crime-Time Interval

The deduction engine must independently derive a time interval from discoverable evidence.

Example:

```text
Evidence-derived interval:
22:16:10–22:18:30
```

For a case to pass validation:

```text
canonicalTime ∈ evidenceDerivedInterval
```

AND:

```text
evidenceDerivedInterval ⊆ acceptedScoringInterval
```

Therefore an evidence-derived interval extending to `22:19:59` would be invalid when the
accepted scoring interval ends at `22:19:00`.

This rule prevents mismatch between proof, validation, and accusation scoring.

## 10.8 Materially Different Time Windows

For MVP, two time windows are considered materially different when they do not overlap
within the configured accusation tolerance or when they would allow different suspect
opportunity conclusions.

The exact comparison rule must be deterministic and covered by tests.

## 10.9 Player-Facing Time

The UI may display simplified values:

```text
22:17
```

but all internal deduction, validation, travel checks, and accusation scoring must use the
canonical timestamp representation.

Event types:

- arrival,
- departure,
- meeting,
- phone_call,
- message,
- transaction,
- confrontation,
- observation,
- murder,
- evidence_creation,
- evidence_move,
- lie,
- other.

# 11. Knowledge Model

NPC knowledge must be separate from ground truth.

A person may know only a subset of facts.

Example:

```json
{
  "personId": "emily_reed",
  "knownFacts": [
    "Sarah suspected Thomas of stealing money",
    "Sarah argued with Thomas",
    "Thomas was present that evening"
  ],
  "observations": [
    "Heard shouting around 22:10",
    "Saw Thomas leave the kitchen around 22:20"
  ],
  "beliefs": [
    "The murder may have happened around 22:10"
  ],
  "unknownFacts": [
    "Exact murder time",
    "Exact murder weapon"
  ]
}
```

Rule:

> NPC responses must never expose facts outside the NPC's available knowledge unless explicitly defined as a lie, belief, rumor, or inference.

---

# 12. Evidence Model

Each evidence item must be typed.

Example:

```json
{
  "id": "EV-004",
  "type": "physical",
  "subtype": "weapon",
  "title": "Kitchen Knife",
  "description": "A kitchen knife found on the counter.",
  "locationId": "kitchen",
  "relatedPersonIds": [
    "sarah_miller",
    "thomas_reed"
  ],
  "relatedEventIds": [
    "evt_017"
  ],
  "truthStrength": 0.95,
  "isRedHerring": false,
  "discoverable": true,
  "interaction": "inspect"
}
```

---

# 13. Required Evidence Types

The generator must support at least:

## Physical

- weapon,
- blood,
- fingerprints,
- clothing,
- object,
- document.

## Digital

- email,
- chat,
- phone log,
- CCTV event,
- computer file,
- metadata.

## Financial

- bank transaction,
- account statement,
- invoice,
- audit record.

## Testimonial

- witness statement,
- suspect statement,
- alibi,
- rumor.

---

# 14. Financial Transactions

Example:

```json
{
  "id": "txn_001",
  "date": "2026-04-03",
  "from": "Miller Consulting",
  "to": "Reed Holdings",
  "amount": 80000,
  "currency": "EUR",
  "description": "Consulting services",
  "suspicious": true
}
```

The UI must render transaction records in a readable table.

---

# 15. Emails

Example:

```json
{
  "id": "mail_001",
  "fromPersonId": "sarah_miller",
  "toPersonIds": ["thomas_reed"],
  "timestamp": "2026-09-11T21:04:00+02:00",
  "subject": "We need to talk",
  "body": "I reviewed the accounts. We need to talk tonight."
}
```

---

# 16. Chats

Example:

```json
{
  "id": "chat_001",
  "participants": [
    "sarah_miller",
    "thomas_reed"
  ],
  "messages": [
    {
      "time": "2026-09-11T21:32:00+02:00",
      "sender": "thomas_reed",
      "text": "We need to settle this tonight."
    }
  ]
}
```

---

# 17. Phone Logs

Example:

```json
{
  "id": "call_001",
  "callerId": "thomas_reed",
  "receiverId": "sarah_miller",
  "startTime": "2026-09-11T20:42:00+02:00",
  "durationSeconds": 192
}
```

---

# 18. CCTV Model

The MVP does not require generated video.

CCTV may initially be represented as:

- timestamped snapshots,
- camera event timeline,
- simplified animation,
- text-backed visual record.

Example:

```json
{
  "cameraId": "hall_cam_01",
  "events": [
    {
      "time": "2026-09-11T21:38:12+02:00",
      "personId": "thomas_reed",
      "action": "enter"
    },
    {
      "time": "2026-09-11T22:23:41+02:00",
      "personId": "thomas_reed",
      "action": "exit"
    }
  ]
}
```

Stretch goal:

- generated or animated CCTV clip.

---

# 19. Alibi Model

```json
{
  "personId": "thomas_reed",
  "claim": "I left the apartment before 21:45.",
  "truthfulness": "false",
  "supportingEvidenceIds": [],
  "contradictingEvidenceIds": [
    "cctv_exit_2223"
  ]
}
```

Alibis may be:

- true,
- false,
- partially true,
- unverifiable.

---

# 20. Red Herrings

Every non-trivial case should contain misleading but logically explainable evidence.

Example:

```text
Michael Carter owed Sarah €35,000.

Evidence:
- angry email,
- phone call,
- fingerprints in apartment.

Ground Truth:
Michael visited Sarah earlier in the day and did not commit the murder.
```

Rules:

- red herrings must not make the case logically impossible,
- red herrings must have an innocent explanation,
- at least one red herring should exist in medium and hard cases.

---

# 21. Story Graph

The generator should build explicit causal relationships.

Example:

```text
Thomas embezzles €240,000
          ↓
Sarah discovers missing money
          ↓
Sarah confronts Thomas
          ↓
Thomas fears exposure
          ↓
Murder
          ↓
False alibi
```

This graph should drive evidence generation.

---

# 22. Evidence Graph

Evidence must link facts rather than exist randomly.

Example:

```text
€240,000 missing
      │
      ├── Bank transactions
      ├── Audit report
      ├── Sarah email
      └── Thomas chat

Thomas present at crime scene
      │
      ├── CCTV entry
      ├── CCTV exit
      ├── witness statement
      └── fingerprints

Murder at 22:17
      │
      ├── phone event
      ├── witness sound
      ├── CCTV timing
      └── forensic evidence
```

---

# 23. World Graph

The AI must convert narrative locations into a renderable structure.

Example:

```json
{
  "locations": [
    {
      "id": "apartment",
      "template": "modern_apartment",
      "rooms": [
        "living_room",
        "kitchen",
        "hallway"
      ]
    }
  ]
}
```

---

# 24. Scene Templates

MVP should use reusable scene templates.

Recommended initial templates:

- modern apartment,
- office,
- hotel room,
- warehouse,
- laboratory.

Do not depend on fully AI-generated geometry for MVP.

---

# 25. Asset System

Use reusable GLB/GLTF assets.

Minimum reusable asset categories:

- tables,
- chairs,
- desks,
- computers,
- phones,
- documents,
- knives,
- bottles,
- boxes,
- cameras,
- doors,
- lights,
- generic male NPC,
- generic female NPC.

The AI determines:

- which object is used,
- where it is placed,
- whether it is evidence,
- which interaction applies.

---

# 26. Object Placement

Example:

```json
{
  "assetId": "PROP_KITCHEN_KNIFE_01",
  "locationId": "kitchen",
  "anchor": "counter_03",
  "interaction": "inspect",
  "evidenceId": "EV-004"
}
```

Prefer semantic anchors over raw coordinates.

Example anchors:

- desk_main,
- kitchen_counter,
- dining_table,
- bedside_table,
- floor_body_position,
- shelf_01.

---

# 27. Interaction Logic

Supported MVP interaction types:

- inspect,
- collect,
- open,
- read,
- activate,
- talk,
- view_record,
- add_to_evidence_board.

The system must generate interaction configuration from case data.

Example:

```json
{
  "objectId": "obj_knife",
  "interaction": {
    "type": "inspect",
    "onInteract": {
      "revealEvidenceId": "EV-004"
    }
  }
}
```

---

# 28. Case Generator

The Case Generator converts the user prompt into `CaseTruth`.

It must:

1. extract explicit constraints,
2. generate missing details,
3. build persons,
4. build relationships,
5. build motive,
6. build timeline,
7. build locations,
8. build causal facts,
9. preserve user-specified facts.

User-provided facts always take priority unless impossible.

---

# 29. Evidence Compiler

The Evidence Compiler derives evidence from ground truth.

Input:

```text
Thomas embezzled €240,000.
```

Possible output:

- bank transfers,
- audit report,
- suspicious invoice,
- Sarah email,
- Thomas chat message.

Input:

```text
Thomas killed Sarah at 22:17.
```

Possible output:

- weapon,
- fingerprints,
- blood evidence,
- CCTV timing,
- witness observation,
- false alibi,
- phone activity.

---

# 30. Consistency Validator

The validator is mandatory.

It must verify at least:

- murderer has opportunity,
- murderer has motive,
- weapon is accessible,
- timeline has no impossible overlap,
- travel times are plausible,
- statements reference valid events,
- CCTV timing is compatible,
- phone logs are compatible,
- bank records are compatible,
- evidence does not accidentally contradict ground truth,
- red herrings have alternative explanations,
- at least one valid evidence chain identifies the murderer.

---

# 31. Solvability Validator — Objective Deduction of the Full Accusation

A case MUST be provably solvable from discoverable evidence.

A heuristic percentage alone is NOT sufficient.

The validator must establish a unique solution for:

- murderer,
- motive,
- weapon,
- crime time.

The guarantee applies to the explicitly declared game model and its objectively enumerable
candidate universes.

## 31.1 Candidate Eligibility and Universe Completeness

Candidate universes MUST be derived from structured, player-visible game-model eligibility
rules, not from reviewer intuition and not from hidden solution labels.

The generator persists:

```text
SuspectUniverse
MotiveUniverse
WeaponUniverse
```

### 31.1.1 Suspect Eligibility

A person belongs to `SuspectUniverse` exactly when the published public model marks that
person with:

```text
SUSPECT_ELIGIBLE
```

Example:

```json
{
  "personId": "michael_carter",
  "publicAffordances": [
    "VISIBLE_CHARACTER",
    "SUSPECT_ELIGIBLE"
  ]
}
```

Therefore:

```text
SuspectUniverse
=
all published person IDs with SUSPECT_ELIGIBLE
```

### 31.1.2 Motive Eligibility

A motive belongs to `MotiveUniverse` exactly when the public/published case model marks the
normalized motive with:

```text
MOTIVE_CANDIDATE
```

Equivalent wording must normalize to one canonical ID before enumeration.

Example:

```json
{
  "motiveId": "cover_up_embezzlement",
  "publicAffordances": [
    "MOTIVE_CANDIDATE"
  ]
}
```

Therefore:

```text
MotiveUniverse
=
all published normalized motive IDs with MOTIVE_CANDIDATE
```

### 31.1.3 Weapon Eligibility

A world object belongs to `WeaponUniverse` exactly when its published player-visible
affordances include:

```text
POTENTIAL_WEAPON
```

Optional subtypes may include:

```text
POTENTIAL_SHARP_WEAPON
POTENTIAL_BLUNT_WEAPON
POTENTIAL_POISON
```

Example:

```json
{
  "objectId": "vase_01",
  "assetId": "PROP_HEAVY_VASE_01",
  "publicAffordances": [
    "INSPECTABLE",
    "POTENTIAL_WEAPON",
    "POTENTIAL_BLUNT_WEAPON"
  ]
}
```

Therefore:

```text
WeaponUniverse
=
all published object IDs with POTENTIAL_WEAPON
```

A visible vase cannot be silently omitted from the weapon universe if the public model gives
it the `POTENTIAL_WEAPON` affordance.

Conversely, an object without that affordance is outside the declared murder-weapon game
model even if a real-world observer could imagine using it as a weapon.

### 31.1.4 Completeness Guarantee

Universe completeness is objectively testable:

```text
persisted universe
==
complete set of IDs satisfying the corresponding public eligibility predicate
```

The test must be computed independently of:

- `CaseTruth.crime.murdererId`,
- `CaseTruth.crime.motiveId`,
- `CaseTruth.crime.weaponId`.

Artificial uniqueness caused by omitting an eligible ID is invalid.

## 31.2 Three-State Proposition Semantics

Every deduction proposition evaluates to exactly one of:

```text
supported
contradicted
unknown
```

`unknown` remains viable.

Absence of evidence MUST NOT become contradiction.

## 31.3 Rule Status and Rule Effect

Every rule must explicitly define:

```text
ruleId
propositionType
status
evidenceIds
effect
targetCandidateId
necessaryForCandidate
```

A contradicted proposition does NOT automatically eliminate its subject.

Example false alibi:

```text
Proposition:
Thomas left at 21:45.

Status:
contradicted

Effect:
ALIBI_CREDIBILITY_DECREASE

necessaryForCandidate:
false
```

Thomas remains a murderer candidate.

Candidate exclusion is allowed only when evidence contradicts a proposition that is
necessary for that candidate solution.

## 31.4 Canonical Solver Time Domain

The temporal solver MUST NOT use arbitrary real-number closed intervals.

For MVP, time reasoning uses discrete integer-second ticks in UTC.

Canonical internal unit:

```text
epochSecond: signed integer
```

All solver time sets use half-open intervals:

```text
[startInclusive, endExclusive)
```

Examples shown as local ISO-8601 timestamps are presentation/debug representations only.

Example:

```text
[22:16:10, 22:18:31)
```

represents every whole-second tick from:

```text
22:16:10
through
22:18:30
```

inclusive.

Half-open intervals are required so set difference, union, and intersection remain exact.

Example:

```text
[0,11) − [5,8)
=
[0,5) ∪ [8,11)
```

No boundary instant is incorrectly retained or discarded.

## 31.5 FeasibleCrimeTimeSet

The deduction engine independently derives:

```text
FeasibleCrimeTimeSet
```

from discoverable structured evidence.

It is represented as a normalized ordered set of disjoint half-open integer-tick intervals.

Example:

```text
[
  [22:05:00, 22:07:11),
  [22:16:10, 22:18:31)
]
```

Required exact operations:

```text
intersection
union
difference
containment
empty-test
connected-component merge
```

Normalization rules:

- intervals are sorted,
- empty intervals are removed,
- overlapping intervals are merged,
- directly adjacent integer-tick intervals are merged,
- output remains disjoint and canonical.

## 31.6 Opportunity Reasoning Uses Only FeasibleCrimeTimeSet

Opportunity MUST NOT be tested against hidden canonical crime time or hidden scoring
tolerance.

For each suspect, derive a `SuspectFeasiblePresenceSet` using:

- discoverable observations,
- structured observation uncertainty,
- travel-time rules,
- public spatial information,
- event durations,
- explicitly public deduction parameters.

Exclude a suspect for opportunity only when:

```text
SuspectFeasiblePresenceSet
∩
FeasibleCrimeTimeSet
=
∅
```

If any feasible integer-second assignment permits participation, the suspect remains viable.

Example:

```text
FeasibleCrimeTimeSet includes seconds through 22:18:30.
Michael can arrive at 22:18:00.
→ opportunity is still feasible.
→ Michael MUST NOT be excluded on opportunity.
```

## 31.7 Accepted Scoring Time Set

Player submissions are normalized to whole-second precision for MVP.

Given:

```text
canonical = T
toleranceSeconds = N
```

the inclusive human-readable scoring range:

```text
T-N through T+N
```

is represented internally as:

```text
AcceptedScoringTimeSet =
[T-N, T+N+1second)
```

Example:

```text
canonical = 22:17:00
tolerance = 120 seconds
```

Human-readable:

```text
22:15:00 through 22:19:00 inclusive
```

Internal:

```text
[22:15:00, 22:19:01)
```

## 31.8 Crime-Time Uniqueness

Crime time is uniquely deduced only when normalized `FeasibleCrimeTimeSet` contains exactly
one connected interval.

Additionally:

```text
canonicalTick ∈ FeasibleCrimeTimeSet
```

and:

```text
FeasibleCrimeTimeSet ⊆ AcceptedScoringTimeSet
```

If two disjoint feasible intervals remain, time is ambiguous.

Arbitrary UI, minute, or five-minute boundaries have no semantic role.

A single feasible interval crossing such a display boundary remains one time solution.

## 31.9 Survivor Semantics for Discrete Dimensions

For murderer, motive, and weapon:

```text
exactly one supported/viable survivor
AND
every alternative candidate is evidence-backed excluded
```

An `unknown` alternative remains viable and blocks uniqueness.

## 31.10 Murderer Deduction

PASS:

```text
exactly one suspect remains viable
AND
every alternative suspect is evidence-backed excluded
AND
survivor == CaseTruth.crime.murdererId
```

`CaseTruth` is used only for final comparison.

## 31.11 Motive Deduction

PASS:

```text
exactly one normalized motive is supported/viable
AND
every alternative motive is evidence-backed excluded
AND
survivor == CaseTruth.crime.motiveId
```

## 31.12 Weapon Deduction

PASS:

```text
exactly one eligible weapon object is supported/viable
AND
every alternative weapon is evidence-backed excluded
AND
survivor corresponds to CaseTruth.crime.weaponId
```

## 31.13 Full Unique-Solution Requirement

A valid case must satisfy:

```text
unique murderer = true
unique motive = true
unique weapon = true
single connected feasible crime-time interval = true
```

No unknown discrete alternative may remain viable.

## 31.14 Solution Proof

The SERVER-ONLY solution proof must include:

- winning discrete candidate IDs,
- evidence-backed exclusions,
- normalized feasible crime-time intervals,
- evidence contributing to time constraints,
- rule diagnostics,
- candidate-universe version/eligibility snapshot.

Example time proof:

```json
{
  "crimeTime": {
    "canonical": "2026-09-11T22:17:00+02:00",
    "acceptedScoring": {
      "startInclusive": "2026-09-11T22:15:00+02:00",
      "endExclusive": "2026-09-11T22:19:01+02:00"
    },
    "feasibleIntervals": [
      {
        "startInclusive": "2026-09-11T22:16:10+02:00",
        "endExclusive": "2026-09-11T22:18:31+02:00"
      }
    ],
    "criticalEvidenceIds": [
      "witness_sound_01",
      "phone_event_01",
      "cctv_hallway_02"
    ]
  }
}
```

## 31.15 Proof Independence

The deduction engine may use only:

```text
PublicCase
+ discoverable structured evidence
+ deterministic deduction rules
+ public eligibility predicates
+ declared candidate universes
+ public spatial/travel metadata
```

It MUST NOT use hidden solution values as premises.

Only after independent deduction may the result be compared to `CaseTruth`.

## 31.16 Reachability

Every critical evidence item must be reachable before accusation.

## 31.17 Robustness Test

At minimum for golden cases:

```text
remove any non-critical clue
→ full accusation remains uniquely solvable
```

and:

```text
remove critical clue
→ validator identifies which proof dimension breaks
```

## 31.18 Internal Validation Result

Internal solver diagnostics are SERVER-ONLY.

## 31.19 Public Generation Validation Response

The browser receives only an allowlisted summary DTO.

# 32. Repair, Regeneration, Resource Budget, and Admission Control

Repair and regeneration must be bounded.

Generation must never enter an unbounded retry loop.

## 32.1 Generation Attempt Identity

Every generation run has a server-owned immutable:

```text
generationAttemptId
```

All asynchronous model/provider responses must carry or be associated with that attempt.

Before applying any asynchronous result, the server must verify that:

```text
generationAttemptId == current active attempt
```

AND that the current state still permits the expected transition.

Late/stale results are discarded.

## 32.2 Recoverable vs Terminal Validation Failure

Validation outcomes must be classified as:

```text
VALID
RECOVERABLE_REPAIR
RECOVERABLE_REGENERATE
TERMINAL_FAILURE
```

Transitions:

```text
VALID
→ PUBLISHED
```

```text
RECOVERABLE_REPAIR
+ repair budget remains
→ REPAIRING
→ VALIDATING
```

```text
RECOVERABLE_REGENERATE
+ regeneration budget remains
→ GENERATING
→ VALIDATING
```

```text
TERMINAL_FAILURE
→ FAILED
```

If the required recovery budget is exhausted:

```text
→ FAILED
```

A recoverable validation failure MUST NOT transition directly to `FAILED` while its allowed
recovery path and budget remain, unless another terminal condition such as deadline
exhaustion applies.

## 32.3 FAILED Is Terminal

Once a generation attempt enters:

```text
FAILED
```

it can never transition to:

```text
GENERATING
REPAIRING
VALIDATING
PUBLISHED
```

A retry requested by the user creates a new generation attempt.

## 32.4 Late Provider Response Protection

Example race:

```text
GENERATING
→ deadline expires
→ FAILED

later:
provider response arrives
```

Required outcome:

```text
late response discarded
FAILED remains FAILED
```

Publication must use an atomic compare-and-set / transaction equivalent such as:

```text
publish only if
generationAttemptId matches
AND currentState == VALIDATING
AND validationResult == VALID
```

A late provider result MUST NOT publish a failed or superseded generation.

## 32.5 Repair and Regeneration Budgets

Canonical defaults:

```text
MAX_REPAIR_PASSES = 2
MAX_FULL_REGENERATIONS = 1
```

All recovery counters belong to one `generationAttemptId`.

Exhaustion results in:

```text
GENERATION_BUDGET_EXHAUSTED
→ FAILED
```

## 32.6 Total Generation Deadline

One wall-clock deadline covers:

- initial generation,
- retries,
- repairs,
- regeneration,
- validation-related provider calls.

Canonical default:

```text
CASE_GENERATION_DEADLINE_SECONDS = 60
```

Deadline exhaustion from ANY nonterminal generation state results in:

```text
FAILED
```

and invalidates all outstanding asynchronous results for that attempt.

## 32.7 Model-Call Budget

Canonical default:

```text
MAX_LLM_CALLS_PER_GENERATION = 8
```

Initial generation, retries, repairs, and regenerations all count toward the same attempt
budget.

## 32.8 Input and Output Bounds

Recommended initial limits:

```text
MAX_PROMPT_CHARS = 4000
MAX_CHARACTERS = 8
MAX_LOCATIONS = 8
MAX_EVIDENCE_ITEMS = 50
MAX_EMAILS = 20
MAX_CHAT_MESSAGES = 80
MAX_PHONE_RECORDS = 50
MAX_CCTV_EVENTS = 50
MAX_DOCUMENT_CHARS = 12000
MAX_SINGLE_TEXT_FIELD_CHARS = 4000
```

Every major generated collection/string must be bounded.

## 32.9 Stable Quota Identity

Generation throttling MUST NOT use `creatorAccessToken`, `caseId`, `generationId`, or another
per-case credential as the sole quota identity.

Before case creation, the server establishes a separate:

```text
anonymousQuotaSessionId
```

This identity is independent of case/playthrough credentials and remains stable for the
configured quota window.

Conceptually:

```text
AnonymousQuotaSession
    ├── Case A / creator credential A
    ├── Case B / creator credential B
    └── Case C / creator credential C
```

All those cases consume the same per-session quota.

Obtaining a new creator token MUST NOT reset:

```text
MAX_GENERATIONS_PER_SESSION_PER_WINDOW
```

## 32.10 Aggregate Admission Limits

Because an anonymous user may attempt to obtain multiple sessions, hosted-demo protection
must also include deployment-level admission controls independent of individual case
credentials.

At minimum:

```text
MAX_CONCURRENT_GENERATIONS_GLOBAL
MAX_GENERATIONS_GLOBAL_PER_WINDOW
```

Optionally, deployment policy may add a privacy-preserving network-derived rate-limit key,
but correctness must not depend solely on it.

Admission control MUST run before the first provider/model call.

Rejected admission must consume no LLM/provider call budget.

## 32.11 Request Rate and Concurrency

At minimum configure:

```text
MAX_CONCURRENT_GENERATIONS
MAX_CONCURRENT_GENERATIONS_GLOBAL
MAX_GENERATIONS_PER_SESSION_PER_WINDOW
MAX_GENERATIONS_GLOBAL_PER_WINDOW
MAX_REQUEST_BODY_SIZE
```

Exceeding a limit returns a terminal user-safe error rather than queueing indefinitely.

## 32.12 Repair Scope

During `REPAIRING`, the server may change only draft-generated fields that are not locked by
explicit user constraints.

Repair must never mutate a published CaseVersion.

## 32.13 Publication After Repair

A repaired or regenerated draft must pass the complete validation suite again.

Only the exact active validated generation attempt may transition to `PUBLISHED`.

# 33. Prompt Handling

The application must accept:

## Structured prompts

```text
Victim: Sarah Miller
Murderer: Thomas Reed
...
```

## Natural-language prompts

```text
Create a difficult murder mystery in a hotel.
```

## Partial prompts

```text
The murderer is the accountant.
The weapon is poison.
```

The system fills in missing information.

---

# 34. Prompt Scope Recovery

If the prompt is not explicitly a detective scenario, the system may transform it into one.

Example:

```text
Create a medieval village.
```

Possible interpretation:

```text
Create a detective mystery set in a medieval village.
```

The user should be informed that the input was adapted.

---

# 35. Difficulty Levels

Optional but recommended.

```text
Easy
Medium
Hard
```

Difficulty may influence:

- number of suspects,
- number of evidence items,
- number of red herrings,
- completeness of CCTV,
- reliability of witnesses,
- quality of alibis.

Suggested:

```text
Easy:
3 suspects
1 red herring

Medium:
5 suspects
2 red herrings

Hard:
6–8 suspects
3+ red herrings
partial/inaccurate witness information
```

---

# 36. Player Evidence State

The player must have a separate evidence collection.

Example:

```json
{
  "playerCaseState": {
    "discoveredEvidenceIds": [],
    "visitedLocationIds": [],
    "interviewedPersonIds": [],
    "accusation": null
  }
}
```

---

# 37. Evidence Board

Recommended MVP or early stretch feature.

Allow the player to review collected evidence.

Initial implementation may be 2D UI.

Preferred future implementation:

- spatial VR board,
- draggable evidence cards,
- relationships drawn between cards.

Do not block MVP on full VR board functionality.

---

# 38. NPC Conversations

NPC conversations are a secondary MVP priority.

If implemented, the LLM prompt must only contain:

- NPC identity,
- NPC personality,
- known facts,
- observations,
- beliefs,
- secrets,
- lies,
- current relationship to player.

The NPC must not receive full `CaseTruth`.

---

# 39. NPC Lie Rules

A lie must be explicitly generated in the case model.

Do not allow the LLM to invent arbitrary lies that break case consistency.

Example:

```json
{
  "lieId": "lie_01",
  "personId": "thomas_reed",
  "claim": "I left at 21:45.",
  "actualFact": "Thomas left at 22:23.",
  "reason": "hide_presence"
}
```

---

# 40. API Endpoints, Generation Ownership, Playthrough Ownership, and Authorization

MVP access model:

```text
CREATOR-PRIVATE CASES
```

Generated cases are private to the anonymous creator session unless a future explicit
`PUBLIC` visibility feature is implemented.

Knowing a `caseId` alone never grants access.

## 40.1 Authentication and Quota Identity Model

Preferred MVP authorization:

```http
Authorization: Bearer <opaque-random-token>
```

Authorization credentials and quota identity are separate concepts.

Token classes:

```text
anonymousSessionToken
creatorAccessToken
playthroughAccessToken
```

The anonymous session establishes the stable quota identity.

Case-specific creator credentials MUST NOT reset or replace that quota identity.

Implementation defaults:

```text
anonymous quota session TTL/window: configurable
creator token TTL: 24 hours
playthrough token TTL: 4 hours
```

Expired authorization response:

```text
401 SESSION_EXPIRED
```

If cookie authentication is used for authorization instead, minimum defaults are:

```text
HttpOnly
Secure
SameSite=Strict
```

and mutating requests require explicit CSRF protection.

A quota-only first-party cookie may be used independently of bearer authorization if desired,
but it must not silently become an authorization credential.

## 40.2 Create Anonymous Quota Session

Before starting generation:

```http
POST /api/sessions/anonymous
```

Response:

```json
{
  "anonymousSessionToken": "...",
  "quotaWindowEndsAt": "..."
}
```

The server maps this token to a stable server-side:

```text
anonymousQuotaSessionId
```

Creating case-specific credentials later does not reset the quota.

Aggregate admission limits from §32.10 still apply even if new anonymous sessions are
requested.

## 40.3 Create / Generate Case

```http
POST /api/cases
```

Requires:

```http
Authorization: Bearer <anonymousSessionToken>
```

Before making ANY provider/model call, the server must atomically check:

- stable anonymous-session quota,
- session concurrency,
- global concurrency,
- global generation-window quota.

Input:

```json
{
  "prompt": "...",
  "difficulty": "medium"
}
```

Response after admission:

```json
{
  "caseId": "CASE-123",
  "generationId": "GEN-456",
  "generationAttemptId": "GA-789",
  "creatorAccessToken": "...",
  "status": "GENERATING"
}
```

The case is owned by the creator identity associated with that admitted anonymous session,
while `creatorAccessToken` is a case access credential rather than the generation quota
identity.

Repeated issuance of creator credentials MUST NOT reset throttling.

### Generation Progress

```http
GET /api/generations/{generationId}
```

Requires the relevant creator authorization.

Returns only sanitized progress:

```json
{
  "status": "VALIDATING",
  "progress": 78,
  "stage": "Validating deduction"
}
```

Must not expose hidden solution data or solver diagnostics.

## 40.4 Get Published Case Metadata

```http
GET /api/cases/{caseId}
```

For MVP private cases, requires creator authorization or an authorized playthrough binding.

Returns only public data for the specific published `caseVersion`.

## 40.5 Create Playthrough

```http
POST /api/cases/{caseId}/versions/{caseVersion}/playthroughs
```

Requires authorization to access that private CaseVersion.

Response:

```json
{
  "playthroughId": "PT-...",
  "caseId": "CASE-123",
  "caseVersion": 1,
  "playthroughAccessToken": "...",
  "status": "PLAYING"
}
```

A playthrough is permanently pinned to:

```text
(caseId, caseVersion)
```

Publishing a newer version does not change the binding.

## 40.6 Playthrough State Machine

Each playthrough independently follows:

```text
CREATED
  ↓
PLAYING
  ↓
ACCUSED
  ↓
REVEALED
```

A CaseVersion itself remains `PUBLISHED`.

## 40.7 Authorization Rule

Every playthrough endpoint must verify:

- token validity,
- playthrough ownership,
- `(caseId, caseVersion)` binding,
- evidence/person/object membership in that exact CaseVersion,
- action validity for current playthrough state.

Never trust client-provided lifecycle state.

## 40.8 Discover Evidence

```http
POST /api/playthroughs/{playthroughId}/evidence/{evidenceId}/discover
```

Validate:

- playthrough auth,
- case-version membership,
- evidence membership,
- reachability/preconditions,
- current playthrough state.

## 40.9 Read/View Record

```http
GET /api/playthroughs/{playthroughId}/records/{recordId}
```

Requires matching authorized playthrough and visibility/discovery rules.

## 40.10 Submit Accusation

```http
POST /api/playthroughs/{playthroughId}/accusation
```

Input:

```json
{
  "murdererId": "thomas_reed",
  "motiveId": "cover_up_embezzlement",
  "weaponId": "kitchen_knife",
  "crimeTime": "2026-09-11T22:17:00+02:00"
}
```

Server transition:

```text
PLAYING → ACCUSED
```

## 40.11 Duplicate / Concurrent Accusations

Exactly one accusation becomes authoritative per playthrough.

Recommended behavior:

First successful submission:

```text
200 / 201
```

Later competing submission:

```text
409 CASE_ALREADY_SUBMITTED
```

Concurrent requests must be transactionally serialized or protected by compare-and-set /
unique-state transition logic.

## 40.12 Reveal

```http
GET /api/playthroughs/{playthroughId}/reveal
```

Allowed only for the SAME authorized playthrough when state is:

```text
ACCUSED
or
REVEALED
```

Before accusation:

```text
403 REVEAL_NOT_AVAILABLE
```

## 40.13 Privacy and Isolation Semantics

For MVP creator-private cases:

- Player A cannot create a playthrough for Player B's private CaseVersion.
- Player A cannot reveal Player B's private CaseVersion.
- Player A cannot mutate Player B's playthrough.
- Player A cannot reuse Player B's evidence IDs against another CaseVersion.
- Merely knowing `caseId`, `caseVersion`, or `playthroughId` is insufficient.

If a future `PUBLIC` case mode is introduced, public replayability may allow creation of
independent playthroughs, but playthrough state must still remain isolated.

## 40.14 Authorization Tests

Tests must cover:

- unauthorized playthrough creation,
- guessed/foreign case ID,
- guessed/foreign caseVersion,
- cross-playthrough evidence mutation,
- cross-playthrough accusation,
- cross-playthrough reveal,
- stale version access,
- forged/expired token,
- concurrent duplicate accusation,
- new creator credentials do not reset anonymous-session quota,
- repeated case credential issuance cannot bypass throttling,
- aggregate admission rejection occurs before any provider/model call.

# 41. Security Requirement — Hidden Truth Separation

The implementation MUST enforce a strict separation between canonical hidden truth,
public case data, and player-discovered knowledge.

The three authoritative data layers are:

```text
CaseTruth
    ↓
PublicCase
    ↓
PlayerKnowledge
```

## 41.1 CaseTruth

`CaseTruth` is server-side authoritative and contains:

- murderer identity,
- true motive,
- exact crime timeline,
- true weapon,
- hidden causal facts,
- true alibis,
- true/false status of statements,
- solution proof metadata.

Requirements:

- MUST NOT be serialized into normal gameplay responses,
- MUST NOT be embedded in frontend bundles,
- MUST NOT be stored in browser-local state before reveal,
- MUST NOT be exposed through debug endpoints in production,
- MUST only become available through an explicit reveal endpoint after the game has ended.

## 41.2 PublicCase

`PublicCase` contains only information that may exist in the world independently of
whether the player has discovered it.

Examples:

- location layout,
- visible NPC identities,
- public suspect profile data,
- non-secret scene metadata,
- IDs for discoverable interactables,
- display-safe descriptions.

`PublicCase` MUST NOT contain hidden solution fields.

## 41.3 PlayerKnowledge

`PlayerKnowledge` contains only information the player has actually discovered.

Examples:

- discovered evidence IDs,
- opened emails,
- viewed CCTV records,
- interviewed NPCs,
- read bank records,
- inferred links explicitly created by gameplay.

The accusation UI and player-facing reasoning tools MUST operate on `PlayerKnowledge`,
not directly on `CaseTruth`.

## 41.4 API Data Boundary

Normal gameplay APIs MUST return only:

```text
PublicCase
+ PlayerKnowledge
```

Never:

```text
CaseTruth
```

before reveal.

Security tests must detect direct AND nested information leaks.

Forbidden pre-reveal examples include:

```json
{ "murdererId": "thomas_reed" }
```

```json
{ "role": "murderer" }
```

```json
{ "truthfulness": "false" }
```

when that flag reveals whether a suspect's alibi is false.

Also forbidden:

- `solutionProof`,
- `remainingCandidateIds`,
- `remainingMotiveIds`,
- `remainingWeaponIds`,
- hidden evidence classifications,
- server deduction traces,
- error messages containing hidden solution values,
- debug serialization of canonical truth,
- nested metadata from which the solution can trivially be inferred.

Public error responses must be sanitized.

Example:

```json
{
  "error": "CASE_VALIDATION_FAILED",
  "message": "The generated case could not be validated and will be regenerated."
}
```

Not:

```text
Thomas Reed remained the only valid murderer but weapon proof failed.
```

## 41.5 Separate Schemas

Define separate server-side models:

```text
InternalValidationResult
PublicGenerationValidationResponse
```

The public response MUST be explicitly serialized from an allowlisted schema.

Do not expose the internal model and remove fields dynamically.

Prefer:

```text
Internal model
    ↓ explicit mapping
Public DTO
```

over:

```text
Internal model
    ↓ blacklist hidden fields
Browser
```

---

# 42. Persistence

MVP may use:

- SQLite,
- local JSON persistence,
- simple database abstraction.

Persist:

- generated cases,
- public case data,
- ground truth,
- player state,
- validation results.

---

# 43. Determinism

Every case should store:

- random seed,
- generation version,
- model/provider information,
- original prompt.

Example:

```json
{
  "generation": {
    "seed": 829143,
    "generatorVersion": "0.1.0",
    "model": "...",
    "prompt": "..."
  }
}
```

This improves debugging and reproducibility.

---

# 44. Logging

Log:

- generation start/end,
- LLM calls,
- validation results,
- repair attempts,
- generation errors,
- case IDs,
- timing metrics.

Never log secrets or API keys.

---

# 45. Configuration

Use environment variables.

Canonical MVP names:

```text
LLM_API_KEY=
LLM_MODEL=
DATABASE_URL=

CASE_GENERATION_DEADLINE_SECONDS=60
MAX_LLM_CALLS_PER_GENERATION=8
MAX_REPAIR_PASSES=2
MAX_FULL_REGENERATIONS=1

MAX_CONCURRENT_GENERATIONS=
MAX_CONCURRENT_GENERATIONS_GLOBAL=
MAX_GENERATIONS_PER_SESSION_PER_WINDOW=
MAX_GENERATIONS_GLOBAL_PER_WINDOW=
MAX_PROMPT_CHARS=4000
MAX_REQUEST_BODY_SIZE=

ANONYMOUS_QUOTA_SESSION_TTL_SECONDS=
CREATOR_TOKEN_TTL_SECONDS=86400
PLAYTHROUGH_TOKEN_TTL_SECONDS=14400
```

Use exactly one canonical configuration name per setting.

Provide:

```text
.env.example
```

Never commit real credentials.

# 46. Repository Structure

Recommended:

```text
procedural-detective/
│
├── frontend/
│   ├── src/
│   ├── public/
│   └── package.json
│
├── backend/
│   ├── app/
│   │   ├── api/
│   │   ├── models/
│   │   ├── generators/
│   │   ├── validators/
│   │   ├── services/
│   │   └── providers/
│   └── pyproject.toml
│
├── shared/
│   └── schemas/
│
├── assets/
│   ├── scenes/
│   ├── props/
│   └── characters/
│
├── tests/
│
├── REQUIREMENTS.md
├── README.md
├── THIRD_PARTY.md
└── .env.example
```

---

# 47. Tests

Testing is required for core logic.

Minimum automated tests:

- schema validation,
- prompt parsing,
- CaseTruth validation,
- event timeline validation,
- evidence references,
- person references,
- duplicate IDs,
- solvability checks,
- hidden truth not exposed by public API,
- unique murderer deduction,
- unique motive deduction,
- unique weapon deduction,
- crime-time interval deduction,
- `supported / contradicted / unknown` semantics,
- candidate elimination only on evidence-backed contradiction,
- candidate roster independence from hidden solution labels,
- proof computation without `CaseTruth` premises,
- critical evidence discoverability and reachability,
- public/hidden/player-knowledge contract separation,
- nested hidden-value leak protection,
- sanitized public validation responses,
- reveal gating,
- complete candidate-universe enumeration,
- unknown alternative blocks uniqueness,
- false alibi does not exclude speaker,
- necessary-condition contradiction can exclude candidate,
- exact crime-time interval containment,
- cross-playthrough authorization/isolation,
- duplicate/concurrent accusation handling,
- escaped malicious generated text,
- invalid asset/interaction identifiers,
- blocked arbitrary URLs/filesystem paths,
- generation deadline/model-call budget exhaustion,
- published-case immutability,
- shared evidence presentation/solver consistency,
- half-open interval set-difference boundary correctness,
- FAILED generation is terminal,
- timeout during GENERATING transitions to FAILED,
- stale/late provider response cannot publish a failed generation,
- candidate universes exactly equal IDs satisfying public eligibility predicates,
- candidate eligibility computation is independent of hidden solution labels,
- new creator credentials do not reset stable quota identity,
- aggregate admission limits are checked before provider calls,
- sanitization never permits generated script/eval/new Function execution.

---

# 48. Golden Test Cases

Create fixed test prompts.

## Case A

```text
Victim: Sarah Miller
Murderer: Thomas Reed
Motive: €240,000 embezzlement
Weapon: Kitchen knife
Time: 22:17
Witness: Emily Reed
```

## Case B

```text
Create a murder mystery in a luxury hotel.
Five suspects.
The murderer is the hotel manager.
Motive: blackmail.
No direct eyewitness.
```

## Case C

```text
Create a difficult murder mystery in a laboratory.
The weapon is poison.
```

All three must generate successfully before MVP is considered stable.

---

# 47A. Shared Structured Evidence Contract

The solver and player-facing presentation MUST consume the same structured evidence facts.

Do not generate one free-form value for the UI and a separate hidden value for the solver.

Example canonical CCTV evidence:

```json
{
  "id": "cctv_exit_01",
  "kind": "cctv_observation",
  "sourceRef": {
    "cameraId": "hall_01"
  },
  "propositions": [
    {
      "type": "PERSON_OBSERVED_AT_LOCATION",
      "personId": "thomas_reed",
      "locationId": "hallway",
      "observedAt": "2026-09-11T22:23:41+02:00",
      "uncertaintySeconds": 2
    }
  ],
  "presentation": {
    "title": "Hallway Camera 01"
  }
}
```

The UI renders from the proposition:

```text
22:23:41 — Thomas Reed observed in hallway
```

The solver evaluates the same `observedAt`.

## 47A.1 Typed Propositions

Evidence must use typed proposition schemas.

Recommended MVP proposition types include:

```text
PERSON_OBSERVED_AT_LOCATION
PERSON_ENTERED_LOCATION
PERSON_EXITED_LOCATION
PERSON_POSSESSED_OBJECT
PERSON_CONTACTED_PERSON
TRANSACTION_OCCURRED
DOCUMENT_ASSERTS
WITNESS_CLAIMS
OBJECT_CONTAINS_FINGERPRINT
OBJECT_CONTAINS_BLOOD
FORENSIC_WEAPON_MATCH
TOXICOLOGY_RESULT
LOCATION_DISTANCE
TRAVEL_TIME_MINIMUM
```

Free-form prose may accompany evidence for presentation, but deduction must rely on typed
fields.

## 47A.2 Source References

Every proposition must identify its evidence/source object.

The solver must be able to explain which source IDs support or contradict a proposition.

## 47A.3 Time Uncertainty

Observations that are approximate must express uncertainty structurally.

Example:

```json
{
  "observedAt": "2026-09-11T22:20:00+02:00",
  "uncertaintySeconds": 180
}
```

Do not encode approximate time only in prose such as `"around 22:20"`.

## 47A.4 Evidence Reliability

Reliability describes source quality, not hidden truth.

Allowed conceptual values:

```text
high
medium
low
```

or a bounded public confidence model.

Reliability MUST NOT directly reveal:

- true/false statement status,
- murderer identity,
- hidden correctness.

A low-reliability witness claim may still be true; a high-reliability system record may
still require contextual interpretation.

## 47A.5 Conflict Resolution

When evidence conflicts, deterministic rules must define how propositions combine.

Example precedence guidance:

```text
objective system timestamp
> calibrated physical measurement
> direct witness observation
> suspect claim
> rumor
```

This is a reasoning policy, not an automatic truth flag.

Conflicts that cannot be resolved must leave the relevant proposition `unknown` and may
cause validation failure if uniqueness is lost.

## 47A.6 Presentation Consistency Tests

Tests must ensure:

- timestamps shown to player equal the structured timestamps used by solver,
- amounts/currencies shown equal solver values,
- person/location IDs resolve to the same entities,
- uncertainty displayed is consistent with solver uncertainty,
- no separate hidden presentation value diverges from deduction input.

# 48A. Deduction Rule Model

Core deduction rules must be structured and deterministic.

Every rule returns:

```text
proposition
status
evidenceIds
effect
targetCandidateId
necessaryForCandidate
```

Status:

```text
supported
contradicted
unknown
```

Effects are separate from status.

Recommended effect enum:

```text
NO_EFFECT
SUPPORT_CANDIDATE
EXCLUDE_SUSPECT
EXCLUDE_MOTIVE
EXCLUDE_WEAPON
EXCLUDE_TIME_WINDOW
ALIBI_CREDIBILITY_DECREASE
INCREASE_SUSPICION
ADD_TIME_CONSTRAINT
```

Critical rule:

> A contradicted proposition does not imply candidate exclusion unless that proposition
> is a necessary condition for that candidate and the rule's declared effect permits
> exclusion.

Example false alibi:

```json
{
  "propositionType": "ALIBI_TIME_CLAIM",
  "status": "contradicted",
  "effect": "ALIBI_CREDIBILITY_DECREASE",
  "necessaryForCandidate": false
}
```

Example impossible opportunity:

```json
{
  "propositionType": "CAN_REACH_CRIME_SCENE_IN_TIME",
  "status": "contradicted",
  "effect": "EXCLUDE_SUSPECT",
  "necessaryForCandidate": true
}
```

## 48A.1 Evidence Input

Rules may use only:

- shared structured evidence propositions,
- public case structure,
- declared candidate universes,
- deterministic domain rules,
- public spatial/travel metadata.

Rules MUST NOT use hidden truth values.

## 48A.2 Unknown Alternatives

If an alternative candidate remains `unknown`, it remains viable.

No dimension passes uniqueness until all alternatives are evidence-backed excluded.

## 48A.3 Reliability

Reliability may influence whether a proposition becomes supported/contradicted/unknown,
but must not act as a disguised hidden truth label.

## 48A.4 Diagnostics

Rule diagnostics are server-only before reveal.

# 48B. Public-vs-Hidden Contract Tests

Add automated tests that verify:

- `GET /api/cases/{caseId}` contains no murderer field,
- public payloads contain no hidden `truthfulness` flags where they reveal the answer,
- hidden solution proof is never returned before reveal,
- `PlayerKnowledge` grows only through explicit discovery actions,
- reveal endpoint is unavailable before game end,
- reveal endpoint matches canonical `CaseTruth`.

# 49. Performance Goals

Target:

- UI reacts immediately,
- generation progress visible,
- case generation ideally below 60 seconds,
- 3D scene loads below 10 seconds after case generation,
- desktop performance target: 30+ FPS,
- preferred target: 60 FPS.

Do not sacrifice correctness for visual complexity.

---

# 50. Error Handling

The system must gracefully handle:

- LLM timeout,
- invalid structured output,
- malformed JSON,
- unsupported assets,
- missing references,
- failed validation,
- backend unavailable,
- unsupported browser capabilities.

The UI must show actionable errors.

---

# 51. Accessibility / Usability

Minimum:

- mouse + keyboard controls,
- readable text,
- subtitles/text equivalents,
- clear interaction hints,
- no mandatory VR headset.

Preferred:

- controller support,
- WebXR,
- optional VR interaction.

---

# 52. Browser Support

Target primarily:

- Chrome,
- Edge,
- Chromium-based Quest Browser.

Firefox/Safari are secondary unless trivial to support.

---

# 53. VR / WebXR

WebXR is a stretch goal after desktop browser stability.

If enabled:

- enter/exit VR,
- teleport or smooth locomotion,
- hand/controller ray,
- object inspect/select.

Do not block core submission on advanced VR mechanics.

---

# 54. Asset Licensing

All third-party assets must be documented in:

```text
THIRD_PARTY.md
```

For each dependency/asset include:

```text
Name
Author/Provider
License
Source
Usage
```

Avoid incompatible or unclear licenses.

---

# 55. AI Disclosure

Document all AI services used.

Example:

```text
LLM Provider:
Purpose:
- case generation
- evidence generation
- repair
- NPC dialogue
```

Generated assets should also be documented where appropriate.

---

# 56. Non-Goals for MVP

Do NOT prioritize:

- photorealism,
- unrestricted world generation,
- infinite room generation,
- fully generated 3D meshes for every prop,
- complex combat,
- multiplayer,
- procedural voice acting,
- realistic forensic simulation,
- police procedural accuracy,
- large open worlds,
- native Unreal build,
- native desktop installer.

---

# 57. Product Priority Order

Implement in this order:

```text
P0
Prompt → CaseTruth

P0
CaseTruth → coherent timeline

P0
CaseTruth → Evidence Graph

P0
Consistency Validator

P0
World Graph

P0
Browser 3D scene

P0
Evidence inspection

P0
Accusation

P0
Truth Reveal

P1
Emails / chats / bank / calls / CCTV UI

P1
Red herrings

P1
Evidence board

P1
NPC dialogue

P2
WebXR

P2
Ghost reconstruction

P2
AI-generated custom 3D assets

P3
Voice input/output

P3
Advanced procedural environment generation
```

---

# 58. Definition of MVP

The MVP is complete when a user can:

1. open the website,
2. enter a crime prompt,
3. click Generate,
4. receive a valid generated case,
5. enter a generated 3D scene,
6. inspect multiple pieces of evidence,
7. read generated digital records,
8. identify a suspect,
9. submit an accusation,
10. receive a solution reveal,
11. repeat the process with a materially different prompt.

---

# 59. MVP Acceptance Test

Given:

```text
Victim: Sarah Miller
Murderer: Thomas Reed
Motive: €240,000 embezzlement
Weapon: Kitchen knife
Time: 22:17
Witness: Emily Reed
```

The application must generate a case where:

- Sarah Miller is the victim,
- Thomas Reed is the murderer,
- the motive is linked to €240,000 embezzlement,
- the murder weapon is a kitchen knife,
- the canonical murder time is 22:17,
- Emily Reed exists as a witness,
- at least 3 additional relevant persons may be generated,
- at least 3 locations exist,
- at least 10 evidence items exist,
- bank records exist,
- at least one email exists,
- at least one chat exists,
- at least one phone record exists,
- at least one CCTV-related record exists,
- at least one alibi exists,
- at least one witness statement exists,
- at least one red herring exists,
- the 3D world contains inspectable evidence,
- the solution is hidden before accusation,
- the solution reveal matches the original Ground Truth,
- deterministic deduction leaves exactly Thomas Reed as the unique valid murderer,
- deterministic deduction uniquely identifies the embezzlement cover-up motive,
- deterministic deduction uniquely identifies the kitchen knife as the murder weapon,
- discoverable evidence constrains the crime time to the configured accepted interval around 22:17,
- every critical clue used for WHO / WHY / WEAPON / WHEN is reachable before accusation,
- the deduction proof is computed independently of hidden `CaseTruth` premises,
- at least one test demonstrates `unknown` rather than false contradiction when evidence is absent,
- removing a designated non-critical clue does not make the golden case ambiguous,
- no normal gameplay API exposes hidden truth directly or through nested diagnostic fields before reveal,
- every alternative motive/weapon/suspect/time candidate is either evidence-backed excluded or the case fails validation,
- false alibi contradiction does not eliminate Thomas,
- evidence-derived crime-time interval is fully contained in the accepted scoring interval,
- candidate universes include every intentionally plausible alternative in the golden case,
- cross-playthrough modification/reveal attempts fail authorization,
- malicious generated document text is rendered inert,
- arbitrary asset URLs/paths and unknown interaction IDs are rejected,
- generation budget exhaustion terminates without unbounded retries,
- published case version cannot be modified by repair,
- player-visible critical evidence values exactly match solver-consumed structured facts,
- CaseVersion remains published while multiple playthroughs independently advance,
- a playthrough remains pinned to its original CaseVersion after a newer version is published,
- unauthorized users cannot create a playthrough for a creator-private case,
- opportunity is evaluated over evidence-derived feasible times rather than hidden canonical time,
- continuous interval reasoning produces the same solvability regardless of arbitrary display/bucket boundaries,
- generation progress API exposes no hidden solution data,
- canonical config uses `CASE_GENERATION_DEADLINE_SECONDS` with no duplicate timeout alias.

---

# 60. Eight-Week Delivery Plan

## Week 1 — Technical Skeleton

Deliver:

- repository,
- frontend,
- backend,
- deployment,
- Babylon scene,
- first-person movement,
- API communication,
- one fixed room.

Exit condition:

> A hosted URL opens a playable browser 3D scene.

---

## Week 2 — Domain Model

Deliver:

- CaseTruth,
- Person,
- Relationship,
- Event,
- Evidence,
- World Graph,
- schemas,
- validators,
- golden case.

Exit condition:

> Sarah/Thomas example can exist entirely as validated JSON.

---

## Week 3 — AI Case Generation

Deliver:

- prompt parser,
- Case Director,
- structured LLM generation,
- multiple prompts,
- deterministic metadata.

Exit condition:

> Three materially different prompts generate valid CaseTruth.

---

## Week 4 — Evidence Compiler

Deliver:

- bank records,
- email,
- chat,
- phone logs,
- CCTV events,
- alibis,
- witness statements,
- red herrings.

Exit condition:

> Generated cases are logically investigable without reading Ground Truth.

---

## Week 5 — World Generator

Deliver:

- scene templates,
- World Graph,
- object placement,
- evidence object spawning,
- interaction system.

Exit condition:

> Generated case data visibly changes the 3D world.

---

## Week 6 — Investigation Gameplay

Deliver:

- evidence discovery,
- evidence UI,
- document viewers,
- suspect profiles,
- optional NPC dialogue.

Exit condition:

> Player can investigate rather than only walk around.

---

## Week 7 — Game Loop

Deliver:

- accusation UI,
- scoring,
- solution reveal,
- evidence board,
- polish.

Exit condition:

> Full prompt-to-solution game loop works.

---

## Week 8 — Submission Hardening

Deliver:

- bug fixes,
- performance,
- browser compatibility,
- WebXR if stable,
- documentation,
- demo video,
- public repository,
- hosted demo,
- third-party/license documentation.

No major new features unless all P0 requirements are stable.

---

# 61. Hackathon Demo Target

The ideal demo should fit within approximately three minutes.

## 0:00–0:20

Enter prompt.

## 0:20–0:40

Generate case and visibly show pipeline.

## 0:40–1:20

Enter generated 3D world and inspect evidence.

## 1:20–1:50

Read financial/digital evidence and optionally question witness.

## 1:50–2:20

Review evidence board.

## 2:20–2:40

Submit accusation.

## 2:40–3:00

Reveal Ground Truth / reconstructed timeline.

---

# 62. UX Message

Primary tagline:

> **Turn a few sentences into a complete, logically consistent 3D investigation.**

Secondary tagline:

> **Every prompt. A new crime. A new world. A hidden truth.**

Technical positioning:

> **Procedural Detective is a domain-specific AI Builder for interactive mysteries.**

---

# 63. Critical Engineering Rules

RAD must follow these rules:

1. Do not expose `CaseTruth` to the client before reveal.
2. Do not let free-form LLM output directly control rendering.
3. Validate all generated data against schemas.
4. Store all generated game state as structured data.
5. Keep AI provider implementation replaceable.
6. Prefer modular reusable assets over AI-generated geometry.
7. Keep the browser demo functional without VR.
8. Prioritize a complete game loop over visual polish.
9. Keep world generation deterministic enough to debug.
10. Add automated tests for every critical validator.
11. Avoid hardcoding the Sarah/Thomas case into production logic.
12. Treat user constraints as canonical.
13. Never invent hidden truth during gameplay.
14. Generated NPC knowledge must remain separated from canonical truth.
15. Any contradiction must either be intentional or fail validation.
16. A case is not considered solvable merely because an LLM assigns it a high score.
17. A valid case must have exactly one remaining suspect after deterministic deduction.
18. Critical evidence required by the solution proof must be discoverable in normal gameplay.
19. `PublicCase`, `PlayerKnowledge`, and `CaseTruth` must be separate models and API contracts.
20. Development tooling must never kill processes based on PID alone.
21. The full accusation — murderer, motive, weapon, and crime time — must be uniquely deducible.
22. Deduction claims must use `supported`, `contradicted`, or `unknown`; never infer contradiction from missing evidence.
23. Candidate rosters must be constructed independently of hidden solution labels.
24. `CaseTruth` may only validate the final deduction result; it must never supply deduction premises.
25. Public validation responses must be allowlisted DTOs separate from internal diagnostics.
26. Internal timestamps must use canonical ISO-8601 values with timezone offsets.
27. Critical clues for every accusation field must be reachable through normal gameplay.
28. Every alternative candidate must be evidence-backed excluded; `unknown` alternatives block uniqueness.
29. Rule status and rule effect are separate; false statements do not automatically exclude their speaker.
30. Candidate universes must be finite, declared, complete for the game model, and persisted with the case version.
31. Evidence-derived crime-time intervals must lie entirely within the accepted scoring interval.
32. Every mutable gameplay endpoint must enforce playthrough authorization and case/evidence membership.
33. Generated text is inert data; generated code, arbitrary URLs, filesystem paths, and unapproved asset IDs are forbidden.
34. Generation retries, repairs, output sizes, request rates, concurrency, and model calls must be bounded.
35. Solver and UI must consume the same structured evidence facts.
36. Published CaseVersions are immutable; repair is allowed only for unpublished drafts.
37. CaseVersion lifecycle and Playthrough lifecycle are separate state machines.
38. Every Playthrough is permanently pinned to one `(caseId, caseVersion)`.
39. MVP cases are creator-private; case ID knowledge alone never grants playthrough access.
40. Opportunity reasoning must use evidence-derived feasible time sets, never hidden canonical crime time.
41. Time deduction uses continuous interval sets, not arbitrary fixed buckets.
42. Bucket/display boundaries must never determine solver uniqueness.
43. Generation/progress endpoints must have explicit sanitized API contracts.
44. Use one canonical configuration name per setting.
45. Bearer-token auth is the preferred MVP default; cookie auth requires SameSite/CSRF rules.
46. Temporal deduction uses integer-second half-open intervals `[startInclusive, endExclusive)`.
47. Solver set difference must preserve exact boundary semantics without rounding away feasible ticks.
48. Candidate universes are defined by structured public eligibility predicates and exact set equality.
49. `FAILED` generation attempts are terminal and stale asynchronous results can never publish them.
50. Generation quota identity is independent of case-specific creator credentials.
51. Aggregate admission limits are enforced before any model/provider call.
52. No sanitizer exception may permit generated script/code execution.

---

# 64. First Implementation Milestone for RAD

RAD should initially implement only this vertical slice:

```text
Prompt
 ↓
FastAPI
 ↓
Generate CaseTruth JSON
 ↓
Generate WorldGraph JSON
 ↓
React/Babylon
 ↓
Load apartment scene
 ↓
Spawn victim
 ↓
Spawn knife
 ↓
Spawn laptop
 ↓
Inspect knife
 ↓
Read one email
 ↓
Accuse Thomas
 ↓
Reveal Ground Truth
```

Use the Sarah Miller / Thomas Reed scenario as the first golden case.

Do not implement NPC dialogue, WebXR, generated CCTV video, or advanced procedural generation until this slice works end-to-end.

---

# 65. Definition of Done for Milestone 1

Milestone 1 is done when the complete secure vertical slice works end-to-end.

Required:

- `docker compose up` or equivalent starts the system,
- frontend and backend communicate,
- anonymous quota session is established before generation,
- authorized creator can submit the example prompt,
- admission limits are checked before any provider call,
- generation progress is retrievable through sanitized API,
- bounded generation produces one validated draft,
- one immutable CaseVersion is atomically published,
- browser 3D apartment loads from allowlisted asset IDs,
- generated evidence objects appear,
- at least one evidence item is interactive,
- at least one generated document is readable as escaped/inert text,
- authorized playthrough is pinned to `(caseId, caseVersion)`,
- player can submit murderer, motive, weapon, and time,
- correct truth is revealed only for that accused playthrough,
- automated tests pass.

## 65.1 Candidate Universe Completeness

Tests must derive expected universes directly from published public eligibility predicates:

```text
SUSPECT_ELIGIBLE
MOTIVE_CANDIDATE
POTENTIAL_WEAPON
```

and assert exact set equality with persisted candidate universes.

The test must not inspect hidden solution labels when constructing expected sets.

## 65.2 Full Unique Deduction

Using only eligible universes + discoverable structured evidence:

```text
Murderer: Thomas Reed
Motive: €240,000 embezzlement cover-up
Weapon: Kitchen knife
Crime time: one connected feasible interval
```

Every discrete alternative must be evidence-backed excluded.

An `unknown` alternative fails uniqueness.

## 65.3 Exact Half-Open Time Algebra

Temporal solver tests must use integer-second half-open intervals.

Required regression:

```text
[0,11) − [5,8)
=
[0,5) ∪ [8,11)
```

Boundary seconds must neither be incorrectly retained nor discarded.

Tests must also verify normalization, union, intersection, containment, and connected
component merging.

## 65.4 Opportunity Without Hidden Time

Opportunity is evaluated only against `FeasibleCrimeTimeSet`.

Example:

```text
Feasible set includes seconds through 22:18:30.
Suspect can arrive at 22:18:00.
→ suspect remains feasible.
```

## 65.5 Generation State Machine and Async Race Safety

Tests must prove:

- recoverable validation failure with repair budget enters `REPAIRING`,
- recoverable regeneration enters `GENERATING`,
- deadline exhaustion from `GENERATING` enters `FAILED`,
- `FAILED` is terminal,
- a late provider response after `FAILED` is discarded,
- stale `generationAttemptId` cannot publish,
- publication succeeds only from the active valid `VALIDATING` attempt.

## 65.6 Stable Quota Identity

Tests must prove:

- per-case creator token issuance does not reset generation quota,
- multiple cases under one anonymous quota session consume the same quota,
- repeatedly obtaining creator credentials cannot bypass throttling,
- global admission limits remain effective independently of per-case credentials,
- rejected admission occurs before any provider/model call.

## 65.7 Authorization

Tests must prove:

- foreign creator cannot create a playthrough,
- playthrough token is bound to exact `(caseId, caseVersion)`,
- cross-playthrough mutation/reveal fails,
- guessed IDs do not grant access,
- expired token returns `401 SESSION_EXPIRED`,
- one concurrent accusation becomes authoritative.

## 65.8 Generated-Content Safety

Tests must prove:

- malicious HTML/script strings remain inert,
- a markup sanitizer can only enable non-executable markup,
- generated `eval`, `new Function`, scripts, shaders, or handlers never execute,
- unknown asset IDs reject/fallback safely,
- arbitrary model URLs/paths are never fetched,
- generated interaction IDs must come from the allowlist.

## 65.9 Publication and Immutability

Only fully validated CaseVersions become playable.

After `PUBLISHED`, truth/evidence/world/proof/candidate universes cannot mutate.

# 65A. RAD Infrastructure Safety Requirements

The current RAD framework contains process-management and adapter-generation risks that
must be fixed before relying on it for automated application lifecycle control.

These requirements apply to the development tooling in this repository.

## 65A.1 Never Kill by PID Alone

A stop script MUST NOT terminate a process based solely on a previously stored PID.

Windows may reuse process IDs.

Before termination, verify process identity using multiple attributes.

Minimum recommended identity tuple:

```text
PID
+ process creation timestamp
+ executable path or command line
+ expected parent process or run token
```

If process identity cannot be proven, cleanup MUST fail safely and leave the process
untouched.

## 65A.2 Ownership Verification During Startup

A process listening on the expected port MUST NOT automatically be assumed to belong to
the launched test application.

Startup logic must verify ownership before writing process metadata.

Valid ownership indicators may include:

- launched PID or descendant process,
- matching creation timestamp,
- matching executable,
- matching command line,
- unique run identifier.

If another application binds the port during startup, RAD must report a port conflict and
must not claim or later terminate that process.

## 65A.3 Unique Run Identifier

Prefer generating a unique run token for each test-app launch.

Example:

```text
PROCEDURAL_DETECTIVE_RUN_ID=8f3c1c3e...
```

The token may be supplied through:

- environment variable,
- command-line argument,
- metadata file,
- health endpoint metadata.

The stop script should use it as part of ownership validation.

## 65A.4 Do Not Discard Tracking Metadata After Failed Shutdown

Process ownership metadata MUST NOT be deleted merely because the expected port becomes
free.

If process termination returns an error or a tracked descendant may still be alive:

- retain diagnostic metadata,
- verify all owned processes,
- report incomplete shutdown,
- only clear ownership state after successful verified cleanup.

## 65A.5 Adapter Drift Detection

Adapter drift checks must detect both:

- missing expected generated files,
- obsolete generated files that should no longer exist.

The generator should compare the complete generated-file set with the expected manifest
output.

Stale generated files must cause drift validation to fail.

## 65A.6 Manifest-Driven Workflow Paths

Generated adapter commands must not hardcode workflow locations when those locations are
defined by a manifest.

Changing a manifest workflow path must update generated adapters automatically.

The manifest is authoritative.

## 65A.7 Tooling Tests

Add automated tests for:

- PID reuse simulation,
- foreign process bound to expected port,
- failed termination with surviving child process,
- stale generated adapter detection,
- manifest workflow path changes.

Dangerous process tests should use isolated dummy processes and must never target arbitrary
system processes.

# 65B. Security Acceptance Criteria for RAD Tooling

RAD tooling is considered safe enough for automated use when:

1. no kill operation uses PID as its sole trust signal,
2. foreign listeners cannot be claimed as owned,
3. failed cleanup does not silently discard ownership metadata,
4. obsolete generated adapters are detected,
5. generated workflow paths follow the manifest,
6. regression tests cover each previously identified issue.

# 66. Implementation Philosophy

The application must not attempt to generate a perfect universe.

It must generate a **small, coherent, solvable investigation**.

Prefer:

```text
4 rooms
6 people
25 meaningful evidence items
1 coherent mystery
```

over:

```text
50 rooms
30 NPCs
200 random clues
an inconsistent story
```

The product wins if the generated case feels intentional, coherent, replayable, and demonstrably AI-native.
