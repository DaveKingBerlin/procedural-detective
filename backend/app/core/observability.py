"""Sanitized structured lifecycle logging for Phase 17E."""

from __future__ import annotations

import json
import logging
import logging.handlers
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SERVICE_LOGGER_NAME = "procedural-detective"
DEFAULT_LOG_FILE = "logs/procedural-detective.log"
LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 3

_SAFE_FIELDS = frozenset(
    {
        "caseId", "caseVersion", "generationId", "generationAttemptId", "playthroughId",
        "stage", "elapsedMs", "totalElapsedMs", "configuredGenerationDeadlineMs",
        "deadlineRemainingMs", "configuredProviderTimeoutMs", "effectiveProviderTimeoutMs",
        "providerCallCount", "repairCount", "regenerationCount", "provider", "model",
        "requestBytes", "responseBytes", "structuredOutput", "success", "published",
        "failureCode", "validationOutcome", "assetRequestCount", "assetRepairCount",
        "reasonCode", "originalStage", "projectionValid",
        # Phase 19 Fix C — sanitized hierarchical provider-accounting fields
        # (§8/§13). Never raw prompts, truth, provider URLs/IPs, tokens,
        # credentials or repair prompt text.
        "semanticObjectId", "globalCallCount", "coreCallCount", "assetCallCount",
        "remainingGlobalCalls", "remainingCoreCalls", "remainingAssetCalls",
        "proceduralAssetCount", "failedAssetCount", "environmentId",
        "fallbackSource", "assetId", "matchedAlias", "provenance", "resolved",
        # Phase 22 — sanitized BYO-Ollama bridge lifecycle fields. Opaque ids /
        # safe labels / bounded numbers only: NEVER the pairing code, the
        # bridge session token, a prompt, model output, CaseTruth, the local
        # Ollama URL or client IP.
        "bridgeSessionId", "pairingSessionId", "jobId", "schemaId",
        "timeoutMs", "latencyMs", "expiresInSeconds", "resultStatus",
        # Phase 19J — safe activity-log lifecycle fields (§40). Sanitized
        # counts/codes only: NEVER CaseTruth, raw provider responses, full log
        # text, prompts or hidden evidence. ``evidenceIdSafe`` is the
        # evidence id scrubbed through ``_sanitize_object_id_for_message``.
        "evidenceIdSafe", "entryCount", "validatorCode",
        # Phase 23 — safe witness-interview lifecycle fields (§38). The closed
        # question type, the bounded per-interview discovery count and the
        # public witness id are player-safe metadata: NEVER the statement
        # text, observations, CaseTruth, hidden evidence or provider output.
        "questionType", "newDiscoveryCount", "witnessId",
    }
)
_DEBUG_FIELDS = frozenset({"issueCodes", "validatorIssueCodes", "geometryIssueCodes", "templateVersion", "fieldNames"})
_generation_debug_logs = False

# PD-SEC-06: the production environment marker. ``ENVIRONMENT=production``
# triggers the development-trace prohibition below.
PRODUCTION_ENVIRONMENT = "production"


class JsonEventFormatter(logging.Formatter):
    """Emit one safe JSON object per line."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            # Only lifecycle events emitted through emit_event carry a safe
            # allowlisted payload. Unstructured third-party messages (notably
            # HTTP client request logs) are deliberately reduced to a marker;
            # they must never leak URLs, prompts, tokens, or provider output.
            "event": getattr(record, "pd_event", "unstructured_log"),
        }
        fields = getattr(record, "pd_fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _level(value: str | None) -> int:
    return getattr(logging, str(value or "INFO").upper(), logging.INFO)


def enforce_production_trace_policy(settings: Any | None = None) -> None:
    """PD-SEC-06: production startup REJECTS development trace logging.

    When ``ENVIRONMENT=production`` and either ``PD_DEV_TRACE=true`` or
    ``PD_GENERATION_DEBUG_LOGS=true`` is enabled, startup is refused with a
    clear SANITIZED ``RuntimeError`` (the phase-preferred policy: reject, not
    force-off-and-warn). The error is deliberately generic — it never echoes
    operator values, filesystem paths or stack traces.

    Because every production entrypoint constructs the app through
    ``create_app`` (which calls this before logging is configured), a
    trace-enabled production process can never serve a request.
    """
    environment = str(
        getattr(settings, "environment", None)
        or os.environ.get("ENVIRONMENT", "")
        or "development"
    )
    if environment != PRODUCTION_ENVIRONMENT:
        return
    dev_trace = os.environ.get("PD_DEV_TRACE") == "true"
    debug_logs = bool(getattr(settings, "pd_generation_debug_logs", False))
    if dev_trace or debug_logs:
        raise RuntimeError(
            "refusing to start in production with development trace logging "
            "enabled (PD_DEV_TRACE and PD_GENERATION_DEBUG_LOGS must be false "
            "or unset in production)"
        )


def configure_logging(settings: Any | None = None) -> None:
    """Configure console logging and optional bounded file logging once."""

    global _generation_debug_logs
    _generation_debug_logs = bool(
        getattr(settings, "pd_generation_debug_logs", False)
        or os.environ.get("PD_GENERATION_DEBUG_LOGS", "").lower() in {"1", "true", "yes"}
    )
    root = logging.getLogger()
    root.setLevel(_level(getattr(settings, "pd_log_level", None) or os.environ.get("PD_LOG_LEVEL")))
    formatter = JsonEventFormatter()
    console = next((h for h in root.handlers if getattr(h, "_pd_console", False)), None)
    if console is None:
        console = logging.StreamHandler(sys.stdout)
        console._pd_console = True  # type: ignore[attr-defined]
        root.addHandler(console)
    console.setFormatter(formatter)

    # httpx/httpcore INFO records include the complete request URL. The
    # provider base URL may be a private LAN address, so suppress those records
    # in addition to the formatter's unstructured-message guard.
    for logger_name in ("httpx", "httpcore", "urllib3"):
        logging.getLogger(logger_name).setLevel(logging.WARNING)

    file_enabled = bool(getattr(settings, "pd_file_logs", False))
    file_value = getattr(settings, "pd_log_file", None)
    if file_enabled or file_value:
        path = Path(str(file_value or DEFAULT_LOG_FILE))
        if not path.is_absolute():
            path = Path.cwd() / path
        path.parent.mkdir(parents=True, exist_ok=True)
        key = str(path.resolve()).lower()
        handler = next((h for h in root.handlers if getattr(h, "_pd_file_path", "") == key), None)
        if handler is None:
            handler = logging.handlers.RotatingFileHandler(
                path, maxBytes=LOG_MAX_BYTES, backupCount=LOG_BACKUP_COUNT, encoding="utf-8"
            )
            handler._pd_file_path = key  # type: ignore[attr-defined]
            root.addHandler(handler)
        handler.setFormatter(formatter)


def emit_event(event: str, *, level: int = logging.INFO, **fields: Any) -> None:
    """Emit one sanitized event; unknown fields are dropped."""

    safe: dict[str, Any] = {}
    for key, value in fields.items():
        if key in _SAFE_FIELDS or (_generation_debug_logs and key in _DEBUG_FIELDS):
            if isinstance(value, (str, int, float, bool)) or value is None:
                safe[key] = value
            elif isinstance(value, (tuple, list)):
                safe[key] = [str(item)[:120] for item in value[:32]]
    logging.getLogger(SERVICE_LOGGER_NAME).log(
        level, event, extra={"pd_event": event, "pd_fields": safe}
    )


def file_logging_description(settings: Any | None = None) -> str:
    if not bool(getattr(settings, "pd_file_logs", False)) and not getattr(settings, "pd_log_file", None):
        return "console only"
    path = Path(str(getattr(settings, "pd_log_file", None) or DEFAULT_LOG_FILE))
    return f"console + {path} (rotating, 5 MB, 3 backups)"


__all__ = [
    "DEFAULT_LOG_FILE",
    "LOG_BACKUP_COUNT",
    "LOG_MAX_BYTES",
    "PRODUCTION_ENVIRONMENT",
    "configure_logging",
    "emit_event",
    "enforce_production_trace_policy",
    "file_logging_description",
]
