#!/usr/bin/env python3
"""Phase 31 — secret-safe Frontier/BYOK benchmark runner.

Reproducible benchmark harness that reuses the PRODUCTION-EOUIVALENT
Procedural Detective generation pipeline. It is a measurement tool, never a
second generation implementation: there is no benchmark-only parser,
validator, solver, repair, schema or publication path.

Two drivers:

  --driver http       (default)  targets a running backend (Dev-Box) through
      its real public HTTP surface: POST /api/v1/sessions/anonymous then
      POST /api/v1/cases per case with the contestant's BYOK frontier block
      and records the CaseStartedDTO fields + client-measured elapsed time.
      Optional per-case rich telemetry is correlated from the app's own
      structured observability JSONL (--telemetry-logs PATH) keyed by
      generationAttemptId. Without that file the richer fields stay null —
      metrics are NEVER fabricated.

  --driver inprocess  constructs a REAL GenerationService over a local SQLite
      Store and calls start_case_generation(...) with generation_provider=
      "frontier" + the frozen BYOK block — the exact entry point the
      production API route uses. A transport mock (monkeypatched
      app.generation.frontier_provider.httpx.post) keeps hermetic tests
      offline; local real runs use the real trusted provider.

Security model:

  - Credentials load ONLY from the process environment or the git-ignored
    `.env.benchmark` file; never from CLI arguments, never from tracked
    config. Values are never printed, never written to artifacts, never
    present in exceptions; headers/raw requests are never logged.
  - The trusted provider endpoint ALWAYS comes from the server-owned
    `app.generation.frontier_registry`. Contestant config rejects arbitrary
    endpoints, inline keys, response_format / schema / custom prompt
    overrides and security/timeout/validator-disabling fields.
  - A post-run artifact scan verifies that no credential used during the run
    appears in any output artifact; a hit aborts with an infrastructure
    failure (never silently persisted).
  - Sentinel (used by the regression tests):
        SECRET-PHASE31-BENCHMARK-MUST-NOT-PERSIST
    may appear ONLY in the mocked outbound authentication slot.

Costs: token/cost fields are `null` unless truthfully derivable. The Phase 30
adapter does not surface OpenRouter usage tokens today, so costSource stays
"unavailable" unless a future safe capture provides real values.

Luna baseline: BLOCKED-EXTERNAL unless an OPERATOR pins a verified official
OpenAI API model id (none exists; `openai/gpt-6-luna` is an OpenRouter id).

Phase 29 integration: `--post-monitoring` runs the existing
`tools.monitoring_report` locally on the Dev-Box (captures Caddy logs + a DB
snapshot to a TEMP dir, produces the derived summary/json/health artifacts,
then deletes the temp raw logs/DB).

Dev-Box real-run flow (also mirrored in the --help text):

    cd ~/procedural-detective
    cp .env.benchmark.example .env.benchmark     # fill only the needed keys
    # Dev-Box Caddy 'tls internal' uses its OWN CA; export + trust it once
    # (the file is git-ignored; NEVER commit a CA/private key):
    docker compose -f docker-compose.prod.yml -f docker-compose.lan.yml \
        cp caddy:/data/caddy/pki/authorities/local/root.crt ./caddy-local-root.crt
    python -m tools.frontier_benchmark --suite smoke \
        --contestants benchmarks/frontier/contestants.toml \
        --base-url https://enshrouded-server --ca-bundle caddy-local-root.crt \
        --post-monitoring --yes

    # standard run:
    python -m tools.frontier_benchmark --suite standard \
        --contestants benchmarks/frontier/contestants.toml \
        --base-url https://enshrouded-server --ca-bundle caddy-local-root.crt \
        --post-monitoring --yes

Resume safety (§22/§46): the non-secret resume state (``state.json``) is
persisted ATOMICALLY after EACH completed case, so an interrupted paid run is
resumed without re-executing completed combos. On restart without ``--rerun``
the runner honours ``state.json`` when present, and when only the
crash-tolerant ``results.jsonl`` exists (no state file) the combos already in
it are treated as completed too (deduped — never silently re-purchased). Use
``--rerun`` to EXPLICITLY repeat the whole workload.

No Gaming-PC, no browser automation, no production access, no public endpoint.
Real paid benchmark runs require explicit operator invocation (`--yes`), and
`--dry-run` always prints a non-secret plan without contacting providers.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import logging
import math
import os
import random
import re
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

# --------------------------------------------------------------------------- #
# path bootstrap (same convention as tools/ollama_smoke.py and friends)
# --------------------------------------------------------------------------- #

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND_DIR = _REPO_ROOT / "backend"
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

# --------------------------------------------------------------------------- #
# constants
# --------------------------------------------------------------------------- #

BENCHMARK_SCHEMA_VERSION = "1.0.0"
TOOL_VERSION = "1.0.0"
SENTINEL = "SECRET-PHASE31-BENCHMARK-MUST-NOT-PERSIST"

DEFAULT_CONTESTANTS_PATH = _REPO_ROOT / "benchmarks" / "frontier" / "contestants.toml"
DEFAULT_SUITES_PATH = _REPO_ROOT / "benchmarks" / "frontier" / "suites.toml"
DEFAULT_CORPUS_DIR = _REPO_ROOT / "benchmarks" / "frontier" / "corpus"
DEFAULT_BASE_URL = "https://enshrouded-server"
DEFAULT_OUTPUT_ROOT = _REPO_ROOT / "benchmark-results"
DEFAULT_ENV_FILE = _REPO_ROOT / ".env.benchmark"

MAX_CONCURRENCY = 4
DEFAULT_CONCURRENCY = 1
MIN_REPEAT = 1
MAX_REPEAT = 50
MAX_CORPUS_PROMPT_CHARS = 4000
CONTRACT_TOLERANCE_MS = 5000  # bounded scheduler/cleanup tolerance (§18)
DEFAULT_HTTP_TIMEOUT_SECONDS = 420.0

DIFFICULTIES = ("easy", "medium", "hard")

# Luna availability (verified against official OpenAI API documentation).
# No officially callable OpenAI API model ID exists for the Luna product
# model. `openai/gpt-6-luna` is an OpenRouter model id only — never a
# verified official OpenAI API model id. BLOCKED-EXTERNAL.
LUNA_STATUS_BLOCKED = "BLOCKED-EXTERNAL"
LUNA_BLOCKED_REASON = (
    "no officially callable OpenAI API model ID is available for the Luna "
    "product model (openai/gpt-6-luna is an OpenRouter model id only)"
)
# Operators may pin a verified official OpenAI API model id here in the future.
LUNA_VERIFIED_OFFICIAL_MODEL_IDS: tuple[str, ...] = ()

FINAL_STATUS_PUBLISHED = "PUBLISHED"
FINAL_STATUS_FAILED = "FAILED"
FINAL_STATUS_ERROR = "ERROR"
FINAL_STATUS_SKIPPED_CREDENTIAL = "SKIPPED_CREDENTIAL_MISSING"

COST_SOURCE_UNAVAILABLE = "unavailable"

# Timeout-family failure codes treated as timeouts in aggregation.
TIMEOUT_FAILURE_CODES = frozenset(
    {
        "FRONTIER_TIMEOUT",
        "PROVIDER_TIMEOUT",
        "LOCAL_PROVIDER_TIMEOUT",
        "GENERATION_DEADLINE_EXCEEDED",
    }
)

# Allowed contestant config fields (anything else is a forbidden override).
ALLOWED_CONTESTANT_FIELDS = frozenset(
    {"id", "label", "provider", "model", "credential_env", "enabled", "tags"}
)
FORBIDDEN_RESERVED_FIELD_HINT = (
    "endpoint", "url", "base_url", "baseUrl", "api_key", "apiKey", "key",
    "response_format", "responseFormat", "schema", "json_schema", "system_prompt",
    "prompt", "timeout", "timeout_seconds", "timeout_disable", "deadline",
    "headers", "proxy", "tls", "validator", "validator_disable",
    "disable_validator", "security_override", "temperature", "top_p",
    "structured_output", "structuredOutput",
)
_CREDENTIAL_ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{1,64}$")
# DEF-038: the env-var name must be an UPPERCASE token-shaped name so a typo
# can never wire an arbitrary local environment variable (PATH, HOME, PWD,
# SSL_CERT_FILE, BENCHMARK_ENV_FILE, ...) into the outbound Bearer credential.
_CREDENTIAL_ENV_STRICT_RE = re.compile(r"^[A-Z][A-Z0-9_]{2,63}$")
_CREDENTIAL_ENV_OK_SUFFIXES = ("_API_KEY", "_KEY", "_TOKEN", "_SECRET")
_CREDENTIAL_ENV_BLOCKED_NAMES = frozenset(
    {
        "PATH", "HOME", "USER", "SHELL", "PWD", "OLDPWD", "TMP", "TEMP",
        "TMPDIR", "TERM", "LANG", "LC_ALL", "CDPATH", "IFS", "ENV", "SHLVL",
        "HOSTNAME", "EDITOR", "VISUAL", "PAGER", "PYTHONPATH",
        "SSL_CERT_FILE", "SSL_CERT_DIR",
        "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "ALL_PROXY",
        "http_proxy", "https_proxy", "no_proxy", "all_proxy",
        "BENCHMARK_ENV_FILE",
    }
)
# DEF-031/DEF-037: bounded printable-ASCII gate shared by every contestant /
# corpus text field that is echoed into plan/metadata/report/CSV artifacts.
_MAX_LABEL_CHARS = 120
_MAX_TAG_CHARS = 80
_MAX_TAGS = 20
_PRINTABLE_ASCII_RE = re.compile(r"^[\x20-\x7e]+$")
_CONTROL_CHARS = set(chr(c) for c in range(32)) | {chr(127)}
_CONTESTANT_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,80}$")

# --------------------------------------------------------------------------- #
# domain exceptions
# --------------------------------------------------------------------------- #


class BenchmarkError(Exception):
    """Benchmark-infrastructure failure: abort only on these (§26)."""


class ConfigError(BenchmarkError):
    """Corrupt/malformed benchmark configuration (aborting infrastructure)."""


class SecretSafetyViolation(BenchmarkError):
    """A credential value was found in the output artifacts."""


class MonitoringCaptureError(Exception):
    """A Dev-Box Phase 29 capture/report step failed (non-fatal)."""


# --------------------------------------------------------------------------- #
# dataclasses (never carry secrets in __repr__)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class BenchmarkContestant:
    id: str
    label: str
    provider: str
    model: str
    credential_env: str
    enabled: bool
    tags: tuple[str, ...] = ()
    # The resolved secret (never serialized, never printed, never repr'ed).
    credential: str = ""


@dataclass(frozen=True)
class BenchmarkCase:
    id: str
    difficulty: str
    prompt: str
    tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class SuiteSpec:
    easy: int = 0
    medium: int = 0
    hard: int = 0

    def total(self) -> int:
        return self.easy + self.medium + self.hard


@dataclass(frozen=True)
class WorkItem:
    contestant_id: str
    case_id: str
    repeat: int

    def as_tuple(self) -> tuple[str, str, int]:
        return (self.contestant_id, self.case_id, self.repeat)


# --------------------------------------------------------------------------- #
# credential / dotenv handling (secret-safe)
# --------------------------------------------------------------------------- #


def parse_env_file_values(path: Path) -> dict[str, str]:
    """Minimal ``KEY=VALUE`` dotenv reader (no expansion, no interpolation).

    Returns the parsed names/values; callers must never print values.
    Malformed lines are ignored (never fatal). Never executes anything.
    """
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return values
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, raw = line.split("=", 1)
        key = key.strip()
        if not key or not raw.strip():
            continue
        value = raw.strip().strip('"').strip("'")
        values[key] = value
    return values


def resolve_benchmark_env_file() -> Path:
    override = os.environ.get("BENCHMARK_ENV_FILE")
    if override:
        return Path(override).expanduser()
    return DEFAULT_ENV_FILE


def load_credential(credential_env: str, dotenv_values: dict[str, str]) -> str:
    """One credential lookup: process env wins over the .env.benchmark file.

    Returns '' when missing (the contestant is then marked
    SKIPPED_CREDENTIAL_MISSING and the benchmark continues).
    """
    value = os.environ.get(credential_env)
    if value is not None and value.strip():
        return value.strip()
    if credential_env in dotenv_values:
        return dotenv_values[credential_env].strip()
    return ""


# --------------------------------------------------------------------------- #
# contestant config parsing
# --------------------------------------------------------------------------- #


def _registry_check() -> tuple[tuple[Any, ...], Callable[[], tuple[Any, ...]]]:
    from app.generation import frontier_registry  # noqa: E402

    return (
        frontier_registry.FRONTIER_PROVIDER_REGISTRY,
        frontier_registry.frontier_enabled_providers,
    )


def _validate_frontier_model(model: str, contestant_id: str) -> None:
    from app.generation.selection import (  # noqa: E402
        InvalidFrontierConfigError,
        validate_frontier_model_string,
    )

    try:
        validate_frontier_model_string(model)
    except InvalidFrontierConfigError:
        raise ConfigError(
            f"contestant {contestant_id!r}: model is invalid or unsupported"
        ) from None


def _known_provider_id(provider: str, contestant_id: str, *, require_enabled: bool) -> str:
    all_registry, enabled_fn = _registry_check()
    known = {definition.provider_id: definition for definition in all_registry}
    definition = known.get(provider)
    if definition is None:
        raise ConfigError(
            f"contestant {contestant_id!r}: unknown trusted provider id"
        )
    if require_enabled and not definition.enabled:
        raise ConfigError(
            f"contestant {contestant_id!r}: provider {provider!r} is disabled "
            "in the trusted registry"
        )
    return definition.provider_id


def _parse_enabled(value: Any, contestant_id: str) -> bool:
    """DEF-032 — strict ``enabled`` semantics.

    Real TOML booleans pass through unchanged. Strings accept ONLY the narrow
    vocabulary ``true``/``false``/``0``/``no`` (case-insensitive, ``0``/``no``
    mean DISABLED) — a non-empty string is NEVER treated as True. Anything
    else (numbers, arrays, tables, empty string, ``"yes"``, ...) raises
    ConfigError instead of silently enabling a DISABLED paying contestant.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered == "true":
            return True
        if lowered in ("false", "0", "no"):
            return False
    raise ConfigError(
        f"contestant {contestant_id!r}: enabled must be a real TOML boolean "
        'or the strings "true"/"false" (0/no also mean disabled); got an '
        "unparseable value"
    )


def _validate_config_text(
    value: Any, *, what: str, context: str, max_chars: int
) -> str:
    """DEF-031/DEF-037 — bounded printable-ASCII gate for config text fields.

    Rejects control characters/CR/LF and non-printable content, trims and
    bounds the length. The failure message never echoes the offending value.
    """
    if not isinstance(value, str):
        raise ConfigError(f"{context}: {what} must be a string")
    text = value.strip()
    if not text:
        raise ConfigError(f"{context}: {what} must be a non-empty string")
    if len(text) > max_chars:
        raise ConfigError(f"{context}: {what} exceeds {max_chars} chars")
    if any(ch in _CONTROL_CHARS for ch in text):
        raise ConfigError(
            f"{context}: {what} must not contain control characters"
        )
    if not _PRINTABLE_ASCII_RE.match(text):
        raise ConfigError(f"{context}: {what} must be printable ASCII")
    return text


def _sanitize_tags(
    tags_raw: Any, *, context: str, max_tags: int = _MAX_TAGS
) -> tuple[str, ...]:
    """DEF-031/DEF-037 — tag list validation (bounded printable-ASCII)."""
    if tags_raw is None:
        tags_raw = ()
    if isinstance(tags_raw, str):
        parts = [t.strip() for t in tags_raw.split(",") if t.strip()]
    elif isinstance(tags_raw, (list, tuple)):
        parts = [str(t).strip() for t in tags_raw if str(t).strip()]
    else:
        raise ConfigError(f"{context}: tags must be a list of strings")
    if len(parts) > max_tags:
        raise ConfigError(f"{context}: too many tags (max {max_tags})")
    out: list[str] = []
    for tag in parts:
        _validate_config_text(
            tag, what="tag", context=context, max_chars=_MAX_TAG_CHARS
        )
        out.append(tag)
    return tuple(out)


def _validate_model_text(model: str, contestant_id: str) -> None:
    """The frontier model validator already gates the value; additionally
    reject control characters (DEF-031) so model can never smuggle control
    chars into artifacts."""
    if any(ch in _CONTROL_CHARS for ch in model):
        raise ConfigError(
            f"contestant {contestant_id!r}: model must not contain control "
            "characters"
        )


def sanitize_base_url(url: str) -> str:
    """DEF-030 — validate + sanitize ``--base-url``.

    Rejects embedded ``user:pass@`` userinfo, query strings, fragments, non
    http(s) schemes and path components; returns the sanitized
    ``scheme://host[:port]`` form. Errors describe the offending PART (never
    the value, which may contain a credential).
    """
    if not isinstance(url, str) or not url.strip():
        raise ConfigError("--base-url must be a non-empty http(s) URL")
    try:
        parts = urllib.parse.urlsplit(url.strip())
    except ValueError:
        raise ConfigError("--base-url is not a valid URL") from None
    if parts.scheme not in ("http", "https"):
        raise ConfigError("--base-url must use scheme http or https")
    if not parts.hostname:
        raise ConfigError("--base-url must include a host")
    if parts.username is not None or parts.password is not None:
        raise ConfigError(
            "--base-url must not embed userinfo (user:pass@); URLs never "
            "carry credentials"
        )
    if parts.query:
        raise ConfigError(
            "--base-url must not carry a query string (secrets in URLs are "
            "rejected)"
        )
    if parts.fragment:
        raise ConfigError("--base-url must not carry a fragment")
    path = parts.path or ""
    if path not in ("", "/"):
        raise ConfigError("--base-url must be scheme://host only (no path)")
    try:
        port = parts.port
    except ValueError:
        raise ConfigError("--base-url has an invalid port") from None
    host = parts.hostname or ""
    if ":" in host and port is None and not host.startswith("["):
        # bare ipv6 literal: keep the brackets for a valid URL
        host = f"[{host}]"
    if port is not None:
        return f"{parts.scheme}://{host}:{port}"
    return f"{parts.scheme}://{host}"


def _credential_env_name_policy(credential_env: str) -> str | None:
    """DEF-038 — credential env-var name policy.

    Returns the accepted name or None (plus a human reason is left to the
    caller). Accepted: uppercase token shape ``[A-Z][A-Z0-9_]{2,63}`` that
    ends with ``_API_KEY``/``_KEY``/``_TOKEN``/``_SECRET`` and is NOT in the
    blocked common-environment set — so an arbitrary local variable can never
    be wired (even by typo) into the outbound Bearer credential.
    """
    if not _CREDENTIAL_ENV_STRICT_RE.match(credential_env):
        return None
    if credential_env in _CREDENTIAL_ENV_BLOCKED_NAMES:
        return None
    if not credential_env.endswith(_CREDENTIAL_ENV_OK_SUFFIXES):
        return None
    return credential_env


def load_contestants_toml(path: Path) -> list[BenchmarkContestant]:
    """Parse + validate the server-owned contestants file (no secrets)."""
    if not path.is_file():
        raise ConfigError(f"contestants file not found: {path}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"malformed contestants file {path}: {exc}") from None
    # [[contestants]] tables arrive as a data["contestant"] list in tomllib
    # (a bare [[x]] array root is exposed as data["x"]).
    candidates = data.get("contestants")
    if candidates is not None and not isinstance(candidates, list):
        raise ConfigError("contestants file: 'contestants' must be a list")
    if candidates is None:
        candidates = data.get("contestant", [])
    if not isinstance(candidates, list):
        raise ConfigError("contestants file: contestant entries must be a list")

    all_registry, enabled_fn = _registry_check()
    contestants: list[BenchmarkContestant] = []
    seen: set[str] = set()
    for index, raw in enumerate(candidates):
        if not isinstance(raw, dict):
            raise ConfigError(f"contestants file entry #{index} must be a table")
        unknown = set(raw) - ALLOWED_CONTESTANT_FIELDS
        if unknown:
            bad = sorted(unknown)[0]
            if bad.lower() in FORBIDDEN_RESERVED_FIELD_HINT:
                raise ConfigError(
                    f"contestants entry #{index}: {bad!r} is a forbidden "
                    "surface (arbitrary endpoint / inline key / schema / prompt "
                    "/ security / timeout / validator overrides are rejected)"
                )
            raise ConfigError(
                f"contestants entry #{index}: unknown field {bad!r}"
            )
        cid = raw.get("id")
        label = raw.get("label")
        provider = raw.get("provider")
        model = raw.get("model")
        credential_env = raw.get("credential_env")
        if not isinstance(cid, str) or not _CONTESTANT_ID_RE.match(cid):
            raise ConfigError(f"contestants entry #{index}: invalid id")
        label = _validate_config_text(
            label, what="label", context=f"contestant {cid!r}",
            max_chars=_MAX_LABEL_CHARS,
        )
        if not isinstance(provider, str) or not provider.strip():
            raise ConfigError(f"contestant {cid!r}: provider must be set")
        if not isinstance(model, str):
            raise ConfigError(f"contestant {cid!r}: model must be a string")
        _validate_model_text(model, cid)
        if not isinstance(credential_env, str) or not _CREDENTIAL_ENV_RE.match(
            credential_env
        ):
            raise ConfigError(
                f"contestant {cid!r}: credential_env must be an env-var name shape"
            )
        if _credential_env_name_policy(credential_env) is None:
            raise ConfigError(
                f"contestant {cid!r}: credential_env={credential_env!r} is not "
                "a safe credential env-var name: it must be an UPPERCASE "
                "token-shaped name ending in _API_KEY/_KEY/_TOKEN/_SECRET and "
                "must not be a common process-environment variable (PATH, "
                "HOME, SHELL, PWD, SSL_CERT_FILE, BENCHMARK_ENV_FILE, ...)"
            )
        enabled = _parse_enabled(raw.get("enabled", True), cid)
        tags = _sanitize_tags(
            raw.get("tags", ()), context=f"contestant {cid!r}"
        )
        if cid in seen:
            raise ConfigError(f"duplicate contestant id {cid!r}")
        seen.add(cid)
        _known_provider_id(provider, cid, require_enabled=enabled)
        if enabled:
            _validate_frontier_model(model, cid)
        elif model.strip():
            _validate_frontier_model(model, cid)
        contestants.append(
            BenchmarkContestant(
                id=cid,
                label=label,
                provider=_known_provider_id(provider, cid, require_enabled=enabled),
                model=model.strip(),
                credential_env=credential_env,
                enabled=enabled,
                tags=tags,
            )
        )
    if not contestants:
        raise ConfigError("contestants file defines no contestants")
    return contestants


# --------------------------------------------------------------------------- #
# corpus + suites parsing
# --------------------------------------------------------------------------- #


def load_corpus(directory: Path) -> list[BenchmarkCase]:
    if not directory.is_dir():
        raise ConfigError(f"corpus directory not found: {directory}")
    files = sorted(directory.glob("*.json"), key=lambda p: p.name)
    if not files:
        raise ConfigError(f"corpus directory contains no JSON files: {directory}")
    cases: list[BenchmarkCase] = []
    seen: set[str] = set()
    for path in files:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError, UnicodeDecodeError) as exc:
            raise ConfigError(f"malformed corpus file {path.name}: {exc}") from None
        if not isinstance(raw, dict):
            raise ConfigError(f"malformed corpus file {path.name}: not a JSON object")
        for required in ("id", "difficulty", "prompt"):
            if required not in raw:
                raise ConfigError(f"corpus file {path.name}: missing {required!r}")
        cid = raw["id"]
        difficulty = raw["difficulty"]
        prompt = raw["prompt"]
        if not isinstance(cid, str) or not _CONTESTANT_ID_RE.match(cid):
            raise ConfigError(
                f"corpus file {path.name}: id must match the safe id shape "
                "[A-Za-z0-9_-] (no control/newline characters)"
            )
        if difficulty not in DIFFICULTIES:
            raise ConfigError(
                f"corpus file {path.name}: difficulty must be one of {DIFFICULTIES}"
            )
        if not isinstance(prompt, str) or not prompt.strip():
            raise ConfigError(
                f"corpus file {path.name}: prompt must be a non-empty string"
            )
        if any(
            ch in _CONTROL_CHARS and ch not in ("\n", "\t") for ch in prompt
        ) or "\x00" in prompt:
            raise ConfigError(
                f"corpus file {path.name}: prompt must not contain control "
                "characters"
            )
        if len(prompt) > MAX_CORPUS_PROMPT_CHARS:
            raise ConfigError(
                f"corpus file {path.name}: prompt exceeds "
                f"{MAX_CORPUS_PROMPT_CHARS} chars"
            )
        if cid in seen:
            raise ConfigError(f"duplicate corpus case id {cid!r}")
        seen.add(cid)
        tags = _sanitize_tags(raw.get("tags", ()), context=f"corpus case {cid!r}")
        cases.append(
            BenchmarkCase(id=cid, difficulty=difficulty, prompt=prompt, tags=tags)
        )
    if not cases:
        raise ConfigError("corpus is empty")
    return cases


def load_suites(path: Path) -> dict[str, SuiteSpec]:
    if not path.is_file():
        raise ConfigError(f"suites file not found: {path}")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError, UnicodeDecodeError) as exc:
        raise ConfigError(f"malformed suites file {path}: {exc}") from None
    tables = data.get("suites")
    if not isinstance(tables, dict):
        raise ConfigError("suites file: missing [suites] table")
    suites: dict[str, SuiteSpec] = {}
    for name, spec in tables.items():
        if not isinstance(spec, dict):
            raise ConfigError(f"suites.{name} must be a table")
        counts: dict[str, int] = {}
        for difficulty in DIFFICULTIES:
            value = spec.get(difficulty, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ConfigError(
                    f"suites.{name}.{difficulty} must be a non-negative integer"
                )
            counts[difficulty] = value
        unknown = set(spec) - set(DIFFICULTIES)
        if unknown:
            raise ConfigError(f"suites.{name}: unknown field {sorted(unknown)[0]!r}")
        suites[name] = SuiteSpec(
            easy=counts["easy"], medium=counts["medium"], hard=counts["hard"]
        )
    if not suites:
        raise ConfigError("suites file defines no suites")
    return suites


def select_cases(
    corpus: Sequence[BenchmarkCase],
    suite: SuiteSpec,
    *,
    difficulty_filter: str | None = None,
    case_ids: Sequence[str] | None = None,
) -> list[BenchmarkCase]:
    """Deterministic case selection.

    With explicit ``case_ids`` the selection comes from the WHOLE corpus by id
    (the suite counts are bypassed — ``--case-ids`` pins the workload for
    debugging/spot-checks). Otherwise the suite counts are applied per
    difficulty: ids sorted, first N per difficulty.
    """
    if case_ids:
        wanted = set(case_ids)
        by_id = {c.id: c for c in corpus}
        missing = wanted - set(by_id)
        if missing:
            raise ConfigError(
                "requested case ids are not present in the corpus: "
                + ", ".join(sorted(missing))
            )
        selected = [by_id[cid] for cid in sorted(wanted)]
    else:
        by_difficulty: dict[str, list[BenchmarkCase]] = {
            d: sorted((c for c in corpus if c.difficulty == d), key=lambda c: c.id)
            for d in DIFFICULTIES
        }
        selected: list[BenchmarkCase] = []
        for difficulty in DIFFICULTIES:
            if difficulty_filter and difficulty != difficulty_filter:
                continue
            count = (
                suite.easy
                if difficulty == "easy"
                else suite.medium
                if difficulty == "medium"
                else suite.hard
            )
            pool = by_difficulty[difficulty]
            if count > len(pool):
                raise ConfigError(
                    f"suite requires {count} {difficulty} cases but the corpus "
                    f"has only {len(pool)}"
                )
            selected.extend(pool[:count])
    if difficulty_filter:
        selected = [c for c in selected if c.difficulty == difficulty_filter]
    if not selected:
        raise ConfigError("the selection produced zero cases")
    return selected


# --------------------------------------------------------------------------- #
# plan / ordering
# --------------------------------------------------------------------------- #


def build_work_items(
    contestants: Sequence[BenchmarkContestant],
    cases: Sequence[BenchmarkCase],
    repeat: int,
    *,
    seed: int | None,
    fixed_order: bool,
) -> tuple[list[WorkItem], list[str], int | None]:
    """Contestant-major plan with seedable contestant shuffle.

    Returns ``(items, contestant_order, effective_seed)``. ``effective_seed``
    is None when fixed_order was requested.
    """
    enabled = [c for c in contestants if c.enabled]
    order: list[BenchmarkContestant] = list(enabled)
    effective_seed = seed
    if not fixed_order:
        if effective_seed is None:
            effective_seed = random.SystemRandom().randrange(1 << 30)
        rng = random.Random(effective_seed)
        rng.shuffle(order)
    else:
        effective_seed = None  # fixed order records no seed (deterministic)
    items: list[WorkItem] = []
    for contestant in order:
        for case in cases:
            for rep in range(repeat):
                items.append(
                    WorkItem(contestant_id=contestant.id, case_id=case.id, repeat=rep)
                )
    return items, [c.id for c in order], effective_seed


# --------------------------------------------------------------------------- #
# telemetry correlation (structured observability events)
# --------------------------------------------------------------------------- #

CALL_EVENTS = frozenset(
    {"provider.call.start", "provider.call.complete",
     "provider.call.timeout", "provider.call.error"}
)
TERMINAL_EVENTS = frozenset({"generation.published", "generation.failed"})

# Phase31A §26 — non-secret per-attempt compatibility artifacts.
COMPATIBILITY_DIR_NAME = "compatibility"
# The artifact is assembled ONLY from the app's own allowlisted observability
# fields; these strings are the documented NEVER-included material (the writer
# never introduces them, and the hermetic tests assert the absence of every one).
COMPAT_FORBIDDEN_FRAGMENTS: frozenset[str] = frozenset(
    {
        "apiKey", "api_key", "authorization", "Authorization",
        "prompt", "promptContext", "prompt_context", "caseTruth", "CaseTruth",
        "upstreamBody", "upstream_body", "sessionSecret", "session_secret",
        "response_format", "json_schema", "messages", "content",
    }
)
# The EXACT closed key set of the §26 safe artifact (everything else would be
# a protocol violation). ``schemaId`` is optional (present only when the
# attempt actually used native structured output).
COMPAT_ARTIFACT_KEYS: frozenset[str] = frozenset(
    {
        "generationAttemptId", "provider", "model", "stage",
        "structuredOutput", "passes", "finalFailureCode", "schemaId",
    }
)
COMPAT_PASS_KEYS: frozenset[str] = frozenset(
    {"repairCount", "validatorCodes", "repairEffectiveness"}
)
REPAIR_EFFECTIVENESS_LABELS: frozenset[str] = frozenset(
    {"VALID", "IMPROVED", "UNCHANGED", "REGRESSED"}
)


def _reject_json_nonfinite(constant: str) -> float:
    raise ValueError(f"non-finite JSON numeric constant {constant}")


def _contains_non_finite_float(value: Any) -> bool:
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, dict):
        return any(_contains_non_finite_float(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_non_finite_float(v) for v in value)
    return False


def parse_telemetry_events_with_notes(
    path: Path | None,
) -> tuple[list[dict[str, Any]], int]:
    """Read the app's own observability JSONL (one JSON object per line).

    DEF-033: lines are parsed with a NaN/Infinity-rejecting parser (and any
    float-overflow-to-inf value such as ``1e309`` is rejected too). A line
    that is malformed, non-object, or carries non-finite numbers NEVER crashes
    the run: it is skipped and counted as an unparseable-telemetry note.

    Returns ``(events, unparseable_count)``.
    """
    if path is None or not path.is_file():
        return [], 0
    events: list[dict[str, Any]] = []
    unparseable = 0
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line, parse_constant=_reject_json_nonfinite)
                except (ValueError, TypeError):
                    unparseable += 1
                    continue
                if not isinstance(obj, dict):
                    unparseable += 1
                    continue
                if _contains_non_finite_float(obj):
                    unparseable += 1
                    continue
                events.append(obj)
    except OSError:
        return [], unparseable
    return events, unparseable


def parse_telemetry_events(path: Path | None) -> list[dict[str, Any]]:
    """Compatibility wrapper — see ``parse_telemetry_events_with_notes``."""
    events, _count = parse_telemetry_events_with_notes(path)
    return events


def index_events_by_attempt(events: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    indexed: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        attempt_id = event.get("generationAttemptId")
        if not isinstance(attempt_id, str) or not attempt_id:
            continue
        indexed.setdefault(attempt_id, []).append(event)
    return indexed


def _last_scalar(events: Sequence[dict[str, Any]], key: str) -> Any:
    for event in reversed(events):
        value = event.get(key)
        if value is not None:
            return value
    return None


def detect_call_timeout_violations(
    events: Sequence[dict[str, Any]], tolerance_ms: int = CONTRACT_TOLERANCE_MS
) -> list[str]:
    """§18 — per-call ``elapsedMs <= effectiveProviderTimeoutMs + tolerance``."""
    violations: list[str] = []
    for event in events:
        name = event.get("event")
        if name not in CALL_EVENTS:
            continue
        elapsed = event.get("elapsedMs")
        effective = event.get("effectiveProviderTimeoutMs")
        if not isinstance(elapsed, (int, float)) or not isinstance(
            effective, (int, float)
        ):
            continue
        if elapsed > effective + tolerance_ms:
            violations.append(
                f"provider.call {name} elapsedMs={int(elapsed)} > "
                f"effectiveProviderTimeoutMs={int(effective)} "
                f"(tolerance {tolerance_ms} ms)"
            )
    return violations


def call_timeout_verifiability(
    events: Sequence[dict[str, Any]],
) -> tuple[int, int]:
    """DEF-034 — how many provider.call events were verifiable.

    Returns ``(verifiable, unverifiable)``. A call is VERIFIABLE only when it
    carries BOTH a numeric ``elapsedMs`` and a numeric
    ``effectiveProviderTimeoutMs``; any other provider.call event (missing or
    non-numeric timeout data) is UNVERIFIABLE. The report must never claim "no
    violation" when zero calls were verifiable.
    """
    verifiable = 0
    unverifiable = 0
    for event in events:
        if event.get("event") not in CALL_EVENTS:
            continue
        elapsed = event.get("elapsedMs")
        effective = event.get("effectiveProviderTimeoutMs")
        if isinstance(elapsed, (int, float)) and isinstance(
            effective, (int, float)
        ):
            verifiable += 1
        else:
            unverifiable += 1
    return verifiable, unverifiable


def _finite_int(value: Any) -> int | None:
    """Coerce a numeric value to int, returning None for non-finite / garbage
    (DEF-033: hostile telemetry numbers must never crash enrichment)."""
    if isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(f):
        return None
    return int(f)


def _finite_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(f):
        return None
    return f


def enrich_result(
    result: dict[str, Any],
    attempt_events: Sequence[dict[str, Any]],
    *,
    deadline_ms: int | float | None = None,
    tolerance_ms: int = CONTRACT_TOLERANCE_MS,
) -> dict[str, Any]:
    """Fill telemetry-derived fields from the app's structured events.

    Every field stays Null when the telemetry cannot truthfully provide it.
    """
    if not attempt_events:
        return result
    terminal = [e for e in attempt_events if e.get("event") in TERMINAL_EVENTS]
    terminal = terminal or attempt_events
    provider_calls = [e for e in attempt_events if e.get("event") in CALL_EVENTS]

    result["providerCallCount"] = _last_scalar(terminal, "providerCallCount")
    result["repairCount"] = _last_scalar(terminal, "repairCount")
    result["regenerationCount"] = _last_scalar(terminal, "regenerationCount")
    result["globalCallCount"] = _last_scalar(terminal, "globalCallCount")
    result["coreCallCount"] = _last_scalar(terminal, "coreCallCount")
    result["assetCallCount"] = _last_scalar(terminal, "assetCallCount")

    server_elapsed = _last_scalar(terminal, "totalElapsedMs")
    bounded_elapsed = _finite_int(server_elapsed)
    if bounded_elapsed is not None:
        result["serverReportedTotalElapsedMs"] = bounded_elapsed

    # Structured output: truthfully from actual call telemetry (never inferred
    # from marketing metadata).
    used_structured = any(
        e.get("event") == "provider.call.complete" and e.get("structuredOutput") is True
        for e in attempt_events
    )
    result["structuredOutputUsed"] = True if used_structured else False

    call_elapsed = [
        f
        for e in provider_calls
        if (f := _finite_float(e.get("elapsedMs"))) is not None
    ]
    if call_elapsed:
        result["maxProviderCallElapsedMs"] = int(max(call_elapsed))
        if len(call_elapsed) >= 2:
            result["medianProviderCallElapsedMs"] = int(
                percentile(sorted(call_elapsed), 50)
            )
        else:
            result["medianProviderCallElapsedMs"] = int(call_elapsed[0])

    start_events = [e for e in attempt_events if e.get("event") == "provider.call.start"]
    if start_events:
        result["effectiveProviderTimeoutMs"] = _last_scalar(
            start_events, "effectiveProviderTimeoutMs"
        )
        if deadline_ms is None:
            deadline_ms = _last_scalar(start_events, "configuredGenerationDeadlineMs")
    result["generationDeadlineMs"] = deadline_ms

    # Validation outcome: published -> VALID (full validation passed); else the
    # last validation_failed outcome truthfully recorded by the controller.
    failed_events = [
        e
        for e in attempt_events
        if e.get("event") == "generation.stage.validation_failed"
    ]
    outcome = _last_scalar(failed_events, "validationOutcome")
    if outcome is None and result.get("published") is True:
        outcome = "VALID"
    result["validationOutcome"] = outcome

    terminal_failure = _last_scalar(
        [e for e in attempt_events if e.get("event") in TERMINAL_EVENTS],
        "failureCode",
    )
    if result.get("failureCode") is None and terminal_failure is not None:
        result["failureCode"] = terminal_failure

    # §18 total-generation timeout contract.
    violations: list[str] = detect_call_timeout_violations(attempt_events, tolerance_ms)
    total_elapsed = result.get("serverReportedTotalElapsedMs") or result.get(
        "totalElapsedMs"
    )
    if (
        isinstance(total_elapsed, (int, float))
        and math.isfinite(float(total_elapsed))
        and deadline_ms is not None
        and isinstance(deadline_ms, (int, float))
        and math.isfinite(float(deadline_ms))
        and total_elapsed > deadline_ms + tolerance_ms
    ):
        violations.append(
            f"total generation elapsedMs={int(total_elapsed)} > "
            f"deadline={int(deadline_ms)} (tolerance {tolerance_ms} ms)"
        )
    verifiable_calls, unverifiable_calls = call_timeout_verifiability(attempt_events)
    result["contractViolationVerifiableCalls"] = verifiable_calls
    result["contractViolationUnverifiableCalls"] = unverifiable_calls
    result["contractViolations"] = violations or None
    result["timeoutContractViolation"] = bool(violations) or None
    result["costSource"] = COST_SOURCE_UNAVAILABLE
    return result


def percentile(sorted_values: Sequence[float], pct: float) -> float:
    """Linear-interpolation percentile over an ascending list.

    Deterministic, matches the common statistical definition
    (``numpy.percentile`` linear method for a sorted input).
    """
    if not sorted_values:
        raise ValueError("percentile of empty sequence")
    n = len(sorted_values)
    if n == 1:
        return float(sorted_values[0])
    rank = (pct / 100.0) * (n - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return float(sorted_values[lo])
    frac = rank - lo
    return float(sorted_values[lo]) + frac * (
        sorted_values[hi] - sorted_values[lo]
    )


# --------------------------------------------------------------------------- #
# Phase31A §26 — per-attempt NON-SECRET compatibility artifacts
# --------------------------------------------------------------------------- #


def _safe_atomic(value: Any) -> Any:
    """Coerce an observability value to a JSON-safe scalar.

    Bounded strings (200 chars), passthrough numbers/booleans/None; every
    composite/unsupported value collapses to ``None`` so hostile telemetry can
    never smuggle structure into the artifact.
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:200]
    return None


def _last_safe_scalar(
    events: Sequence[dict[str, Any]], key: str
) -> Any:
    """Last non-null JSON-safe scalar for ``key`` across ``events``."""
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        value = event.get(key)
        if value is None:
            continue
        scalar = _safe_atomic(value)
        if scalar is not None:
            return scalar
    return None


def _bounded_int(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def build_compatibility_artifact(
    generation_attempt_id: str,
    attempt_events: Sequence[dict[str, Any]],
) -> dict[str, Any] | None:
    """Phase31A §26 — build the NON-SECRET per-attempt compatibility artifact.

    Safe shape (see ``COMPAT_ARTIFACT_KEYS``): the attempt id, provider/model,
    the failing stage, whether structured output was used, an ordered
    ``passes`` list (initial validation at ``repairCount`` 0 plus each repair's
    re-validation with its validator-code tuple and — when the controller
    emitted it — the deterministic ``repairEffectiveness`` label), the final
    failure code and the optional schema id.

    The artifact is assembled ONLY from the app's own allowlisted
    observability fields (the same events ``enrich_result`` consumes). It
    NEVER includes prompt / raw JSON draft / CaseTruth / API key /
    Authorization header / upstream body / session secret. Returns ``None``
    when no usable telemetry exists (a writer must then omit the artifact —
    never fabricate).
    """
    if not attempt_events:
        return None
    events = [e for e in attempt_events if isinstance(e, dict)]
    if not events:
        return None
    call_events = [e for e in events if e.get("event") in CALL_EVENTS]
    terminal = [e for e in events if e.get("event") in TERMINAL_EVENTS]
    validation_failed = [
        e for e in events if e.get("event") == "generation.stage.validation_failed"
    ]
    repair_outcomes = [
        e for e in events if e.get("event") == "generation.repair.outcome"
    ]

    # provider: prefer the safe allowlisted trusted sub-provider id
    # (``frontierProvider`` — e.g. "openrouter"), falling back to the logical
    # generation provider name ("frontier"/"ollama"/"fake"). Never a key /
    # endpoint / Authorization header.
    provider = _last_safe_scalar(
        events, "frontierProvider"
    ) or _last_safe_scalar(events, "provider")
    model = _last_safe_scalar(call_events, "model")
    schema_id = _last_safe_scalar(call_events, "schemaId")
    final_failure = _last_safe_scalar(
        terminal, "failureCode"
    ) or _last_safe_scalar(events, "failureCode")

    # stage: the repair/validation family reports "validation"; otherwise the
    # last provider.call stage (e.g. the Cohere evidence rejection).
    if any(
        e.get("event")
        in (
            "generation.stage.validation_failed",
            "generation.repair.outcome",
            "generation.validation.complete",
        )
        for e in events
    ):
        stage = "validation"
    else:
        stage = _last_safe_scalar(call_events, "stage")

    structured = any(
        e.get("event") == "provider.call.complete" and e.get("structuredOutput") is True
        for e in events
    )

    # passes: keyed by the repairCount recorded on each validation-failed pass.
    passes: dict[int, dict[str, Any]] = {}
    for event in validation_failed:
        count = _bounded_int(event.get("repairCount"), 0)
        entry = passes.setdefault(
            count, {"repairCount": count, "validatorCodes": []}
        )
        codes = event.get("validatorCodes")
        if isinstance(codes, (list, tuple)):
            entry["validatorCodes"] = [
                str(code)[:120] for code in codes if code is not None
            ][:64]
        elif isinstance(codes, str) and codes:
            entry["validatorCodes"] = [codes[:120]]
    # Attach the deterministic effectiveness label from the matching
    # repair.outcome event (its repairCount is the pass index it produced).
    for outcome in repair_outcomes:
        count = _bounded_int(outcome.get("repairCount"), -1)
        if count < 0:
            continue
        entry = passes.setdefault(
            count, {"repairCount": count, "validatorCodes": []}
        )
        label = outcome.get("repairEffectiveness")
        if isinstance(label, str) and label in REPAIR_EFFECTIVENESS_LABELS:
            entry["repairEffectiveness"] = label

    artifact: dict[str, Any] = {
        "generationAttemptId": str(generation_attempt_id)[:120],
        "provider": provider,
        "model": model,
        "stage": stage,
        "structuredOutput": bool(structured),
        "passes": [passes[key] for key in sorted(passes)],
        "finalFailureCode": final_failure,
    }
    if schema_id is not None:
        artifact["schemaId"] = schema_id
    return artifact


def write_compatibility_artifacts(
    output_dir: Path,
    records: Sequence[dict[str, Any]],
    telemetry_by_attempt: Mapping[str, Sequence[dict[str, Any]]],
) -> int:
    """Phase31A §26 — write one non-secret artifact per attempt with telemetry.

    Artifacts land in ``<output_dir>/compatibility/<attempt-id>.json`` to a
    deterministic JSON shape (``_atomic_write_json``). Returns how many were
    written. When no attempt has real telemetry the ``compatibility/``
    directory is simply ABSENT — artifacts are NEVER fabricated.
    """
    written = 0
    for record in records:
        attempt_id = record.get("generationAttemptId")
        if not isinstance(attempt_id, str) or not attempt_id:
            continue
        attempt_events = telemetry_by_attempt.get(attempt_id)
        if not attempt_events:
            continue
        artifact = build_compatibility_artifact(attempt_id, attempt_events)
        if artifact is None:
            continue
        compat_dir = output_dir / COMPATIBILITY_DIR_NAME
        compat_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write_json(compat_dir / f"{attempt_id}.json", artifact)
        written += 1
    return written


# --------------------------------------------------------------------------- #
# drivers
# --------------------------------------------------------------------------- #


def _upgrade_database(database_url: str) -> None:
    """Apply the backend's Alembic migration head to the inprocess DB file.

    The benchmark is a measurement tool: the production schema contract is
    reused unchanged (same heads the production backend applies at boot).
    Alembic's env.py reconfigures logging (fileConfig) while migrating; the
    migration-side effects on the root logger are rolled back afterwards so
    the harness console stays clean.
    """
    from alembic import command  # noqa: E402
    from alembic.config import Config as AlembicConfig  # noqa: E402

    root = logging.getLogger()
    saved_root_handlers = list(root.handlers)
    saved_alembic_level = logging.getLogger("alembic").level
    ini = _BACKEND_DIR / "alembic.ini"
    cfg = AlembicConfig(str(ini))
    cfg.set_main_option("sqlalchemy.url", database_url)
    # Alembic's env.py calls fileConfig which reconfigures the root logger and
    # emits per-revision INFO to stderr; isolate that console noise (a
    # benchmark harness must keep its own output clean).
    import contextlib
    import io

    _silenced = io.StringIO()
    with contextlib.redirect_stdout(_silenced), contextlib.redirect_stderr(_silenced):
        try:
            command.upgrade(cfg, "head")
        finally:
            pass
    # Roll back the fileConfig side effects alembic/env.py applies.
    logging.getLogger("alembic").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy").setLevel(logging.WARNING)
    for handler in list(root.handlers):
        if handler not in saved_root_handlers:
            try:
                root.removeHandler(handler)
            except Exception:  # noqa: BLE001 - best effort
                pass
    try:
        logging.getLogger("alembic").setLevel(saved_alembic_level)
    except ValueError:  # pragma: no cover
        pass


RESULT_FIELDS = (
    "benchmarkSchemaVersion",
    "benchmarkRunId",
    "startedAt",
    "completedAt",
    "executionOrder",
    "contestantId",
    "contestantLabel",
    "provider",
    "model",
    "benchmarkCaseId",
    "difficulty",
    "repeatIndex",
    "generationAttemptId",
    "finalStatus",
    "published",
    "failureCode",
    "validationOutcome",
    "providerCallCount",
    "repairCount",
    "regenerationCount",
    "totalElapsedMs",
    "serverReportedTotalElapsedMs",
    "maxProviderCallElapsedMs",
    "medianProviderCallElapsedMs",
    "structuredOutputUsed",
    "globalCallCount",
    "coreCallCount",
    "assetCallCount",
    "inputTokens",
    "outputTokens",
    "totalTokens",
    "providerReportedCost",
    "calculatedCost",
    "costCurrency",
    "costSource",
    "effectiveProviderTimeoutMs",
    "generationDeadlineMs",
    "timeoutContractViolation",
    "contractViolationVerifiableCalls",
    "contractViolationUnverifiableCalls",
    "contractViolations",
    "errorDetailSanitized",
)


def _empty_result(
    run_id: str,
    contestant: BenchmarkContestant,
    case: BenchmarkCase,
    repeat: int,
    execution_order: int,
    *,
    final_status: str = FINAL_STATUS_ERROR,
    published: bool | None = None,
    failure_code: str | None = None,
    error_detail: str | None = None,
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "benchmarkSchemaVersion": BENCHMARK_SCHEMA_VERSION,
        "benchmarkRunId": run_id,
        "startedAt": None,
        "completedAt": None,
        "executionOrder": execution_order,
        "contestantId": contestant.id,
        "contestantLabel": contestant.label,
        "provider": contestant.provider,
        "model": contestant.model,
        "benchmarkCaseId": case.id,
        "difficulty": case.difficulty,
        "repeatIndex": repeat,
        "generationAttemptId": None,
        "finalStatus": final_status,
        "published": published,
        "failureCode": failure_code,
        "validationOutcome": None,
        "providerCallCount": None,
        "repairCount": None,
        "regenerationCount": None,
        "totalElapsedMs": None,
        "serverReportedTotalElapsedMs": None,
        "maxProviderCallElapsedMs": None,
        "medianProviderCallElapsedMs": None,
        "structuredOutputUsed": None,
        "globalCallCount": None,
        "coreCallCount": None,
        "assetCallCount": None,
        "inputTokens": None,
        "outputTokens": None,
        "totalTokens": None,
        "providerReportedCost": None,
        "calculatedCost": None,
        "costCurrency": None,
        "costSource": COST_SOURCE_UNAVAILABLE,
        "effectiveProviderTimeoutMs": None,
        "generationDeadlineMs": None,
        "timeoutContractViolation": None,
        "contractViolationVerifiableCalls": None,
        "contractViolationUnverifiableCalls": None,
        "contractViolations": None,
        "errorDetailSanitized": error_detail,
    }
    return record


def _sanitize_error(error: Exception) -> str:
    """A bounded, sanitized error line.

    DEF-040: returns ONLY the exception class name (plus a numeric TLS
    verify-code category for SSL errors) — NEVER ``str(exc)``, which may embed
    URLs/headers/credentials. Wrapper exceptions (``urllib.error.URLError``)
    are unwrapped one level so the meaningful class name surfaces while the
    inner message is still never used. Callers interpolate the result into
    ``errorDetailSanitized`` records and sanitized CLI errors.
    """
    name = type(error).__name__
    reason = getattr(error, "reason", None)
    if isinstance(reason, Exception) and type(reason).__name__ != name:
        return _sanitize_error(reason)
    if isinstance(error, ssl.SSLError):
        verify_code = getattr(error, "verify_code", None)
        if isinstance(verify_code, int):
            return f"{name}(verify_code={verify_code})"
    return name


def _markdown_clean(value: Any) -> str:
    """DEF-037: strip control characters (incl. CR/LF) from any untrusted text
    that is later interpolated into report.md, so telemetry/provider-derived
    strings can never splice markdown structure."""
    text = str(value)
    return "".join(" " if ord(ch) in range(32) else ch for ch in text)


def build_http_tls_context(
    *, ca_bundle: str | Path | None = None, insecure_tls: bool = False
) -> ssl.SSLContext | None:
    """Build the http driver's TLS trust context (Dev-Box/local stacks only).

    Returns ``None`` to keep urllib's DEFAULT system trust store — the secure
    baseline every production/public run uses. A Dev-Box edge running
    ``tls internal`` (Caddy's own CA) must be reached with ``--ca-bundle`` so
    the runner trusts that CA (e.g. the exported ``caddy-local-root.crt``);
    hostname verification STAYS ENABLED (``CERT_REQUIRED``) in that path.
    ``insecure_tls`` is the UNSUPPORTED escape hatch that disables
    verification entirely (``CERT_NONE``) — prefer ``--ca-bundle`` and never
    use it against non-local stacks.
    """
    if ca_bundle is not None or insecure_tls:
        if insecure_tls:
            return ssl._create_unverified_context()
        return ssl.create_default_context(cafile=str(ca_bundle))
    return None


class HttpDriver:
    """Real backend driver over the public HTTP surface (stdlib urllib only).

    ``request_fn``/``session_fn`` are injectable transport seams used ONLY by
    hermetic tests; the default implementation uses ``urllib.request``.
    ``ssl_context`` is an optional injected context (test seam); the CLI wires
    ``--ca-bundle`` / ``--insecure-tls`` through ``build_http_tls_context``.
    """

    def __init__(
        self,
        *,
        base_url: str,
        http_timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
        request_fn: Callable[..., Any] | None = None,
        session_fn: Callable[..., Any] | None = None,
        ssl_context: ssl.SSLContext | None = None,
        ca_bundle: str | Path | None = None,
        insecure_tls: bool = False,
    ) -> None:
        self._base_url = str(base_url).rstrip("/")
        self._http_timeout = float(http_timeout_seconds)
        self._request_fn = request_fn
        self._session_fn = session_fn
        self._session_token: str | None = None
        self._session_lock = threading.Lock()
        # TLS trust: an injected context (test seam) wins; otherwise build one
        # from --ca-bundle / --insecure-tls; otherwise None keeps urllib's
        # default system-trust context (the secure baseline).
        if ssl_context is not None:
            self._ssl_context = ssl_context
        else:
            self._ssl_context = build_http_tls_context(
                ca_bundle=ca_bundle, insecure_tls=insecure_tls
            )

    # -- low-level transport --------------------------------------------------

    def _http_post(
        self, url: str, body: dict[str, Any], token: str | None, *, inner: bool = False
    ) -> tuple[int, Any]:
        """POST JSON; returns (status_code, parsed_body)."""
        if self._request_fn is not None:
            return self._request_fn(url, body=body, token=token)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = json.dumps(body).encode("utf-8")
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(
                request, timeout=self._http_timeout, context=self._ssl_context
            ) as response:
                raw = response.read(1024 * 1024)
                try:
                    parsed = json.loads(raw.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    parsed = {}
                return int(response.status), parsed
        except urllib.error.HTTPError as exc:
            raw = b""
            try:
                raw = exc.read(8192)
            except Exception:  # noqa: BLE001 - degrade
                pass
            parsed: Any = {}
            try:
                parsed = json.loads(raw.decode("utf-8", errors="replace"))
            except ValueError:
                parsed = {}
            return int(exc.code), parsed
        except Exception as exc:  # noqa: BLE001 - network/timeout
            raise BenchmarkError(
                f"http request failed: {_sanitize_error(exc)}"
            ) from None

    def create_anonymous_session(self) -> str:
        url = self._base_url + "/api/v1/sessions/anonymous"
        if self._session_fn is not None:
            status, body = self._session_fn()
        else:
            status, body = self._http_post(url, {}, None)
        if status != 201 or not isinstance(body, dict):
            code = "HTTP_%s" % status
            if isinstance(body, dict) and isinstance(body.get("error"), dict):
                code = str(body["error"].get("code") or code)
            raise BenchmarkError(f"anonymous session creation failed: {code}")
        token = body.get("anonymousSessionToken")
        if not isinstance(token, str) or not token:
            raise BenchmarkError("anonymous session response carried no token")
        return token

    def _ensure_session(self) -> str:
        with self._session_lock:
            if self._session_token is None:
                self._session_token = self.create_anonymous_session()
            return self._session_token

    def _refresh_session(self) -> None:
        # 429/admission refresh: creating a session costs no provider call.
        with self._session_lock:
            self._session_token = self.create_anonymous_session()

    def run_case(
        self, contestant: BenchmarkContestant, case: BenchmarkCase
    ) -> dict[str, Any]:
        url = self._base_url + "/api/v1/cases"
        body = {
            "prompt": case.prompt,
            "difficulty": case.difficulty,
            "generationProvider": "frontier",
            "frontier": {
                "provider": contestant.provider,
                "apiKey": contestant.credential,
                "model": contestant.model,
            },
        }
        started_wall = time.perf_counter()
        token = self._ensure_session()
        status, parsed = self._http_post(url, body, token=token)
        # Admission / per-IP / session-quota 429s happen BEFORE any provider
        # call; a one-time session refresh + retry is therefore cost-safe.
        deny_statuses = {"TOO_MANY_REQUESTS", "ADMISSION_DENIED"}
        if status == 429:
            err = parsed.get("error", {}) if isinstance(parsed, dict) else {}
            code = err.get("code") if isinstance(err, dict) else None
            if isinstance(code, str) and code in deny_statuses:
                try:
                    self._refresh_session()
                    token = self._session_token
                    status, parsed = self._http_post(url, body, token=token)
                except BenchmarkError:
                    pass
        elapsed_ms = int((time.perf_counter() - started_wall) * 1000)
        if status == 201 and isinstance(parsed, dict):
            return {
                "generationAttemptId": parsed.get("generationAttemptId"),
                "caseId": parsed.get("caseId"),
                "generationId": parsed.get("generationId"),
                "created": parsed.get("status"),
                "failureCode": parsed.get("failureCode"),
                "totalElapsedMs": elapsed_ms,
            }
        err = parsed.get("error", {}) if isinstance(parsed, dict) else {}
        code = "HTTP_%d" % status
        if isinstance(err, dict):
            code = str(err.get("code") or code)
        return {
            "generationAttemptId": None,
            "caseId": None,
            "generationId": None,
            "created": "ERROR",
            "failureCode": code,
            "totalElapsedMs": elapsed_ms,
        }


class _CaptureHandler:
    """In-process observability capture (attach/detach around a run)."""

    def __init__(self, sink: list[dict[str, Any]]) -> None:
        from app.core.observability import SERVICE_LOGGER_NAME  # noqa: E402

        self._logger = logging.getLogger(SERVICE_LOGGER_NAME)
        self._sink = sink
        self._previous_level = self._logger.level

        class _Handler(logging.Handler):
            def emit(_self, record: logging.LogRecord) -> None:
                event = getattr(record, "pd_event", None)
                if not event:
                    return
                fields = dict(getattr(record, "pd_fields", None) or {})
                fields.setdefault("event", event)
                sink.append(fields)

        self._handler = _Handler(level=logging.DEBUG)

    def attach(self) -> None:
        self._handler.setLevel(logging.DEBUG)
        self._logger.addHandler(self._handler)
        if self._logger.level > logging.DEBUG or self._logger.level == 0:
            self._logger.setLevel(logging.DEBUG)

    def detach(self) -> None:
        self._logger.removeHandler(self._handler)
        try:
            self._logger.setLevel(self._previous_level)
        except ValueError:  # pragma: no cover - level already detached
            pass


class InProcessDriver:
    """REAL generation pipeline through ``GenerationService.start_case_generation``.

    ``frontier_timeout_seconds`` / ``generation_deadline_seconds`` default to
    the production ``Settings`` resolution (env / .env); pass explicit values
    only when the operator wants to pin them (recorded in metadata).
    """

    def __init__(
        self,
        *,
        concurrency: int,
        frontier_timeout_seconds: float | None = None,
        generation_deadline_seconds: int | None = None,
        database_url: str | None = None,
        capture_events: bool = True,
    ) -> None:
        from app.core.config import Settings  # noqa: E402
        from app.persistence.store import Store  # noqa: E402
        from app.services.generation import GenerationService  # noqa: E402

        self._tmp = Path(tempfile.mkdtemp(prefix="pd-bench-inproc-"))
        db_path = self._tmp / "benchmark.db"
        kwargs: dict[str, Any] = {
            "database_url": database_url or f"sqlite:///{db_path.as_posix()}",
            "generation_provider": "fake",
            "frontier_enabled": True,
            "max_concurrent_generations": max(concurrency, 1),
            "max_concurrent_generations_global": max(concurrency * 2, 2),
            "max_generations_per_session_per_window": 1000,
            "max_generations_global_per_window": 100000,
            "global_generation_window_seconds": 3600,
            "generation_limit_per_ip_per_hour": 100000,
        }
        if frontier_timeout_seconds is not None:
            kwargs["frontier_timeout_seconds"] = frontier_timeout_seconds
        if generation_deadline_seconds is not None:
            # ADV-31A-H01: Settings.generation_deadline_seconds is bound via
            # validation_alias="CASE_GENERATION_DEADLINE_SECONDS" (config.py),
            # so the non-canonical field-name kwarg is silently ignored by the
            # pydantic-settings constructor and the field would stay at its 60s
            # default. Always pass the canonical alias as the constructor key
            # (the CLI flag name --generation-deadline-seconds is unchanged;
            # metadata/result recording reads the resolved field, which works).
            kwargs["CASE_GENERATION_DEADLINE_SECONDS"] = int(
                generation_deadline_seconds
            )
        self._settings = Settings(**kwargs)
        # The Store requires a MIGRATED database (the same Alembic head the
        # production backend applies at boot).
        _upgrade_database(self._settings.database_url)
        self._store = Store(self._settings.database_url)
        self._service = GenerationService(settings=self._settings, store=self._store)
        self._events: list[dict[str, Any]] = []
        self._handler: _CaptureHandler | None = None
        if capture_events:
            self._handler = _CaptureHandler(self._events)
            self._handler.attach()

    @property
    def settings(self) -> Any:
        return self._settings

    @property
    def captured_events(self) -> list[dict[str, Any]]:
        """A SNAPSHOT of events captured so far."""
        return list(self._events)

    @property
    def live_events(self) -> list[dict[str, Any]]:
        """The LIVE event sink (used to correlate events produced during the run)."""
        return self._events

    def run_case(
        self, contestant: BenchmarkContestant, case: BenchmarkCase
    ) -> dict[str, Any]:
        started_wall = time.perf_counter()
        failure_code: str | None = None
        created = "ERROR"
        attempt_id = None
        try:
            session = self._service.create_anonymous_quota_session()
            started = self._service.start_case_generation(
                case.prompt,
                anonymous_quota_session_id=session.anonymous_quota_session_id,
                difficulty=case.difficulty,
                generation_provider="frontier",
                frontier_provider=contestant.provider,
                frontier_api_key=contestant.credential,
                frontier_model=contestant.model,
            )
            attempt_id = started.generation_attempt_id
            created = started.status
            failure_code = started.failure_code
        except Exception as exc:  # noqa: BLE001 - recorded as ERROR
            failure_code = "BENCHMARK_DRIVER_ERROR"
        elapsed_ms = int((time.perf_counter() - started_wall) * 1000)
        return {
            "generationAttemptId": attempt_id,
            "caseId": None,
            "generationId": None,
            "created": created,
            "failureCode": failure_code,
            "totalElapsedMs": elapsed_ms,
        }

    def close(self) -> None:
        try:
            if self._handler is not None:
                self._handler.detach()
        finally:
            try:
                self._store.dispose()
            finally:
                shutil.rmtree(self._tmp, ignore_errors=True)


# --------------------------------------------------------------------------- #
# result assembly per attempt
# --------------------------------------------------------------------------- #


def assemble_result(
    run_id: str,
    contestant: BenchmarkContestant,
    case: BenchmarkCase,
    repeat: int,
    execution_order: int,
    driver_outcome: dict[str, Any],
) -> dict[str, Any]:
    created = driver_outcome.get("created")
    if created == FINAL_STATUS_SKIPPED_CREDENTIAL:
        published = None
        final_status = FINAL_STATUS_SKIPPED_CREDENTIAL
    elif created == "PUBLISHED":
        published = True
        final_status = FINAL_STATUS_PUBLISHED
    elif created in ("FAILED", "ERROR"):
        published = False
        final_status = FINAL_STATUS_FAILED if created == "FAILED" else FINAL_STATUS_ERROR
    else:
        published = None
        final_status = FINAL_STATUS_ERROR
    record = _empty_result(
        run_id,
        contestant,
        case,
        repeat,
        execution_order,
        final_status=final_status,
        published=published,
        failure_code=driver_outcome.get("failureCode") or None,
    )
    record["generationAttemptId"] = driver_outcome.get("generationAttemptId") or None
    record["totalElapsedMs"] = driver_outcome.get("totalElapsedMs")
    return record


# --------------------------------------------------------------------------- #
# aggregation
# --------------------------------------------------------------------------- #


def _executed_results(results: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in results if r["finalStatus"] != FINAL_STATUS_SKIPPED_CREDENTIAL]


def _summarize_counts(values: Sequence[float]) -> dict[str, float | int]:
    if not values:
        return {"n": 0}
    sorted_values = sorted(values)
    return {
        "min": round(min(values), 2),
        "max": round(max(values), 2),
        "mean": round(sum(values) / len(values), 2),
        "median": round(percentile(sorted_values, 50), 2),
        "p90": round(percentile(sorted_values, 90), 2),
        "p95": round(percentile(sorted_values, 95), 2),
        "n": len(values),
    }


def aggregate_per_contestant(
    contestant: BenchmarkContestant,
    results: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    mine = [r for r in results if r["contestantId"] == contestant.id]
    executed = _executed_results(mine)
    skipped = [r for r in mine if r["finalStatus"] == FINAL_STATUS_SKIPPED_CREDENTIAL]
    n = len(executed)
    published_n = sum(1 for r in executed if r["published"] is True)
    failed_n = n - published_n
    published_rate = round(published_n / n, 4) if n else None

    def _count(predicate: Callable[[dict[str, Any]], bool]) -> int:
        return sum(1 for r in executed if predicate(r))

    zero_repair = _count(lambda r: r["published"] is True and r.get("repairCount") == 0)
    repair1 = _count(lambda r: r["published"] is True and r.get("repairCount") == 1)
    repair2 = _count(lambda r: r["published"] is True and r.get("repairCount") == 2)
    repair_exhausted = _count(
        lambda r: r.get("failureCode") == "REPAIR_BUDGET_EXHAUSTED"
    )
    timeout_failures = _count(lambda r: r.get("failureCode") in TIMEOUT_FAILURE_CODES)
    contract_violations = _count(lambda r: r.get("timeoutContractViolation") is True)
    contract_unverifiable = _count(
        lambda r: isinstance(r.get("contractViolationUnverifiableCalls"), int)
        and r["contractViolationUnverifiableCalls"] > 0
    )
    contract_verifiable_calls = sum(
        int(r.get("contractViolationVerifiableCalls") or 0) for r in executed
    )
    contract_unverifiable_calls = sum(
        int(r.get("contractViolationUnverifiableCalls") or 0) for r in executed
    )

    latency_values = [
        float(r["totalElapsedMs"])
        for r in executed
        if isinstance(r.get("totalElapsedMs"), (int, float))
    ]
    max_call_values = [
        float(r["maxProviderCallElapsedMs"])
        for r in executed
        if isinstance(r.get("maxProviderCallElapsedMs"), (int, float))
    ]
    calls_values = [
        int(r["providerCallCount"])
        for r in executed
        if isinstance(r.get("providerCallCount"), int)
    ]
    repairs_values = [
        int(r["repairCount"])
        for r in executed
        if isinstance(r.get("repairCount"), int)
    ]
    regens_values = [
        int(r["regenerationCount"])
        for r in executed
        if isinstance(r.get("regenerationCount"), int)
    ]
    structured_used = _count(lambda r: r.get("structuredOutputUsed") is True)
    structured_known = _count(lambda r: r.get("structuredOutputUsed") is not None)

    failure_codes: dict[str, int] = {}
    for r in executed:
        if r.get("failureCode"):
            failure_codes[r["failureCode"]] = failure_codes.get(r["failureCode"], 0) + 1

    return {
        "contestantId": contestant.id,
        "label": contestant.label,
        "provider": contestant.provider,
        "model": contestant.model,
        "enabled": contestant.enabled,
        "skippedCredentialMissing": len(skipped),
        "attempts": n,
        "published": published_n,
        "failed": failed_n,
        "publishedRate": published_rate,
        "zeroRepairPublished": zero_repair,
        "zeroRepairRate": round(zero_repair / n, 4) if n else None,
        "repair1Published": repair1,
        "repair2Published": repair2,
        "repairExhausted": repair_exhausted,
        "timeoutFailures": timeout_failures,
        "contractViolations": contract_violations,
        "contractVerifiableCalls": contract_verifiable_calls,
        "contractUnverifiableCalls": contract_unverifiable_calls,
        "latency": _summarize_counts(latency_values),
        "maxProviderCallElapsedMs": _summarize_counts(max_call_values),
        "meanProviderCallCount": round(sum(calls_values) / len(calls_values), 2)
        if calls_values
        else None,
        "meanRepairCount": round(sum(repairs_values) / len(repairs_values), 2)
        if repairs_values
        else None,
        "meanRegenerationCount": round(sum(regens_values) / len(regens_values), 2)
        if regens_values
        else None,
        "structuredOutputAttempts": structured_used,
        "structuredOutputKnownAttempts": structured_known,
        "structuredOutputRate": round(structured_used / n, 4)
        if n and structured_known
        else None,
        "failureCodes": failure_codes,
        "cost": {"source": COST_SOURCE_UNAVAILABLE, "amount": None, "currency": None},
    }


# --------------------------------------------------------------------------- #
# resume state
# --------------------------------------------------------------------------- #


def _state_path(run_dir: Path) -> Path:
    return run_dir / "state.json"


def _is_benchmark_run_dir(run_dir: Path) -> bool:
    """DEF-028 — does ``run_dir`` provably belong to a previous benchmark run
    of THIS tool?

    A ``--rerun`` reset may only ever remove a directory carrying one of this
    tool's identity markers: a ``metadata.json`` with the tool signature + a
    non-empty ``benchmarkRunId``, a ``state.json`` ``benchmarkSchemaVersion``
    + ``benchmarkRunId``, or a ``results.jsonl`` line carrying the schema
    version + run id. Arbitrary directories (typos, data/repo dirs) are never
    deleted.
    """
    if not run_dir.is_dir():
        return False
    metadata_path = run_dir / "metadata.json"
    if metadata_path.is_file():
        try:
            meta = json.loads(metadata_path.read_text(encoding="utf-8"))
            if isinstance(meta, dict):
                if meta.get("toolName") == "tools.frontier_benchmark":
                    run_id = meta.get("benchmarkRunId")
                    if isinstance(run_id, str) and run_id:
                        return True
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            pass
    state_path = run_dir / "state.json"
    if state_path.is_file():
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                if data.get("benchmarkSchemaVersion") == BENCHMARK_SCHEMA_VERSION:
                    run_id = data.get("benchmarkRunId")
                    if isinstance(run_id, str) and run_id:
                        return True
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            pass
    results_path = run_dir / "results.jsonl"
    if results_path.is_file():
        try:
            text = results_path.read_text(encoding="utf-8")
        except OSError:
            return False
        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                if record.get("benchmarkSchemaVersion") == BENCHMARK_SCHEMA_VERSION:
                    run_id = record.get("benchmarkRunId")
                    if isinstance(run_id, str) and run_id:
                        return True
    return False


def load_state(run_dir: Path) -> set[tuple[str, str, int]]:
    path = _state_path(run_dir)
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        raise BenchmarkError(f"corrupt resume state: {path}") from None
    completed: set[tuple[str, str, int]] = set()
    for entry in data.get("completed", []):
        try:
            completed.add(
                (
                    str(entry["contestantId"]),
                    str(entry["benchmarkCaseId"]),
                    int(entry["repeat"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            raise BenchmarkError(f"corrupt resume state entry: {path}") from None
    return completed


def write_state(
    run_dir: Path, run_id: str, completed: Iterable[tuple[str, str, int]]
) -> None:
    path = _state_path(run_dir)
    payload = {
        "benchmarkSchemaVersion": BENCHMARK_SCHEMA_VERSION,
        "benchmarkRunId": run_id,
        "completed": [
            {"contestantId": c, "benchmarkCaseId": k, "repeat": r}
            for c, k, r in sorted(completed)
        ],
    }
    _atomic_write_json(path, payload)


def load_completed_from_results(results_path: Path) -> set[tuple[str, str, int]]:
    """Crash-tolerant resume source: combos already durable in results.jsonl.

    Reads the (contestantId, benchmarkCaseId, repeatIndex) combos from the
    crash-tolerant JSONL so an interrupted run whose ``state.json`` never got
    written can still resume without re-executing completed combos (§22/§46).
    ``SKIPPED_CREDENTIAL_MISSING`` lines are NEVER treated as completed: a
    later run where the operator supplies the credential must be able to
    execute them. A missing file, unreadable file, malformed lines and
    records without a combo key are all tolerated (empty set / line skipped) —
    the same tolerance the final results merge applies.
    """
    completed: set[tuple[str, str, int]] = set()
    if not results_path.is_file():
        return completed
    try:
        lines = results_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return completed
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        if record.get("finalStatus") == FINAL_STATUS_SKIPPED_CREDENTIAL:
            continue
        try:
            completed.add(
                (
                    str(record["contestantId"]),
                    str(record["benchmarkCaseId"]),
                    int(record["repeatIndex"]),
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return completed


def run_id_from_results(results_path: Path) -> str | None:
    """First durable ``benchmarkRunId`` seen in an existing results.jsonl.

    Used to keep the run identity stable when resuming an interrupted run
    whose ``state.json`` is missing (the results file records the ORIGINAL
    run id; a fresh timestamp-based id would fork the run's artifact
    identity mid-way).
    """
    if not results_path.is_file():
        return None
    try:
        lines = results_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            run_id = record.get("benchmarkRunId")
            if isinstance(run_id, str) and run_id:
                return run_id
    return None


def refresh_resume_state(
    output_dir: Path,
    run_id: str,
    completed: set[tuple[str, str, int]],
    record: dict[str, Any],
) -> None:
    """Atomically persist the resume state AFTER each completed case (§22).

    Must be called AFTER the record's ``results.jsonl`` line was flushed, so
    the crash-tolerant JSONL line is durable before the state refresh. Because
    resume merges BOTH `state.json` and `results.jsonl` (see ``run_benchmark``),
    the state file can only ever lag the results file — no completed combo can
    be silently re-executed after an interruption, and no duplicate paid call
    is made. ``SKIPPED_CREDENTIAL_MISSING`` records never add a combo.
    """
    if record.get("finalStatus") == FINAL_STATUS_SKIPPED_CREDENTIAL:
        return
    try:
        combo = (
            str(record["contestantId"]),
            str(record["benchmarkCaseId"]),
            int(record["repeatIndex"]),
        )
    except (KeyError, TypeError, ValueError):
        return
    if combo in completed:
        return
    completed.add(combo)
    write_state(output_dir, run_id, completed)


def _atomic_write_json(path: Path, payload: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


# --------------------------------------------------------------------------- #
# artifact writers
# --------------------------------------------------------------------------- #


def write_results_jsonl(results_path: Path, record: dict[str, Any]) -> None:
    with results_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n")
        handle.flush()


def rewrite_results_jsonl(results_path: Path, records: Sequence[dict[str, Any]]) -> None:
    """Atomically replace results.jsonl with enriched lines (JSONL, not array)."""
    text = "".join(
        json.dumps(record, ensure_ascii=True, sort_keys=True) + "\n"
        for record in records
    )
    tmp = results_path.with_suffix(results_path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, results_path)


def git_commit_and_branch() -> tuple[str | None, str | None]:
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        branch = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    commit_val = commit.stdout.strip() if commit.returncode == 0 else None
    branch_val = branch.stdout.strip() if branch.returncode == 0 else None
    return commit_val or None, branch_val or None


def build_metadata(
    *,
    run_id: str,
    started_at: float,
    finished_at: float,
    suite_name: str,
    case_count: int,
    repeat_count: int,
    contestants: Sequence[BenchmarkContestant],
    runner_args: dict[str, Any],
    driver: str,
    seed: int | None,
    contestant_order: Sequence[str],
    generation_deadline_seconds: int | None,
    provider_timeout_seconds: float | None,
    core_call_budget: int | None,
    global_call_budget: int | None,
    max_repair_passes: int | None,
    max_full_regenerations: int | None,
    telemetry_notes: Sequence[str] = (),
) -> dict[str, Any]:
    commit, branch = git_commit_and_branch()
    return {
        "benchmarkSchemaVersion": BENCHMARK_SCHEMA_VERSION,
        "benchmarkRunId": run_id,
        "toolName": "tools.frontier_benchmark",
        "toolVersion": TOOL_VERSION,
        "applicationServiceVersion": "procedural-detective-backend (repo)",
        "driver": driver,
        "startedAt": started_at,
        "finishedAt": finished_at,
        "startedAtIso": _dt.datetime.fromtimestamp(
            started_at, _dt.timezone.utc
        ).isoformat(),
        "finishedAtIso": _dt.datetime.fromtimestamp(
            finished_at, _dt.timezone.utc
        ).isoformat(),
        "gitCommit": commit,
        "gitBranch": branch,
        "suite": suite_name,
        "caseCount": case_count,
        "repeatCount": repeat_count,
        "totalCaseAttempts": case_count * repeat_count,
        "seed": seed,
        "contestantOrder": list(contestant_order),
        "contestants": [
            {
                "id": c.id,
                "label": c.label,
                "provider": c.provider,
                "model": c.model,
                "credentialEnv": c.credential_env,
                "enabled": c.enabled,
                "tags": sorted(c.tags),
            }
            for c in contestants
        ],
        "generationDeadlineSeconds": generation_deadline_seconds,
        "providerTimeoutSeconds": provider_timeout_seconds,
        "coreProviderCallBudget": core_call_budget,
        "globalProviderCallBudget": global_call_budget,
        "maxRepairPasses": max_repair_passes,
        "maxFullRegenerations": max_full_regenerations,
        "telemetryNotes": [str(note) for note in telemetry_notes],
        "cli": runner_args,
    }


def write_metadata(run_dir: Path, metadata: dict[str, Any]) -> None:
    _atomic_write_json(run_dir / "metadata.json", metadata)


def write_summary_json(run_dir: Path, summary: dict[str, Any]) -> None:
    _atomic_write_json(run_dir / "summary.json", summary)


_CSV_FORMULA_PREFIXES = ("=", "+", "-", "@")


def _csv_cell(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    # DEF-036 — spreadsheet formula injection: neutralize cells starting with
    # = + - @ by prefixing an apostrophe (the OWASP-recommended neutralizer).
    if text.startswith(_CSV_FORMULA_PREFIXES):
        text = "'" + text
    if "," in text or '"' in text or "\n" in text:
        return '"' + text.replace('"', '""') + '"'
    return text


def write_summary_csv(run_dir: Path, per_contestant: Sequence[dict[str, Any]]) -> None:
    header = [
        "contestantId", "label", "provider", "model", "attempts", "skipped",
        "published", "failed", "publishedRate", "zeroRepairPublished",
        "zeroRepairRate", "repair1Published", "repair2Published",
        "repairExhausted", "timeoutFailures", "contractViolations",
        "medianMs", "p90Ms", "p95Ms", "meanMs",
        "avgProviderCalls", "avgRepairs", "avgRegenerations",
        "structuredOutputAttempts", "costSource",
    ]
    rows: list[list[str]] = [header]
    for row in per_contestant:
        latency = row.get("latency") or {}
        rows.append(
            [
                _csv_cell(row["contestantId"]),
                _csv_cell(row["label"]),
                _csv_cell(row["provider"]),
                _csv_cell(row["model"]),
                _csv_cell(row["attempts"]),
                _csv_cell(row["skippedCredentialMissing"]),
                _csv_cell(row["published"]),
                _csv_cell(row["failed"]),
                _csv_cell(row["publishedRate"]),
                _csv_cell(row["zeroRepairPublished"]),
                _csv_cell(row["zeroRepairRate"]),
                _csv_cell(row["repair1Published"]),
                _csv_cell(row["repair2Published"]),
                _csv_cell(row["repairExhausted"]),
                _csv_cell(row["timeoutFailures"]),
                _csv_cell(row["contractViolations"]),
                _csv_cell(latency.get("median")),
                _csv_cell(latency.get("p90")),
                _csv_cell(latency.get("p95")),
                _csv_cell(latency.get("mean")),
                _csv_cell(row["meanProviderCallCount"]),
                _csv_cell(row["meanRepairCount"]),
                _csv_cell(row["meanRegenerationCount"]),
                _csv_cell(row["structuredOutputAttempts"]),
                _csv_cell(row["cost"]["source"]),
            ]
        )
    text = "\n".join(",".join(row) for row in rows) + "\n"
    (run_dir / "summary.csv").write_text(text, encoding="utf-8")


# --------------------------------------------------------------------------- #
# secret-safety scan over the produced artifacts
# --------------------------------------------------------------------------- #


# Token-shaped credential patterns mirrored from tools/release_check.py
# (OpenAI-style keys); scanned over artifact bytes in addition to the exact
# credential-value scan (DEF-031 scope).
_TOKEN_SHAPED_RE = re.compile(rb"\bsk-[A-Za-z0-9]{20,}\b")


def _secret_byte_forms(value: str) -> tuple[bytes, ...]:
    """DEF-041 — deterministic candidate byte-encodings of one suspicious
    value: UTF-8, UTF-16LE/BE, the fully JSON ``\\u``-escaped rendering,
    URL-encoded (plus-form) bytes, base64 (standard + urlsafe, padded +
    unpadded) and hex (lower + upper). Keeps the scan hermetic and
    deterministic (no network, no randomness)."""
    import base64 as _b64

    utf8 = value.encode("utf-8")
    forms: list[bytes] = [utf8]
    forms.append(value.encode("utf-16-le"))
    forms.append(value.encode("utf-16-be"))
    forms.append(
        "".join("\\u%04x" % ord(ch) for ch in value).encode("utf-8")
    )
    forms.append(urllib.parse.quote(value, safe="").encode("utf-8"))
    forms.append(urllib.parse.quote_plus(value, safe="").encode("utf-8"))
    for encoded in (
        _b64.b64encode(utf8),
        _b64.b64encode(utf8).rstrip(b"="),
        _b64.urlsafe_b64encode(utf8),
        _b64.urlsafe_b64encode(utf8).rstrip(b"="),
    ):
        forms.append(encoded)
    hex_lower = utf8.hex().encode("ascii")
    forms.append(hex_lower)
    forms.append(hex_lower.upper())
    # Deduplicate while preserving order.
    return tuple(dict.fromkeys(forms))


def scan_files_for_secrets(
    run_dir: Path,
    secrets: Sequence[str],
    *,
    extra: Sequence[str] = (),
    token_patterns: bool = True,
) -> list[tuple[str, str]]:
    """Return [(rel_path, '<redacted>')] for every artifact containing a secret.

    DEF-031/DEF-041: ``extra`` accepts additional suspicious values from the
    operator/tests (e.g. config/corpus string fields) and the scan also
    detects UTF-16 / ``\\u``-escaped / URL-encoded / base64 / hex renderings
    of every suspicious value. ``token_patterns`` (default True) additionally
    flags token-shaped secrets (``sk-`` + 20+ alphanumerics, mirroring
    tools.release_check). The scan is deterministic and hermetic.
    """
    hits: list[tuple[str, str]] = []
    suspicious = [s for s in list(secrets) + list(extra) if s]
    if not suspicious and token_patterns is False:
        return hits
    secret_forms = [_secret_byte_forms(s) for s in suspicious]
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file():
            continue
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        found = False
        for forms in secret_forms:
            for form in forms:
                if form in raw:
                    hits.append((str(path.relative_to(run_dir)), "<redacted>"))
                    found = True
                    break
            if found:
                break
        if not found and token_patterns and _TOKEN_SHAPED_RE.search(raw):
            hits.append((str(path.relative_to(run_dir)), "<redacted>"))
    return hits


# --------------------------------------------------------------------------- #
# report.md
# --------------------------------------------------------------------------- #


def _fmt_rate(value: float | None) -> str:
    return f"{value * 100:.2f}%" if value is not None else "n/a"


def run_statistical_caveat() -> str:
    return (
        "Statistical note: single small runs cannot establish superiority (see "
        "caveats). Do not treat small rate differences as meaningful."
    )


def build_report(
    *,
    metadata: dict[str, Any],
    per_contestant: Sequence[dict[str, Any]],
    suite: SuiteSpec,
    cases: Sequence[BenchmarkCase],
    secret_scan_hits: Sequence[tuple[str, str]],
    luna_status: str,
    luna_reason: str,
    monitoring_artifacts: dict[str, Any] | None,
    caveats: Sequence[str],
    recommendation_notes: Sequence[str],
) -> str:
    lines: list[str] = []
    lines.append("# Phase 31 — Frontier/BYOK Benchmark Report")
    lines.append("")
    lines.append("## RUN METADATA")
    lines.append("")
    lines.append(f"- benchmarkRunId: `{metadata['benchmarkRunId']}`")
    lines.append(f"- benchmarkSchemaVersion: `{metadata['benchmarkSchemaVersion']}`")
    lines.append(f"- tool: `{metadata['toolName']} v{metadata['toolVersion']}`")
    lines.append(f"- driver: `{metadata['driver']}`")
    lines.append(f"- startedAt: `{metadata.get('startedAtIso')}`")
    lines.append(f"- finishedAt: `{metadata.get('finishedAtIso')}`")
    lines.append(f"- gitCommit: `{metadata.get('gitCommit') or 'unknown'}`")
    lines.append(f"- gitBranch: `{metadata.get('gitBranch') or 'unknown'}`")
    lines.append(f"- suite: `{metadata['suite']}`")
    lines.append(
        f"- caseCount: `{metadata['caseCount']}`  "
        f"repeatCount: `{metadata['repeatCount']}`"
    )
    lines.append(
        f"- seed: `{metadata.get('seed')}`  contestantOrder: "
        f"`{', '.join(metadata.get('contestantOrder') or [])}`"
    )
    lines.append(
        f"- generation deadline seconds: `{metadata.get('generationDeadlineSeconds')}`; "
        f"provider timeout seconds: `{metadata.get('providerTimeoutSeconds')}`"
    )
    lines.append(
        f"- core provider-call budget: `{metadata.get('coreProviderCallBudget')}`; "
        f"global provider-call budget: `{metadata.get('globalProviderCallBudget')}`; "
        f"max repairs: `{metadata.get('maxRepairPasses')}`; "
        f"max regenerations: `{metadata.get('maxFullRegenerations')}`"
    )
    lines.append("")
    lines.append("## CONTESTANTS")
    lines.append("")
    lines.append("| id | label | provider | model | enabled | credentialEnv |")
    lines.append("|---|---|---|---|---|---|")
    for entry in metadata.get("contestants", []):
        lines.append(
            f"| {entry['id']} | {entry['label']} | {entry['provider']} | "
            f"`{entry['model'] or '(no verified model)'}` | {entry['enabled']} | "
            f"`{entry['credentialEnv']}` |"
        )
    lines.append("")
    lines.append("## WORKLOAD")
    lines.append("")
    lines.append(
        f"- suite `{metadata['suite']}` -> Easy {suite.easy} / Medium {suite.medium} / "
        f"Hard {suite.hard} cases per contestant; `--repeat {metadata['repeatCount']}`."
    )
    lines.append(f"- selected case ids: `{', '.join(c.id for c in cases)}`.")
    lines.append("")
    lines.append("## QUALITY RESULTS")
    lines.append("")
    lines.append(
        "| Contestant | N | Published | Published rate | Zero-repair rate | "
        "Repair-1 | Repair-2 | Repair-exhausted |"
    )
    lines.append("|---|---|---|---|---|---|---|---|")
    for row in per_contestant:
        skipped = row["skippedCredentialMissing"]
        skip_note = f" ({skipped} skipped)" if skipped else ""
        lines.append(
            f"| {row['contestantId']} | {row['attempts']}{skip_note} | "
            f"{row['published']} | {_fmt_rate(row['publishedRate'])} | "
            f"{_fmt_rate(row['zeroRepairRate'])} | {row['repair1Published']} | "
            f"{row['repair2Published']} | {row['repairExhausted']} |"
        )
    lines.append("")
    lines.append(
        "Primary quality metric: PUBLISHED success rate. A case is a success "
        "only when the normal pipeline reaches its canonical published/playable "
        "state."
    )
    lines.append("")
    lines.append("## LATENCY RESULTS")
    lines.append("")
    lines.append(
        "Client/total elapsed ms per executed attempt (published AND failed; "
        "timed-out runs remain visible as failures)."
    )
    lines.append("")
    lines.append("| Contestant | Min | Median | Mean | P90 | P95 | Max | n |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for row in per_contestant:
        lat = row.get("latency") or {}
        if not lat.get("n"):
            lines.append(f"| {row['contestantId']} | n/a | n/a | n/a | n/a | n/a | n/a | 0 |")
            continue
        lines.append(
            f"| {row['contestantId']} | {lat.get('min')} | {lat.get('median')} | "
            f"{lat.get('mean')} | {lat.get('p90')} | {lat.get('p95')} | "
            f"{lat.get('max')} | {lat.get('n')} |"
        )
    lines.append("")
    lines.append("max provider-call elapsed ms per contestant:")
    lines.append("")
    lines.append("| Contestant | Median | P90 | P95 |")
    lines.append("|---|---|---|---|")
    for row in per_contestant:
        stat = row.get("maxProviderCallElapsedMs") or {}
        if not stat.get("n"):
            lines.append(f"| {row['contestantId']} | n/a | n/a | n/a |")
            continue
        lines.append(
            f"| {row['contestantId']} | {stat.get('median')} | "
            f"{stat.get('p90')} | {stat.get('p95')} |"
        )
    lines.append("")
    lines.append("## REPAIR / REGENERATION RESULTS")
    lines.append("")
    lines.append("| Contestant | Mean repairs | Mean regenerations | Mean provider calls |")
    lines.append("|---|---|---|---|")
    for row in per_contestant:
        lines.append(
            f"| {row['contestantId']} | "
            f"{row['meanRepairCount'] if row['meanRepairCount'] is not None else 'n/a'} | "
            f"{row['meanRegenerationCount'] if row['meanRegenerationCount'] is not None else 'n/a'} | "
            f"{row['meanProviderCallCount'] if row['meanProviderCallCount'] is not None else 'n/a'} |"
        )
    lines.append("")
    lines.append(
        "Repair counts come from the production controller's sanitized "
        "observability (null where telemetry is unavailable — never fabricated)."
    )
    lines.append("")
    lines.append("## FAILURE DISTRIBUTION")
    lines.append("")
    for row in per_contestant:
        lines.append(f"### {row['contestantId']}")
        codes = row.get("failureCodes") or {}
        if not codes:
            lines.append("- no failure codes")
        else:
            for code in sorted(codes):
                lines.append(f"- {_markdown_clean(code)}: {codes[code]}")
    lines.append("")
    lines.append("## STRUCTURED OUTPUT")
    lines.append("")
    lines.append(
        "Structured-output usage is measured from ACTUAL execution telemetry "
        "(the adapter reports what it really sent), never from model marketing."
    )
    lines.append("")
    lines.append("| Contestant | Attempts using structured output | Known attempts | Rate |")
    lines.append("|---|---|---|---|")
    for row in per_contestant:
        lines.append(
            f"| {row['contestantId']} | {row['structuredOutputAttempts']} | "
            f"{row['structuredOutputKnownAttempts'] or 'n/a'} | "
            f"{_fmt_rate(row['structuredOutputRate'])} |"
        )
    lines.append("")
    lines.append("## TOKEN / COST RESULTS")
    lines.append("")
    lines.append(
        "Token counts and costs are `null` unless truthfully derivable. The "
        "Phase 30 adapter does not currently surface OpenRouter usage, so every "
        "contestant reports costSource=`unavailable` with no fabricated amount."
    )
    lines.append("")
    for row in per_contestant:
        cost = row.get("cost") or {}
        lines.append(
            f"- {row['contestantId']}: costSource=`{cost.get('source')}` "
            f"amount={cost.get('amount')} currency={cost.get('currency')}"
        )
    lines.append("")
    lines.append("## TIMEOUT CONTRACT CHECK")
    lines.append("")
    lines.append(
        f"Contract: per-call `elapsedMs <= effectiveProviderTimeoutMs + "
        f"{CONTRACT_TOLERANCE_MS} ms` and total generation `elapsedMs <= "
        f"generation deadline + {CONTRACT_TOLERANCE_MS} ms`. Violations are "
        "flagged CONTRACT_VIOLATION and counted separately from ordinary model "
        "slowness."
    )
    lines.append("")
    total_violations = 0
    total_verifiable = 0
    total_unverifiable = 0
    for row in per_contestant:
        total_violations += row["contractViolations"]
        verifiable = row.get("contractVerifiableCalls") or 0
        unverifiable = row.get("contractUnverifiableCalls") or 0
        total_verifiable += verifiable
        total_unverifiable += unverifiable
        lines.append(
            f"- {row['contestantId']}: {row['contractViolations']} contract "
            f"violations (verified within contract: "
            f"{max(verifiable - row['contractViolations'], 0)} calls; NOT "
            f"verifiable: {unverifiable} calls missing timeout data)"
        )
    # DEF-034 — never claim an all-clear when zero calls were verifiable.
    if total_violations > 0:
        lines.append(f"- Total CONTRACT_VIOLATION detected: {total_violations}.")
    elif total_verifiable > 0:
        lines.append(
            "- No CONTRACT_VIOLATION detected in this run "
            f"({total_verifiable} call(s) verified within contract)."
        )
    else:
        lines.append(
            "- CONTRACT check NOT VERIFIABLE: zero provider.call events carried "
            "the required timeout data "
            f"({total_unverifiable} call(s) unverifiable) — no all-clear is "
            "claimed for this run."
        )
    lines.append("")
    lines.append("## LUNA VS FRONTIER")
    lines.append("")
    if luna_status == LUNA_STATUS_BLOCKED:
        lines.append(f"Luna baseline: `{LUNA_STATUS_BLOCKED}`")
        lines.append("")
        lines.append(f"Reason: {luna_reason}")
        lines.append("")
        lines.append(
            "No Luna baseline row can be rendered — no fake Luna network "
            "behavior is ever created. The Frontier contestants above are the "
            "benchmark."
        )
    else:
        lines.append("Luna baseline available (operator-pinned verified id).")
    lines.append("")
    lines.append("## RECOMMENDATION")
    lines.append("")
    if not recommendation_notes:
        lines.append("No recommendation — insufficient or unavailable evidence. ")
        lines.append(run_statistical_caveat())
    else:
        for note in recommendation_notes:
            lines.append(f"- {note}")
        lines.append("")
        lines.append(run_statistical_caveat())
    lines.append("")
    lines.append("## CAVEATS")
    lines.append("")
    for caveat in caveats:
        lines.append(f"- {caveat}")
    lines.append("")
    lines.append(
        "Each recommendation, if any, cites the metrics above; the benchmark "
        "never edits production configuration."
    )
    lines.append("")
    lines.append("## SECRET-SAFETY CHECK")
    lines.append("")
    lines.append(
        "- Credentials were loaded only from the process environment or the "
        "git-ignored `.env.benchmark` file and are never written to artifacts, "
        "logs, exceptions, resume state or reports."
    )
    lines.append(
        "- Sentinel used by the regression tests: the mandatory Phase 31 "
        "sentinel constant (defined in `tools/frontier_benchmark.py`) is "
        "proven to appear only in the mocked outbound authentication slot "
        "and is never rendered inside benchmark artifacts."
    )
    if secret_scan_hits:
        lines.append(
            f"- **SECRET-SAFETY SCAN FAILED**: {len(secret_scan_hits)} "
            "artifact(s) contain credential material — the run aborted without "
            "persisting it."
        )
    else:
        lines.append(
            "- Post-run artifact scan: **CLEAN** — no credential used during "
            "the run appears in any output artifact."
        )
    lines.append("")
    lines.append("## PHASE 29 MONITORING EVALUATION")
    lines.append("")
    if not monitoring_artifacts:
        lines.append(
            "This section is unavailable for this run: the benchmark was "
            "executed without `--post-monitoring` (Phase 29 monitoring "
            "evaluation is a Dev-Box-only local step). Run with "
            "`--post-monitoring` and re-run to populate it."
        )
    else:
        paused = monitoring_artifacts

        def _p29(value: Any) -> Any:
            return value if value is not None else "n/a"

        lines.append(f"- monitoring window: {_p29(paused.get('hours'))} h (evaluated after the benchmark run)")
        lines.append(f"- HTTP requests: {_p29(paused.get('http_total'))}")
        lines.append(f"- Page/API requests: {_p29(paused.get('http_page_api'))}")
        lines.append(f"- 4xx responses: {_p29(paused.get('http_4xx'))}")
        lines.append(f"- 5xx responses: {_p29(paused.get('http_5xx'))}")
        lines.append(f"- Playthroughs started: {_p29(paused.get('playthroughs_started'))}")
        lines.append(f"- Cases started: {_p29(paused.get('cases_started'))}")
        lines.append(f"- Cases completed: {_p29(paused.get('cases_completed'))}")
        lines.append(f"- Completion rate: {_p29(paused.get('completion_rate'))}")
        lines.append(f"- Application container state/health: {_p29(paused.get('health_app'))}")
        lines.append(f"- Caddy state/health: {_p29(paused.get('health_caddy'))}")
        warnings = paused.get("warnings") or []
        lines.append(f"- Monitoring warnings: {len(warnings)}")
        for warning in warnings:
            lines.append(f"  - {_markdown_clean(warning)}")
        lines.append("")
        lines.append("HTTP request count is not equivalent to visitors or players.")
        lines.append("")
        lines.append(
            "The Phase 29 section is an operational/usage validation layer "
            "only; it does NOT rank benchmark contestants — the model "
            "benchmark remains based on the Phase 31 per-case metrics above."
        )
    lines.append("")
    return "\n".join(lines)


def build_summary_document(
    *,
    run_id: str,
    generated_at: float,
    per_contestant: Sequence[dict[str, Any]],
    luna_status: str,
    luna_reason: str,
    monitoring: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "benchmarkSchemaVersion": BENCHMARK_SCHEMA_VERSION,
        "benchmarkRunId": run_id,
        "generatedAt": generated_at,
        "perContestant": {row["contestantId"]: row for row in per_contestant},
        "luna": {"status": luna_status, "reason": luna_reason},
        "monitoring": monitoring,
    }


# --------------------------------------------------------------------------- #
# Phase 29 monitoring integration (Dev-Box local, raw temp deleted)
# --------------------------------------------------------------------------- #

PHASE29_COMPOSE_ARGS = ["-f", "docker-compose.prod.yml", "-f", "docker-compose.lan.yml"]
PHASE29_DB_CONTAINER = "procedural-detective"
PHASE29_DB_PATH_IN_CONTAINER = "/data/procedural_detective.db"


def _default_capture_caddy_logs(tmp_dir: Path, hours: int, compose_args: list[str]) -> None:
    cmd = [
        "docker", "compose", *compose_args, "logs", f"--since={hours}h",
        "--no-color", "caddy",
    ]
    completed = subprocess.run(
        cmd, cwd=str(_REPO_ROOT), capture_output=True, text=True, timeout=300,
        check=False,
    )
    if completed.returncode != 0:
        raise MonitoringCaptureError(
            "docker compose logs caddy failed (exit %d)" % completed.returncode
        )
    (tmp_dir / "caddy.log").write_text(
        completed.stdout, encoding="utf-8", errors="replace"
    )


def _default_capture_sqlite_db(tmp_dir: Path, compose_args: list[str]) -> None:
    cmd = [
        "docker", "compose", *compose_args, "cp",
        f"{PHASE29_DB_CONTAINER}:{PHASE29_DB_PATH_IN_CONTAINER}",
        str(tmp_dir / "pd.db"),
    ]
    completed = subprocess.run(
        cmd, cwd=str(_REPO_ROOT), capture_output=True, text=True, timeout=300,
        check=False,
    )
    if completed.returncode != 0:
        raise MonitoringCaptureError(
            "docker compose cp database snapshot failed (exit %d)"
            % completed.returncode
        )


def _default_monitoring_subprocess(args: Sequence[str]) -> str:
    cmd = [sys.executable, "-m", "tools.monitoring_report", *args]
    completed = subprocess.run(
        cmd, cwd=str(_REPO_ROOT), capture_output=True, text=True, timeout=300,
        check=False,
    )
    if completed.returncode != 0:
        raise MonitoringCaptureError(
            "monitoring_report exited %d" % completed.returncode
        )
    return completed.stdout


def build_phase29_artifacts(
    run_dir: Path,
    caddy_log_text: str,
    db_path: Path,
    hours: int,
    *,
    monitoring_subprocess: Callable[[Sequence[str]], str] | None = None,
    include_health: bool = True,
    existing_health_text: str | None = None,
) -> dict[str, Any]:
    """Produce the three derived Phase 29 artifacts from a TEMP capture.

    Raw Caddy logs and the DB snapshot live only in the caller-provided temp
    location and are removed by the caller (nothing raw is ever written into
    ``run_dir``).
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = db_path.parent
    tmp_dir.mkdir(parents=True, exist_ok=True)
    log_path = tmp_dir / "caddy.log"
    log_path.write_text(caddy_log_text, encoding="utf-8", errors="replace")

    monitor = monitoring_subprocess or _default_monitoring_subprocess
    summary_text = monitor(
        ["--logs", str(log_path), "--db", str(db_path),
         "--hours", str(hours), "--summary"]
    )
    json_text = monitor(
        ["--logs", str(log_path), "--db", str(db_path),
         "--hours", str(hours), "--json"]
    )
    (run_dir / "phase29-monitoring-summary.txt").write_text(
        summary_text, encoding="utf-8"
    )
    (run_dir / "phase29-monitoring.json").write_text(json_text, encoding="utf-8")

    health_text = existing_health_text
    health_ok = True
    if include_health:
        try:
            health_text = monitor(["--health"])
        except MonitoringCaptureError as exc:
            health_text = f"phase29 health unavailable: {exc}"
            health_ok = False
    if health_text is not None:
        (run_dir / "phase29-health.txt").write_text(health_text, encoding="utf-8")

    return summarize_phase29_json(
        json_text, hours=hours, health_ok=health_ok, health_text=health_text or ""
    )


def summarize_phase29_json(
    json_text: str, *, hours: int, health_ok: bool, health_text: str
) -> dict[str, Any]:
    enriched: dict[str, Any] = {
        "hours": hours,
        "health_ok": health_ok,
        "health_text_present": bool(health_text),
        "warnings": [],
    }
    try:
        payload = json.loads(json_text)
    except (ValueError, TypeError):
        enriched["warnings"].append("phase29 JSON could not be parsed")
        enriched["http_total"] = None
        enriched["http_page_api"] = None
        enriched["http_4xx"] = None
        enriched["http_5xx"] = None
        enriched["playthroughs_started"] = None
        enriched["cases_started"] = None
        enriched["cases_completed"] = None
        enriched["completion_rate"] = None
        enriched["health_app"] = None
        enriched["health_caddy"] = None
        return enriched
    http = payload.get("http") or {}
    product = payload.get("product") or {}
    if not isinstance(http, dict):
        http = {}
        enriched["warnings"].append("monitoring 'http' block was not an object")
    if not isinstance(product, dict):
        product = {}
        enriched["warnings"].append("monitoring 'product' block was not an object")
    status_classes = http.get("status_classes") or {}
    if not isinstance(status_classes, dict):
        enriched["warnings"].append(
            "monitoring status_classes was not an object, treated as empty"
        )
        status_classes = {}
    enriched["http_total"] = http.get("total")
    enriched["http_page_api"] = http.get("page_api_requests")

    def _safe_count(value: Any, field_name: str) -> int:
        """DEF-044 — unparseable monitoring counts degrade to 0 with a warning
        (never a ValueError abort after all paid work)."""
        if isinstance(value, bool):
            enriched["warnings"].append(
                f"monitoring {field_name} was a boolean, treated as 0"
            )
            return 0
        try:
            converted = int(value)
        except (TypeError, ValueError, OverflowError):
            enriched["warnings"].append(
                f"monitoring {field_name} was not parseable, treated as 0"
            )
            return 0
        return converted

    enriched["http_4xx"] = sum(
        _safe_count(v, f"status_classes[{k!r}]")
        for k, v in status_classes.items()
        if str(k).startswith("4")
    )
    raw_count_5xx = http.get("count_5xx")
    if raw_count_5xx in (None, ""):
        count_5xx = 0
        enriched["http_5xx"] = None
    else:
        count_5xx = _safe_count(raw_count_5xx, "count_5xx")
        enriched["http_5xx"] = count_5xx
    enriched["playthroughs_started"] = product.get("playthroughs_started")
    enriched["cases_started"] = product.get("cases_started")
    enriched["cases_completed"] = product.get("cases_completed")
    completion_rate = product.get("completion_rate")
    if completion_rate is not None:
        try:
            completion_rate = round(float(completion_rate), 4)
        except (TypeError, ValueError, OverflowError):
            enriched["warnings"].append(
                "monitoring completion_rate was not parseable, treated as None"
            )
            completion_rate = None
    enriched["completion_rate"] = completion_rate
    if count_5xx > 0:
        enriched["warnings"].append("unexpected 5xx count in the monitoring window")
    health_app = health_caddy = None
    for line in (health_text or "").splitlines():
        lower = line.lower()
        if "procedural-detective" in lower and "state=" in lower:
            health_app = line
        if "caddy" in lower and "state=" in lower:
            health_caddy = line
    enriched["health_app"] = health_app
    enriched["health_caddy"] = health_caddy
    if not health_ok:
        enriched["warnings"].append("monitoring --health step failed")
    if not enriched.get("http_total"):
        enriched["warnings"].append("no access-log entries in the monitoring window")
    return enriched


def run_phase29_monitoring(
    run_dir: Path,
    window_seconds: float,
    *,
    compose_args: Sequence[str] | None = None,
    capture_logs: Callable[..., Any] | None = None,
    capture_db: Callable[..., Any] | None = None,
    monitoring_subprocess: Callable[[Sequence[str]], str] | None = None,
    existing_health_text: str | None = None,
    include_health: bool = True,
) -> dict[str, Any]:
    """Post-run Phase 29 evaluation entirely on the Dev-Box (temp raw deleted).

    Captures Caddy logs + a DB snapshot into a TEMP dir, runs the existing
    tools.monitoring_report summary + json (+ health), writes the DERIVED
    artifacts into the run dir, then deletes the temp raw logs/DB.
    """
    hours = max(1, int(math.ceil(window_seconds / 3600)))
    tmp = Path(tempfile.mkdtemp(prefix="pd-phase29-"))
    try:
        log_capture = capture_logs or _default_capture_caddy_logs
        db_capture = capture_db or _default_capture_sqlite_db
        compose = list(compose_args or PHASE29_COMPOSE_ARGS)
        log_capture(tmp, hours, compose)
        db_capture(tmp, compose)
        log_path = tmp / "caddy.log"
        db_path = tmp / "pd.db"
        caddy_text = log_path.read_text(encoding="utf-8", errors="replace")
        return build_phase29_artifacts(
            run_dir,
            caddy_text,
            db_path,
            hours,
            monitoring_subprocess=monitoring_subprocess,
            include_health=include_health,
            existing_health_text=existing_health_text,
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------- #
# runner orchestration
# --------------------------------------------------------------------------- #


def _new_run_id(now: float) -> str:
    stamp = _dt.datetime.fromtimestamp(now, _dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"phase31-{stamp}"


def _plan_summary_text(
    *,
    run_id: str,
    suite_name: str,
    contestants: Sequence[BenchmarkContestant],
    cases: Sequence[BenchmarkCase],
    repeat: int,
    pending_count: int,
    skip_count: int,
    driver: str,
    concurrency: int,
    base_url: str | None,
    credential_map: dict[str, bool],
    max_cases: int | None,
    output_dir: Path,
    dry_run: bool,
) -> str:
    lines: list[str] = [
        f"Phase 31 benchmark plan (benchmarkRunId={run_id})",
        f"  driver:            {driver}",
        f"  suite:             {suite_name}",
        f"  cases in suite:    {', '.join(c.id for c in cases)}",
        f"  repeat:            {repeat}",
        "  contestants:",
    ]
    for contestant in contestants:
        if not contestant.enabled:
            lines.append(f"    - {contestant.id} (DISABLED, not executed)")
            continue
        credential_ok = credential_map.get(contestant.id, False)
        state = "credential OK" if credential_ok else "MISSING CREDENTIAL -> SKIPPED_CREDENTIAL_MISSING"
        lines.append(
            f"    - {contestant.id}  provider={contestant.provider}  "
            f"model={contestant.model}  ({state})"
        )
    lines.append(f"  total case attempts: {pending_count}  (resume-skipped: {skip_count})")
    lines.append(f"  concurrency:         {concurrency}")
    if max_cases is not None:
        lines.append(f"  max-cases cap:       {max_cases}")
    lines.append(
        "  paid provider calls: no (dry-run contacts NO provider)"
        if dry_run
        else "  paid provider calls: yes"
    )
    if base_url:
        lines.append(f"  base-url:            {base_url}")
    lines.append(f"  output-dir:          {output_dir}")
    if dry_run:
        lines.append(
            "  DRY RUN: NO provider is contacted, NO paid call is made, "
            "NO artifact is written."
        )
    return "\n".join(lines)


def _execute_item(
    item: WorkItem,
    *,
    run_id: str,
    started_at: float,
    item_to_contestant: dict[str, BenchmarkContestant],
    case_map: dict[str, BenchmarkCase],
    driver_instance: Any,
    execution_counter: list[int],
    execution_lock: threading.Lock,
    deadline_ms: int | float | None,
) -> tuple[WorkItem, dict[str, Any]]:
    contestant = item_to_contestant[item.contestant_id]
    case = case_map[item.case_id]
    try:
        if contestant.credential:
            started_wall = time.perf_counter()
            outcome = driver_instance.run_case(contestant, case)
            if outcome.get("totalElapsedMs") is None:
                outcome["totalElapsedMs"] = int(
                    (time.perf_counter() - started_wall) * 1000
                )
        else:
            outcome = {
                "generationAttemptId": None,
                "caseId": None,
                "generationId": None,
                "created": FINAL_STATUS_SKIPPED_CREDENTIAL,
                "failureCode": None,
                "totalElapsedMs": None,
            }
    except Exception as exc:  # noqa: BLE001 - per-item isolation
        outcome = {
            "generationAttemptId": None,
            "caseId": None,
            "generationId": None,
            "created": "ERROR",
            "failureCode": "BENCHMARK_DRIVER_ERROR",
            "totalElapsedMs": None,
            "errorDetailSanitized": _sanitize_error(exc),
        }
    with execution_lock:
        execution_counter[0] += 1
        order = execution_counter[0]
    record = assemble_result(
        run_id, contestant, case, item.repeat, order, outcome
    )
    record["startedAt"] = started_at
    record["completedAt"] = time.time()
    return item, record


class _ArtifactScanGuard:
    """DEF-039 — positional artifact secret scan for EVERY exit path.

    ``main`` arms this guard the moment a real run starts writing artifacts and
    re-runs the secret scan in a ``finally``, so abort paths (KeyboardInterrupt,
    enrichment failure, max-cost trip, ...) can never leave artifacts on disk
    without a scan. A hit aborts with exit code 2.
    """

    def __init__(
        self, output_dir: Path, credentials_provider: Callable[[], list[str]]
    ) -> None:
        self._output_dir = output_dir
        self._credentials_provider = credentials_provider

    def scan(self) -> list[tuple[str, str]]:
        return scan_files_for_secrets(self._output_dir, self._credentials_provider())


_CURRENT_RUN_GUARD: _ArtifactScanGuard | None = None


def run_benchmark(args: argparse.Namespace) -> int:
    global _CURRENT_RUN_GUARD
    now = time.time()
    if args.output_dir is None:
        run_id = _new_run_id(now)
        output_dir = DEFAULT_OUTPUT_ROOT / run_id
    else:
        output_dir = Path(args.output_dir)
        run_id = _new_run_id(now)
        if output_dir.is_dir() and (output_dir / "state.json").is_file():
            try:
                run_id = json.loads((output_dir / "state.json").read_text(encoding="utf-8")).get(
                    "benchmarkRunId"
                ) or run_id
            except (json.JSONDecodeError, OSError):
                pass
    # NOTE (DEF-028/DEF-043): the destructive --rerun reset and the output-dir
    # creation are deferred until AFTER explicit confirmation below; a dry-run
    # NEVER touches the filesystem. --rerun only ever resets a directory that
    # provably belongs to a previous benchmark run of this tool.
    state_path = _state_path(output_dir)

    # ---- config load (fail-closed before any provider call) ---------------
    dotenv_values = parse_env_file_values(resolve_benchmark_env_file())

    def _with_credentials() -> tuple[list[BenchmarkContestant], dict[str, bool]]:
        base = load_contestants_toml(Path(args.contestants_path))
        credential_map: dict[str, bool] = {}
        resolved: list[BenchmarkContestant] = []
        for contestant in base:
            if not contestant.enabled:
                resolved.append(contestant)
                continue
            credential = load_credential(contestant.credential_env, dotenv_values)
            if credential:
                credential_map[contestant.id] = True
            resolved.append(
                BenchmarkContestant(
                    id=contestant.id,
                    label=contestant.label,
                    provider=contestant.provider,
                    model=contestant.model,
                    credential_env=contestant.credential_env,
                    enabled=contestant.enabled,
                    tags=contestant.tags,
                    credential=credential,
                )
            )
        return resolved, credential_map

    contestants, credential_map = _with_credentials()

    suites = load_suites(Path(args.suites_path))
    suite_name = args.suite
    if suite_name not in suites:
        raise ConfigError(
            f"unknown suite {suite_name!r}; available: {', '.join(sorted(suites))}"
        )
    suite = suites[suite_name]
    corpus = load_corpus(Path(args.corpus_dir))
    case_ids = [
        part.strip() for part in (args.case_ids or "").split(",") if part.strip()
    ]
    cases = select_cases(
        corpus, suite, difficulty_filter=args.difficulty, case_ids=case_ids or None
    )

    if args.contestant:
        allowed = set(args.contestant)
        contestants = [c for c in contestants if c.id in allowed]
        if not contestants:
            raise ConfigError(
                f"no contestants matched the --contestant filter {sorted(allowed)}"
            )

    # ---- resume state ------------------------------------------------------
    # Resume sources (never applied with --rerun, which explicitly repeats the
    # whole workload):
    #   1. state.json                       — persisted after EACH completed
    #      case (atomic), the primary resume record;
    #   2. results.jsonl (crash-tolerant)   — when state.json is absent (a run
    #      interrupted before any state refresh, or the file lost), the combos
    #      already durable in the JSONL are treated as completed TOO so no
    #      paid combo is ever silently re-executed (§22/§46). The design is
    #      documented "dedupe, never surprise re-purchase": use --rerun to
    #      explicitly repeat the whole workload.
    completed: set[tuple[str, str, int]] = set()
    resumed_from_results = False
    from_results: set[tuple[str, str, int]] = set()
    state_existed = state_path.is_file()
    if output_dir.exists() and not args.rerun:
        if state_existed:
            completed = load_state(output_dir)
        results_path_known = output_dir / "results.jsonl"
        if results_path_known.is_file():
            from_results = load_completed_from_results(results_path_known)
            if from_results:
                resumed_from_results = True
                completed |= from_results
                # Keep the interrupted run's identity (do not fork it with a
                # fresh timestamp-based id) when the state file is gone.
                if not state_existed:
                    run_id = run_id_from_results(results_path_known) or run_id
        if resumed_from_results and not state_existed:
            print(
                f"[resume] {state_path.name} absent: treating "
                f"{len(from_results)} combo(s) already present in "
                f"'results.jsonl' as completed (dedupe — no paid work is "
                f"re-executed; use --rerun to repeat them explicitly)"
            )
    work_items, contestant_order, effective_seed = build_work_items(
        contestants,
        cases,
        args.repeat,
        seed=args.seed,
        fixed_order=args.fixed_order,
    )

    # ---- resume filtering --------------------------------------------------
    pending_items: list[WorkItem] = []
    for item in work_items:
        combo: tuple[str, str, int] = (item.contestant_id, item.case_id, item.repeat)
        if args.rerun or combo not in completed:
            pending_items.append(item)
    skip_count = len(work_items) - len(pending_items)

    # DEF-042 — an explicit --contestant filter that matches only DISABLED
    # contestants would otherwise silently run a zero-attempt benchmark. Print
    # the (non-secret) plan so the operator sees exactly what matched, then
    # fail closed.
    if args.contestant and contestants and not any(c.enabled for c in contestants):
        print(
            _plan_summary_text(
                run_id=run_id,
                suite_name=suite_name,
                contestants=contestants,
                cases=cases,
                repeat=args.repeat,
                pending_count=0,
                skip_count=0,
                driver=args.driver,
                concurrency=args.concurrency,
                base_url=args.base_url if args.driver == "http" else None,
                credential_map=credential_map,
                max_cases=args.max_cases,
                output_dir=output_dir,
                dry_run=args.dry_run,
            )
        )
        raise ConfigError(
            "--contestant filter matched only DISABLED contestants "
            f"({', '.join(c.id for c in contestants)}); nothing would be "
            "executed — re-enable a contestant or drop the filter"
        )

    if args.max_cases is not None and len(pending_items) > args.max_cases:
        raise ConfigError(
            f"the plan requires {len(pending_items)} attempts which exceeds "
            f"--max-cases {args.max_cases}"
        )

    if args.dry_run:
        print(
            _plan_summary_text(
                run_id=run_id,
                suite_name=suite_name,
                contestants=contestants,
                cases=cases,
                repeat=args.repeat,
                pending_count=len(pending_items),
                skip_count=skip_count,
                driver=args.driver,
                concurrency=args.concurrency,
                base_url=args.base_url if args.driver == "http" else None,
                credential_map=credential_map,
                max_cases=args.max_cases,
                output_dir=output_dir,
                dry_run=True,
            )
        )
        return 0

    # DEF-043 — explicit confirmation before ANY filesystem mutation (neither
    # the output dir is created nor a previous run dir is deleted without a
    # confirmed plan). EOF on stdin defaults to "no" with a clear message.
    if not args.yes:
        if not sys.stdin.isatty():
            raise ConfigError(
                "non-interactive execution requires --yes (the benchmark makes "
                "real paid provider calls)"
            )
        try:
            answer = input("Proceed with the paid benchmark? [y/N]: ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in ("y", "yes"):
            print(
                "aborted (the benchmark was not confirmed; non-interactive "
                "execution requires --yes)"
            )
            return 0

    # DEF-028 — the --rerun reset only ever removes a directory that provably
    # belongs to a previous benchmark run of this tool (identity marker check),
    # and a refused/aborted run above never reaches this point.
    if args.rerun and output_dir.exists():
        if not _is_benchmark_run_dir(output_dir):
            raise ConfigError(
                f"--rerun refuses to remove {output_dir}: the directory is not "
                "a recognized benchmark-run directory (no metadata.json / "
                "state.json / results.jsonl identity marker of "
                "tools.frontier_benchmark); refusing to delete arbitrary "
                "directories"
            )
        shutil.rmtree(output_dir, ignore_errors=True)
        run_id = _new_run_id(time.time())

    # Real runs create the output dir (dry-runs never touch the filesystem).
    output_dir.mkdir(parents=True, exist_ok=True)

    # DEF-039 — from this point on, artifacts may exist on disk; arm the guard
    # so every exit path (including abort paths) still runs the secret scan.
    _CURRENT_RUN_GUARD = _ArtifactScanGuard(
        output_dir,
        lambda: [c.credential for c in contestants if c.enabled and c.credential],
    )

    started_at = time.time()
    started_iso = _dt.datetime.fromtimestamp(
        started_at, _dt.timezone.utc
    ).isoformat()
    print(
        f"[{run_id}] started {started_iso} driver={args.driver} "
        f"suite={suite_name} contestants={len(contestants)} cases={len(cases)} "
        f"repeat={args.repeat} pending={len(pending_items)} skipped(resume)={skip_count}"
    )

    # ---- drivers -----------------------------------------------------------
    driver_instance: HttpDriver | InProcessDriver | None = None
    deadline_ms: int | float | None = None
    provider_timeout: float | None = None
    core_budget: int | None = None
    global_budget: int | None = None
    max_repairs: int | None = None
    max_regens: int | None = None
    gen_deadline: int | None = None
    telemetry_event_list: list[dict[str, Any]] = []

    try:
        if args.driver == "inprocess":
            driver_instance = InProcessDriver(
                concurrency=args.concurrency,
                frontier_timeout_seconds=args.frontier_timeout_seconds,
                generation_deadline_seconds=args.generation_deadline_seconds,
            )
            settings = driver_instance.settings
            provider_timeout = float(settings.frontier_timeout_seconds)
            gen_deadline = int(settings.generation_deadline_seconds)
            core_budget = int(settings.max_core_llm_calls_per_generation or 0) or None
            global_budget = int(settings.max_llm_calls_per_generation or 0) or None
            max_repairs = int(settings.max_repair_passes)
            max_regens = int(settings.max_full_regenerations)
            deadline_ms = gen_deadline * 1000
            telemetry_event_list = driver_instance.live_events  # live sink
        else:
            driver_instance = HttpDriver(
                base_url=args.base_url,
                http_timeout_seconds=args.http_timeout_seconds,
                ca_bundle=args.ca_bundle,
                insecure_tls=args.insecure_tls,
            )
        if args.telemetry_logs:
            parsed_events, unparseable_telemetry = parse_telemetry_events_with_notes(
                Path(args.telemetry_logs)
            )
            telemetry_event_list = telemetry_event_list + parsed_events
        else:
            unparseable_telemetry = 0

        execution_counter = [0]
        execution_lock = threading.Lock()
        results_path = output_dir / "results.jsonl"

        # NOTE (DEF-024): the results file is NEVER unlinked on resume. A
        # missing state.json no longer means "fresh run": combos already
        # durable in the crash-tolerant results.jsonl are treated as completed
        # (deduped above), and pre-existing lines are merged into the final
        # records below. `--rerun` is the ONLY way to cleanly repeat the
        # workload (it removes the whole output dir up front).

        item_to_contestant = {c.id: c for c in contestants if c.enabled}
        case_map = {c.id: c for c in cases}

        raw_records: list[dict[str, Any]] = []

        def _execute(item: WorkItem) -> tuple[WorkItem, dict[str, Any]]:
            return _execute_item(
                item,
                run_id=run_id,
                started_at=started_at,
                item_to_contestant=item_to_contestant,
                case_map=case_map,
                driver_instance=driver_instance,
                execution_counter=execution_counter,
                execution_lock=execution_lock,
                deadline_ms=deadline_ms,
            )

        if args.concurrency <= 1:
            for item in pending_items:
                _item, record = _execute(item)
                write_results_jsonl(results_path, record)
                refresh_resume_state(output_dir, run_id, completed, record)
                raw_records.append(record)
        else:
            # DEF-029 — per-case durability under concurrency: instead of
            # collecting every future and persisting only after ALL futures
            # finish (an interruption then left ZERO durable lines), each
            # completed future's result line + resume-state refresh is written
            # AS IT COMPLETES, under a lock so the shared resume set and the
            # JSONL appends never race. An interrupted concurrent run therefore
            # still resumes without re-executing the combos that completed.
            executor = ThreadPoolExecutor(max_workers=args.concurrency)
            persist_lock = threading.Lock()
            try:
                futures = {
                    executor.submit(_execute, item): item for item in pending_items
                }
                for future in as_completed(futures):
                    try:
                        _item, record = future.result()
                    except KeyboardInterrupt:
                        raise
                    except Exception as exc:  # noqa: BLE001 - per-item isolation
                        item = futures[future]
                        contestant = item_to_contestant[item.contestant_id]
                        case = case_map[item.case_id]
                        with execution_lock:
                            execution_counter[0] += 1
                            order = execution_counter[0]
                        error_record = _empty_result(
                            run_id, contestant, case, item.repeat, order,
                            final_status=FINAL_STATUS_ERROR,
                            failure_code="BENCHMARK_DRIVER_ERROR",
                            error_detail=_sanitize_error(exc),
                        )
                        error_record["startedAt"] = started_at
                        error_record["completedAt"] = time.time()
                        _item, record = item, error_record
                    with persist_lock:
                        write_results_jsonl(results_path, record)
                        refresh_resume_state(output_dir, run_id, completed, record)
                        raw_records.append(record)
            except KeyboardInterrupt:
                executor.shutdown(wait=False, cancel_futures=True)
                raise
            finally:
                executor.shutdown(wait=True)
    finally:
        if driver_instance is not None and isinstance(driver_instance, InProcessDriver):
            driver_instance.close()

    # ---- collect raw records (merge pre-existing resume lines) -------------
    records = list(raw_records)
    existing_lines: list[dict[str, Any]] = []
    if results_path.is_file():
        for raw_line in results_path.read_text(encoding="utf-8").splitlines():
            if not raw_line.strip():
                continue
            try:
                existing_lines.append(json.loads(raw_line))
            except json.JSONDecodeError:
                continue
    # Dedupe by combo (contestantId, caseId, repeatIndex): resume history stays
    # under its combos, this run's fresh records win on any (impossible without
    # --rerun) collision. Combination identities never collide across runs.
    combo_seen: set[tuple[str, str, int]] = set()
    ordered: list[dict[str, Any]] = []
    for record in existing_lines + records:
        try:
            combo = (
                str(record["contestantId"]),
                str(record["benchmarkCaseId"]),
                int(record["repeatIndex"]),
            )
        except KeyError:
            continue
        if combo in combo_seen:
            continue
        combo_seen.add(combo)
        ordered.append(record)
    records = ordered

    # ---- enrich every record from telemetry (post-hoc, never fabricated) ---
    telemetry_by_attempt = index_events_by_attempt(telemetry_event_list)
    telemetry_notes: list[str] = []
    if unparseable_telemetry:
        telemetry_notes.append(
            f"{unparseable_telemetry} telemetry line(s) were malformed or "
            "carried non-finite numbers (NaN/Inf) and were skipped; the "
            "run continued and every artifact was still secret-scanned"
        )
    for record in records:
        attempt_id = record.get("generationAttemptId")
        if attempt_id:
            attempt_events = telemetry_by_attempt.get(attempt_id)
            if attempt_events:
                try:
                    enrich_result(record, attempt_events, deadline_ms=deadline_ms)
                except Exception as exc:  # noqa: BLE001 - DEF-033 guard
                    telemetry_notes.append(
                        f"telemetry enrichment failed for attempt "
                        f"{attempt_id!r} ({_sanitize_error(exc)}): record left "
                        "unenriched; the run continued and every artifact was "
                        "still secret-scanned"
                    )
        if (
            record.get("inputTokens") is None
            and record.get("providerReportedCost") is None
            and record.get("costSource") is None
        ):
            record["costSource"] = COST_SOURCE_UNAVAILABLE

    # Rewrite results.jsonl enriched (atomic) so the persisted artifacts match.
    if records:
        rewrite_results_jsonl(
            results_path, sorted(records, key=lambda r: r["executionOrder"])
        )

    # Phase31A §26 — per-attempt NON-SECRET compatibility artifacts. Written
    # ONLY for attempts whose telemetry actually exists (the compatibility/
    # dir stays absent otherwise — never fabricated); the resulting files are
    # covered by the post-run secret-safety scan below.
    write_compatibility_artifacts(output_dir, records, telemetry_by_attempt)

    finished_at = time.time()
    finished_iso = _dt.datetime.fromtimestamp(
        finished_at, _dt.timezone.utc
    ).isoformat()
    print(f"[{run_id}] finished {finished_iso} records={len(records)}")

    # ---- resume state (every genuinely executed combo) ---------------------
    # Already refreshed atomically after EACH completed case above; this final
    # write is idempotent and also folds in combos recovered from pre-existing
    # results.jsonl lines (crash-recovered / resumed runs).
    completed_now = set(completed)
    for record in records:
        if record.get("finalStatus") == FINAL_STATUS_SKIPPED_CREDENTIAL:
            continue
        try:
            completed_now.add(
                (
                    str(record["contestantId"]),
                    str(record["benchmarkCaseId"]),
                    int(record["repeatIndex"]),
                )
            )
        except KeyError:
            continue
    write_state(output_dir, run_id, completed_now)

    # ---- aggregation --------------------------------------------------------
    enabled_contestants = [c for c in contestants if c.enabled]
    per_contestant = [
        aggregate_per_contestant(contestant, records)
        for contestant in enabled_contestants
    ]

    luna_status, luna_reason = luna_report()
    monitoring_artifacts: dict[str, Any] | None = None
    if args.post_monitoring:
        try:
            monitoring_artifacts = run_phase29_monitoring(
                output_dir,
                window_seconds=max(finished_at - started_at, 1.0),
            )
            print("[phase29] monitoring artifacts written to the run directory")
        except MonitoringCaptureError as exc:
            print(f"[phase29] WARNING: monitoring step unavailable: {exc}")
            monitoring_artifacts = {
                "hours": None,
                "health_ok": False,
                "warnings": [str(exc)[:200]],
                "http_total": None,
                "http_page_api": None,
                "http_4xx": None,
                "http_5xx": None,
                "playthroughs_started": None,
                "cases_started": None,
                "cases_completed": None,
                "completion_rate": None,
                "health_app": None,
                "health_caddy": None,
            }

    # ---- best-effort estimated-cost guard (§21) ------------------------------
    if args.max_estimated_cost is not None:
        usable_cost = 0.0
        for record in records:
            value = record.get("calculatedCost")
            if value is None:
                value = record.get("providerReportedCost")
            if isinstance(value, (int, float)):
                usable_cost += float(value)
        if usable_cost > args.max_estimated_cost:
            raise BenchmarkError(
                f"--max-estimated-cost guard tripped: cumulative estimable "
                f"cost {usable_cost:.2f} > {args.max_estimated_cost} "
                "(best-effort guard; no further artifacts were persisted)"
            )

    # ---- metadata -----------------------------------------------------------
    runner_args = {key: value for key, value in vars(args).items()}
    runner_args["maxEstimatedCostLabeled"] = (
        "best-effort only; provider cost is currently unavailable so the guard "
        "cannot bind"
        if args.max_estimated_cost is not None
        else None
    )
    metadata = build_metadata(
        run_id=run_id,
        started_at=started_at,
        finished_at=finished_at,
        suite_name=suite_name,
        case_count=len(cases),
        repeat_count=args.repeat,
        contestants=contestants,
        runner_args=runner_args,
        driver=args.driver,
        seed=effective_seed,
        contestant_order=contestant_order,
        generation_deadline_seconds=gen_deadline,
        provider_timeout_seconds=provider_timeout,
        core_call_budget=core_budget,
        global_call_budget=global_budget,
        max_repair_passes=max_repairs,
        max_full_regenerations=max_regens,
        telemetry_notes=telemetry_notes,
    )
    write_metadata(output_dir, metadata)

    # ---- summary artifacts ---------------------------------------------------
    summary_doc = build_summary_document(
        run_id=run_id,
        generated_at=finished_at,
        per_contestant=per_contestant,
        luna_status=luna_status,
        luna_reason=luna_reason,
        monitoring=monitoring_artifacts,
    )
    write_summary_json(output_dir, summary_doc)
    write_summary_csv(output_dir, per_contestant)

    # ---- secret-safety scan over the artifacts -------------------------------
    used_secrets = [c.credential for c in contestants if c.enabled and c.credential]
    scan_hits = scan_files_for_secrets(output_dir, used_secrets)
    if scan_hits:
        raise SecretSafetyViolation(
            "credential material detected in benchmark artifacts: "
            + ", ".join(rel for rel, _marker in scan_hits)
        )

    # ---- report ---------------------------------------------------------------
    caveats = build_caveats(
        driver=args.driver,
        telemetry_present=bool(telemetry_by_attempt),
        telemetry_notes=telemetry_notes,
    )
    recommendation_notes = build_recommendations(per_contestant)
    report_text = build_report(
        metadata=metadata,
        per_contestant=per_contestant,
        suite=suite,
        cases=cases,
        secret_scan_hits=scan_hits,
        luna_status=luna_status,
        luna_reason=luna_reason,
        monitoring_artifacts=monitoring_artifacts,
        caveats=caveats,
        recommendation_notes=recommendation_notes,
    )
    (output_dir / "report.md").write_text(report_text, encoding="utf-8")

    # re-scan after the report (belt and braces)
    scan_hits_after = scan_files_for_secrets(output_dir, used_secrets)
    if scan_hits_after:
        raise SecretSafetyViolation(
            "credential material detected after report generation: "
            + ", ".join(rel for rel, _marker in scan_hits_after)
        )

    print(f"[{run_id}] artifacts written to {output_dir}")
    print(
        f"[{run_id}] secret-safety scan: CLEAN "
        f"({len(used_secrets)} credentials checked)"
    )
    return 0


def luna_report() -> tuple[str, str]:
    if LUNA_VERIFIED_OFFICIAL_MODEL_IDS:
        return "available", (
            "operator-pinned verified official OpenAI API model id(s): "
            + ", ".join(LUNA_VERIFIED_OFFICIAL_MODEL_IDS)
        )
    return LUNA_STATUS_BLOCKED, LUNA_BLOCKED_REASON


def build_caveats(
    *, driver: str, telemetry_present: bool, telemetry_notes: Sequence[str] = ()
) -> list[str]:
    caveats = [
        "provider load varies over time",
        "aggregator routing may vary",
        "model versions may change",
        "pricing may change",
        "small N limits confidence",
        "providers may rate-limit (HTTP 429 recorded truthfully)",
        "structured-output support is provider/model specific",
        "cost/token fields are null unless truthfully derivable",
    ]
    if not telemetry_present:
        caveats.append(
            "no structured telemetry was available for this run: "
            "providerCallCount/repairCount/regenerationCount/structuredOutput and "
            "the timeout-contract check stay null (they are never fabricated)"
        )
    if driver == "http":
        caveats.append(
            "http driver: totalElapsedMs is client-measured around the "
            "synchronous POST /cases request and includes transport overhead"
        )
        caveats.append(
            "the running backend's admission budgets (per-IP/per-session/global) "
            "apply; operators may need to raise them on the Dev-Box for large "
            "suites"
        )
    if driver == "inprocess":
        caveats.append(
            "inprocess driver: muted per-attempt anonymous quota sessions use the "
            "harness's own generous admission settings; generation correctness "
            "rules, validators, budgets, deadlines and publication are the "
            "production pipeline unchanged"
        )
    caveats.extend(str(note) for note in telemetry_notes)
    return caveats


def build_recommendations(per_contestant: Sequence[dict[str, Any]]) -> list[str]:
    notes: list[str] = []
    best_rate: tuple[float | None, str | None] = (None, None)
    best_latency: tuple[float | None, str | None] = (None, None)
    for row in per_contestant:
        if row["attempts"] == 0:
            continue
        rate = row["publishedRate"]
        if rate is not None and (best_rate[0] is None or rate > best_rate[0]):
            best_rate = (rate, row["contestantId"])
        lat = (row.get("latency") or {}).get("median")
        if lat is not None and (best_latency[0] is None or lat < best_latency[0]):
            best_latency = (lat, row["contestantId"])
    if best_rate[1]:
        notes.append(
            f"Best published rate: {best_rate[1]} ({_fmt_rate(best_rate[0])}) "
            "based on the PUBLISHED success rate above."
        )
    if best_latency[1]:
        notes.append(
            f"Best median latency: {best_latency[1]} ({best_latency[0]} ms) "
            "based on the LATENCY RESULTS above."
        )
    return notes


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="frontier_benchmark",
        description=(
            "Phase 31 secret-safe Frontier/BYOK benchmark runner. Reuses the "
            "production-equivalent Procedural Detective generation pipeline "
            "(never a benchmark-only correctness path). Credentials load ONLY "
            "from the process environment or the git-ignored .env.benchmark "
            "file. The trusted provider endpoint always comes from the "
            "server-owned frontier registry. --dry-run prints a non-secret "
            "plan and contacts NO provider."
        ),
        epilog=(
            "Dev-Box real-run flow (local stack, no production access):\n"
            "\n"
            "  cd ~/procedural-detective\n"
            "  cp .env.benchmark.example .env.benchmark    # fill only needed keys\n"
            "  # Dev-Box Caddy 'tls internal' uses its OWN CA; export + trust\n"
            "  # it once (the file is git-ignored, never commit it):\n"
            "  docker compose -f docker-compose.prod.yml -f docker-compose.lan.yml \\\n"
            "      cp caddy:/data/caddy/pki/authorities/local/root.crt ./caddy-local-root.crt\n"
            "  python -m tools.frontier_benchmark --suite smoke \\\n"
            "      --contestants benchmarks/frontier/contestants.toml \\\n"
            "      --base-url https://enshrouded-server --ca-bundle caddy-local-root.crt \\\n"
            "      --post-monitoring --yes\n"
            "\n"
            "Standard run:\n"
            "  python -m tools.frontier_benchmark --suite standard \\\n"
            "      --contestants benchmarks/frontier/contestants.toml \\\n"
            "      --base-url https://enshrouded-server --ca-bundle caddy-local-root.crt \\\n"
            "      --post-monitoring --yes\n"
            "\n"
            "Resume safety: state.json is persisted atomically after EACH\n"
            "completed case; an interrupted paid run resumes without\n"
            "re-executing completed combos, and a missing state.json falls back\n"
            "to deduplicating the combos already in results.jsonl (never a\n"
            "surprise re-purchase). Use --rerun to explicitly repeat everything.\n"
            "\n"
            "Post-monitoring reduces runtime caddy logs (`docker compose -f\n"
            "docker-compose.prod.yml -f docker-compose.lan.yml logs caddy`) and a\n"
            "DB snapshot (`docker compose -f docker-compose.prod.yml -f\n"
            "docker-compose.lan.yml cp procedural-detective:/data/procedural_detective.db\n"
            "<tmp>/pd.db`) into a TEMP dir, runs tools.monitoring_report\n"
            "(--summary/--json/--health with --hours fitted to the run window,\n"
            "min 1h), writes phase29-monitoring-summary.txt / -monitoring.json /\n"
            "-health.txt into the run dir, then DELETES the temp raw logs/DB.\n"
            "HTTP request count is NOT equivalent to visitors or players.\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--suite",
        default="smoke",
        help="suite name from the suites file (default: smoke)",
    )
    parser.add_argument(
        "--contestants",
        dest="contestants_path",
        default=str(DEFAULT_CONTESTANTS_PATH),
        help=f"path to contestants TOML (default: {DEFAULT_CONTESTANTS_PATH})",
    )
    parser.add_argument(
        "--suites",
        dest="suites_path",
        default=str(DEFAULT_SUITES_PATH),
        help=f"path to suites TOML (default: {DEFAULT_SUITES_PATH})",
    )
    parser.add_argument(
        "--corpus-dir",
        default=str(DEFAULT_CORPUS_DIR),
        help=f"directory of corpus *.json cases (default: {DEFAULT_CORPUS_DIR})",
    )
    parser.add_argument(
        "--case-ids",
        default="",
        help="comma-separated subset of suite case ids to run",
    )
    parser.add_argument(
        "--difficulty",
        choices=DIFFICULTIES,
        default=None,
        help="restrict the workload to one difficulty",
    )
    parser.add_argument(
        "--contestant",
        action="append",
        default=[],
        help="restrict to one contestant id (repeatable)",
    )
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        help=f"repeat each case per contestant ({MIN_REPEAT}..{MAX_REPEAT}, default 1)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="random seed for contestant execution order",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=None,
        help="hard cap on the number of paid case attempts in this run",
    )
    parser.add_argument(
        "--max-estimated-cost",
        type=float,
        default=None,
        help="best-effort estimated-cost guard (NOT a hard ceiling; cost is "
             "unavailable today, so this is labeled and non-binding)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"parallel attempts (default {DEFAULT_CONCURRENCY}; hard max {MAX_CONCURRENCY})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print a non-secret plan and exit without contacting any provider",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="non-interactive execution (do not prompt before paid calls)",
    )
    parser.add_argument(
        "--rerun",
        action="store_true",
        help="EXPLICIT 'repeat everything' override: remove the output dir and "
             "restart the workload from scratch (repeat paid work). Without it, "
             "resume skips combos already completed in state.json / "
             "results.jsonl (never a surprise re-purchase).",
    )
    parser.add_argument(
        "--fixed-order",
        action="store_true",
        help="do not randomize contestant execution order",
    )
    parser.add_argument(
        "--driver",
        choices=("http", "inprocess"),
        default="http",
        help="http: real backend (Dev-Box); inprocess: real GenerationService locally",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"http driver base URL (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--http-timeout-seconds",
        type=float,
        default=DEFAULT_HTTP_TIMEOUT_SECONDS,
        help="http driver request timeout (default %g)" % DEFAULT_HTTP_TIMEOUT_SECONDS,
    )
    parser.add_argument(
        "--ca-bundle",
        default=None,
        metavar="PATH",
        help="http driver: PEM CA bundle to TRUST when the Dev-Box edge uses "
             "its own CA (Caddy 'tls internal'; e.g. caddy-local-root.crt "
             "exported from the caddy container). Hostname verification stays "
             "ON. Default (no flag) = system trust store.",
    )
    parser.add_argument(
        "--insecure-tls",
        action="store_true",
        help="UNSUPPORTED escape hatch: disable TLS verification entirely for "
             "the http driver (Dev-Box/local debugging only). Prefer "
             "--ca-bundle; never use against non-local stacks.",
    )
    parser.add_argument(
        "--telemetry-logs",
        default=None,
        help="JSONL of the app's own observability events to correlate per attempt",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="output directory (default: benchmark-results/<run-id>)",
    )
    parser.add_argument(
        "--frontier-timeout-seconds",
        type=float,
        default=None,
        help="inprocess driver: pin FRONTIER_TIMEOUT_SECONDS (else production "
             "resolution)",
    )
    parser.add_argument(
        "--generation-deadline-seconds",
        type=int,
        default=None,
        help="inprocess driver: pin CASE_GENERATION_DEADLINE_SECONDS (else "
             "production resolution)",
    )
    parser.add_argument(
        "--luna-enabled",
        action="store_true",
        help="no-op unless a verified official OpenAI API Luna model id exists "
             "(default off; see Luna BLOCKED-EXTERNAL)",
    )
    parser.add_argument(
        "--post-monitoring",
        action="store_true",
        help="run the Phase 29 Dev-Box monitoring evaluation after the benchmark "
             "(writes phase29-* artifacts; temp raw logs/DB deleted)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="accepted for compatibility (the tool only ever prints safe content)",
    )
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if args.repeat < MIN_REPEAT or args.repeat > MAX_REPEAT:
        raise ConfigError(f"--repeat must be between {MIN_REPEAT} and {MAX_REPEAT}")
    if args.concurrency < 1 or args.concurrency > MAX_CONCURRENCY:
        raise ConfigError(
            f"--concurrency must be between 1 and {MAX_CONCURRENCY}"
        )
    if args.max_cases is not None and args.max_cases < 1:
        raise ConfigError("--max-cases must be >= 1")
    if args.max_estimated_cost is not None:
        if not math.isfinite(args.max_estimated_cost):
            raise ConfigError("--max-estimated-cost must be a finite number")
        if args.max_estimated_cost <= 0:
            raise ConfigError("--max-estimated-cost must be > 0")
    if not math.isfinite(args.http_timeout_seconds) or args.http_timeout_seconds <= 0:
        raise ConfigError("--http-timeout-seconds must be finite and > 0")
    # DEF-030 — validate + sanitize the base URL once (downstream plan,
    # metadata and driver all use the sanitized scheme://host[:port] form).
    args.base_url = sanitize_base_url(args.base_url)
    if args.ca_bundle and args.insecure_tls:
        raise ConfigError("--ca-bundle and --insecure-tls are mutually exclusive")
    if args.ca_bundle:
        ca_path = Path(args.ca_bundle).expanduser()
        if not ca_path.is_file():
            raise ConfigError(f"--ca-bundle file not found: {args.ca_bundle}")
    if args.insecure_tls:
        print(
            "warning: --insecure-tls disables TLS certificate verification "
            "for the http driver (unsupported escape hatch; prefer --ca-bundle)",
            file=sys.stderr,
        )
    if args.frontier_timeout_seconds is not None and (
        not math.isfinite(args.frontier_timeout_seconds)
        or args.frontier_timeout_seconds < 5
        or args.frontier_timeout_seconds > 300
    ):
        raise ConfigError(
            "--frontier-timeout-seconds must be a finite value within the "
            "production bounds 5..300"
        )
    if args.generation_deadline_seconds is not None and (
        args.generation_deadline_seconds <= 0
    ):
        raise ConfigError("--generation-deadline-seconds must be > 0")
    if args.luna_enabled and not LUNA_VERIFIED_OFFICIAL_MODEL_IDS:
        print(
            "warning: --luna-enabled has no effect: no verified official OpenAI "
            "API Luna model id exists (Luna baseline = BLOCKED-EXTERNAL)",
            file=sys.stderr,
        )


def main(argv: Sequence[str] | None = None) -> int:
    global _CURRENT_RUN_GUARD
    exit_code = 0
    secret_guard_is_current_error = False
    try:
        parser = build_parser()
        args = parser.parse_args(argv)
        validate_args(args)
        exit_code = run_benchmark(args)
    except KeyboardInterrupt:
        print("aborted", file=sys.stderr)
        exit_code = 130
    except (ConfigError, SecretSafetyViolation, BenchmarkError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        exit_code = 2
        secret_guard_is_current_error = isinstance(exc, SecretSafetyViolation)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - sanitized fallback
        print(f"error: {_sanitize_error(exc)}", file=sys.stderr)
        exit_code = 2
    finally:
        # DEF-039 — the artifact secret scan runs on EVERY exit path the
        # moment a real run was armed (never skippable by an abort path); a
        # hit overrides the exit code with the infrastructure-failure code 2.
        guard = _CURRENT_RUN_GUARD
        _CURRENT_RUN_GUARD = None
        if guard is not None and not secret_guard_is_current_error:
            hits = guard.scan()
            if hits:
                print(
                    "error: credential material detected in benchmark artifacts: "
                    + ", ".join(rel for rel, _marker in hits),
                    file=sys.stderr,
                )
                exit_code = 2
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())