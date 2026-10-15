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

from app.assets.depthguard import MAX_STRUCT_NESTING
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


# --------------------------------------------------------------------------- #
# Phase43B (P43B-FR-07/08, AC-06/08) — parser-aligned TRANSPORT closure.
#
# The canonical MODELED objects derived from ``prompts._stage_contract`` are
# rendered as ``{"type": "object", "properties": {...}}`` WITHOUT an
# ``additionalProperties`` marker, i.e. open by JSON-Schema default. The strict
# canonical parser, however, REJECTS unknown keys (``_check_keys``). This
# projection closes every modeled object with ``additionalProperties: false``
# so a native structured-output provider can no longer be forced into emitting
# the DEF-072 ``public_world`` extra fields (``description``/``name``/
# ``locationId``) that the parser must later reject.
#
# It is a TRANSPORT-ONLY deep copy: the canonical schema, its fingerprint, the
# parser and every validator are untouched. Intentionally OPEN/type-dependent
# nodes are preserved:
#
# - any node that already declares ``additionalProperties`` (e.g. the evidence
#   proposition ``structured`` open object, and the ``presentedData`` open
#   mapping) is left unchanged;
# - the evidence ``presentation`` object is keyed as an OPEN/type-dependent
#   node (its typed public fields and its arbitrary ``rows``/``events`` maps
#   depend on the evidence ``kind``), so it is NOT closed here;
# - nodes without a non-empty ``properties`` map are not closed.
# --------------------------------------------------------------------------- #

# Object nodes whose parent key marks them as intentionally OPEN/type-dependent:
# never closed by the transport closure projection (P43B-FR-08).
_TRANSPORT_OPEN_OBJECT_KEYS: frozenset[str] = frozenset({"presentation"})


def _close_modeled_node(value: Any, field_key: str | None, depth: int) -> Any:
    """Bounded (depth-capped) deep copy that closes modeled objects.

    Never mutates the input; a container deeper than ``MAX_STRUCT_NESTING``
    is copied unchanged (bounded recursion).
    """
    if depth > MAX_STRUCT_NESTING:
        return _deep_copy(value)
    if isinstance(value, dict):
        closed = {
            key: _close_modeled_node(item, key, depth + 1)
            for key, item in value.items()
        }
        declared_type = closed.get("type")
        is_object = declared_type == "object" or (
            isinstance(declared_type, list) and "object" in declared_type
        )
        properties = closed.get("properties")
        if (
            is_object
            and isinstance(properties, Mapping)
            and properties
            and "additionalProperties" not in closed
            and field_key not in _TRANSPORT_OPEN_OBJECT_KEYS
        ):
            closed["additionalProperties"] = False
        return closed
    if isinstance(value, list):
        return [
            _close_modeled_node(item, field_key, depth + 1) for item in value
        ]
    return value


def close_transport_modeled_objects(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Return the Phase43B parser-aligned TRANSPORT closure projection.

    A deep-copied schema where every modeled object (a node with
    ``type == "object"`` and a non-empty ``properties`` map that does NOT
    already declare ``additionalProperties``) gains
    ``"additionalProperties": false``. Intentionally open/type-dependent nodes
    (see the module notes) are preserved. Never mutates the input; a malformed
    input degrades to an unchanged deep copy.
    """
    if not isinstance(schema, Mapping):
        return {}
    result = _close_modeled_node(schema, None, 1)
    if not isinstance(result, dict):
        return _deep_copy(schema)
    return result


# --------------------------------------------------------------------------- #
# Phase43B (P43B-FR-05, AC-04) — strict-schema compatibility check.
#
# ``json_schema.strict=true`` may be sent ONLY when the effective provider
# capability declares native strict JSON Schema support AND the rendered
# transport schema is a PROVEN strict-compatible combination: it uses only the
# supported keyword surface AND every modeled object is closed
# (``additionalProperties: false``) with EVERY property listed in ``required``
# (the OpenAI strict-mode requirement — optional keys are not supported).
# A schema with any optional/dangling property therefore stays in the explicit
# hint-only mode. This is deterministic and structural (never a heuristic).
# --------------------------------------------------------------------------- #

# The closed keyword surface the strict dialect is allowed to see. This is
# exactly the subset the transport schemas emit and the local validator
# enforces (plus the inert ``title``/``default`` annotations).
_STRICT_SUPPORTED_KEYWORDS: frozenset[str] = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "minItems",
        "maxItems",
        "pattern",
        "format",
        "maxLength",
        "description",
        "title",
        "default",
    }
)


def _strict_unsupported_keyword_found(node: Any, depth: int) -> bool:
    """Schema-aware walk: only ``properties``/``items``/``additionalProperties``
    values are sub-schemas (property NAMES are not schema keywords)."""
    if depth > MAX_STRUCT_NESTING:
        return True
    if not isinstance(node, Mapping):
        return False
    if any(key not in _STRICT_SUPPORTED_KEYWORDS for key in node):
        return True
    properties = node.get("properties")
    if isinstance(properties, Mapping):
        for subschema in properties.values():
            if _strict_unsupported_keyword_found(subschema, depth + 1):
                return True
    items = node.get("items")
    if isinstance(items, Mapping):
        if _strict_unsupported_keyword_found(items, depth + 1):
            return True
    additional = node.get("additionalProperties")
    if isinstance(additional, Mapping):
        if _strict_unsupported_keyword_found(additional, depth + 1):
            return True
    return False


def _strict_open_object_found(node: Any, depth: int) -> bool:
    """``True`` when any object node is NOT OpenAI-strict compatible.

    OpenAI strict JSON Schema requires EVERY object to declare
    ``additionalProperties: false``. An object that is NOT closed — including a
    genuinely OPEN no-``properties`` object (``additionalProperties: true``,
    absent, or a sub-schema mapping) — is therefore NOT strict-compatible
    (ADV-43B-03). A closed object must additionally list EVERY property in
    ``required`` (optional keys are unsupported; nullable values use a nullable
    type with the key required). A schema with any optional property is NOT a
    proven strict-capable combination and must stay in hint-only mode.
    """
    if depth > MAX_STRUCT_NESTING:
        return True
    if not isinstance(node, Mapping):
        return False
    declared = node.get("type")
    is_object = declared == "object" or (
        isinstance(declared, list) and "object" in declared
    )
    properties = node.get("properties")
    if is_object:
        # Every object node MUST be closed, whether or not it declares a
        # property map: an open no-property object is not strict-compatible.
        if node.get("additionalProperties") is not False:
            return True
        if isinstance(properties, Mapping) and properties:
            required = node.get("required")
            if not isinstance(required, list) or set(required) != set(properties):
                return True
    if isinstance(properties, Mapping):
        for subschema in properties.values():
            if _strict_open_object_found(subschema, depth + 1):
                return True
    items = node.get("items")
    if isinstance(items, Mapping):
        if _strict_open_object_found(items, depth + 1):
            return True
    additional = node.get("additionalProperties")
    if isinstance(additional, Mapping):
        if _strict_open_object_found(additional, depth + 1):
            return True
    return False


def openai_strict_schema_is_compatible(schema: Mapping[str, Any]) -> bool:
    """``True`` iff the schema is a proven strict-compatible combination.

    Deterministic, bounded (depth-capped) and purely structural: it must use
    only the supported keyword surface AND every modeled object must be
    closed (``additionalProperties: false``) with EVERY property required.
    A non-mapping schema, or any schema with an optional/dangling property, is
    incompatible (fail-closed → the adapter keeps the explicit hint-only mode).
    """
    if not isinstance(schema, Mapping):
        return False
    if _strict_unsupported_keyword_found(schema, 1):
        return False
    return not _strict_open_object_found(schema, 1)


__all__ = [
    "CLOSED_SCHEMA_ADAPTER_IDS",
    "COHERE_TRANSPORT_STRIP_KEYS",
    "OPEN_OBJECT_SYNTHETIC_PROPERTY",
    "SCHEMA_ADAPTER_ID_COHERE_OPENAI_JSON_SCHEMA",
    "adapt_schema_for_transport",
    "close_transport_modeled_objects",
    "openai_strict_schema_is_compatible",
    "schema_adapter_id_for_frontier_call",
]