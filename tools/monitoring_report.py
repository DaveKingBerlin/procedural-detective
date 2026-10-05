#!/usr/bin/env python3
"""Phase 29 — Monitoring & Usage Analytics CLI (MON-04/05/08/10/11).

A thin, production-grade observability layer for Procedural Detective that
reads ONLY existing data sources — it adds no database, no new public port,
no long-running container and no SaaS:

  1. HTTP metrics (MON-04/05): parses the Caddy 2 ``format json`` access-log
     lines that the edge emits on stdout (``docker compose ... logs caddy``
     or a captured file), and computes request totals, methods, top paths,
     status classes, 404/5xx counts, transferred bytes, an hourly time series
     and a Page/API/Static/Health/Other categorization based on the ACTUAL
     repository routes.
  2. Product metrics (MON-08): reads the EXISTING SQLite schema (cases,
     case_versions, playthroughs, published_versions,
     anonymous_quota_sessions, generation_attempts) with read-only stdlib
     sqlite3 and reports playthroughs started, cases started, cases
     completed (= CaseVersion state terminal PUBLISHED, i.e. a row in the
     insert-only ``published_versions`` table), completion rate, usage
     timestamps and per-day usage. Session DURATION is deliberately NOT
     computed — the schema records ``created_at`` and ``expires_at`` (a fixed
     token validity window), never when a session actually ended, so any
     derived duration would be fabricated (documented, MON-08 acceptance).
  3. Health summary (MON-11): ``--health`` surfaces container state through
     ``docker compose ps`` (safe, read-only, host-side); it never opens ports,
     never mounts the Docker socket and never adds an external monitor. 5xx
     spikes are flagged from the access-log aggregation itself.
  4. Compact status block (MON-10) printing the "Last 24 hours" summary.

Data minimization (MON-03/MON-09): the tool keeps ONLY in-memory aggregates.
It strips query strings before the top-path report (query parameters with
potentially sensitive content are never persisted or printed), never logs
request bodies / Authorization headers / cookies / API keys, and prints no IP
addresses. Long-term storage is bounded exclusively by the Docker json-file
rotation (10 MB x 5). No persistent user identifiers exist; there is no
web dashboard, so MON-07 is satisfied by construction (nothing is exposed).

Hostile/garbled input NEVER crashes the report (P29-01): non-finite or absurd
timestamps, implausible status/size values and unparseable/too-deep JSON are
each counted as a skipped (malformed) line and processing continues — the
counter is surfaced in every output mode ("N lines skipped (malformed)"). Only
the DATA SOURCES themselves (missing log file / missing database) exit 1.

Usage (operator, on the server):

    # 1) Access logs via Docker (bounded json-file rotation):
    docker compose -f docker-compose.prod.yml logs caddy | \\
        python -m tools.monitoring_report --logs - --db <snapshot.db>

    # 2) Or from a captured log file; DB snapshot via docker compose cp:
    docker compose -f docker-compose.prod.yml cp \\
        procedural-detective:/data/procedural_detective.db ./pd-snapshot.db
    python -m tools.monitoring_report --logs ./caddy.log --db ./pd-snapshot.db

    # 3) JSON output for scripting:
    python -m tools.monitoring_report --logs ./caddy.log --db ./pd-snapshot.db --json

Exit codes: 0 report produced; 1 data-source error; 2 usage error.

The tool never requires root. It performs only SELECTs and no network calls
(``--health`` is the only subprocess path and is strictly a local
``docker compose ps``; it is never exercised by the hermetic test suite).
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import math
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_PROD_COMPOSE = "docker-compose.prod.yml"

# --------------------------------------------------------------------------- #
# hostile-input bounds (P29-01) — the report must NEVER crash on a garbage line
# --------------------------------------------------------------------------- #

# Epoch-seconds sanity bound for Caddy `ts`: |ts| > 1e12 (~year 33658 CE /
# 969 BCE) is absurd for any real deployment log and would blow up datetime
# formatting.
_MAX_TS_ABS = 1e12
# Per-request `size` cap: 1 GiB. The app's own body limit is 70 KB, so any
# larger value is implausible; it is excluded from the byte accounting.
_MAX_REQUEST_BYTES = 1024 * 1024 * 1024
# Echo truncation for a (hostile) long request path: never print/store a
# 128 KB URI in the top-path report.
_MAX_PATH_ECHO = 200
_PATH_ELLIPSIS = "..."
# The parse/aggregate hazards a malformed/hostile capture may raise
# (RecursionError is caught explicitly — the malicious-nesting JSON path).
_HAZARD_EXCEPTIONS = (ValueError, TypeError, OverflowError, RecursionError)

# --------------------------------------------------------------------------- #
# request categorization — anchored to the ACTUAL repository routes (MON-05)
# --------------------------------------------------------------------------- #

CATEGORY_PAGE = "Page"
CATEGORY_API = "API"
CATEGORY_STATIC = "Static"
CATEGORY_HEALTH = "Health"
CATEGORY_OTHER = "Other"
_CATEGORY_ORDER = (
    CATEGORY_PAGE,
    CATEGORY_API,
    CATEGORY_STATIC,
    CATEGORY_HEALTH,
    CATEGORY_OTHER,
)

# The public SPA routes, read from frontend/src/main.tsx:
#   <Route index         element={<Home/>} />          -> "/"
#   path="new"           -> "/new"
#   path="generating"    -> "/generating"
#   path="scene"         -> "/scene"
#   path="accuse"        -> "/accuse"
#   path="reveal"        -> "/reveal"
# A test pins this set against the source file so it can never drift.
PAGE_PATHS: frozenset[str] = frozenset(
    {"/", "/new", "/generating", "/scene", "/accuse", "/reveal"}
)

# The infra probes (backend/app/api/v1/health.py). The docker HEALTHCHECK hits
# /api/v1/health every 30s; /api/v1/readiness is the operator probe. Neither
# may ever count as player activity (MON-05).
HEALTH_PATHS: frozenset[str] = frozenset({"/api/v1/health", "/api/v1/readiness"})

# Vite emits content-hashed bundles under /assets; /static is the documented
# production static mount (STATIC_DIR). Static assets are never "visitors".
STATIC_PREFIXES: tuple[str, ...] = ("/assets/", "/static/")

# Every other /api/v1 path is a real API route (backend/app/api/v1/*).
API_PREFIX = "/api/v1/"

# The documented warning (MON-04): one page view fans out to many requests.
_REQUESTS_VS_USERS_NOTE = (
    "NOTE: HTTP requests are NOT equivalent to visitors or players. One page "
    "load produces several requests (HTML, JS, CSS, assets, API). Health and "
    "static-asset requests are deliberately excluded from player-activity "
    "numbers below."
)


def categorize(path: str) -> str:
    """Bucket one query-stripped request path into the MON-05 categories."""
    if path in HEALTH_PATHS:
        return CATEGORY_HEALTH
    if path in PAGE_PATHS:
        return CATEGORY_PAGE
    if path.startswith(API_PREFIX):
        return CATEGORY_API
    for prefix in STATIC_PREFIXES:
        if path.startswith(prefix):
            return CATEGORY_STATIC
    return CATEGORY_OTHER


# --------------------------------------------------------------------------- #
# Caddy 2 `format json` access-log parsing (MON-04)
# --------------------------------------------------------------------------- #
#
# Field names verified against the Caddy 2 documentation ("How Logging Works",
# JSON-encoded standard log format): the access-log line is a single JSON
# object with top-level {"level","ts","logger","msg","request",...} where
#   request = {remote_ip, remote_port, client_ip, proto, method, host, uri,
#              headers, tls}
# and the response fields are top-level {bytes_read, user_id, duration, size,
# status, resp_headers}. `ts` is an epoch-seconds float; `uri` includes the
# query string. Older nginx-style names (request_method / remote_addr / ...)
# are NOT part of Caddy's `format json` and are not consumed.
#
# The lines may arrive wrapped by the Docker json-file driver
# ({"log": "<json>", "stream": "stdout", "time": "..."}) and/or prefixed by
# `docker compose logs` ("caddy-1  | ..."), so the parser unwraps both before
# decoding.

_COMPOSE_PREFIX_RE = re.compile(r"^[A-Za-z0-9_.-]+\s+\|\s*")


@dataclass(frozen=True)
class CaddyLogEntry:
    """One decoded Caddy access-log line (aggregated in memory only)."""

    ts: float  # epoch seconds (UTC) — `ts` field of the log line
    method: str
    uri: str  # full request URI including the query string
    path: str  # uri without query string, used for categorization / top paths
    status: int
    size: int | None  # response size (bytes), when present


def _extract_json_object(raw_line: str) -> dict | None:
    """Decode one raw log line into its Caddy log object (or None).

    Handles plain Caddy JSON, a Docker json-file envelope and a leading
    ``docker compose logs`` "service  |" prefix. Never raises: unparseable,
    wrong-type and pathologically-nested (``RecursionError``) lines return
    None and are counted as skipped by the caller (P29-01).
    """
    line = _COMPOSE_PREFIX_RE.sub("", raw_line).strip()
    try:
        obj = json.loads(line)
    except (ValueError, TypeError, RecursionError):
        return None
    if not isinstance(obj, dict):
        return None
    if "log" in obj and isinstance(obj.get("log"), str):
        # Docker json-file wrapper: the embedded string is the real line
        # (may itself carry a compose prefix in multi-line captures).
        inner = _COMPOSE_PREFIX_RE.sub("", obj["log"].strip())
        try:
            inner_obj = json.loads(inner)
        except (ValueError, TypeError, RecursionError):
            return None
        return inner_obj if isinstance(inner_obj, dict) else None
    return obj


def entry_from_object(obj: dict) -> CaddyLogEntry | None:
    """Build a CaddyLogEntry from a decoded object; None for non-access lines.

    P29-01 plausibility clamps: a non-finite/absurd ``ts`` (|ts| > 1e12),
    a ``status`` outside [100, 599] and an absent request line all skip the
    line; a ``size`` outside [0, 1 GiB] is dropped from the byte accounting;
    hostile long paths are truncated for the echo report. Never raises.
    """
    request = obj.get("request")
    if not isinstance(request, dict):
        return None
    uri = request.get("uri")
    method = request.get("method")
    status = obj.get("status")
    ts = obj.get("ts")
    if not isinstance(uri, str) or not isinstance(method, str):
        return None
    if status is None or ts is None:
        return None
    try:
        status_int = int(status)
        ts_float = float(ts)
    except (TypeError, ValueError, OverflowError):
        return None
    # Timestamp sanity: NaN/Inf/-Inf and absurd epochs would crash the UTC
    # formatting below (ValueError/OverflowError/OSError) — skip the line.
    if not math.isfinite(ts_float) or abs(ts_float) > _MAX_TS_ABS:
        return None
    # Status plausibility: an HTTP status outside [100, 599] is garbled.
    if not 100 <= status_int <= 599:
        return None
    size_raw = obj.get("size")
    size: int | None = None
    if isinstance(size_raw, (int, float)) and not isinstance(size_raw, bool):
        try:
            size_int = int(size_raw)
        except (ValueError, OverflowError):
            size_int = -1  # absurd size literal -> dropped below
        if 0 <= size_int <= _MAX_REQUEST_BYTES:
            size = size_int
        # else: negative or > 1 GiB per request -> not determinable, skip it.
    path = uri.split("?", 1)[0]
    if len(path) > _MAX_PATH_ECHO:
        path = path[:_MAX_PATH_ECHO] + _PATH_ELLIPSIS
    return CaddyLogEntry(
        ts=ts_float,
        method=method,
        uri=uri,
        path=path,
        status=status_int,
        size=size,
    )


def parse_caddy_log_line(raw_line: str) -> CaddyLogEntry | None:
    """Parse one raw access-log line (unwrapping Docker/compose layers).

    Never raises (P29-01): hostile JSON nesting, non-finite timestamps and
    implausible status/size values return None so the caller can count the
    line as skipped (malformed) instead of crashing.
    """
    obj = _extract_json_object(raw_line)
    if obj is None:
        return None
    return entry_from_object(obj)


# --------------------------------------------------------------------------- #
# HTTP aggregation (MON-04/05/10)
# --------------------------------------------------------------------------- #


@dataclass
class HttpStats:
    total: int = 0
    methods: Counter[str] = field(default_factory=Counter)
    top_paths: list[tuple[str, int]] = field(default_factory=list)
    status_classes: Counter[str] = field(default_factory=Counter)
    count_404: int = 0
    count_5xx: int = 0
    bytes_transferred: int = 0
    per_day: dict[str, int] = field(default_factory=dict)
    hourly_series: list[tuple[int, int]] = field(default_factory=list)
    requests_per_hour: float = 0.0
    categories: Counter[str] = field(default_factory=Counter)
    page_api_requests: int = 0
    first_ts: float | None = None
    last_ts: float | None = None
    skipped_lines: int = 0


def _status_class(status: int) -> str:
    if 100 <= status <= 599:
        return f"{status // 100}xx"
    return "other"


def aggregate(entries: list[CaddyLogEntry], window_hours: float = 24.0) -> HttpStats:
    """Compute every MON-04 aggregate from decoded log entries (deterministic).

    Health and static requests are tallied in their categories but are NOT
    part of ``page_api_requests`` (the player-activity figure, MON-05).

    P29-01 hardening: entries carrying a non-finite/absurd timestamp are
    skipped (counted in ``stats.skipped_lines``) instead of crashing the UTC
    formatting, and requests/hour floors a sub-second observed span at 1 s so
    two entries 1 us apart cannot fabricate ~7.5e9 req/h. Never raises.
    """
    stats = HttpStats()
    valid: list[CaddyLogEntry] = []
    for entry in entries:
        if not isinstance(entry, CaddyLogEntry) or not (
            math.isfinite(entry.ts) and abs(entry.ts) <= _MAX_TS_ABS
        ):
            stats.skipped_lines += 1
            continue
        valid.append(entry)
    stats.total = len(valid)
    sorted_entries = sorted(valid, key=lambda e: e.ts)

    for entry in sorted_entries:
        if stats.first_ts is None or entry.ts < stats.first_ts:
            stats.first_ts = entry.ts
        if stats.last_ts is None or entry.ts > stats.last_ts:
            stats.last_ts = entry.ts
        stats.methods[entry.method] += 1
        stats.status_classes[_status_class(entry.status)] += 1
        if entry.status == 404:
            stats.count_404 += 1
        if entry.status >= 500:
            stats.count_5xx += 1
        if entry.size is not None and entry.size >= 0:
            stats.bytes_transferred += entry.size
        day = _utc_format(entry.ts, "%Y-%m-%d")
        stats.per_day[day] = stats.per_day.get(day, 0) + 1
        hour = int(entry.ts // 3600) * 3600
        stats.hourly_series.append((hour, 1))
        category = categorize(entry.path)
        stats.categories[category] += 1
        if category in (CATEGORY_PAGE, CATEGORY_API):
            stats.page_api_requests += 1

    # Deduplicate hourly buckets (deterministic ascending order).
    hour_counts: Counter[int] = Counter()
    for hour, _count in stats.hourly_series:
        hour_counts[hour] += 1
    stats.hourly_series = sorted(hour_counts.items())

    # Top paths (query strings stripped; never persisted).
    path_counts: Counter[str] = Counter()
    for entry in sorted_entries:
        path_counts[entry.path] += 1
    stats.top_paths = sorted(
        path_counts.items(), key=lambda item: (-item[1], item[0])
    )[:10]

    # Average requests/hour over the OBSERVED span (fallback: window_hours).
    # P29-01: a hostile 2-line capture 1 us apart must not fabricate a
    # ~7.5e9 req/h figure — floor a tiny-but-positive span at 1 s. A zero
    # span (identical timestamps / single entry) keeps the window fallback.
    span_seconds = 0.0
    if stats.first_ts is not None and stats.last_ts is not None:
        span_seconds = stats.last_ts - stats.first_ts
    if span_seconds <= 0:
        elapsed_hours = float(window_hours)
    else:
        elapsed_hours = max(span_seconds, 1.0) / 3600.0
    stats.requests_per_hour = stats.total / elapsed_hours if stats.total else 0.0

    # Deterministic category ordering.
    ordered: Counter[str] = Counter()
    for name in _CATEGORY_ORDER:
        ordered[name] = stats.categories[name]
    stats.categories = ordered
    return stats


# --------------------------------------------------------------------------- #
# Product metrics from the EXISTING SQLite schema (MON-08)
# --------------------------------------------------------------------------- #
#
# Schema semantics (read from backend/app/models/*.py — no invented meanings):
#   cases                 — the stable logical case; one row per started
#                           generation request (created_at)
#   case_versions         — attempts of the §7.3 generation state machine
#                           (state DRAFT/GENERATING/VALIDATING/REPAIRING/
#                           PUBLISHED/FAILED; PUBLISHED is terminal)
#   published_versions    — INSERT-ONLY frozen payload rows; the exact
#                           database-level proof that a CaseVersion reached
#                           terminal PUBLISHED ("case completed")
#   playthroughs          — one player session pinned to a published version
#                           (created_at, expires_at = fixed token TTL)
#   anonymous_quota_sessions — the anonymous quota identity (created_at)
#   generation_attempts   — durable {status, stage, progress} snapshot
# This tool issues COUNT(*) / MIN / MAX SELECTs only, in read-only mode when
# the filesystem allows it, and keeps the numbers in memory.

_REQUIRED_PRODUCT_TABLES = (
    "cases",
    "case_versions",
    "playthroughs",
    "published_versions",
)

_PRODUCT_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "cases": ("case_id", "quota_session_id", "created_at"),
    "case_versions": ("case_id", "version", "state", "generation_id", "created_at"),
    "playthroughs": ("playthrough_id", "case_id", "case_version", "state",
                     "created_at", "expires_at"),
    "published_versions": ("case_id", "case_version", "payload_json", "published_at"),
}


@dataclass
class ProductStats:
    hours: float
    playthroughs_started: int = 0
    cases_started: int = 0
    case_versions_started: int = 0
    published_versions: int = 0
    cases_completed: int = 0
    completion_rate: float | None = None
    generation_failed_attempts: int = 0
    anonymous_quota_sessions: int = 0
    first_usage_ts: float | None = None
    last_usage_ts: float | None = None
    playthroughs_per_day: dict[str, int] = field(default_factory=dict)


def sqlite_url_to_path(value: str) -> Path:
    """Convert a ``sqlite:///`` URL (or plain path) into a filesystem Path.

    SQLAlchemy semantics: ``sqlite:///relative.db`` (3 slashes) is a path
    relative to the working directory; ``sqlite:////data/x.db`` (4 slashes) is
    an absolute path. After stripping the ``sqlite://`` prefix, exactly one
    leading ``/`` encodes the path start and is removed.
    """
    if value.startswith("sqlite://"):
        stripped = value[len("sqlite://"):]
        if not stripped:
            raise ValueError("monitoring report requires a file-backed SQLite database")
        if stripped.startswith("/"):
            stripped = stripped[1:]
        if not stripped or stripped == ":memory:":
            raise ValueError("monitoring report requires a file-backed SQLite database")
        return Path(stripped)
    return Path(value)


def _open_database(path: Path) -> sqlite3.Connection:
    """Open the SQLite database read-only when possible (SELECTs only)."""
    if not path.is_file():
        raise FileNotFoundError(f"database file not found: {path}")
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    except sqlite3.OperationalError:
        # Read-only is not always possible (e.g. WAL without -shm/-wal, some
        # network filesystems). Fall back to a normal connection — the tool
        # still issues SELECTs only and never writes.
        conn = sqlite3.connect(str(path), timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _require_schema(conn: sqlite3.Connection, path: Path) -> None:
    present = {
        row["name"]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    missing = [t for t in _REQUIRED_PRODUCT_TABLES if t not in present]
    if missing:
        raise ValueError(
            f"{path} is not a Procedural Detective database (missing tables: "
            + ", ".join(missing) + ")"
        )


def query_product_metrics(
    database: Path, now_ts: float, hours: float = 24.0
) -> ProductStats:
    """Compute MON-08 aggregates from the existing schema for the last ``hours``.

    Deterministic, read-only, stdlib sqlite3. Raises FileNotFoundError /
    ValueError with a clean operator-facing message on unusable inputs.
    """
    start_ts = now_ts - float(hours) * 3600.0
    conn = _open_database(database)
    try:
        _require_schema(conn, database)
        stats = ProductStats(hours=float(hours))
        stats.playthroughs_started = int(
            conn.execute(
                "SELECT COUNT(*) FROM playthroughs WHERE created_at >= ?",
                (start_ts,),
            ).fetchone()[0]
        )
        stats.cases_started = int(
            conn.execute(
                "SELECT COUNT(*) FROM cases WHERE created_at >= ?", (start_ts,)
            ).fetchone()[0]
        )
        stats.case_versions_started = int(
            conn.execute(
                "SELECT COUNT(*) FROM case_versions WHERE created_at >= ?",
                (start_ts,),
            ).fetchone()[0]
        )
        stats.published_versions = int(
            conn.execute(
                "SELECT COUNT(*) FROM published_versions WHERE published_at >= ?",
                (start_ts,),
            ).fetchone()[0]
        )
        stats.cases_completed = int(
            conn.execute(
                "SELECT COUNT(DISTINCT case_id) FROM published_versions "
                "WHERE published_at >= ?",
                (start_ts,),
            ).fetchone()[0]
        )
        stats.generation_failed_attempts = int(
            conn.execute(
                "SELECT COUNT(*) FROM generation_attempts "
                "WHERE UPPER(status)='FAILED' AND created_at >= ?",
                (start_ts,),
            ).fetchone()[0]
        )
        stats.anonymous_quota_sessions = int(
            conn.execute(
                "SELECT COUNT(*) FROM anonymous_quota_sessions "
                "WHERE created_at >= ?",
                (start_ts,),
            ).fetchone()[0]
        )
        usage_rows = conn.execute(
            "SELECT MIN(created_at) AS lo, MAX(created_at) AS hi FROM ("
            "  SELECT created_at FROM playthroughs WHERE created_at >= ?"
            "  UNION ALL"
            "  SELECT created_at FROM cases WHERE created_at >= ?"
            ")",
            (start_ts, start_ts),
        ).fetchone()
        if usage_rows is not None:
            stats.first_usage_ts = (
                float(usage_rows["lo"]) if usage_rows["lo"] is not None else None
            )
            stats.last_usage_ts = (
                float(usage_rows["hi"]) if usage_rows["hi"] is not None else None
            )
        per_day = conn.execute(
            "SELECT created_at FROM playthroughs WHERE created_at >= ?",
            (start_ts,),
        ).fetchall()
        for row in per_day:
            day = _utc_format(float(row[0]), "%Y-%m-%d")
            stats.playthroughs_per_day[day] = stats.playthroughs_per_day.get(day, 0) + 1
        stats.playthroughs_per_day = dict(sorted(stats.playthroughs_per_day.items()))
        if stats.cases_started > 0:
            stats.completion_rate = stats.cases_completed / stats.cases_started
        return stats
    finally:
        conn.close()


def product_session_duration_note() -> str:
    """MON-08 acceptance: why a reliable session DURATION is not determinable.

    A playthrough row records ``created_at`` (session start) and
    ``expires_at`` (the FIXED token-validity TTL, not a measured end). The
    state machine stores no ``ended_at`` / ``completed_at``: players idle,
    leave mid-session and may reveal at any point before expiry, and
    ``{CREATED, PLAYING} -> ACCUSED -> REVEALED`` transitions carry no
    session-end timestamp. Any duration derived from the schema would be a
    fabricated number, so MON-08 reports the counts that ARE reliable and
    deliberately omits duration.
    """
    return (
        "Session duration: NOT REPORTED — the schema stores playthrough "
        "created_at and expires_at (a fixed token TTL), never when a session "
        "actually ended, so a reliable per-session duration cannot be derived "
        "from the existing tables (MON-08 acceptance: documented with the "
        "existing data model)."
    )


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #


def _fmt_ts(ts: float | None) -> str:
    if ts is None:
        return "-"
    return _utc_format(ts, "%Y-%m-%d %H:%M") + " UTC"


def format_human(
    http: HttpStats,
    product: ProductStats | None,
    *,
    hours: float,
    skipped_lines: int,
    http_present: bool = True,
    product_present: bool = True,
    summary_only: bool = False,
) -> str:
    """Render the MON-10 compact report (+ full MON-04/MON-05/MON-08 view)."""
    lines: list[str] = []
    lines.append("Procedural Detective Monitoring")
    window_end = product.last_usage_ts if product is not None else http.last_ts
    lines.append(
        "Window: last %g h (UTC ends %s)" % (hours, _fmt_ts(window_end))
    )
    lines.append("----------------------------")
    lines.append(
        f"HTTP requests:          "
        + (f"{http.total}" if http_present else "n/a (no --logs source)")
    )
    lines.append(
        f"Page/API requests:      "
        + (f"{http.page_api_requests}" if http_present else "n/a (no --logs source)")
    )
    four_xx = sum(
        v for k, v in http.status_classes.items() if k.startswith("4")
    )
    lines.append(
        f"4xx responses:          "
        + (f"{four_xx}" if http_present else "n/a (no --logs source)")
    )
    lines.append(
        f"5xx responses:          "
        + (f"{http.count_5xx}" if http_present else "n/a (no --logs source)")
    )
    product = product or ProductStats(hours=hours)
    lines.append(
        f"Playthroughs started:   "
        + (
            f"{product.playthroughs_started}"
            if product_present
            else "n/a (no --db source)"
        )
    )
    lines.append(
        f"Cases completed:        "
        + (
            f"{product.cases_completed}"
            if product_present
            else "n/a (no --db source)"
        )
    )
    lines.append("")
    if skipped_lines:
        lines.append(f"Lines skipped (malformed): {skipped_lines}")
    if summary_only:
        return "\n".join(lines)

    # HTTP section (MON-04/MON-05).
    lines.append("HTTP traffic")
    lines.append("-----------")
    if not http_present:
        lines.append(
            "no access-log source: pass --logs FILE|- (pipe `docker compose "
            "-f docker-compose.prod.yml logs caddy`)"
        )
    elif http.total == 0:
        lines.append("no access-log entries in the window (is Caddy access logging live?)")
    else:
        lines.append(f"Total requests:            {http.total}")
        lines.append(f"Requests/hour (avg, span): {http.requests_per_hour:.1f}")
        lines.append(
            "Requests/day:             "
            + ", ".join(f"{day}: {n}" for day, n in sorted(http.per_day.items()))
        )
        lines.append(
            "HTTP methods:             "
            + ", ".join(f"{m} {n}" for m, n in sorted(http.methods.items()))
        )
        lines.append("Most-requested paths (query strings stripped):")
        for path, count in http.top_paths:
            lines.append(f"  {count:>6}  {path}")
        lines.append(
            "Status classes:           "
            + ", ".join(
                f"{klass}: {count}"
                for klass, count in sorted(http.status_classes.items())
            )
        )
        lines.append(f"404 count:                {http.count_404}")
        lines.append(f"5xx count:                {http.count_5xx}")
        lines.append(
            f"Transferred bytes (size): {http.bytes_transferred}"
        )
        lines.append("Hourly request series (UTC):")
        if http.hourly_series:
            for hour_epoch, count in http.hourly_series:
                lines.append(
                    f"  {_utc_format(hour_epoch, '%m-%d %H')}:00 "
                    f"{count}"
                )
        else:
            lines.append("  (none)")
        lines.append(
            "Requests by category:     "
            + ", ".join(
                f"{name}: {http.categories[name]}"
                for name in _CATEGORY_ORDER
                if http.categories[name]
            )
        )
        lines.append(f"Page/API requests:        {http.page_api_requests}")
        if http.count_5xx > 0:
            rate = http.count_5xx / http.total
            spike = (
                http.count_5xx >= 20 or rate >= 0.05
            )
            lines.append(
                "5xx spike check:          "
                + (
                    "WARNING — elevated 5xx ratio; investigate "
                    "(docs/MONITORING.md §troubleshooting)"
                    if spike
                    else f"{http.count_5xx} 5xx responses (ratio {rate:.1%})"
                )
            )
    lines.append(f"Lines skipped (malformed): {skipped_lines}")
    lines.append("")
    lines.append(_REQUESTS_VS_USERS_NOTE)

# Product section (MON-08).
    lines.append("")
    lines.append("Product metrics (existing SQLite schema)")
    lines.append("----------------------------------------")
    if not product_present:
        lines.append(
            "no database source: pass --db PATH (e.g. `docker compose -f "
            "docker-compose.prod.yml cp procedural-detective:"
            "/data/procedural_detective.db ./pd-snapshot.db`)"
        )
    else:
        lines.append(f"Playthroughs started:     {product.playthroughs_started}")
        lines.append(f"Cases started:            {product.cases_started}")
        lines.append(f"Case versions started:    {product.case_versions_started}")
        lines.append(f"Cases completed (published): {product.cases_completed}")
        lines.append(f"Published versions:       {product.published_versions}")
        lines.append(
            f"Completion rate:          "
            + (
                f"{product.completion_rate:.1%}"
                if product.completion_rate is not None
                else "n/a (no cases started in the window)"
            )
        )
        lines.append(f"Failed generation attempts: {product.generation_failed_attempts}")
        lines.append(f"Anonymous quota sessions: {product.anonymous_quota_sessions}")
        lines.append(
            f"Usage period (window):    {_fmt_ts(product.first_usage_ts)} -> {_fmt_ts(product.last_usage_ts)}"
        )
        if product.playthroughs_per_day:
            lines.append(
                "Playthroughs per day:     "
                + ", ".join(f"{d}: {n}" for d, n in product.playthroughs_per_day.items())
            )
        lines.append("")
        lines.append(product_session_duration_note())

    # Privacy note (MON-03/MON-09).
    lines.append("")
    lines.append(
        "Privacy: aggregates only. Query strings stripped, no request bodies, "
        "no Authorization/cookies/API keys, no IP addresses printed, no "
        "long-term storage beyond the Docker 10 MB x 5 log rotation, no "
        "persistent user identifiers, no web dashboard exposed."
    )
    return "\n".join(lines)


def format_json(http: HttpStats, product: ProductStats | None, *, hours: float) -> str:
    product = product or ProductStats(hours=hours)
    payload = {
        "window_hours": hours,
        "skipped_lines": http.skipped_lines,
        "http": {
            "total": http.total,
            "requests_per_hour": round(http.requests_per_hour, 2),
            "per_day": dict(sorted(http.per_day.items())),
            "methods": dict(sorted(http.methods.items())),
            "top_paths": [{"path": p, "count": c} for p, c in http.top_paths],
            "status_classes": dict(sorted(http.status_classes.items())),
            "count_404": http.count_404,
            "count_5xx": http.count_5xx,
            "bytes_transferred": http.bytes_transferred,
            "hourly_series": [
                {"hour_epoch": h, "count": c} for h, c in http.hourly_series
            ],
            "categories": {
                name: http.categories[name] for name in _CATEGORY_ORDER
            },
            "page_api_requests": http.page_api_requests,
        },
        "product": {
            "playthroughs_started": product.playthroughs_started,
            "cases_started": product.cases_started,
            "case_versions_started": product.case_versions_started,
            "cases_completed": product.cases_completed,
            "published_versions": product.published_versions,
            "completion_rate": product.completion_rate,
            "generation_failed_attempts": product.generation_failed_attempts,
            "anonymous_quota_sessions": product.anonymous_quota_sessions,
            "first_usage_ts": product.first_usage_ts,
            "last_usage_ts": product.last_usage_ts,
            "playthroughs_per_day": product.playthroughs_per_day,
        },
    }
    return json.dumps(payload, indent=2, sort_keys=True)


# --------------------------------------------------------------------------- #
# log source helpers
# --------------------------------------------------------------------------- #


def _decode_log_bytes(raw: bytes) -> str:
    """Decode captured log bytes tolerantly (UTF-8/BOM, UTF-16 PowerShell).

    ``docker compose logs caddy`` emits UTF-8 without a BOM, but operators
    may capture logs with PowerShell redirection (UTF-16 LE/BE) or editors
    that add a UTF-8 BOM. Never raises.
    """
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        return raw.decode("utf-16", errors="replace")
    return raw.decode("utf-8-sig", errors="replace")


def _read_log_lines(path: str) -> tuple[list[str], int]:
    """Read access-log lines from a file / '-' (stdin). Returns (lines, errors)."""
    if path == "-":
        try:
            text = sys.stdin.buffer.read()
        except AttributeError:  # pragma: no cover - replaced stdin in tests
            text = sys.stdin.read().encode("utf-8")
        lines = _decode_log_bytes(text).splitlines()
    else:
        log_path = Path(path)
        if not log_path.is_file():
            raise FileNotFoundError(f"access-log file not found: {path}")
        lines = _decode_log_bytes(log_path.read_bytes()).splitlines()
    return lines, 0


def _utc_format(ts: float, fmt: str = "%Y-%m-%d") -> str:
    """Format an epoch float as UTC wall time (tz-aware, no deprecation)."""
    return _dt.datetime.fromtimestamp(ts, tz=_dt.timezone.utc).strftime(fmt)


# --------------------------------------------------------------------------- #
# health summary (MON-11) — operator only, read-only ``docker compose ps``
# --------------------------------------------------------------------------- #

# Container/services the operator is expected to see in `docker compose ps`.
_EXPECTED_SERVICES = ("procedural-detective", "caddy")

_HEALTH_CHECKLIST = (
    "Manual health checks (no SaaS monitor is used, MON-11):",
    " 1. App container runs:      docker compose -f docker-compose.prod.yml ps",
    " 2. App is healthy:          docker inspect --format '{{.State.Health.Status}}'",
    "                              <procedural-detective-container>  (>= 'healthy')",
    " 3. Caddy runs:              docker compose -f docker-compose.prod.yml ps caddy",
    " 4. DB reachable:            curl -sk https://<host>/api/v1/readiness -> status ready",
    " 5. Public app status:       curl -sk https://<host>/api/v1/health -> 200/OK",
    " 6. 5xx spike:               run this report and read the '5xx spike check' line.",
)


def health_summary() -> str:
    """Best-effort container state via local ``docker compose ps`` (no ports,
    no socket mount, no root). Returns an explanation string."""
    import subprocess

    lines: list[str] = ["Health summary (MON-11)"]
    lines.append("-------------------------")
    try:
        completed = subprocess.run(
            [
                "docker", "compose", "-f", str(_REPO_ROOT / _PROD_COMPOSE),
                "ps", "--format", "json",
            ],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        lines.append(
            f"could not run docker compose ps (is the daemon reachable?): {exc}"
        )
        lines.append("(health is an operator aid only — it never opens ports or")
        lines.append("mounts the Docker socket)")
        lines.extend(_HEALTH_CHECKLIST)
        return "\n".join(lines)
    if completed.returncode != 0:
        lines.append(
            "docker compose ps failed (is the production stack running with "
            "docker-compose.prod.yml?)."
        )
        lines.extend(_HEALTH_CHECKLIST)
        return "\n".join(lines)
    # `docker compose ps --format json` emits ONE JSON object per line (not a
    # JSON array) — parse each line independently.
    containers: list[dict] = []
    for raw in completed.stdout.splitlines():
        try:
            item = json.loads(raw)
        except ValueError:
            continue
        if isinstance(item, dict):
            containers.append(item)
    present = {
        str(item.get("Service") or item.get("Name")): {
            "state": str(item.get("State")),
            "health": str(item.get("Health")),
        }
        for item in containers
        if isinstance(item, dict)
    }
    for name in _EXPECTED_SERVICES:
        info = present.get(name)
        if info is None:
            lines.append(f"{name}: NOT RUNNING")
        else:
            lines.append(
                f"{name}: state={info['state']}, health={info.get('health') or 'n/a'}"
            )
    lines.extend(_HEALTH_CHECKLIST)
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="monitoring_report",
        description=(
            "Procedural Detective monitoring report: HTTP metrics from the "
            "Caddy JSON access logs (docker compose logs caddy) plus product "
            "metrics from the existing SQLite database. No network, no root, "
            "read-only DB access, aggregates only (MON-03/MON-04/MON-08/MON-10)."
        ),
    )
    parser.add_argument(
        "--logs",
        metavar="PATH",
        default=None,
        help=(
            "Caddy JSON access-log lines: a captured file or '-' for stdin "
            "(recommended: `docker compose -f docker-compose.prod.yml logs "
            "caddy | python -m tools.monitoring_report --logs - ...`). "
            "Docker json-file envelopes and compose prefixes are unwrapped."
        ),
    )
    parser.add_argument(
        "--db",
        metavar="PATH",
        default=None,
        help=(
            "Procedural Detective SQLite database (file path or sqlite:/// URL). "
            "Read-only SELECTs only. Take a snapshot with `docker compose -f "
            "docker-compose.prod.yml cp procedural-detective:"
            "/data/procedural_detective.db ./pd-snapshot.db`. Falls back to "
            "$DATABASE_URL when omitted."
        ),
    )
    parser.add_argument(
        "--hours",
        type=float,
        default=24.0,
        help="Report window in hours (default 24).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit a deterministic JSON document instead of the human report.",
    )
    parser.add_argument(
        "--summary",
        action="store_true",
        help="Print only the compact MON-10 status block.",
    )
    parser.add_argument(
        "--health",
        action="store_true",
        help=(
            "Append the MON-11 health summary via read-only `docker compose "
            "ps` (operator aid; daemon required; never part of the hermetic "
            "tests)."
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # --hours must be finite and positive: NaN/Inf (e.g. `--hours nan|inf`)
    # would silently poison the window arithmetic (usage error, exit 2).
    if not math.isfinite(args.hours) or args.hours <= 0:
        print("error: --hours must be positive (and finite)", file=sys.stderr)
        return 2

    # --- HTTP metrics (MON-04/05) -----------------------------------------
    # Every input path is fail-clean (P29-01): a garbled/hostile line is
    # counted as skipped ("N lines skipped (malformed)") and processing
    # continues. Only the DATA SOURCE itself (missing log file / missing
    # database) exits 1; a malformed CAPTURE can never crash the report.
    http = HttpStats()
    skipped = 0
    http_present = args.logs is not None
    if args.logs is not None:
        try:
            lines, _errors = _read_log_lines(args.logs)
        except FileNotFoundError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        entries: list[CaddyLogEntry] = []
        for line in lines:
            try:
                entry = parse_caddy_log_line(line)
            except _HAZARD_EXCEPTIONS:
                skipped += 1
                continue
            if entry is None:
                skipped += 1
            else:
                entries.append(entry)
        try:
            http = aggregate(entries, window_hours=args.hours)
        except _HAZARD_EXCEPTIONS:
            # aggregate() is already defensive; this is the last-resort
            # fail-clean guarantee — every line is treated as malformed.
            http = HttpStats()
            skipped += len(entries)
        http.skipped_lines = skipped + http.skipped_lines
    else:
        http.skipped_lines = 0

    # --- product metrics (MON-08) -----------------------------------------
    product: ProductStats | None = None
    product_present = False
    db_source: str | None = args.db
    if db_source is None:
        import os

        db_source = os.environ.get("DATABASE_URL")
    if db_source:
        try:
            database = sqlite_url_to_path(db_source)
            now_ts = _dt.datetime.now(_dt.timezone.utc).timestamp()
            product = query_product_metrics(database, now_ts, hours=args.hours)
            product_present = True
        except (FileNotFoundError, ValueError, OverflowError, OSError,
                RecursionError) as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1
        except sqlite3.Error as exc:
            print(f"error: database could not be read: {exc}", file=sys.stderr)
            return 1

    try:
        if args.json:
            print(format_json(http, product, hours=args.hours))
        else:
            print(
                format_human(
                    http,
                    product,
                    hours=args.hours,
                    skipped_lines=http.skipped_lines,
                    http_present=http_present,
                    product_present=product_present,
                    summary_only=args.summary,
                )
            )
    except BrokenPipeError:  # pragma: no cover - `... | head`
        return 0

    if args.health:
        print()
        print(health_summary())

    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CATEGORY_API",
    "CATEGORY_HEALTH",
    "CATEGORY_OTHER",
    "CATEGORY_PAGE",
    "CATEGORY_STATIC",
    "CaddyLogEntry",
    "HEALTH_PATHS",
    "HttpStats",
    "PAGE_PATHS",
    "ProductStats",
    "STATIC_PREFIXES",
    "aggregate",
    "categorize",
    "entry_from_object",
    "format_human",
    "format_json",
    "parse_caddy_log_line",
    "product_session_duration_note",
    "query_product_metrics",
    "sqlite_url_to_path",
]