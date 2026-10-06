"""Phase31A (Track B — root cause class B, schema construction) — the
affordance ARRAY-ITEMS transport enum (THIRD smallest-safe fix).

PROVEN ROOT CAUSE (post-fix live Dev-Box evidence): after the Cohere schema
adapter and the OpenAI envelope content extraction, BOTH Cohere and DeepSeek
reliably hit REPAIR_BUDGET_EXHAUSTED with validator codes
``["STRUCTURED_OUTPUT_INVALID", "VALIDATION_FAILED"]`` (repairEffectiveness
UNCHANGED across passes 0/1/2). The free-text validator issues were
``... value 'discovered' is not in the documented vocabulary`` /
``'dusting for prints'`` / ``'investigate'`` / ``'autopsy'`` plus unresolved-id
cross-reference issues. Root cause class B: the canonical closed affordance
vocabulary exists (``app.generation.schemas.AFFORDANCE_VOCABULARY``), and the
strict parser + validators ALREADY enforce it — but the FULL-DRAFT / stage
OUTPUT JSON Schema rendered every ``affordances`` field as an unconstrained
``{"type":"array","items":{"type":"string"}}`` (no enum), so the structured
output grammar never taught the model the documented tokens. The model freely
invented free-text affordances; the correct parser then rejected them on every
pass, and repairs could not converge (the repair prompt must keep fixing the
same invented tokens).

THE FIX (nothing else): ``prompts._contract_to_json_schema`` now renders ANY
list-of-strings hint found under a key named ``affordances`` as
``{"type":"array","items":{"type":"string","enum":<sorted canonical
vocabulary>}}``. Both the stage-output derivation
(``json_schema_for_stage_output``) and the Ollama transport derivation
(``json_schema_for_generation_stage`` — SAME function) therefore carry the
closed enum. This is a server-owned transport-schema STRENGTHENING only: the
strict parsers, every validator, budgets, deadlines, repair limits, prompt
text, registry, provider vocabulary, the OpenAI envelope extraction and the
Cohere open-object adapter are all untouched and remain the sole acceptance
authority.

Every test proves ONE regression property:

  1. ``affordances`` arrays in ``public_world`` and ``repair``
     (persons/motives/objects) carry ``items.type == "string"`` AND
     ``items.enum == sorted(AFFORDANCE_VOCABULARY)`` (closed + deterministic);
  2. the change is SCOPED: the evidence stage-output schema/fingerprint are
     BYTE-IDENTICAL (evidence has NO ``affordances`` anywhere; the pinned
     fingerprint ``c9c675fe...c0c0`` + canonical byte length 1480 are the
     before/after proof), and every NON-affordance list-of-strings shape
     (``rooms`` / ``tags`` / ``locationTokens`` / ``travelRules`` object
     arrays) stays plain;
  3. golden fixtures (valid tokens SUSPECT_ELIGIBLE / VISIBLE_CHARACTER /
     INSPECTABLE / POTENTIAL_WEAPON / MOTIVE_CANDIDATE / POTENTIAL_SHARP_WEAPON)
     still parse and publish unchanged;
  4. a conformance document using ONLY vocabulary tokens passes the strict
     parser; a document using a free-text affordance ("investigate") is
     REJECTED with the EXACT unchanged issue text (validator behavior
     unchanged);
  5. the Cohere transport adapter preserves the affordance enum byte-identical
     (it rewrites ONLY open objects, so the closed enum nodes pass through);
  6. the Ollama transport contract (``json_schema_for_generation_stage``)
     carries the SAME enum and stays consistent with the stage-output
     derivation.

Every external provider is a mock; no real paid call is ever made.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.domain.public import AFFORDANCES  # noqa: E402
from app.generation import parser, prompts  # noqa: E402
from app.generation import schema_adapters as sa  # noqa: E402
from app.generation.frontier_registry import (  # noqa: E402
    STRUCTURED_OUTPUT_MODE_OPENAI_JSON_SCHEMA,
)
from app.generation.provider import GenerationStage  # noqa: E402
from app.generation.schemas import (  # noqa: E402
    AFFORDANCE_VOCABULARY,
    _OPTIONAL_AFFORDANCES,
)
from app.main import create_app  # noqa: E402
from conftest import upgrade_db  # noqa: E402
from fixtures.golden_generation import (  # noqa: E402
    GOLDEN_FULL_DRAFT,
    GOLDEN_STAGE_PAYLOADS,
)
from phase5_helpers import auth, create_session  # noqa: E402

_REGISTRY_STAGES = (
    GenerationStage.CASE_TRUTH,
    GenerationStage.PUBLIC_WORLD,
    GenerationStage.EVIDENCE,
    GenerationStage.WORLD_GRAPH,
)
_G = GOLDEN_STAGE_PAYLOADS
_GOLDEN_STAGE_STRINGS = [_G[stage] for stage in _REGISTRY_STAGES]

_PROMPT = (
    "Victim: sarah_miller\nMurderer: thomas_reed\nMotive: cover_up_embezzlement\n"
    "Weapon: kitchen_knife\nTime: 2026-09-11T22:17:00+02:00\nWitness: emily_reed\n"
)

OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

# The exact pre-change fingerprint of the EVIDENCE_v1 stage-OUTPUT schema. The
# evidence contract carries NO affordances anywhere, so this fingerprint and its
# canonical byte length MUST stay byte-identical after the change — the
# cryptographic proof that the derivation change is scoped to affordance arrays
# only.
EVIDENCE_FINGERPRINT_PREFIX = "c9c675fef576d570"  # c9c675fe...c0c0
EVIDENCE_CANONICAL_BYTE_LENGTH = 1480


def _walk(node):
    """Yield every (key, value) pair of a JSON-Schema object graph."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield key, value
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _affordances_node(schema, section):
    return (
        schema["properties"][section]["items"]["properties"]["affordances"]
    )


def _make_app(database_url, **settings_overrides):
    upgrade_db(database_url)
    kwargs = dict(
        cors_allowed_origins=["http://localhost:5173"],
        max_concurrent_generations=2,
        max_concurrent_generations_global=4,
        max_generations_per_session_per_window=8,
        max_generations_global_per_window=50,
        generation_limit_per_ip_per_hour=1000,
        frontier_enabled=True,
    )
    kwargs.update(settings_overrides)
    return create_app(Settings(database_url=database_url, **kwargs))


def _dispose(application) -> None:
    application.state.engine.dispose()
    if application.state.store is not None:
        try:
            application.state.store.dispose()
        except Exception:  # noqa: BLE001 - teardown must never mask
            pass


def _post_case(client, token, prompt=_PROMPT, **extra):
    body = {"prompt": prompt}
    body.update(extra)
    return client.post("/api/v1/cases", json=body, headers=auth(token))


class _Wire:
    """httpx.post stand-in (identical contract to the Phase 30/31 wires)."""

    class _FakeResponse:
        def __init__(self, body: bytes) -> None:
            self.status_code = 200
            self._body = body

        def iter_bytes(self, chunk_size):
            yield self._body

    def __init__(self, golden=None) -> None:
        from app.generation import frontier_provider as fp_mod

        self._fp_mod = fp_mod
        self._original_post = fp_mod.httpx.post
        self.posts: list[tuple[str, dict, dict]] = []
        self._queue = list(golden or [])

    def install(self) -> "_Wire":
        self._fp_mod.httpx.post = self._post
        return self

    def restore(self) -> None:
        self._fp_mod.httpx.post = self._original_post

    def _post(self, url, json=None, headers=None, timeout=None):
        body = dict(json or {})
        self.posts.append((url, body, dict(headers or {})))
        content = self._queue.pop(0) if self._queue else "<not-json>"
        return self._FakeResponse(content.encode("utf-8"))


# --------------------------------------------------------------------------- #
# §1 — affordance ARRAY ITEMS carry the closed canonical enum
# --------------------------------------------------------------------------- #


def test_affordance_array_items_carry_the_closed_enum():
    """``public_world`` AND ``repair`` stage-OUTPUT schemas render every
    ``affordances`` array (persons/motives/objects) as
    ``{"type": "array", "items": {"type": "string", "enum":
    [<sorted canonical vocabulary>]}}`` — closed and deterministic."""
    expected_enum = sorted(AFFORDANCE_VOCABULARY)
    for stage_value in ("public_world", "repair"):
        schema = prompts.json_schema_for_stage_output(stage_value)
        assert schema is not None, stage_value
        for section in ("persons", "motives", "objects"):
            affordances = _affordances_node(schema, section)
            assert affordances["type"] == "array", (stage_value, section)
            assert affordances["items"]["type"] == "string", (stage_value, section)
            assert affordances["items"]["enum"] == expected_enum, (
                stage_value, section,
            )
            # closed: exactly the canonical vocabulary, nothing else.
            assert set(affordances["items"]["enum"]) == set(AFFORDANCE_VOCABULARY)
            # the enum is exactly the 8 documented tokens.
            assert len(affordances["items"]["enum"]) == 8
    # Deterministic across independent derivations (frozenset -> sorted list).
    again = prompts.json_schema_for_stage_output("public_world")
    assert again == prompts.json_schema_for_stage_output("public_world")


def test_affordance_vocabulary_enum_matches_the_documented_model_vocabulary():
    """The enum derives from the SINGLE canonical source: the ``AFFORDANCES``
    public tuple PLUS the documented optional weapon-subtype tokens — exactly
    what the strict parser's ``AFFORDANCE_VOCABULARY`` check enforces."""
    assert set(AFFORDANCE_VOCABULARY) == set(AFFORDANCES) | set(
        _OPTIONAL_AFFORDANCES
    )
    assert sorted(AFFORDANCE_VOCABULARY) == sorted(
        set(AFFORDANCES) | _OPTIONAL_AFFORDANCES
    )
    for token in (
        "SUSPECT_ELIGIBLE",
        "VISIBLE_CHARACTER",
        "INSPECTABLE",
        "POTENTIAL_WEAPON",
        "MOTIVE_CANDIDATE",
        "POTENTIAL_SHARP_WEAPON",
        "POTENTIAL_BLUNT_WEAPON",
        "POTENTIAL_POISON",
    ):
        assert token in AFFORDANCE_VOCABULARY


# --------------------------------------------------------------------------- #
# §2 — the change is SCOPED (evidence byte-identical; no other list gains enum)
# --------------------------------------------------------------------------- #


def test_evidence_schema_fingerprint_and_bytes_byte_identical():
    """The EVIDENCE_v1 stage-OUTPUT schema carries NO affordances anywhere, so
    the derivation change leaves it BYTE-IDENTICAL: the pinned fingerprint
    ``c9c675fe...c0c0`` and the canonical byte length 1480 are unchanged
    (a cryptographic proof of scoping), and the schema has no ``affordances``
    key at any depth."""
    assert (
        prompts.schema_fingerprint_for_stage_output("evidence").startswith(
            EVIDENCE_FINGERPRINT_PREFIX
        )
    )
    assert (
        prompts.schema_fingerprint_for_generation_stage("evidence").startswith(
            EVIDENCE_FINGERPRINT_PREFIX
        )
    )
    assert (
        prompts.schema_byte_length_for_stage_output("evidence")
        == EVIDENCE_CANONICAL_BYTE_LENGTH
    )
    evidence = prompts.json_schema_for_stage_output("evidence")
    affordance_keys = [
        key for key, _value in _walk(evidence) if key == "affordances"
    ]
    assert affordance_keys == []


def test_non_affordance_list_of_strings_shapes_stay_plain():
    """Scoping proof on the OTHER list-of-strings / array shapes: only a list
    under a key literally named ``affordances`` gains the enum. ``rooms`` /
    ``tags`` / ``locationTokens`` stay permissive string arrays and
    ``travelRules`` stays an array of objects — no unrelated schema drift."""
    # world_requirements: locationTokens (list of strings) + object tags.
    world_req = prompts.schema_contract_as_json_schema("world_requirements")
    assert world_req["properties"]["locationTokens"] == {
        "type": "array",
        "items": {"type": "string"},
    }
    assert world_req["properties"]["objects"]["items"]["properties"]["tags"] == {
        "type": "array",
        "items": {"type": "string"},
    }
    # world_graph stage-output: worldGraph.locations[].rooms (list of strings).
    wg = prompts.json_schema_for_stage_output("world_graph")
    assert wg["properties"]["worldGraph"]["properties"]["locations"]["items"][
        "properties"
    ]["rooms"] == {"type": "array", "items": {"type": "string"}}
    # case_truth transport: travelRules is an array of OBJECT nodes (never a
    # string enum) and its section objects gain no enum.
    ct = prompts.json_schema_for_generation_stage("case_truth")
    travel_rules = ct["properties"]["travelRules"]
    assert travel_rules["type"] == "array"
    assert travel_rules["items"]["type"] == "object"
    assert "enum" not in travel_rules["items"]
    # Every ENUM anywhere in the case_truth transport is exactly the closed
    # affordance vocabulary (the ONLY enum-bearing nodes are the persons/
    # motives ``affordances`` arrays — no other enum marker exists here).
    all_enums = [
        value["enum"]
        for _key, value in _walk(ct)
        if isinstance(value, dict) and "enum" in value
    ]
    assert all_enums
    for enum in all_enums:
        assert enum == sorted(AFFORDANCE_VOCABULARY)


# --------------------------------------------------------------------------- #
# §3 — golden fixtures (valid vocabulary tokens) still parse and publish
# --------------------------------------------------------------------------- #


def test_golden_fixtures_parse_and_publish_unchanged(database_url):
    """Every controller stage golden (whose affordances use only the documented
    tokens like SUSPECT_ELIGIBLE / VISIBLE_CHARACTER / INSPECTABLE /
    POTENTIAL_WEAPON / MOTIVE_CANDIDATE / POTENTIAL_SHARP_WEAPON) still passes
    the STRICT parser, and an end-to-end mocked DeepSeek run still PUBLISHES
    with the PARSER-SHAPED stage schemas (now carrying the enum) on the wire."""
    # direct parser proof
    for stage in _REGISTRY_STAGES:
        parsed = parser.parse_stage(stage, _G[stage], non_throwing=False)
        assert parsed is not None, stage
        assert parser.collect_issues(stage, _G[stage]) == (), stage
    assert parser.collect_full_draft_issues(GOLDEN_FULL_DRAFT) == ()

    # end-to-end publish (mocked wire, zero repairs)
    wire = _Wire(golden=list(_GOLDEN_STAGE_STRINGS))
    wire.install()
    try:
        application = _make_app(database_url)
        try:
            with TestClient(application) as c:
                token, _ = create_session(c)
                res = _post_case(
                    c, token,
                    generationProvider="frontier",
                    frontier={
                        "provider": "openrouter",
                        "apiKey": "AFF-ENUM-KEY",
                        "model": "deepseek/deepseek-chat",
                    },
                )
                assert res.status_code == 201, res.text
                assert res.json()["status"] == "PUBLISHED"
                assert len(wire.posts) == 4, wire.posts
                for index, stage in enumerate(_REGISTRY_STAGES):
                    _url, body, _h = wire.posts[index]
                    rf = (body.get("response_format") or {}).get("json_schema")
                    assert rf is not None
                    assert rf["schema"] == prompts.json_schema_for_stage_output(
                        stage.value
                    ), stage
        finally:
            _dispose(application)
    finally:
        wire.restore()


# --------------------------------------------------------------------------- #
# §4 — conformance: vocabulary-only doc passes; free-text affordance rejected
# --------------------------------------------------------------------------- #


def test_vocabulary_only_document_passes_and_free_text_still_rejected():
    """A public_world document whose affordance arrays use ONLY canonical
    vocabulary tokens passes the STRICT parser exactly as before; the SAME
    document with a free-text affordance (e.g. 'investigate') is REJECTED with
    the exact unchanged issue text. The parser behavior is untouched — the fix
    only changed the transport GRAMMAR."""
    doc = json.loads(_G[GenerationStage.PUBLIC_WORLD])
    doc["persons"][0]["affordances"] = [
        "SUSPECT_ELIGIBLE",
        "VISIBLE_CHARACTER",
        "INSPECTABLE",
    ]
    good = json.dumps(doc)
    parsed = parser.parse_stage(
        GenerationStage.PUBLIC_WORLD, good, non_throwing=False
    )
    assert parsed is not None
    assert parser.collect_issues(GenerationStage.PUBLIC_WORLD, good) == ()

    doc["persons"][0]["affordances"] = ["investigate"]
    bad = json.dumps(doc)
    # non_throwing=True returns None (the strict parser does not raise for a
    # THROWAWAY probe); the issue text is the exact unchanged message.
    assert (
        parser.parse_stage(
            GenerationStage.PUBLIC_WORLD, bad, non_throwing=True
        )
        is None
    )
    issues = parser.collect_issues(GenerationStage.PUBLIC_WORLD, bad)
    assert (
        "public_world.persons[0].affordances[0] value 'investigate' is not in "
        "the documented vocabulary"
    ) in issues
    # motives/objects keep the same behavior (the live evidence patterns).
    motive_doc = json.loads(_G[GenerationStage.PUBLIC_WORLD])
    motive_doc["motives"][0]["affordances"] = ["discovered"]
    motive_issues = parser.collect_issues(
        GenerationStage.PUBLIC_WORLD, json.dumps(motive_doc)
    )
    assert (
        "public_world.motives[0].affordances[0] value 'discovered' is not in "
        "the documented vocabulary"
    ) in motive_issues
    assert (
        "public_world.persons[1].affordances[0] value 'autopsy' is not in "
        "the documented vocabulary"
    ) not in motive_issues  # untouched persons parse fine


# --------------------------------------------------------------------------- #
# §5 — the Cohere transport adapter preserves the affordance enum
# --------------------------------------------------------------------------- #


def test_cohere_adapter_preserves_the_affordance_enum():
    """The Cohere-compatible transport adapter rewrites ONLY open objects
    (``{"type":"object","additionalProperties":true}`` with no properties/
    required). The new closed enum nodes are never open objects, so they pass
    through the adapter BYTE-IDENTICAL: the adapted Cohere public_world wire
    still carries the affordance enum wherever affordances appear."""
    canonical = prompts.json_schema_for_stage_output("public_world")
    adapter = sa.schema_adapter_id_for_frontier_call(
        "cohere/command-a-plus", STRUCTURED_OUTPUT_MODE_OPENAI_JSON_SCHEMA
    )
    assert adapter == sa.SCHEMA_ADAPTER_ID_COHERE_OPENAI_JSON_SCHEMA
    adapted = sa.adapt_schema_for_transport(canonical, adapter=adapter)
    expected_enum = sorted(AFFORDANCE_VOCABULARY)
    for section in ("persons", "motives", "objects"):
        canonical_node = _affordances_node(canonical, section)
        adapted_node = _affordances_node(adapted, section)
        assert adapted_node == canonical_node, section  # byte-identical
        assert adapted_node["items"]["enum"] == expected_enum, section
    # The unaffected evidence schema (no affordances) is unchanged by THIS fix:
    # its canonical fingerprint is still the pinned c9c675fe...c0c0 value and
    # it carries no affordances node at any depth. (The Cohere adapter DOES
    # intentionally rewrite the open ``structured`` object per the proven
    # Track-A fix — that is a separate, already-covered adaptation.)
    evidence = prompts.json_schema_for_stage_output("evidence")
    assert (
        prompts.schema_fingerprint(evidence).startswith(EVIDENCE_FINGERPRINT_PREFIX)
    )
    assert [k for k, _v in _walk(evidence) if k == "affordances"] == []
    adapted_evidence = sa.adapt_schema_for_transport(evidence, adapter=adapter)
    assert [k for k, _v in _walk(adapted_evidence) if k == "affordances"] == []
    # deterministic adapter output for evidence (Track A unchanged).
    assert adapted_evidence == sa.adapt_schema_for_transport(
        evidence, adapter=adapter
    )


# --------------------------------------------------------------------------- #
# §6 — the Ollama transport schema shares the derivation (carries the enum)
# --------------------------------------------------------------------------- #


def test_ollama_transport_schema_carries_the_same_enum():
    """``json_schema_for_generation_stage`` (the Ollama/Direct/Bridge transport
    contract) uses the SAME ``_contract_to_json_schema`` derivation, so the
    affordance enum applies to Ollama's grammar too — a STRENGTHENING (the
    documented tokens are all still renderable)."""
    expected_enum = sorted(AFFORDANCE_VOCABULARY)
    for stage_value in ("public_world", "repair"):
        schema = prompts.json_schema_for_generation_stage(stage_value)
        assert schema is not None, stage_value
        for section in ("persons", "motives", "objects"):
            affordances = _affordances_node(schema, section)
            assert affordances["items"]["type"] == "string"
            assert affordances["items"]["enum"] == expected_enum, (
                stage_value, section,
            )
        # both consumers derive from the SAME trusted contract — the
        # stage-output and generation-stage representations stay consistent.
        assert schema == prompts.json_schema_for_stage_output(stage_value)
    # evidence (no affordances) is untouched in the Ollama transport too.
    assert prompts.json_schema_for_generation_stage("evidence") == (
        prompts.json_schema_for_stage_output("evidence")
    )