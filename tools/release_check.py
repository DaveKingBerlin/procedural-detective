"""Phase 18A — release-hygiene check CLI (Task 9) + importable check suite.

Verifies that the submission tree is release-safe BEFORE packaging/judging:

  1. release docs (README.md / SUBMISSION.md) — no placeholder tokens that
     would embarrass a public submission (TODO / TBD / generic GitHub-root
     links / ``<owner>`` / ``<repo-url>`` / "placeholder" markers in the
     SUBMISSION asset table). Hosting/video-dependent URLs ("lands here after
     deployment", "lands here after recording", ``<demo-url>``, ``<video-url>``)
     MAY legitimately remain until hosting/video exists (Phase 18A task 9);
     pass ``--allow-hosted-placeholders`` to REPORT those separately instead
     of failing on them.
  2. THIRD_PARTY.md — must exist at the repo root (Phase 18A task 4).
  3. git-tracked secret/log/db files — never a tracked ``.env``, ``logs/``,
     ``*.db``, ``*.sqlite``/``*.sqlite3`` or ``*.log``.
  4. private/secret endpoint leakage — no LAN IP literals
     (192.168.* / 10.* / 172.16-31.*), no ``http(s)://`` URL with a private
     host and no ``OLLAMA_BASE_URL=http://<private-host>``-style literals in
     the tracked release surface (repo root, source, build config, docs).
     ``127.0.0.1`` / ``localhost`` / ``host.docker.internal`` and
     ``.env.example`` are the sanctioned examples; the RFC1918 range
     DEFINITIONS inside ``backend/app/core/config.py`` (``ip_network(...)``)
     and hermetic test vectors (``backend/tests/``, ``bridge/tests/``,
     ``e2e/probes/``, frontend ``*.test.*`` / ``*.spec.*``) are documented
     exceptions — they are the allowlist definition, the local-provider
     bridge URL-rejection vectors (Phase 22) and the environment-matrix
     regression tests.
  5. FRONTEND BUILD OUTPUT (frontend/dist, or ``--frontend-dir``) — the same
     private host/IP/URL literal scan plus the ``11434`` Ollama port and the
     provider configuration variable NAMES (``OLLAMA_BASE_URL`` / ``LLM_API_KEY``
     / ...), which never belong in a player-side build. The build directory is
     git-ignored; the check skips cleanly when it does not exist.
     Phase20 PD-SEC-04 (§12.2): the scan ALSO flags a production-bundle embed
     of ``localhost:8000`` (the audited HTTP fallback base) and any bare
     ``127.0.0.1`` — a production build must reference the API same-origin
     (relative ``/api/v1``) only.
6. DOCKER BUILD-CONTEXT HYGIENE (Phase20 PD-SEC-07) — ``.dockerignore`` at
      the repo root must exist and exclude ``.env`` / ``.env.*`` (while keeping
      ``.env.example``), ``logs/``, ``*.db`` / ``*.sqlite*`` runtime databases,
      ``tmp/``/``temp/`` and local Ollama config (``.ollama/``) so operator
      secrets never reach a Docker daemon/builder/build cache.
   7. PROD COMPOSE LOGGING BOUNDS (Phase21 F-04) — every public service in
      ``docker-compose.prod.yml`` (``procedural-detective`` and ``caddy``) must
      carry a ``logging.driver: json-file`` block with ``max-size`` /
      ``max-file`` so container stdout logs (uvicorn/Caddy access logs) are
      rotated by the runtime instead of growing without limit.
   8. PROD ENV PROFILE SPLIT (Phase21 B Finding 2) — both ``.env.example``
      (DEV: ``ENVIRONMENT=development``, ``TRUST_PROXY=false``) and
      ``.env.production.example`` (PROD: ``ENVIRONMENT=production``,
      ``PD_DEV_TRACE=false``, ``TRUST_PROXY=true`` behind the shipped Caddy
      edge) must exist, and the production example must never carry a dev-only
      value (``TRUST_PROXY=false`` / ``ENVIRONMENT=development`` active).
   9. PROD EFFECTIVE CONFIG PREFLIGHT (Phase21C AUD-21B-02) — invokes Docker
      Compose's authoritative ``config --format json`` render with the current
      shell environment, project directory and production compose file, then
      asserts: ``ENVIRONMENT=production``, ``PD_DEV_TRACE=false``,
      ``TRUST_PROXY=true`` for the Caddy ingress profile (a ``.env`` that
      overrides with dev values FAILS with a clear message), the P-02 timeout
      envelope (frontend 360s > backend deadline; proxy 420s > frontend), Docker
      log bounds on both public services, the backend port is never publicly
      published (``expose`` only, no ``ports:``), the Ollama port ``11434`` is
      never a published host port, and the production frontend bundle uses the
      same-origin API (no ``localhost:8000`` / ``127.0.0.1`` embed). The
``CADDY_DOMAIN`` ready-to-host verdict rejects empty, local, IP, malformed and
       reserved/example hostnames. ``--allow-local`` / ``allow_local`` forgives
       only an explicit local-smoke hostname; the strict single command is
       ``python -m tools.prod_preflight``.

The production-DEPLOYMENT checks (7, 8, 9) apply ONLY when the deployment
artifacts exist: a document tree without ``docker-compose.prod.yml`` or the
``.env.production.example`` template reports those checks as ``skip`` (the same
pattern as the frontend-build bundle scan), never ``fail`` — a repo that simply
is not a production-deployment root must not be gate-blocked. When the files
ARE present the checks still fail-closed on every asserted violation
(dev overrides, placeholder domains, unbounded logs, published ports, ...).

Exit code: 0 ONLY when every non-optional check passes (with
``--allow-hosted-placeholders``, hosting/video-only placeholders and
query-gated debug-aid tokens are REPORTED separately and never fail the run).

The tool never prints Docker Compose's rendered model, configured endpoint or
credential. Compose reads the working-tree ``.env`` and current shell itself.
A custom startup env file is inspected only when the operator explicitly gives
the matching ``--env-file`` option to ``tools.prod_preflight``. Findings name
only validation keys and sanitized verdicts.

Usage:
    python -m tools.release_check [--allow-hosted-placeholders] [--frontend-dir PATH]
    python -m tools.prod_preflight [--allow-local]
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = _SCRIPT_DIR.parent

# --------------------------------------------------------------------------- #
# placeholders
# --------------------------------------------------------------------------- #

RELEASE_DOCS = ("README.md", "SUBMISSION.md")

# Hosting/video-only signals: allowed to remain when the operator accepts them
# with --allow-hosted-placeholders (Phase 18A task 9). Reported, never failed.
# "lands here" is matched AFTER "lands here after" so the longer token wins and
# the two are never double-counted on the same line.
_HOSTED_PLACEHOLDER_TOKENS = (
    "lands here after",
    "lands here",
    "<demo-url>",
    "<demo-host>",
    "<video-url>",
)

# A line carrying one of these signals is a HOSTING/VIDEO-dependent statement:
# a TODO/TBD token there ("TBD after hosting is chosen", "TBD after recording")
# is an explicit hosting/video placeholder, not an unfinished release sentence.
_HOSTED_CONTEXT_RE = re.compile(
    r"hosting|recording|lands here|<demo|<video|demo url|demo-|video url|video-",
    re.IGNORECASE,
)

# Placeholder tokens that ALWAYS block a release until the docs track replaces
# them (not hosting/video dependent) — UNLESS the containing line is an
# explicit hosting/video statement (see _HOSTED_CONTEXT_RE).
_HARD_PLACEHOLDER_TOKENS = ("TODO", "TBD", "<repo-url>", "<owner>")

# A "generic GitHub-root" link: https://github.com/ NOT followed by an
# owner/repository path (i.e. followed by whitespace / quote / backtick / paren
# / angle bracket / end-of-text) before any path characters.
_GITHUB_ROOT_RE = re.compile(
    r"https://github\.com/(?=\s|['\"`()<>]|$)", re.IGNORECASE
)

# "placeholder" markers in SUBMISSION.md's asset table (Phase 18A task 6 will
# replace the whole table). A marker on a demo/video row is hosting/video-only;
# every other marker (repository / category / heading) is hard.
_SUBMISSION_PLACEHOLDER_RE = re.compile(r"placeholder", re.IGNORECASE)
_HOSTED_ROW_HINT_RE = re.compile(r"<demo|<video|demo|video", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# private-endpoint leakage
# --------------------------------------------------------------------------- #

# RFC1918 private IPv4 ranges the release surface must never contain:
# 10/8, 172.16/12, 192.168/16 (same ranges Settings allows for operator
# Ollama hosts — but they belong only in operator .env, never in the tree).
_PRIVATE_V4_RE = re.compile(
    r"\b(?:"
    r"10\.(?:[0-9]{1,3})\.(?:[0-9]{1,3})\.(?:[0-9]{1,3})"
    r"|172\.(?:1[6-9]|2[0-9]|3[01])\.(?:[0-9]{1,3})\.(?:[0-9]{1,3})"
    r"|192\.168\.(?:[0-9]{1,3})\.(?:[0-9]{1,3})"
    r")\b"
)

# http(s):// URL literals (host captured without credentials/port).
_URL_RE = re.compile(r"https?://([^\s/\"'<>`]+)", re.IGNORECASE)

# Hosts that are SANCTIONED wherever they appear (loopback / docker internal).
_ALLOWED_URL_HOSTS = frozenset(
    {"127.0.0.1", "localhost", "::1", "host.docker.internal", "0.0.0.0"}
)

# The Ollama port: its literal presence in a PLAYER build marks an endpoint
# leak; in tracked source it is only relevant when composed with a URL.
_OLLAMA_PORT_TOKEN = ":11434"
OLLAMA_PORT_TOKEN = "11434"

# Provider configuration env NAMES that never belong in a player build.
_PROVIDER_ENV_NAMES = (
    "OLLAMA_BASE_URL",
    "OLLAMA_MODEL",
    "OLLAMA_TIMEOUT_SECONDS",
    "LLM_API_KEY",
    "LLM_MODEL",
    "LIVE_PROVIDER_URL",
)

# Query-gated developer debug aids (frontend DEF-056 diagnostic hook). They are
# gated behind the exact query (?pd-debug-pick=1); the tool REPORTS (never
# fails) on them in a player build so the frontend track sees them.
_DEBUG_AID_TOKENS = ("pd-debug-pick", "__pdDebugScene", "PD_DEV_TRACE")

# Dev/loopback endpoints a PLAYER build must never embed as API bases
# (Phase20 PD-SEC-04 §12.2 expects ZERO hits in a production build). A bare
# host token like ``http://localhost`` appears legitimately inside react-router
# library internals, so the check is deliberately targeted: the audited leak is
# the concrete ``http://localhost:8000`` API-base fallback, and loopback IPv4
# (``127.0.0.1``) never belongs in a player build at all.
_FRONTEND_DEV_HOST_TOKENS = ("localhost:8000", "127.0.0.1")

# --------------------------------------------------------------------------- #
# docker build-context hygiene (Phase20 PD-SEC-07 §18/§18.1)
# --------------------------------------------------------------------------- #

# Required `.dockerignore` exclusions, each searched as a whole-file regex
# (multiline, case-insensitive). The dotenv family must be excluded with the
# documented ``.env.example`` re-inclusion, operator logs/dbs must never enter
# the context, and local Ollama config (``.ollama/``) is defence-in-depth.
_DOCKERIGNORE_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    (".env", re.compile(r"^\s*\.env\s*$", re.MULTILINE | re.IGNORECASE)),
    (
        ".env.* variants",
        re.compile(r"^\s*\.env\.\*\s*$", re.MULTILINE | re.IGNORECASE),
    ),
    (
        "!.env.example re-inclusion",
        re.compile(r"^\s*!\s*\.env\.example\s*$", re.MULTILINE | re.IGNORECASE),
    ),
    (
        "logs/ directory",
        re.compile(r"^\s*logs/?\s*(?:#.*)?$", re.MULTILINE | re.IGNORECASE),
    ),
    (
        "*.log files",
        re.compile(r"^\s*\*\.log\s*$", re.MULTILINE | re.IGNORECASE),
    ),
    (
        "*.db files",
        re.compile(r"^\s*\*\.db\s*$", re.MULTILINE | re.IGNORECASE),
    ),
    (
        "*.sqlite / *.sqlite3 files",
        re.compile(r"^\s*\*\.sqlite(?:3)?\s*$", re.MULTILINE | re.IGNORECASE),
    ),
    (
        "tmp/ and temp/ directories",
        re.compile(r"^\s*(?:tmp|temp)/?\s*(?:#.*)?$", re.MULTILINE | re.IGNORECASE),
    ),
    (
        ".ollama local config",
        re.compile(r"^\s*\.ollama/?\s*(?:#.*)?$", re.MULTILINE | re.IGNORECASE),
    ),
)


# --------------------------------------------------------------------------- #
# private-host detection (ADV-207): dotted IPv4 in ANY numeral base, integer
# IPv4 spellings, bracket IPv6 (ULA / link-local), userinfo-stripping, and
# embedded private literals inside longer host tokens.
# --------------------------------------------------------------------------- #

# A dotted-quad candidate whose octets may be decimal, octal (leading 0) or
# hex (0x...) — the spellings browsers/curl accept for the SAME private host.
_OCT_OR_HEX_GROUP = r"(?:0[xX][0-9a-fA-F]+|[0-9]+)"
_DOTTED_ANY_BASE_RE = re.compile(
    _OCT_OR_HEX_GROUP + r"(?:\." + _OCT_OR_HEX_GROUP + r"){3}"
)

# An integer IPv4 spelling (decimal 3232235777 / hex 0xC0A80101 / octal
# 030000001004001). Only meaningful as a URL HOST, so it is only examined by
# ``_host_is_private`` (never as a bare line literal — a bare number in a doc
# is not an IP and would only add false positives).
_INT_IPV4_RE = re.compile(r"(?:0[xX][0-9a-fA-F]{1,8}|[0-9]{6,13})")

# Unique-local IPv6 (fc00::/7; fd00::/8 is the lower half) — RFC 4193 ULA.
_ULA_V6_NET = ipaddress.ip_network("fc00::/7")


def _octet_value(token: str) -> int | None:
    """One dotted-quad octet token -> 0..255, or None when not a number.

    C-style base inference: ``0x``/``0X`` -> hex; a leading ``0`` -> octal
    (falling back to decimal when the octal parse fails, matching common
    parsers); otherwise decimal.
    """
    lowered = token.lower()
    try:
        if lowered.startswith("0x"):
            value = int(token, 16)
        elif len(token) > 1 and token.startswith("0"):
            try:
                value = int(token, 8)
            except ValueError:
                value = int(token, 10)
        else:
            value = int(token, 10)
    except ValueError:
        return None
    return value if 0 <= value <= 255 else None


def _ipv4_dotted_windows(hostname: str) -> list[tuple[int, int, int, int]]:
    """Every 4-octet window of dot-separated numeric groups in ``hostname``.

    Split on dots and slide a 4-window over the groups: this finds a private
    dotted literal EMBEDDED anywhere inside a longer dotted token
    (``0xC0.0xA8.0x01.0x05.evil.com`` — the hex spelling of the private
    192.168.* quad — and ``sub.<dotted-private-quad>.example.com``) as well as
    the plain dotted form, evaluating each decimal/octal/hex spelling.
    """
    parts = hostname.split(".")
    windows: list[tuple[int, int, int, int]] = []
    for start in range(0, len(parts) - 3):
        octets: list[int] = []
        for token in parts[start:start + 4]:
            value = _octet_value(token)
            if value is None:
                octets = []
                break
            octets.append(value)
        if len(octets) == 4:
            windows.append(tuple(octets))  # type: ignore[arg-type]
    return windows


def _ipv4_candidates_from(hostname: str) -> list[ipaddress.IPv4Address]:
    """Every IPv4 literal a browser/HTTP stack could resolve inside ``hostname``.

    - every dotted-quad spelling (decimal/octal/hex per octet), INCLUDING
      embedded inside a longer token (``0xC0.0xA8.0x01.0x05.evil.com`` -> the
      embedded private quad is found and evaluated);
    - every single-integer spelling (``3232235777`` / ``0xC0A80101``), which
      only makes sense when the integer is (part of) the HOST — but it is
      evaluated the same way for safety.
    """
    candidates: list[ipaddress.IPv4Address] = []
    for octets in _ipv4_dotted_windows(hostname):
        try:
            candidates.append(ipaddress.IPv4Address(bytes(octets)))
        except ValueError:
            continue
    for match in _INT_IPV4_RE.finditer(hostname):
        token = match.group(0)
        try:
            if token.lower().startswith("0x"):
                value = int(token, 16)
            elif len(token) > 1 and token.startswith("0"):
                try:
                    value = int(token, 8)
                except ValueError:
                    value = int(token, 10)
            else:
                value = int(token, 10)
        except ValueError:
            continue
        if 0 <= value < 2**32:
            try:
                candidates.append(ipaddress.IPv4Address(value))
            except ValueError:
                continue
    return candidates


def _ipv6_is_private(addr: ipaddress.IPv6Address) -> bool:
    """True for private/link-local IPv6: ULA (fc00::/7) or link-local (fe80::/10).

    Loopback (``::1``) is deliberately NOT private — it is a sanctioned
    loopback host in ``_ALLOWED_URL_HOSTS``.
    """
    return addr in _ULA_V6_NET or addr.is_link_local


def _host_is_private(host: str) -> bool:
    """True for a URL host that resolves to a private address.

    ``host`` is the raw netloc captured by ``_URL_RE`` (may carry userinfo
    and/or a port). Coverage (ADV-207):

    - RFC1918 dotted-decimal IPv4 (the ``10.*`` / ``172.16-31.*`` /
      ``192.168.*`` quads);
    - DECIMAL / HEX / OCTAL spellings of private IPv4 ranges
      (``3232235777`` / ``0xC0A80101`` / ``0300.0250.0001.0001`` all resolve
      to the SAME private quad);
    - private / link-local IPv6 (``[fc00::1]``, ``[fd00::1]``, ``[fe80::1]``,
      ``[fd00::abcd]:11434``);
    - ``user:pass@host`` credentials wrapping any of the above;
    - an embedded private literal inside a longer host token
      (``0xC0.0xA8.0x01.0x05.evil.com``).

    The sanctioned loopback/docker names (``localhost``, ``127.0.0.1``,
    ``::1``, ``host.docker.internal``, ``0.0.0.0``) and ``.env.example``
    (handled by ``_is_sanctioned``) stay allowed everywhere.
    """
    netloc = host.split("@")[-1]  # strip any embedded userinfo
    bracketed = netloc.startswith("[")
    if bracketed:
        hostname = netloc.split("]", 1)[0].strip("[]")
    else:
        hostname = netloc.split(":", 1)[0] if ":" in netloc else netloc
    lowered = hostname.lower()
    if lowered in _ALLOWED_URL_HOSTS:
        return False
    if ":" in hostname or bracketed:
        # IPv6 literal (bracketed or bare colons): evaluate the whole host.
        try:
            addr = ipaddress.ip_address(hostname)
        except ValueError:
            addr = None
        if addr is not None and addr.version == 6 and _ipv6_is_private(addr):
            return True
    for candidate in _ipv4_candidates_from(lowered):
        if candidate.is_private:
            return True
    return False


# --------------------------------------------------------------------------- #
# git tracked-file access
# --------------------------------------------------------------------------- #

_TRACKED_SECRET_NAMES = (".env",)
_TRACKED_SECRET_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".log")
_TRACKED_SECRET_DIRS = ("logs",)
# The basenames that stay SANCTIONED as example/config-template files (never a
# real secret file; the private-endpoint scan also exempts them via
# _SANCTIONED_EXAMPLE_FILES). Phase 21B split the docs examples in two: the DEV
# example (.env.example) and the PRODUCTION profile example
# (.env.production.example) are BOTH templates, never operator secrets.
_SANCTIONED_ENV_EXAMPLES = frozenset({".env.example", ".env.production.example"})


def _git(
    repo_root: Path, *args: str
) -> subprocess.CompletedProcess[str] | None:
    """One ``git`` subprocess over ``repo_root``; None when git cannot run."""
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def git_tracked_files(repo_root: Path) -> list[str] | None:
    """``git ls-files`` relative paths, or None when git/the repo is unusable.

    FAIL-CLOSED (ADV-205): an empty ``git ls-files`` output is NOT assumed to
    mean "empty repo". A missing/corrupt ``.git/index`` makes ``ls-files``
    exit 0 with empty stdout (indistinguishable from a truly empty repo), which
    would silently disable BOTH the secret scan and the private-endpoint scan.
    When ``ls-files`` is empty, ``git status --porcelain`` is consulted: if it
    reports ANY file, the index is unusable -> None (fail closed). Only a
    genuinely empty tree (no files at all) returns the empty list.

    Only TRACKED files are ever scanned: the working-tree ``.env`` (operator
    secret) is untracked and intentionally never read.
    """
    inside = _git(repo_root, "rev-parse", "--is-inside-work-tree")
    if (
        inside is None
        or inside.returncode != 0
        or inside.stdout.strip().lower() != "true"
    ):
        return None  # not inside a usable work tree -> cannot enumerate
    result = _git(repo_root, "ls-files")
    if result is None or result.returncode != 0:
        return None
    tracked = [line for line in result.stdout.splitlines() if line.strip()]
    if tracked:
        return tracked
    # Empty ls-files: prove it is REALLY an empty repo (no working-tree files
    # at all). With a missing/corrupt index, git status is non-empty -> fail
    # closed (ADV-205).
    status = _git(repo_root, "status", "--porcelain")
    if status is None or status.returncode != 0:
        return None
    if status.stdout.strip():
        return None
    return tracked


def tracked_secret_entries(tracked: list[str]) -> list[str]:
    """Tracked entries that must never exist: .env family / logs/ / *.db / *.sqlite / *.log.

    The whole dotenv family is banned (ADV-206): ``.env`` AND every
    ``.env.<suffix>`` variant (``.env.production``, ``.env.local``,
    ``.env.development``, ``.env.test``, ...) — Vite/dotenv load those at build
    time, so a tracked ``.env.production`` with a secret is the same class of
    leak as a tracked ``.env``. The sanctioned exceptions are the two DOCUMENTED
    EXAMPLES: the ``.env.example`` basename (developer example everywhere) and
    ``.env.production.example`` (the Phase 21B production profile template).
    """
    bad: list[str] = []
    for rel in tracked:
        name = rel.rsplit("/", 1)[-1]
        if name in _TRACKED_SECRET_NAMES:
            bad.append(rel)
            continue
        lower = name.lower()
        if lower in _SANCTIONED_ENV_EXAMPLES:
            continue  # sanctioned example files, never a secret
        if lower == ".env" or lower.startswith(".env."):
            bad.append(rel)
            continue
        parts = rel.split("/")
        if any(part in _TRACKED_SECRET_DIRS for part in parts):
            bad.append(rel)
            continue
        if lower.endswith(_TRACKED_SECRET_SUFFIXES):
            bad.append(rel)
    return sorted(bad)


# --------------------------------------------------------------------------- #
# scan surface for the private-endpoint check
# --------------------------------------------------------------------------- #

# The sanctioned example locations (private-IP examples may legitimately live
# there; the PRODUCTION profile template is documented NEVER to carry them but
# is exempted the same way so it cannot fail the release surface scan).
_SANCTIONED_EXAMPLE_FILES = (".env.example", ".env.production.example")

# Hermetic test vectors / QA contract audits: they MUST legitimately exercise
# private/LAN hosts (the config validator accepts them), so they are the
# documented exceptions for the *tracked-tree* leak scan. ``bridge/tests/`` is
# the Phase 22 local-provider bridge test tree (ADV-249): its URL-validation
# vectors use private/LAN literals as intended-to-be-REJECTED inputs — the
# same bounded-test-vector status as ``backend/tests/`` / ``e2e/probes/`` /
# frontend ``*.test.*`` / ``*.spec.*``. Production/tracked source (repo root,
# backend/app, frontend/src, docs, docker, compose) is NOT exempted and still
# fails on any private endpoint literal.
_SANCTIONED_TREE_PREFIXES = (
    "backend/tests/",
    "bridge/tests/",
    "e2e/probes/",
)

# The RFC1918 range DEFINITIONS inside the config allowlist (ip_network(...)).
_RANGE_DEFINITION_LINE_RE = re.compile(r"ip_network\(")


def _is_sanctioned(rel: str) -> bool:
    """True when ``rel`` is a documented scan exception (examples/tests)."""
    if rel.replace("\\", "/") in _SANCTIONED_EXAMPLE_FILES:
        return True
    norm = rel.replace("\\", "/")
    if norm.startswith(_SANCTIONED_TREE_PREFIXES):
        return True
    # Frontend unit / E2E specs are test vectors too.
    name = norm.rsplit("/", 1)[-1]
    if ".test." in name or name.endswith(".spec.ts") or name.endswith(".spec.tsx"):
        return True
    return False


def _interface_scan_targets(repo_root: Path, tracked: list[str]) -> list[tuple[str, Path]]:
    """Release-relevant tracked files: repo root + tech source + build config."""
    targets: list[tuple[str, Path]] = []
    for rel in tracked:
        norm = rel.replace("\\", "/")
        if _is_sanctioned(norm):
            continue
        path = (repo_root / norm).resolve()
        if not path.is_file():
            continue
        if not _looks_textual(path):
            continue
        targets.append((norm, path))
    return targets


_TEXT_EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".yml", ".yaml", ".toml",
    ".ini", ".cfg", ".md", ".txt", ".sh", ".html", ".css", ".env.example",
    ".dockerignore", ".gitignore", "",
}
_TEXT_EXTENSIONS.add("Dockerfile")
_TEXT_EXTENSIONS.add("LICENSE")


def _looks_textual(path: Path) -> bool:
    """True for files we scan line-wise (never binaries)."""
    suffix = path.suffix.lower()
    if suffix in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff",
                  ".woff2", ".ttf", ".eot", ".webp", ".mp4", ".webm"):
        return False
    # Lock/GH files contain base64 + URLs but are release-relevant build input.
    return True


# --------------------------------------------------------------------------- #
# finding model
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Finding:
    """One release-check result line."""

    check: str          # e.g. "placeholders" / "third-party" / "tracked-secrets"
    severity: str       # "ok" | "fail" | "report" | "skip"
    message: str

    def render(self) -> str:
        return f"[{self.severity}] {self.check}: {self.message}"


# --------------------------------------------------------------------------- #
# checks
# --------------------------------------------------------------------------- #


def scan_placeholders(repo_root: Path, allow_hosted: bool) -> list[Finding]:
    """Release-doc placeholder scan (README.md + SUBMISSION.md)."""
    findings: list[Finding] = []
    for doc_name in RELEASE_DOCS:
        doc = repo_root / doc_name
        if not doc.is_file():
            findings.append(
                Finding("placeholders", "fail", f"{doc_name} is missing at the repo root")
            )
            continue
        try:
            lines = doc.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            findings.append(
                Finding("placeholders", "fail", f"{doc_name} cannot be read: {exc}")
            )
            continue
        for number, line in enumerate(lines, start=1):
            lowered = line.casefold()
            in_hosted_context = bool(_HOSTED_CONTEXT_RE.search(line))
            hosted_matched: list[str] = []
            for token in _HARD_PLACEHOLDER_TOKENS:
                if token.casefold() not in lowered:
                    continue
                if token in ("TODO", "TBD") and in_hosted_context:
                    # Explicit "TBD after hosting/recording" style statement.
                    severity = "report" if allow_hosted else "fail"
                    findings.append(
                        Finding(
                            "placeholders", severity,
                            f"{doc_name}:{number}: hosting/video placeholder {token!r}"
                            + (" (allowed while hosting/video is pending)" if allow_hosted else ""),
                        )
                    )
                    continue
                findings.append(
                    Finding(
                        "placeholders", "fail",
                        f"{doc_name}:{number}: hard placeholder token {token!r}",
                    )
                )
            if _GITHUB_ROOT_RE.search(line):
                findings.append(
                    Finding(
                        "placeholders", "fail",
                        f"{doc_name}:{number}: generic GitHub-root link "
                        "(no owner/repository)",
                    )
                )
            for token in _HOSTED_PLACEHOLDER_TOKENS:
                if token.casefold() not in lowered:
                    continue
                # Longest-token-wins: "lands here after" already covers a
                # "lands here" match on the same line — never double-count.
                if any(token.casefold() in other.casefold() for other in hosted_matched):
                    continue
                hosted_matched.append(token)
                severity = "report" if allow_hosted else "fail"
                findings.append(
                    Finding(
                        "placeholders", severity,
                        f"{doc_name}:{number}: hosting/video placeholder {token!r}"
                        + (" (allowed while hosting/video is pending)" if allow_hosted else ""),
                    )
                )
            if doc_name == "SUBMISSION.md" and _SUBMISSION_PLACEHOLDER_RE.search(line):
                # Classify the asset-table marker: demo/video rows are
                # hosting/video-only; repository/category/heading markers are not.
                is_hosted_row = bool(_HOSTED_ROW_HINT_RE.search(line))
                severity = "report" if (allow_hosted and is_hosted_row) else "fail"
                findings.append(
                    Finding(
                        "placeholders", severity,
                        f"{doc_name}:{number}: 'placeholder' marker in the submission "
                        "asset table"
                        + (" (hosting/video row; allowed while pending)" if severity == "report" else ""),
                    )
                )
    if allow_hosted:
        findings.append(
            Finding(
                "placeholders", "ok",
                "release docs scanned (hosting/video placeholders reported separately)",
            )
        )
    elif not any(f.severity == "fail" for f in findings):
        findings.append(Finding("placeholders", "ok", "release docs are placeholder-free"))
    return findings


def check_third_party(repo_root: Path) -> list[Finding]:
    """THIRD_PARTY.md must exist at the repo root (Phase 18A task 4)."""
    if (repo_root / "THIRD_PARTY.md").is_file():
        return [Finding("third-party", "ok", "THIRD_PARTY.md is present at the repo root")]
    return [
        Finding(
            "third-party", "fail",
            "THIRD_PARTY.md is missing at the repo root "
            "(Phase 18A task 4 — docs track)",
        )
    ]


def check_tracked_secrets(tracked: list[str]) -> list[Finding]:
    """No tracked .env / logs/ / *.db / *.sqlite / *.log in the index."""
    if tracked is None:
        return [
            Finding(
                "tracked-secrets", "fail",
                "cannot enumerate tracked files (git ls-files failed) — "
                "fail-closed: cannot prove no .env/logs/db are tracked",
            )
        ]
    bad = tracked_secret_entries(tracked)
    if bad:
        return [
            Finding("tracked-secrets", "fail", "tracked runtime artifacts: " + ", ".join(bad))
        ]
    return [Finding("tracked-secrets", "ok", "no tracked .env / logs/ / *.db / *.sqlite / *.log")]


def scan_private_endpoints(
    repo_root: Path, tracked: list[str]
) -> list[Finding]:
    """LAN-IP / provider-URL literal scan over the tracked release surface."""
    if tracked is None:
        return [
            Finding(
                "private-endpoint", "fail",
                "cannot enumerate tracked files (git ls-files failed) — "
                "fail-closed: cannot prove the tree is leak-free",
            )
        ]
    targets = _interface_scan_targets(repo_root, tracked)
    findings: list[Finding] = []
    for rel, path in targets:
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if _RANGE_DEFINITION_LINE_RE.search(line):
                continue  # config allowlist range DEFINITIONS (documented)
            if _PRIVATE_V4_RE.search(line):
                findings.append(
                    Finding(
                        "private-endpoint", "fail",
                        f"{rel}:{number}: private/LAN IP literal in the tracked "
                        "release surface",
                    )
                )
            for match in _URL_RE.finditer(line):
                host = match.group(1)
                if _host_is_private(host):
                    findings.append(
                        Finding(
                            "private-endpoint", "fail",
                            f"{rel}:{number}: http(s):// URL composed with a private "
                            f"host ({host})",
                        )
                    )
    if not findings:
        findings.append(
            Finding(
                "private-endpoint", "ok",
                "no private/LAN IP, private URL host or OLLAMA_BASE_URL-style "
                "literal in the tracked release surface",
            )
        )
    return findings


def scan_frontend_build(frontend_dir: Path) -> list[Finding]:
    """Provider endpoint / LAN IP scan over the built frontend (skip if absent)."""
    if frontend_dir is None or not frontend_dir.is_dir():
        return [
            Finding(
                "frontend-build", "skip",
                f"frontend build not present ({frontend_dir}) — was not scanned"
                if frontend_dir is not None else
                "frontend build not present (frontend/dist) — was not scanned",
            )
        ]
    findings: list[Finding] = []
    files_scanned = 0
    for path in sorted(frontend_dir.rglob("*")):
        if not path.is_file():
            continue
        try:
            text = path.read_bytes().decode("utf-8", errors="replace")
        except OSError:
            continue
        files_scanned += 1
        for number, line in enumerate(text.splitlines(), start=1):
            lowered = line.casefold()
            if _PRIVATE_V4_RE.search(line):
                findings.append(
                    Finding(
                        "frontend-build", "fail",
                        f"{path.relative_to(frontend_dir)}:{number}: private/LAN IP "
                        "literal in the player build",
                    )
                )
            for match in _URL_RE.finditer(line):
                host = match.group(1)
                if _host_is_private(host):
                    findings.append(
                        Finding(
                            "frontend-build", "fail",
                            f"{path.relative_to(frontend_dir)}:{number}: http(s):// URL "
                            f"with a private host ({host}) in the player build",
                        )
                    )
            if _OLLAMA_PORT_TOKEN in line:
                findings.append(
                    Finding(
                        "frontend-build", "fail",
                        f"{path.relative_to(frontend_dir)}:{number}: Ollama port literal "
                        "(:11434) in the player build",
                    )
                )
            for token in _FRONTEND_DEV_HOST_TOKENS:
                if token.casefold() in lowered:
                    findings.append(
                        Finding(
                            "frontend-build", "fail",
                            f"{path.relative_to(frontend_dir)}:{number}: dev/loopback "
                            f"API base {token!r} in the player build (must be "
                            "same-origin /api/v1)",
                        )
                    )
            for name in _PROVIDER_ENV_NAMES:
                if name.casefold() in lowered:
                    findings.append(
                        Finding(
                            "frontend-build", "fail",
                            f"{path.relative_to(frontend_dir)}:{number}: provider config "
                            f"variable name {name!r} in the player build",
                        )
                    )
            for token in _DEBUG_AID_TOKENS:
                if token.casefold() in lowered:
                    findings.append(
                        Finding(
                            "frontend-build", "report",
                            f"{path.relative_to(frontend_dir)}:{number}: query-gated "
                            f"debug aid token {token!r} compiled into the build "
                            "(gated behind its exact query; frontend track owns it)",
                        )
                    )
    if not findings:
        findings.append(
            Finding(
                "frontend-build", "ok",
                f"frontend build clean ({files_scanned} files: no private host/IP/"
                "URL literal, no :11434, no provider config names, no "
                "localhost:8000/loopback API base)",
            )
        )
    return findings


def check_dockerignore(repo_root: Path) -> list[Finding]:
    """Phase20 PD-SEC-07: secret files must be excluded from the Docker context.

    Asserts the root ``.dockerignore`` exists and excludes the dotenv family
    (``.env`` / ``.env.*`` while re-including ``.env.example``), ``logs/`` and
    ``*.log``, runtime database files, ``tmp/``/``temp/`` and local Ollama
    config. An unexcluded ``.env`` is sent to remote Docker daemons / builders
    / build caches even when the Dockerfile never ``COPY``s it into the image.
    """
    dockerignore = repo_root / ".dockerignore"
    if not dockerignore.is_file():
        return [
            Finding(
                "dockerignore", "fail",
                ".dockerignore is missing at the repo root — .env could reach "
                "the Docker build context (PD-SEC-07)",
            )
        ]
    try:
        text = dockerignore.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [
            Finding("dockerignore", "fail", f".dockerignore cannot be read: {exc}")
        ]
    missing = [name for name, pattern in _DOCKERIGNORE_PATTERNS
               if not pattern.search(text)]
    if missing:
        return [
            Finding(
                "dockerignore", "fail",
                ".dockerignore is missing required exclusions: " + ", ".join(missing),
            )
        ]
    return [
        Finding(
            "dockerignore", "ok",
            ".dockerignore excludes .env / .env.* (keeps .env.example), logs, "
            "runtime DBs, tmp/temp and local Ollama config from the build context",
        )
    ]


# --------------------------------------------------------------------------- #
# Phase 21 F-04 — bounded container stdout logs in the PROD compose profile
# --------------------------------------------------------------------------- #

# The public services in ``docker-compose.prod.yml`` that write access logs to
# stdout (uvicorn access logs; Caddy access/error logs). Both MUST carry a
# bounded json-file rotation policy so container stdout never grows without
# limit. The backend's OWN application file logs (RotatingFileHandler,
# 5 MB x 3) are separate and stay as-is — this gate pins the container envelope.
#
# The parsing below is STDLIB-ONLY (the tool deliberately adds no undeclared
# dependency): the compose file is structurally scanned by indentation, and the
# logging block's option values are matched exactly (quoted or unquoted).
_COMPOSE_PROD_FILE = "docker-compose.prod.yml"
_BOUNDED_COMPOSE_SERVICES = ("procedural-detective", "caddy")
_BOUNDED_LOG_DRIVER = "json-file"
_BOUNDED_LOG_MAX_SIZE = "10m"
_BOUNDED_LOG_MAX_FILE = "5"

# The canonical bounded logging block, matched relative to a service block:
#   logging:
#     driver: json-file
#     options:
#       max-size: "10m"
#       max-file: "5"
# Values may appear quoted or unquoted (compose renderers normalize to unquoted).
_COMPOSE_LOGGING_BLOCK_RE = re.compile(
    r"^\s*logging:\s*$"
    r"\n\s+driver:\s+json-file\s*$"
    r"\n\s+options:\s*$"
    r"\n\s+max-size:\s+(?:\"10m\"|10m)\s*$"
    r"\n\s+max-file:\s+(?:\"5\"|5)\s*$",
    re.MULTILINE,
)


def _compose_service_blocks(text: str) -> dict[str, list[str]]:
    """``{service name: raw lines}`` of every top-level ``services.`` block.

    A minimal, dependency-free YAML-subset scanner: top-level keys sit at
    column 0; ``services`` at column 0 opens the map; service names are the
    column-2 keys (``  <name>:``); everything more-indented belongs to the
    current service until a column-2 key (or end of ``services``). Comments
    and blank lines are kept (harmless) so block boundaries stay reliable.
    """
    services: dict[str, list[str]] = {}
    in_services = False
    current: str | None = None
    for raw in text.splitlines():
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        content = raw.strip()
        if not in_services:
            if content == "services:":
                in_services = True
            continue
        if indent == 0 and not content.startswith("#"):
            break  # services map ended (next top-level key / end of file)
        if indent == 2 and content.endswith(":") and not content.startswith(("#", "-", "&", "*")):
            current = content[:-1]
            services.setdefault(current, [])
            continue
        if current is not None:
            services.setdefault(current, []).append(raw.rstrip())
    return services


def check_compose_logging_bounds(repo_root: Path) -> list[Finding]:
    """F-04: the PROD compose services carry a container logging bound.

    Asserts every public service (``procedural-detective`` and ``caddy``) in
    ``docker-compose.prod.yml`` declares ``logging.driver: json-file`` with
    ``max-size: "10m"`` / ``max-file: "5"`` options, so the container runtime
    rotates their stdout immediately instead of letting them grow unbounded.
    """
    compose = repo_root / _COMPOSE_PROD_FILE
    if not compose.is_file():
        return [
            Finding(
                "compose-logging-bounds", "skip",
                f"{_COMPOSE_PROD_FILE} not present in this document tree — "
                "the prod logging-bounds gate applies only when the production "
                "compose exists (deployment artifact check)",
            )
        ]
    try:
        text = compose.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return [
            Finding(
                "compose-logging-bounds", "fail",
                f"{_COMPOSE_PROD_FILE} cannot be read: {exc}",
            )
        ]
    services = _compose_service_blocks(text)
    problems: list[str] = []
    for name in _BOUNDED_COMPOSE_SERVICES:
        block = services.get(name)
        if block is None:
            problems.append(f"service {name!r} is missing from {_COMPOSE_PROD_FILE}")
            continue
        body = "\n".join(block)
        if not _COMPOSE_LOGGING_BLOCK_RE.search(body):
            problems.append(
                f"service {name!r} must carry logging.driver: "
                f"{_BOUNDED_LOG_DRIVER!r} with max-size "
                f"{_BOUNDED_LOG_MAX_SIZE!r} / max-file {_BOUNDED_LOG_MAX_FILE!r}"
            )
    if problems:
        return [
            Finding("compose-logging-bounds", "fail", "; ".join(problems))
        ]
    return [
        Finding(
            "compose-logging-bounds", "ok",
            f"{_COMPOSE_PROD_FILE}: procedural-detective + caddy bound stdout via "
            f"{_BOUNDED_LOG_DRIVER} ({_BOUNDED_LOG_MAX_SIZE} x {_BOUNDED_LOG_MAX_FILE} "
            "rotating files)",
        )
    ]


# --------------------------------------------------------------------------- #
# Phase 21B Finding 2/7 — DEV vs PROD env examples + effective prod preflight
# --------------------------------------------------------------------------- #

# The two documented template files. The DEV example is ENVIRONMENT=development
# with TRUST_PROXY=false; the PRODUCTION profile example is ENVIRONMENT=
# production with PD_DEV_TRACE=false and TRUST_PROXY=true (Caddy edge ONLY).
_ENV_EXAMPLE_DEV = ".env.example"
_ENV_EXAMPLE_PROD = ".env.production.example"

# Effective-config assertions for the shipped Caddy ingress profile. An
# operator .env that overrides ANY of these with a dev value FAILS the
# fail-closed preflight (Phase21B Finding 2/7).
_PROD_REQUIRED_ENVIRONMENT = "production"
_PROD_REQUIRED_PD_DEV_TRACE = "false"

def _parse_env_file(text: str) -> dict[str, str]:
    """Minimal stdlib-only dotenv reader used ONLY for the documented example
    files and the operator ``.env`` validation key set.

    Handles ``KEY=VALUE`` (optionally ``export ``-prefixed), quoted values and
    line/whole-line comments. Inline trailing comments are NOT stripped: the
    validation keys are booleans/numbers/domains that never carry them, and a
    value like an example URL must not be silently truncated. Unknown/invalid
    lines are skipped.
    """
    out: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key] = value
    return out


def _read_env_file(repo_root: Path, name: str) -> dict[str, str]:
    """Parse one example/template env file; {} when missing/unreadable."""
    path = repo_root / name
    if not path.is_file():
        return {}
    try:
        return _parse_env_file(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return {}


def check_prod_env_profile(repo_root: Path) -> list[Finding]:
    """Phase21B Finding 2 — the DEV / PROD env examples are a clean split.

    Asserts:
      - both ``.env.example`` and ``.env.production.example`` exist;
      - ``.env.example`` is the DEV profile (``ENVIRONMENT=development``,
        ``TRUST_PROXY=false``);
      - ``.env.production.example`` is the PRODUCTION profile
        (``ENVIRONMENT=production``, ``PD_DEV_TRACE=false``,
        ``TRUST_PROXY=true``) and never carries a dev-only value
        (``ENVIRONMENT=development`` / ``TRUST_PROXY=false`` / ``PD_DEV_TRACE=true``
        as an ACTIVE assignment).
    """
    problems: list[str] = []

    dev_path = repo_root / _ENV_EXAMPLE_DEV
    prod_path = repo_root / _ENV_EXAMPLE_PROD
    missing = [
        name for name, path in (
            (_ENV_EXAMPLE_DEV, dev_path),
            (_ENV_EXAMPLE_PROD, prod_path),
        )
        if not path.is_file()
    ]
    if missing:
        return [
            Finding(
                "prod-env-profile", "skip",
                "not present in this document tree: " + ", ".join(missing)
                + " — the DEV/PROD env-profile split check applies only when "
                "the production deployment templates exist (deployment "
                "artifact check)",
            )
        ]
    dev = _read_env_file(repo_root, _ENV_EXAMPLE_DEV)
    prod = _read_env_file(repo_root, _ENV_EXAMPLE_PROD)

    if dev_path.is_file() and dev:
        if dev.get("ENVIRONMENT") != "development":
            problems.append(
                f"{_ENV_EXAMPLE_DEV} must be the DEV profile: active "
                "ENVIRONMENT=development"
            )
        if dev.get("TRUST_PROXY") != "false":
            problems.append(
                f"{_ENV_EXAMPLE_DEV} must be the DEV profile: active "
                "TRUST_PROXY=false"
            )
    if prod_path.is_file() and prod:
        if prod.get("ENVIRONMENT") != "production":
            problems.append(
                f"{_ENV_EXAMPLE_PROD}: effective ENVIRONMENT must be "
                "'production', got {prod.get('ENVIRONMENT')!r}"
            )
        if prod.get("PD_DEV_TRACE") != "false":
            problems.append(
                f"{_ENV_EXAMPLE_PROD}: effective PD_DEV_TRACE must be 'false', "
                f"got {prod.get('PD_DEV_TRACE')!r}"
            )
        if prod.get("TRUST_PROXY") != "true":
            problems.append(
                f"{_ENV_EXAMPLE_PROD}: effective TRUST_PROXY must be 'true' for "
                "the shipped Caddy edge (NOT portable to unverified ingresses; "
                f"see the comment in the file), got {prod.get('TRUST_PROXY')!r}"
            )
        for key, dev_only in (
            ("ENVIRONMENT", "development"),
            ("TRUST_PROXY", "false"),
            ("PD_DEV_TRACE", "true"),
        ):
            if prod.get(key, "").lower() == dev_only:
                problems.append(
                    f"{_ENV_EXAMPLE_PROD} carries a dev-only value: {key}={prod[key]} "
                    "(a production example must never set a development value)"
                )
    if problems:
        return [Finding("prod-env-profile", "fail", "; ".join(problems))]
    return [
        Finding(
            "prod-env-profile", "ok",
            f"{_ENV_EXAMPLE_DEV} is the DEV profile (ENVIRONMENT=development, "
            "TRUST_PROXY=false) and .env.production.example is the PRODUCTION "
            "profile (ENVIRONMENT=production, PD_DEV_TRACE=false, TRUST_PROXY=true "
            "behind the Caddy edge), with no dev-only value in the production "
            "example",
        )
    ]


class _ComposeRenderError(RuntimeError):
    """Sanitized failure from the authoritative Docker Compose render."""


def _render_prod_compose_config(
    repo_root: Path,
    compose: Path,
    *,
    env_file: Path | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> dict[str, object]:
    """Return Docker Compose's effective production model as JSON.

    Compose itself remains the sole authority for interpolation and precedence:
    the command inherits the current process environment and uses the deployment
    project directory. A working-tree ``.env`` is selected by Compose in the
    normal way. A non-default startup env file is used only when the caller
    explicitly supplies the same ``--env-file`` option used for deployment.

    Errors are deliberately sanitized because the rendered model may contain
    provider credentials from the service ``env_file``.
    """
    invoke = runner or subprocess.run
    command = [
        "docker",
        "compose",
        "--project-directory",
        str(repo_root),
    ]
    if env_file is not None:
        command.extend(["--env-file", str(env_file.resolve())])
    command.extend(["-f", str(compose), "config", "--format", "json"])

    try:
        completed = invoke(
            command,
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise _ComposeRenderError(
            "docker compose config could not be executed"
        ) from exc
    if completed.returncode != 0:
        raise _ComposeRenderError(
            "docker compose config exited non-zero"
        )
    try:
        rendered = json.loads(completed.stdout)
    except (TypeError, json.JSONDecodeError) as exc:
        raise _ComposeRenderError(
            "docker compose config did not return valid JSON"
        ) from exc
    if not isinstance(rendered, dict):
        raise _ComposeRenderError(
            "docker compose config returned an unexpected JSON document"
        )
    return rendered


def _rendered_service(
    rendered: dict[str, object], service_name: str
) -> dict[str, object] | None:
    services = rendered.get("services")
    if not isinstance(services, dict):
        return None
    service = services.get(service_name)
    return service if isinstance(service, dict) else None


def _rendered_environment(service: dict[str, object]) -> dict[str, str]:
    raw = service.get("environment")
    if not isinstance(raw, dict):
        return {}
    return {
        str(key): "" if value is None else str(value)
        for key, value in raw.items()
    }


def _rendered_log_bounds_problem(
    service_name: str, service: dict[str, object]
) -> str | None:
    logging = service.get("logging")
    if not isinstance(logging, dict):
        return f"service {service_name!r} has no rendered logging policy"
    options = logging.get("options")
    if (
        logging.get("driver") != _BOUNDED_LOG_DRIVER
        or not isinstance(options, dict)
        or str(options.get("max-size")) != _BOUNDED_LOG_MAX_SIZE
        or str(options.get("max-file")) != _BOUNDED_LOG_MAX_FILE
    ):
        return (
            f"service {service_name!r} must render logging.driver "
            f"{_BOUNDED_LOG_DRIVER!r} with max-size {_BOUNDED_LOG_MAX_SIZE!r} "
            f"/ max-file {_BOUNDED_LOG_MAX_FILE!r}"
        )
    return None


def _rendered_port_targets(service: dict[str, object]) -> list[str]:
    raw_ports = service.get("ports")
    if not isinstance(raw_ports, list):
        return []
    targets: list[str] = []
    for entry in raw_ports:
        if isinstance(entry, dict):
            target = entry.get("target")
            published = entry.get("published")
            if target is not None:
                targets.append(str(target))
            if published is not None:
                targets.append(str(published))
        else:
            targets.append(str(entry))
    return targets


def _has_private_data_volume(
    rendered: dict[str, object], backend: dict[str, object]
) -> bool:
    raw_volumes = backend.get("volumes")
    declared = rendered.get("volumes")
    if not isinstance(raw_volumes, list) or not isinstance(declared, dict):
        return False
    for mount in raw_volumes:
        if not isinstance(mount, dict):
            continue
        source = mount.get("source")
        if (
            mount.get("type") == "volume"
            and mount.get("target") == "/data"
            and isinstance(source, str)
            and source in declared
        ):
            return True
    return False


_PUBLIC_HOST_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_RESERVED_PUBLIC_HOST_SUFFIXES = (
    "example",
    "invalid",
    "test",
    "example.com",
    "example.net",
    "example.org",
)


def _caddy_domain_problem(value: str) -> tuple[str | None, bool]:
    """Return ``(sanitized_problem, explicit_local_smoke)`` for a hostname.

    This is deliberately a syntax/reserved-name gate. DNS resolution,
    certificate issuance and operator ownership remain part of the public TLS
    smoke and are not inferred here.
    """
    domain = value.strip().lower().rstrip(".")
    if not domain:
        return "CADDY_DOMAIN is empty", False

    if (
        domain == "localhost"
        or domain.endswith(".localhost")
        or domain.endswith(".local")
    ):
        return "CADDY_DOMAIN is an explicit local-smoke hostname", True

    try:
        ipaddress.ip_address(domain)
    except ValueError:
        pass
    else:
        return "CADDY_DOMAIN is an IP address, not a public hostname", False

    if len(domain) > 253 or "." not in domain:
        return "CADDY_DOMAIN is not a fully qualified public hostname", False

    for suffix in _RESERVED_PUBLIC_HOST_SUFFIXES:
        if domain == suffix or domain.endswith(f".{suffix}"):
            return "CADDY_DOMAIN uses a reserved placeholder domain", False

    if any(not _PUBLIC_HOST_LABEL_RE.fullmatch(label) for label in domain.split(".")):
        return "CADDY_DOMAIN is not a syntactically valid public hostname", False

    return None, False


def check_prod_effective_config(
    repo_root: Path,
    *,
    allow_local: bool = False,
    ingress_profile: str = "caddy",
    compose_path: Path | None = None,
    client_ts_path: Path | None = None,
    caddyfile_path: Path | None = None,
    frontend_dir: Path | None = None,
    compose_env_file: Path | None = None,
    compose_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
) -> list[Finding]:
    """Fail-closed preflight of Docker Compose's effective production model.

    ``docker compose config --format json`` is authoritative and inherits the
    current shell environment. The parsed render, rather than a second Compose
    interpolation implementation, is validated. Asserts:

      - ``ENVIRONMENT=production`` and ``PD_DEV_TRACE=false``; the Caddy profile
        requires ``TRUST_PROXY=true`` while an unverified alternate ingress
        requires the safe ``TRUST_PROXY=false`` default;
      - P-02 timeout envelope: backend deadline
        (``CASE_GENERATION_DEADLINE_SECONDS`` effective) < FRONTEND request
        timeout (``frontend/src/api/client.ts`` ``REQUEST_TIMEOUT_MS``) < proxy
        timeout (``docker/Caddyfile`` ``response_header_timeout``);
      - rendered Docker log bounds json-file 10m x 5 on BOTH public services;
      - the backend port is NEVER publicly published, no service publishes the
        Ollama port 11434, and the backend mounts a declared volume at ``/data``;
      - ``CADDY_DOMAIN`` is a plausible non-reserved FQDN; explicit local names
        are accepted only as REPORTs with ``allow_local``. DNS, ownership and
        certificate issuance remain part of the separate public TLS smoke;
      - the production frontend bundle uses the same-origin API (no
        ``localhost:8000`` / ``127.0.0.1`` embed — reuses the bundle scan).

    Never prints a configured value — only key names and verdicts.
    """
    if ingress_profile not in {"caddy", "alternate"}:
        return [
            Finding(
                "prod-effective-config", "fail",
                "unknown ingress profile; choose 'caddy' or 'alternate' "
                "(fail closed)",
            )
        ]

    compose = (compose_path or (repo_root / _COMPOSE_PROD_FILE)).resolve()
    if not compose.is_file():
        return [
            Finding(
                "prod-effective-config", "skip",
                f"{_COMPOSE_PROD_FILE} not present in this document tree — "
                "the effective-production preflight applies only when the "
                "production compose exists (deployment artifact check)",
            )
        ]
    try:
        rendered = _render_prod_compose_config(
            repo_root.resolve(), compose,
            env_file=compose_env_file,
            runner=compose_runner,
        )
    except _ComposeRenderError as exc:
        return [
            Finding(
                "prod-effective-config", "fail",
                f"{exc}; effective production configuration was not validated "
                "(fail closed)",
            )
        ]

    backend = _rendered_service(rendered, "procedural-detective")
    caddy = _rendered_service(rendered, "caddy")
    if backend is None or caddy is None:
        missing = "backend" if backend is None else "Caddy"
        return [
            Finding(
                "prod-effective-config", "fail",
                f"rendered production configuration is missing the {missing} "
                "service (fail closed)",
            )
        ]
    backend_env = _rendered_environment(backend)
    caddy_env = _rendered_environment(caddy)

    # The backend Settings default is 60 seconds when the rendered container
    # environment does not explicitly set the deadline.
    deadline_raw = backend_env.get("CASE_GENERATION_DEADLINE_SECONDS") or "60"
    try:
        deadline = int(float(deadline_raw))
    except (TypeError, ValueError):
        deadline = -1  # unparseable -> fail-closed below

    client_ts = client_ts_path or (repo_root / "frontend" / "src" / "api" / "client.ts")
    frontend_timeout = 0
    if client_ts.is_file():
        try:
            m = re.search(r"REQUEST_TIMEOUT_MS\s*=\s*(\d+)",
                          client_ts.read_text(encoding="utf-8", errors="replace"))
            if m:
                frontend_timeout = int(m.group(1)) // 1000
        except OSError:
            pass

    caddyfile = caddyfile_path or (repo_root / "docker" / "Caddyfile")
    proxy_timeout = 0
    if caddyfile.is_file():
        try:
            m = re.search(r"response_header_timeout\s+(\d+)\s*s",
                          caddyfile.read_text(encoding="utf-8", errors="replace"))
            if m:
                proxy_timeout = int(m.group(1))
        except OSError:
            pass

    findings: list[Finding] = []

    # 1. Production markers from the authoritative Compose render.
    if backend_env.get("ENVIRONMENT", "") != _PROD_REQUIRED_ENVIRONMENT:
        findings.append(
            Finding(
                "prod-effective-config", "fail",
                "rendered ENVIRONMENT is not 'production'; the current shell, "
                ".env and Compose inputs must produce ENVIRONMENT=production",
            )
        )
    if backend_env.get("PD_DEV_TRACE", "").lower() != _PROD_REQUIRED_PD_DEV_TRACE:
        findings.append(
            Finding(
                "prod-effective-config", "fail",
                "rendered PD_DEV_TRACE is not 'false'; dev tracing must never "
                "run in a public build (PD-SEC-06)",
            )
        )
    expected_trust_proxy = "true" if ingress_profile == "caddy" else "false"
    if backend_env.get("TRUST_PROXY", "").lower() != expected_trust_proxy:
        profile_reason = (
            "the shipped Caddy edge requires TRUST_PROXY=true so clients do not "
            "collapse into one shared rate-limit bucket"
            if ingress_profile == "caddy"
            else "an alternate ingress must default to TRUST_PROXY=false until "
            "its forwarded-header behavior is independently verified"
        )
        findings.append(
            Finding(
                "prod-effective-config", "fail",
                f"rendered TRUST_PROXY is not '{expected_trust_proxy}' for the "
                f"{ingress_profile} ingress profile; {profile_reason} "
                "(PD-SEC-02 / Phase21 P-01)",
            )
        )

    # 2. P-02 timeout envelope: deadline < frontend < proxy.
    if deadline < 0:
        findings.append(
            Finding(
                "prod-effective-config", "fail",
                "rendered CASE_GENERATION_DEADLINE_SECONDS is unparseable; "
                "the P-02 envelope cannot be verified "
                "(fail closed)",
            )
        )
    elif frontend_timeout <= 0:
        findings.append(
            Finding(
                "prod-effective-config", "fail",
                "frontend request timeout could not be read from "
                "frontend/src/api/client.ts (REQUEST_TIMEOUT_MS) — envelope "
                "cannot be verified (fail closed)",
            )
        )
    elif deadline >= frontend_timeout:
        findings.append(
            Finding(
                "prod-effective-config", "fail",
                f"backend generation deadline ({deadline}s) is NOT below the "
                f"frontend request timeout ({frontend_timeout}s) — the browser "
                "can abort a request the backend still allows (P-02 envelope)",
            )
        )
    if ingress_profile == "alternate":
        findings.append(
            Finding(
                "prod-effective-config", "fail",
                "alternate ingress cannot receive a ready-to-host verdict from "
                "the shipped full-stack Compose configuration because it still "
                "publishes Caddy and does not describe the exact selected-service "
                "startup or external edge; TRUST_PROXY remains false, and the "
                "alternate deployment requires separate verification (fail closed)",
            )
        )
    elif proxy_timeout <= 0:
        findings.append(
            Finding(
                "prod-effective-config", "fail",
                "reverse-proxy timeout could not be read from docker/Caddyfile "
                "(response_header_timeout) — envelope cannot be verified "
                "(fail closed)",
            )
        )
    elif frontend_timeout > 0 and proxy_timeout <= frontend_timeout:
        findings.append(
            Finding(
                "prod-effective-config", "fail",
                f"reverse-proxy timeout ({proxy_timeout}s) is NOT above the "
                f"frontend request timeout ({frontend_timeout}s) — the TLS edge "
                "can abort before the browser (P-02 envelope)",
            )
        )

    # 3. Docker log bounds on both public services (F-04), from the render.
    for service_name, service in (
        ("procedural-detective", backend),
        ("caddy", caddy),
    ):
        problem = _rendered_log_bounds_problem(service_name, service)
        if problem:
            findings.append(Finding("prod-effective-config", "fail", problem))

    # 4. Backend port never publicly published + Ollama port never published.
    if _rendered_port_targets(backend):
        findings.append(
            Finding(
                "prod-effective-config", "fail",
                "the rendered backend service publishes a host port (`ports:`); "
                "production must expose :8000 privately only (PD-SEC-03)",
            )
        )
    exposes = backend.get("expose")
    if not isinstance(exposes, list) or "8000" not in {str(item) for item in exposes}:
        findings.append(
            Finding(
                "prod-effective-config", "fail",
                "the rendered backend service does not expose private port 8000",
            )
        )
    services = rendered.get("services")
    if isinstance(services, dict):
        for service in services.values():
            if not isinstance(service, dict):
                continue
            if "11434" in _rendered_port_targets(service):
                findings.append(
                    Finding(
                        "prod-effective-config", "fail",
                        "the rendered configuration publishes the Ollama port "
                        "11434; Ollama must stay private (PD-SEC-03)",
                    )
                )
                break
    if not _has_private_data_volume(rendered, backend):
        findings.append(
            Finding(
                "prod-effective-config", "fail",
                "the rendered backend has no declared named volume mounted at "
                "/data; production persistence cannot be verified",
            )
        )

    # 5. CADDY_DOMAIN syntax/reserved-name gate (ready-to-host verdict).
    domain_problem, explicit_local_smoke = _caddy_domain_problem(
        caddy_env.get("CADDY_DOMAIN") or ""
    )
    if ingress_profile == "caddy" and domain_problem:
        if allow_local and explicit_local_smoke:
            findings.append(
                Finding(
                    "prod-effective-config", "report",
                    f"{domain_problem} — accepted only for local smoke; set a real "
                    "public CADDY_DOMAIN before ANY public hosting (run "
                    "python -m tools.prod_preflight for the strict ready-to-host "
                    "verdict)",
                )
            )
        else:
            findings.append(
                Finding(
                    "prod-effective-config", "fail",
                    f"{domain_problem} — the ready-to-host verdict FAILS until "
                    "CADDY_DOMAIN "
                    "is a real public domain in the actual startup .env (or the "
                    "same explicit --env-file used by preflight and startup); pass "
                    "--allow-local only with an explicit local-smoke hostname. "
                    "DNS, ownership and certificate issuance are verified by the "
                    "separate public TLS smoke",
                )
            )

    # 6. Production frontend uses the same-origin API (bundle scan reuse).
    dist = frontend_dir or (repo_root / "frontend" / "dist")
    if dist.is_dir():
        bundle_fails = [
            f for f in scan_frontend_build(dist) if f.severity == "fail"
        ]
        if bundle_fails:
            findings.append(
                Finding(
                    "prod-effective-config", "fail",
                    "the production frontend bundle is not same-origin/clean: "
                    + "; ".join(f.message for f in bundle_fails[:4])
                    + (f" (+{len(bundle_fails) - 4} more)" if len(bundle_fails) > 4 else ""),
                )
            )
    else:
        findings.append(
            Finding(
                "prod-effective-config", "report",
                "frontend/dist not present — the same-origin bundle scan is "
                "skipped (release_check flags it when the build exists)",
            )
        )

    if not any(f.severity == "fail" for f in findings):
        findings.append(
            Finding(
                "prod-effective-config", "ok",
                "effective production configuration validated (fail-closed): "
                "ENVIRONMENT=production, PD_DEV_TRACE=false, "
                f"TRUST_PROXY={expected_trust_proxy} ({ingress_profile} ingress), "
                f"deadline {deadline}s < frontend {frontend_timeout}s "
                f"< proxy {proxy_timeout}s, log bounds present, backend private, "
                "no published Ollama port, persistent data volume present, "
                "same-origin production bundle"
            )
        )
    return findings


# --------------------------------------------------------------------------- #
# orchestration
# --------------------------------------------------------------------------- #


def run_all(
    repo_root: Path,
    *,
    allow_hosted: bool = False,
    frontend_dir: Path | None = None,
    prod_allow_local: bool = True,
) -> list[Finding]:
    """Every release check; the CLI exits 1 when any finding has severity fail.

    ``prod_allow_local`` (default True) treats an explicit local hostname as
    the documented local-smoke REPORT; the STRICT
    ready-to-host verdict lives in ``python -m tools.prod_preflight`` (which
    runs the same ``check_prod_effective_config`` with ``allow_local=False``).
    """
    findings: list[Finding] = []
    tracked = git_tracked_files(repo_root)
    findings.extend(scan_placeholders(repo_root, allow_hosted=allow_hosted))
    findings.extend(check_third_party(repo_root))
    findings.extend(check_tracked_secrets(tracked))
    findings.extend(scan_private_endpoints(repo_root, tracked))
    findings.extend(check_dockerignore(repo_root))
    findings.extend(check_compose_logging_bounds(repo_root))
    findings.extend(check_prod_env_profile(repo_root))
    findings.extend(
        check_prod_effective_config(repo_root, allow_local=prod_allow_local)
    )
    findings.extend(scan_frontend_build(frontend_dir))
    return findings


def _print_findings(findings: list[Finding]) -> None:
    for finding in findings:
        print(finding.render())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="release_check",
        description=(
            "Release-hygiene check: placeholders, THIRD_PARTY.md, tracked "
            ".env/logs/dbs, private endpoint leakage, Docker build-context "
            "hygiene (.dockerignore), PROD compose logging bounds (F-04), the "
            "DEV/PROD env-example split (Phase21B) + effective PROD "
            "configuration preflight (Phase21B; strict ready-to-host verdict "
            "in `python -m tools.prod_preflight`) and the frontend build."
        ),
    )
    parser.add_argument(
        "--allow-hosted-placeholders",
        action="store_true",
        help=(
            "Report hosting/video-dependent placeholders (\"lands here after\", "
            "demo/video URLs) separately instead of failing — Phase 18A "
            "task 9 allows those to remain while hosting/video is pending."
        ),
    )
    parser.add_argument(
        "--frontend-dir",
        type=Path,
        default=None,
        help=(
            "Explicit frontend build directory to scan (default: "
            "<repo>/frontend/dist; skipped cleanly when absent)."
        ),
    )
    args = parser.parse_args(argv)

    frontend_dir = args.frontend_dir
    if frontend_dir is not None:
        frontend_dir = frontend_dir.resolve()
    else:
        frontend_dir = REPO_ROOT / "frontend" / "dist"

    findings = run_all(
        REPO_ROOT, allow_hosted=args.allow_hosted_placeholders, frontend_dir=frontend_dir
    )
    _print_findings(findings)
    failures = [f for f in findings if f.severity == "fail"]
    reports = [f for f in findings if f.severity == "report"]
    print()
    if failures:
        print(f"release check: {len(findings)} checks run, "
              f"{len(failures)} FAILING (first: {failures[0].check})")
        if reports:
            print(f"  {len(reports)} REPORT-only items (hosting/video / gated debug aids)")
        return 1
    print(f"release check: ALL {len(findings)} checks OK")
    if reports:
        print(f"  {len(reports)} REPORT-only items (hosting/video / gated debug aids)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
