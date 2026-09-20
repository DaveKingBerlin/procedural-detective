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
     and hermetic test vectors (``backend/tests/``, ``e2e/probes/``, frontend
     ``*.test.*`` / ``*.spec.*``) are documented exceptions — they are the
     allowlist definition and the environment-matrix regression tests.
  5. FRONTEND BUILD OUTPUT (frontend/dist, or ``--frontend-dir``) — the same
     private host/IP/URL literal scan plus the ``11434`` Ollama port and the
     provider configuration variable NAMES (``OLLAMA_BASE_URL`` / ``LLM_API_KEY``
     / ...), which never belong in a player-side build. The build directory is
     git-ignored; the check skips cleanly when it does not exist.

Exit code: 0 ONLY when every non-optional check passes (with
``--allow-hosted-placeholders``, hosting/video-only placeholders and
query-gated debug-aid tokens are REPORTED separately and never fail the run).

The tool never reads the working-tree ``.env`` (operator secrets stay
operator-local) and never prints any configured endpoint or credential.

Usage:
    python -m tools.release_check [--allow-hosted-placeholders] [--frontend-dir PATH]
"""

from __future__ import annotations

import argparse
import re
import subprocess
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


def _host_is_private(host: str) -> bool:
    """True for a URL host that is an RFC1918 private address.

    ``host`` is the raw netloc captured by ``_URL_RE`` (may carry userinfo
    and/or a port). Bracketed IPv6 literals (``[::1]``) and the sanctioned
    loopback/docker names are allowed everywhere.
    """
    netloc = host.split("@")[-1]  # strip any embedded userinfo
    if netloc.startswith("["):
        hostname = netloc.split("]", 1)[0].strip("[]")
    else:
        hostname = netloc.split(":", 1)[0]
    lowered = hostname.lower()
    if lowered in _ALLOWED_URL_HOSTS or lowered == "::1":
        return False
    return bool(_PRIVATE_V4_RE.search(lowered))


# --------------------------------------------------------------------------- #
# git tracked-file access
# --------------------------------------------------------------------------- #

_TRACKED_SECRET_NAMES = (".env",)
_TRACKED_SECRET_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".log")
_TRACKED_SECRET_DIRS = ("logs",)


def git_tracked_files(repo_root: Path) -> list[str] | None:
    """``git ls-files`` relative paths, or None when git/the repo is unusable.

    Only TRACKED files are ever scanned: the working-tree ``.env`` (operator
    secret) is untracked and intentionally never read.
    """
    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None
    if result.returncode != 0:
        return None
    return [line for line in result.stdout.splitlines() if line.strip()]


def tracked_secret_entries(tracked: list[str]) -> list[str]:
    """Tracked entries that must never exist: .env / logs/ / *.db / *.sqlite / *.log."""
    bad: list[str] = []
    for rel in tracked:
        name = rel.rsplit("/", 1)[-1]
        if name in _TRACKED_SECRET_NAMES:
            bad.append(rel)
            continue
        parts = rel.split("/")
        if any(part in _TRACKED_SECRET_DIRS for part in parts):
            bad.append(rel)
            continue
        lower = name.lower()
        if lower.endswith(_TRACKED_SECRET_SUFFIXES):
            bad.append(rel)
    return sorted(bad)


# --------------------------------------------------------------------------- #
# scan surface for the private-endpoint check
# --------------------------------------------------------------------------- #

# The single sanctioned example location (private-IP examples may live there).
_SANCTIONED_EXAMPLE_FILES = (".env.example",)

# Hermetic test vectors / QA contract audits: they MUST legitimately exercise
# private/LAN hosts (the config validator accepts them), so they are the
# documented exceptions for the *tracked-tree* leak scan.
_SANCTIONED_TREE_PREFIXES = ("backend/tests/", "e2e/probes/")

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
                "URL literal, no :11434, no provider config names)",
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
) -> list[Finding]:
    """Every release check; the CLI exits 1 when any finding has severity fail."""
    findings: list[Finding] = []
    tracked = git_tracked_files(repo_root)
    findings.extend(scan_placeholders(repo_root, allow_hosted=allow_hosted))
    findings.extend(check_third_party(repo_root))
    findings.extend(check_tracked_secrets(tracked))
    findings.extend(scan_private_endpoints(repo_root, tracked))
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
            ".env/logs/dbs, private endpoint leakage and the frontend build."
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