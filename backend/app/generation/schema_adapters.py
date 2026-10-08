"""Phase31A (Track A) — server-owned transport schema adaptation (Cohere).

PROVEN ROOT CAUSE (``ADVERSARIAL_REVIEW.md`` — "Phase 31A — Live Dev-Box
root-cause session", Track A): cohere/command-a-plus rejects the EVIDENCE_v1
``response_format`` schema with HTTP 400 (public ``FRONTIER_PROVIDER_ERROR``,
internal ``safeProviderErrorClass=SCHEMA_REJECTED``; the Phase31A-state
fingerprint was ``c9c675fe...c0c0`` / canonical byte length 1480 — Phase31B
since aligned the evidence contract, changing those diagnostic pins). The
canonical evidence schema contains an OPEN object node — the evidence
proposition ``structured`` field: ``{"type": "object",
"additionalProperties": true}`` with NO ``required`` list. Cohere's official
JSON-Schema structured-output contract requires EVERY object to declare at
least one ``required`` property, and ``additionalProperties`` alone does not
enforce a constraint in its JSON mode.
Live minimization proves the smallest fix: giving the open node a
``properties`` entry plus a ``required`` list (e.g.
``{"type":"object","properties":{"v":{"type":"string"}},"required":["v"]}``)
is ACCEPTED (6007-byte response).

THIS MODULE IS THE PROTOCOL-ADAPTATION LAYER (Phase31A §9). It rewrites
ONLY the TRANSPORT representation placed in the outbound ``response_format``
— never the canonical application schema. The canonical schema (``prompts.
json_schema_for_stage_output``), its stable fingerprint, the strict parsers
and the full canonical validation suite remain BYTE-IDENTICAL: the adapter
operates at the adapter/transport boundary only, and the same strict parser +
full validation run afterwards unchanged.

Adapter selection is server-owned: ``schema_adapter_id_for_frontier_call``
derives a CLOSED adapter id from the validated per-attempt model string and
the trusted registry ``structured_output_mode``. The browser can never
supply schema / response_format / endpoint / protocol — the ``FrontierBlock``
still accepts exactly provider/apiKey/model, and every adapter is a fixed
local constant.

Guard rails (documented and tested):

- pure function: the input schema mapping is NEVER mutated (a fresh deep
  copy is always built);
- NEVER ``required: []``;
- an object's EXISTING ``required`` list is never removed or altered;
- ``enum`` / ``const`` semantics are never changed;
- ONLY open objects (``{"type":"object","additionalProperties":true}`` with
  no ``properties`` and no ``required``) are transformed; nullable-union /
  property-carrying / closed objects pass through byte-identical;
- deterministic: the synthetic property is the fixed literal ``"v"`` and the
  transformation is a pure function of the schema object graph;
- the open-object INTENT is preserved in the transport representation too:
  ``additionalProperties: true`` stays set, so a model response containing
  the synthetic key (or any other object) is still accepted by the full
  canonical application validator afterward.
"""

from __future__ import annotations

from typing import Any, Callable, Mapping

from app.generation.frontier_registry import (
    STRUCTURED_OUTPUT_MODE_OPENAI_JSON_SCHEMA,
)

# The single supported schema-adapter family id: the Cohere-compatible
# normalizer for the OpenAI-JSON-Schema structured-output family (Cohere
# models are served through the trusted OpenRouter entry). The value is a
# SERVER-OWNED closed token — never a browser-supplied field.
SCHEMA_ADAPTER_ID_COHERE_OPENAI_JSON_SCHEMA = "cohere_openai_json_schema"

# The closed set of known adapter ids (fail-closed: an unknown id applies no
# adaptation).
CLOSED_SCHEMA_ADAPTER_IDS: frozenset[str] = frozenset(
    {SCHEMA_ADAPTER_ID_COHERE_OPENAI_JSON_SCHEMA}
)

# The model-family prefix that selects the Cohere-compatible normalizer
# (validated per-attempt BYOK model strings; comparison is casefolded).
_COHERE_MODEL_PREFIX = "cohere/"

# The deterministic synthetic property added to OPEN object nodes in the
# TRANSPORT representation only (Cohere's ">= 1 required property per object"
# rule). The canonical parsers treat the node as an open object
# (``additionalProperties``), so a model may emit ANY object shape and the
# full canonical validator stays the sole acceptance authority.
OPEN_OBJECT_SYNTHETIC_PROPERTY = "v"

# Phase31B — string-node keywords the Cohere-family JSON-Schema dialect is not
# proven to accept (the same limitation class that rejected the open-object
# node in Phase31A Track A). The CANONICAL schema may teach the model the
# timestamp grammar / interaction bound via ``pattern``/``format``/``maxLength``;
# the Cohere TRANSPORT representation strips those keywords (deterministic,
# narrowly-scoped — only the three string keywords, only inside the Cohere
# adapter). The STRICT parser remains the sole acceptance authority and the
# canonical schema/fingerprint stay byte-identical.
COHERE_TRANSPORT_STRIP_KEYS: frozenset[str] = frozenset(
    {"pattern", "format", "maxLength"}
)


def _deep_copy(value: Any) -> Any:
    """Iterative/recursive-free deep copy of a small JSON-schema mapping
    (dicts/list leaves are primitive JSON scalars)."""
    if isinstance(value, dict):
        return {key: _deep_copy(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_deep_copy(item) for item in value]
    return value


def _is_open_object(node: Mapping[str, Any]) -> bool:
    """True exactly for an OPEN object node: ``{"type": "object",
    "additionalProperties": true}`` (no ``properties``, no ``required``).

    Deliberately narrow per the guard rails: property-carrying objects,
    nullable-union types and closed objects are never transformed.
    """
    return (
        isinstance(node, dict)
        and node.get("type") == "object"
        and node.get("additionalProperties") is True
        and "properties" not in node
        and "required" not in node
    )


def _adapt_open_object(node: Mapping[str, Any]) -> dict[str, Any]:
    """Rewrite ONE open object into a provider-acceptable shape that keeps the
    open-object intent (``additionalProperties: true``) and adds exactly one
    synthetic required property:

        before: {"type": "object", "additionalProperties": true}
        after:  {"type": "object", "additionalProperties": true,
                 "properties": {"v": {"type": "string"}}, "required": ["v"]}

    Other keys (e.g. a ``description``) are preserved; an existing
    ``required`` list cannot exist here by ``_is_open_object``'s guard.
    """
    adapted = dict(node)
    adapted["properties"] = {
        OPEN_OBJECT_SYNTHETIC_PROPERTY: {"type": "string"}
    }
    adapted["required"] = [OPEN_OBJECT_SYNTHETIC_PROPERTY]
    return adapted


def _adapt_openai_json_schema(node: Any) -> Any:
    """Deterministic deep transformation of a JSON-Schema object graph.

    Builds a fresh copy of every container; primitives pass through by
    value. Open object nodes are rewritten by ``_adapt_open_object``; every
    other node is copied unchanged — its remaining constraints
    (``enum``/``const``/``required``/bounds) and order-insensitive structure
    are preserved. Phase31B additionally strips the ISO-string teaching
    keywords (``pattern``/``format``/``maxLength``) from the Cohere transport
    representation — a documented provider-limitation fallback the STRICT
    parser fully re-enforces.
    """
    if isinstance(node, dict):
        if _is_open_object(node):
            return _adapt_open_object(node)
        return {
            key: _adapt_openai_json_schema(value)
            for key, value in node.items()
            if key not in COHERE_TRANSPORT_STRIP_KEYS
        }
    if isinstance(node, list):
        return [_adapt_openai_json_schema(item) for item in node]
    return node


# The adapter registry: closed adapter id -> deterministic normalizer.
_ADAPTER_FACTORIES: dict[str, Callable[[Any], Any]] = {
    SCHEMA_ADAPTER_ID_COHERE_OPENAI_JSON_SCHEMA: _adapt_openai_json_schema,
}


def schema_adapter_id_for_frontier_call(
    model: str | None,
    structured_output_mode: str | None,
) -> str | None:
    """Closed server-side selection of the transport schema adapter family.

    The Cohere-compatible normalizer is selected ONLY when BOTH hold:

    - the registry entry actually declared the native OpenAI-JSON-Schema
      capability (``structured_output_mode == "openai_json_schema"``); and
    - the validated per-attempt model string is a Cohere-family model
      (``cohere/*``) — the family proven to enforce the ">= 1 required
      property per object" JSON-Schema rule.

    Every other combination returns ``None`` (the canonical schema travels to
    the provider BYTE-IDENTICAL — the exact Phase 30 / Phase 31 wire).
    Deterministic; never a browser-supplied value.
    """
    if structured_output_mode != STRUCTURED_OUTPUT_MODE_OPENAI_JSON_SCHEMA:
        return None
    if not isinstance(model, str) or not model.strip():
        return None
    if model.strip().casefold().startswith(_COHERE_MODEL_PREFIX):
        return SCHEMA_ADAPTER_ID_COHERE_OPENAI_JSON_SCHEMA
    return None


def adapt_schema_for_transport(
    schema: Mapping[str, Any],
    *,
    adapter: str | None,
) -> dict[str, Any]:
    """Return the TRANSPORT representation of a server-owned schema.

    ``adapter=None`` or an unknown adapter id returns a deep copy of the
    canonical schema UNCHANGED (identity semantics — non-Cohere providers keep
    the exact canonical wire). A known adapter id applies that family's
    deterministic rewrite. Never mutates the input mapping, never weakens the
    canonical contract (the strict parser + full validation run afterwards).
    """
    factory = _ADAPTER_FACTORIES.get(adapter) if adapter is not None else None
    if factory is None:
        return _deep_copy(schema)
    result = factory(schema)
    if not isinstance(result, dict):
        # A schema is always a JSON object; a malformed input degrades to the
        # unchanged deep copy (fail-closed, never a broken transport schema).
        return _deep_copy(schema)
    return result


__all__ = [
    "CLOSED_SCHEMA_ADAPTER_IDS",
    "COHERE_TRANSPORT_STRIP_KEYS",
    "OPEN_OBJECT_SYNTHETIC_PROPERTY",
    "SCHEMA_ADAPTER_ID_COHERE_OPENAI_JSON_SCHEMA",
    "adapt_schema_for_transport",
    "schema_adapter_id_for_frontier_call",
]