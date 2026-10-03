"""Trusted LOCAL structured-output schemas for the bridge client.

Phase 26C2 (R1) — the bridge job frame carries an authoritative ``schemaId``
(the server's closed allowlist already validated it on both protocol copies);
this module maps a SMALL subset of those ids to a trusted local JSON Schema
that the bridge client hands to the operator-owned Ollama as ``format``.
Never any browser/job-supplied schema: the mapping is a fixed local constant.

Why only ``ACTIVITY_LOG_v1`` / ``ACTIVITY_LOG_REPAIR_v1``: those two ids are
the live residual evidence. A direct Ollama call receives the authoritative
transport JSON Schema (``prompts.json_schema_for_generation_stage`` — the
single source the strict server validator derives from) and its grammar
enforces ``minItems``/``maxItems``/``enum``; the bridge path sent only free-form
``format: "json"``, so 21/29-entry over-runs and occasional invalid
``activityType`` values reached the model on the Bridge but not on direct
Ollama. This module closes that channel with a TRUSTED copy of the SAME
schema.

Drift guard: ``bridge/tests/test_vocabulary_drift.py`` structurally asserts
``activationlog_schema() == schema_contract_as_json_schema("activity_log")``
against the backend copy, so a server-side contract change without a client
update FAILS CI instead of silently diverging.

Fails closed: an unknown / unsupported schemaId returns ``None`` and the
``OllamaClient`` keeps ``format: "json"`` (documented fallback).
"""

from __future__ import annotations

from typing import Any, Mapping

# The authoritative schemaId subset the bridge maps to a trusted local schema.
# MUST stay in sync with the server-side ``STAGE_TO_SCHEMA_ID`` registrations for
# ACTIVITY_LOG / ACTIVITY_LOG_REPAIR (the drift guard pins the two ids).
TRUSTED_SCHEMA_IDS: frozenset[str] = frozenset(
    {
        "ACTIVITY_LOG_v1",
        "ACTIVITY_LOG_REPAIR_v1",
    }
)

# Bounded deterministic size guard for the trusted local schemas (defense in
# depth: a local copy that somehow grew unbounded would be a programming error,
# never a wire input). The current schema is ~700 bytes.
_MAX_TRUSTED_SCHEMA_BYTES = 8192


def _activity_log_schema() -> dict[str, Any]:
    """The TRUSTED local copy of the authoritative ACTIVITY_LOG transport JSON
    Schema (``schema_contract_as_json_schema("activity_log")``).

    This constant is a MIRROR of the backend single-source schema; the drift
    guard test enforces structural equality with the backend copy so the two
    can never diverge silently. It intentionally carries the exact subset the
    local Ollama's grammar enforces (probed on Ollama 0.35.0 with the real
    hermes3:8b): ``required``, ``minItems``, ``maxItems`` and the closed
    ``activityType`` enum.
    """
    return {
        "properties": {
            "entries": {
                "description": "array of 15..20 chronological entries",
                "items": {
                    "properties": {
                        "activity": {
                            "type": "string",
                        },
                        "activityType": {
                            "enum": [
                                "APPLICATION_CLOSE",
                                "APPLICATION_OPEN",
                                "BACKGROUND_SYNC",
                                "BACKUP",
                                "BROWSER_ACTIVITY",
                                "CLOUD_SYNC",
                                "DOCUMENT_ACCESS",
                                "DOCUMENT_AUTOSAVE",
                                "FILE_COPY",
                                "FILE_OPEN",
                                "FILE_WRITE",
                                "LOCAL_ACTIVITY",
                                "LOGIN",
                                "LOGOUT",
                                "MAIL_SYNC",
                                "NETWORK_ACTIVITY",
                                "SESSION_LOCK",
                                "SESSION_UNLOCK",
                                "SYSTEM_IDLE",
                                "SYSTEM_RESUME",
                                "USB_CONNECTED",
                            ],
                            "type": "string",
                        },
                        "timestamp": {
                            "type": "string",
                        },
                    },
                    "required": [
                        "activity",
                        "activityType",
                        "timestamp",
                    ],
                    "type": "object",
                },
                "maxItems": 20,
                "minItems": 15,
                "type": "array",
            }
        },
        "required": [
            "entries",
        ],
        "type": "object",
    }


# The trusted mapping (one schema; the REPAIR stage shares the SAME
# activity-log contract — exactly like the server's ``STAGE_TO_CONTRACT``).
_ACTIVITY_LOG_SCHEMA: Mapping[str, Any] = _activity_log_schema()

TRUSTED_SCHEMAS: Mapping[str, Mapping[str, Any]] = {
    "ACTIVITY_LOG_v1": _ACTIVITY_LOG_SCHEMA,
    "ACTIVITY_LOG_REPAIR_v1": _ACTIVITY_LOG_SCHEMA,
}


def schema_for(schema_id: str | None) -> Mapping[str, Any] | None:
    """The trusted local JSON Schema for an authoritative ``schemaId``, or None.

    ``None`` means the id is NOT in the trusted subset and the Ollama call keeps
    the documented free-form ``format: "json"`` fallback. The id must already
    have passed the strict job-frame allowlist on both protocol copies; this
    lookup ONLY decides the local ``format`` value — it can never introduce a
    schema the operator's local Ollama was not already trusted to receive.
    """
    if schema_id is None:
        return None
    return TRUSTED_SCHEMAS.get(schema_id)


__all__ = [
    "TRUSTED_SCHEMA_IDS",
    "TRUSTED_SCHEMAS",
    "schema_for",
    "_MAX_TRUSTED_SCHEMA_BYTES",
]