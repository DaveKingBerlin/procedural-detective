#!/usr/bin/env python3
"""Phase 49 unified release assembly (Phase49.md Sec.19/Sec.20/Sec.31/Sec.36/Sec.38).

Collects and validates the already-built Windows ZIP and Linux tar.gz
artifacts, asserts version consistency against the SINGLE canonical bridge
version source, scans the RAW archive bytes (the container, the ZIP comment and
any post-EOCD trailing bytes) plus every member's raw bytes (binary-safe, with a
bounded zlib inflate) for accidental secrets, then generates ``SHA256SUMS.txt``
and ``release-manifest.json`` from the final archive bytes and copies the
manifest to the website (the only generated file allowed in Git).

``--scan-only`` exposes the same member-validation + secret gate as a standalone
entry point (used by ``build-bridge-windows.ps1``) so no platform build can emit
an ungated archive.

It builds nothing and publishes nothing: the Windows binary must be produced
natively on the Dev-Box (``build-bridge-windows.ps1``) and the Linux binary in
Docker (``build-bridge-linux.sh``) before this runs.

Never prints a matched secret value. Fails closed on any credential/secret-marker
hit (including bare Sec.31 markers on the raw surfaces).

Usage::

    python tools/build_release.py                       # canonical dist dir
    python tools/build_release.py --allow-dirty         # development override
    python tools/build_release.py --windows-zip PATH --linux-tar PATH --out DIR

Exit codes: 0 = assembled, non-zero = any failure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform as platform_mod
import re
import subprocess
import sys
import tarfile
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
INIT_PY = REPO_ROOT / "bridge" / "pd_ollama_bridge" / "__init__.py"
FRONTEND_MANIFEST = REPO_ROOT / "frontend" / "public" / "releases" / "release-manifest.json"

VERSION_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"\s*$', re.MULTILINE)

WINDOWS = "windows"
LINUX = "linux"

ARCHIVE_SPEC: dict[str, dict[str, Any]] = {
    WINDOWS: {
        "arch": "x64",
        "ext": ".zip",
        "binary": "pd-ollama-bridge.exe",
        "required": {
            "pd-ollama-bridge.exe",
            "QUICKSTART.md",
            "LICENSE",
            "THIRD_PARTY_NOTICES.txt",
        },
    },
    LINUX: {
        "arch": "x64",
        "ext": ".tar.gz",
        "binary": "pd-ollama-bridge",
        "required": {
            "pd-ollama-bridge",
            "QUICKSTART.md",
            "LICENSE",
            "THIRD_PARTY_NOTICES.txt",
        },
    },
}

# --------------------------------------------------------------------------- #
# Secret / integrity scan (Phase 49 Sec.31).
#
# CREDENTIAL patterns are value-bearing credential material (or a personal
# filesystem path): a hit FAILS the release (fail closed).
# MARKER patterns are the bare keywords from Sec.31: a hit is REPORTED as a
# warning for maintainer review but does not fail, because the bare word
# (e.g. "bearer" in a docstring) is not itself a secret. Matched values are
# NEVER printed -- only the label, file and byte offset.
# --------------------------------------------------------------------------- #
CREDENTIAL_PATTERNS: list[tuple[str, re.Pattern[bytes]]] = [
    ("openrouter-key", re.compile(rb"sk-or-v1-[A-Za-z0-9]{16,}")),
    ("google-api-key", re.compile(rb"AIza[0-9A-Za-z_\-]{35}")),
    ("private-key-header", re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")),
    (
        "authorization-bearer-token",
        re.compile(rb"Authorization:\s*Bearer\s+\S{8,}", re.IGNORECASE),
    ),
    (
        "secret-assignment",
        re.compile(
            rb"(?:API_KEY|SECRET|ACCESS_TOKEN|AUTH_TOKEN|PASSWORD)"
            rb"\s*[=:]\s*[\"']?[A-Za-z0-9_\-]{16,}"
        ),
    ),
    ("personal-windows-path", re.compile(rb"C:\\Users\\[A-Za-z0-9._\-]+")),
    ("personal-unix-home", re.compile(rb"(?:/home|/Users)/[A-Za-z0-9._\-]+")),
]

MARKER_PATTERNS: list[tuple[str, re.Pattern[bytes]]] = [
    ("OPENROUTER", re.compile(rb"OPENROUTER", re.IGNORECASE)),
    ("GEMINI", re.compile(rb"GEMINI", re.IGNORECASE)),
    ("API_KEY", re.compile(rb"API_KEY", re.IGNORECASE)),
    ("Authorization:", re.compile(rb"Authorization:", re.IGNORECASE)),
    ("Bearer", re.compile(rb"Bearer", re.IGNORECASE)),
    ("PRIVATE KEY", re.compile(rb"PRIVATE KEY", re.IGNORECASE)),
]

Finding = dict[str, Any]


def fail(message: str) -> "None":
    print(f"\nRELEASE ASSEMBLY FAILED: {message}", file=sys.stderr)
    sys.exit(1)


def bold(message: str) -> None:
    print(f"\n==> {message}")


# --------------------------------------------------------------------------- #
# Canonical version + provenance
# --------------------------------------------------------------------------- #
def resolve_canonical_version() -> str:
    if not INIT_PY.is_file():
        fail(f"canonical version source not found: {INIT_PY}")
    match = VERSION_RE.search(INIT_PY.read_text(encoding="utf-8"))
    if match is None:
        fail(f"could not parse __version__ from {INIT_PY}")
    return match.group(1)


def git_output(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(REPO_ROOT), *args],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def git_provenance() -> dict[str, Any]:
    full = (git_output("rev-parse", "HEAD") or "").strip() or None
    short = (git_output("rev-parse", "--short", "HEAD") or "").strip() or None
    status = git_output("status", "--porcelain")
    dirty = bool(status and status.strip())
    return {"gitCommitFull": full, "gitCommitShort": short, "dirty": dirty}


# --------------------------------------------------------------------------- #
# Archive validation + scanning
# --------------------------------------------------------------------------- #
def _canonical_member_name(name: str) -> str:
    """Normalise an archive member path for safety / exact-set comparison.

    Backslashes are folded to ``/`` so a Windows-authored ``sub\\file`` member is
    treated identically to ``sub/file``.
    """
    return name.replace("\\", "/")


def _reject_unsafe_member(name: str) -> None:
    """Reject absolute / drive-letter / ``..`` / empty member names.

    The check runs on the FULL member name (never the basename) so a
    ``..\\..\\..\\x.exe`` or ``/abs/x.exe`` member cannot be laundered into a
    benign-looking basename (ADV-49-002).
    """
    normalized = _canonical_member_name(name)
    if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized):
        fail(f"unsafe absolute path in archive: {name!r}")
    if ".." in normalized.split("/"):
        fail(f"path traversal in archive member: {name!r}")
    if normalized in ("", "."):
        fail(f"empty archive member name: {name!r}")


def _require_exact_members(
    archive_label: str, names: list[str], required: set[str]
) -> None:
    """The archive must contain EXACTLY the required root-level member set."""
    got = set(names)
    if len(names) != len(got):
        fail(f"{archive_label}: duplicate archive member names")
    missing = required - got
    extra = got - required
    if missing:
        fail(f"{archive_label}: missing required members: {sorted(missing)}")
    if extra:
        fail(f"{archive_label}: unexpected members: {sorted(extra)}")


def scan_blob(
    file_name: str, data: bytes, findings: list[Finding], *, strict: bool = False
) -> None:
    """Regex-scan RAW bytes (binary-safe) for credential material + markers.

    ``strict`` escalates a bare MARKER hit (e.g. the literal word ``OPENROUTER``)
    to a *failing* finding: used on the raw-archive / comment / post-EOCD / member
    byte surfaces where ANY secret marker is anomalous. Matched VALUES are NEVER
    recorded or printed -- only the label, the surface name and the byte offset.
    """
    for label, pattern in CREDENTIAL_PATTERNS:
        for match in pattern.finditer(data):
            findings.append(
                {
                    "severity": "credential",
                    "pattern": label,
                    "file": file_name,
                    "offset": match.start(),
                }
            )
    for label, pattern in MARKER_PATTERNS:
        for match in pattern.finditer(data):
            findings.append(
                {
                    "severity": "credential" if strict else "marker",
                    "pattern": label,
                    "file": file_name,
                    "offset": match.start(),
                }
            )


def _scan_bounded_zlib(
    file_name: str, data: bytes, findings: list[Finding], cap: int = 8 << 20
) -> None:
    """Boundedly inflate a member that is a SINGLE zlib stream and scan the first
    ``cap`` bytes of the result.

    This catches a compressed payload smuggled inside a member without ever
    decompressing a whole PyInstaller PYZ: a real executable is a PE image (not a
    bare zlib stream), so ``decompressobj`` raises immediately and the raw byte
    scan above is the only surface. Output is hard-capped to keep memory and time
    bounded.
    """
    if not data:
        return
    try:
        inflated = zlib.decompressobj().decompress(data, cap)
    except zlib.error:
        return
    if inflated:
        scan_blob(f"{file_name}#inflated", inflated, findings, strict=True)


def _zip_post_eocd_bytes(data: bytes) -> bytes:
    """Return bytes appended AFTER the final ZIP End-Of-Central-Directory record.

    A well-formed archive has none; anything here is an out-of-band overlay that
    is scanned explicitly (ADV-49-001).
    """
    index = data.rfind(b"PK\x05\x06")
    if index < 0:
        return b""
    eocd = data[index : index + 22]
    if len(eocd) < 22:
        return b""
    comment_len = int.from_bytes(eocd[20:22], "little")
    end = index + 22 + comment_len
    return data[end:] if end < len(data) else b""


def inspect_windows_zip(path: Path, version: str, findings: list[Finding]) -> None:
    spec = ARCHIVE_SPEC[WINDOWS]
    raw = path.read_bytes()
    # 1. RAW archive file bytes (catches the ZIP comment + post-EOCD overlay + any
    #    uncompressed secret bytes in the container itself).
    scan_blob(f"raw:{path.name}", raw, findings, strict=True)
    try:
        with zipfile.ZipFile(path) as archive:
            # 2. The archive COMMENT and 3. any TRAILING bytes after the EOCD.
            scan_blob(f"{path.name}#zip-comment", archive.comment, findings, strict=True)
            trailing = _zip_post_eocd_bytes(raw)
            if trailing:
                scan_blob(f"{path.name}#post-eocd", trailing, findings, strict=True)

            # 4. EXACT root member set: full names only, no nesting, no traversal.
            members = [m for m in archive.infolist() if not m.is_dir()]
            names: list[str] = []
            for member in members:
                _reject_unsafe_member(member.filename)
                name = _canonical_member_name(member.filename)
                if "/" in name:
                    fail(
                        f"{path.name}: nested archive member not allowed at the "
                        f"archive root: {member.filename!r}"
                    )
                names.append(name)
            _require_exact_members(path.name, names, spec["required"])

            # 5. Every member's RAW (decompressed) bytes + bounded zlib inflate.
            for member in members:
                data = archive.read(member)
                label = f"{path.name}!{member.filename}"
                scan_blob(label, data, findings, strict=True)
                _scan_bounded_zlib(label, data, findings)
    except zipfile.BadZipFile as exc:
        fail(f"{path.name}: not a valid ZIP archive ({exc})")


def inspect_linux_tar(path: Path, version: str, findings: list[Finding]) -> None:
    spec = ARCHIVE_SPEC[LINUX]
    # 1. RAW archive file bytes.
    scan_blob(f"raw:{path.name}", path.read_bytes(), findings, strict=True)
    try:
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()

            # 2. EXACT root member set: full names only, no nesting/traversal/links.
            names: list[str] = []
            for member in members:
                _reject_unsafe_member(member.name)
                if member.issym() or member.islnk() or member.isdev():
                    fail(f"{path.name}: unexpected link/device member: {member.name!r}")
                if not member.isfile():
                    fail(
                        f"{path.name}: non-regular-file member not allowed: "
                        f"{member.name!r}"
                    )
                name = _canonical_member_name(member.name)
                if "/" in name:
                    fail(
                        f"{path.name}: nested archive member not allowed at the "
                        f"archive root: {member.name!r}"
                    )
                names.append(name)
            _require_exact_members(path.name, names, spec["required"])

            binary = next(
                m
                for m in members
                if _canonical_member_name(m.name) == spec["binary"]
            )
            if not (binary.mode & 0o111):
                fail(f"{path.name}: executable bit not preserved on {spec['binary']}")

            # 3. Every member's RAW bytes + bounded zlib inflate.
            for member in members:
                extracted = archive.extractfile(member)
                if extracted is None:
                    fail(f"{path.name}: could not read member {member.name!r}")
                data = extracted.read()
                label = f"{path.name}!{member.name}"
                scan_blob(label, data, findings, strict=True)
                _scan_bounded_zlib(label, data, findings)
    except tarfile.TarError as exc:
        fail(f"{path.name}: not a valid tar.gz archive ({exc})")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------- #
# Findings report + shared scan entry point
# --------------------------------------------------------------------------- #
def report_findings(findings: list[Finding]) -> None:
    """Print a value-free findings report; fail closed on any credential hit."""
    credential_findings = [f for f in findings if f["severity"] == "credential"]
    marker_findings = [f for f in findings if f["severity"] == "marker"]

    if marker_findings:
        print(f"  marker warnings ({len(marker_findings)}) -- review, no values shown:")
        for finding in marker_findings:
            print(
                f"    - {finding['pattern']} in {finding['file']} @ byte {finding['offset']}"
            )
    else:
        print("  marker patterns: 0 hits")

    if credential_findings:
        print(
            f"  CONFIRMED SECRET/MARKER findings ({len(credential_findings)}):",
            file=sys.stderr,
        )
        for finding in credential_findings:
            print(
                f"    - {finding['pattern']} in {finding['file']} @ byte {finding['offset']}",
                file=sys.stderr,
            )
        fail("secret scan failed closed on confirmed credential / secret-marker material.")
    print("  credential patterns: 0 hits -- clean")


def run_scan_only(args: argparse.Namespace, version: str) -> int:
    """Shared secret/integrity gate: validate + scan the supplied archive(s).

    Used by build-bridge-windows.ps1 (and any platform build) so a bare platform
    build cannot emit an ungated artifact (ADV-49-011). Writes nothing and does
    not require both platforms; exits non-zero on any member/secret failure and
    prints the SHA-256 of each scanned archive.
    """
    targets: list[tuple[str, Path]] = []
    if args.windows_zip is not None:
        targets.append((WINDOWS, args.windows_zip))
    if args.linux_tar is not None:
        targets.append((LINUX, args.linux_tar))
    if not targets:
        fail("--scan-only requires --windows-zip and/or --linux-tar")

    bold(f"Scan-only release gate (Phase 49 Sec.31) -- canonical version {version}")
    findings: list[Finding] = []
    for platform_key, path in targets:
        if not path.is_file():
            fail(f"{platform_key} artifact not found: {path}")
        if platform_key == WINDOWS and path.name.endswith(".zip"):
            inspect_windows_zip(path, version, findings)
        elif platform_key == LINUX and path.name.endswith(".tar.gz"):
            inspect_linux_tar(path, version, findings)
        else:
            fail(f"unrecognised {platform_key} artifact extension: {path.name}")

    report_findings(findings)

    bold("SHA-256 of scanned artifact(s)")
    for _platform_key, path in targets:
        print(f"  {path.name}: {path.stat().st_size} bytes  {sha256_file(path)}")
    print("\nScan-only gate PASSED: archive(s) are member-valid and secret-clean.")
    return 0


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #
def parse_args(argv: Iterable[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="build_release.py",
        description="Phase 49 unified release assembly (checksums + manifest + secret scan).",
    )
    parser.add_argument("--windows-zip", type=Path, default=None, help="path to the Windows release ZIP")
    parser.add_argument("--linux-tar", type=Path, default=None, help="path to the Linux release tar.gz")
    parser.add_argument(
        "--scan-only",
        action="store_true",
        help="only validate + secret-scan the supplied archive(s); write nothing (Phase 49 Sec.31/44)",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="release output directory (default: dist/releases/<VERSION>)",
    )
    parser.add_argument(
        "--allow-dirty",
        action="store_true",
        help="allow assembly from a dirty working tree (development only; Phase 49 Sec.37)",
    )
    parser.add_argument(
        "--manifest-out",
        type=Path,
        default=FRONTEND_MANIFEST,
        help="where to copy the generated manifest (default: frontend/public/releases/release-manifest.json)",
    )
    parser.add_argument(
        "--released-at",
        default=None,
        help="override the release timestamp (ISO-8601, defaults to now UTC)",
    )
    return parser.parse_args(list(argv))


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)

    version = resolve_canonical_version()
    bold(f"Canonical bridge version: {version}")

    if args.scan_only:
        return run_scan_only(args, version)

    out_dir = args.out if args.out is not None else (REPO_ROOT / "dist" / "releases" / version)

    expected = {
        WINDOWS: f"pd-ollama-bridge-v{version}-windows-x64{ARCHIVE_SPEC[WINDOWS]['ext']}",
        LINUX: f"pd-ollama-bridge-v{version}-linux-x64{ARCHIVE_SPEC[LINUX]['ext']}",
    }
    artifacts = {
        WINDOWS: args.windows_zip if args.windows_zip is not None else (out_dir / expected[WINDOWS]),
        LINUX: args.linux_tar if args.linux_tar is not None else (out_dir / expected[LINUX]),
    }

    # ---- provenance ------------------------------------------------------- #
    provenance = git_provenance()
    if provenance["dirty"] and not args.allow_dirty:
        fail(
            "working tree is dirty. Commit your changes or re-run with --allow-dirty "
            "for a NON-publishable development build (Phase 49 Sec.37)."
        )
    if provenance["dirty"]:
        print("WARNING: working tree is DIRTY -- development build, must NOT be published.")

    # ---- collect + validate artifacts ------------------------------------ #
    for platform_key, path in artifacts.items():
        spec = ARCHIVE_SPEC[platform_key]
        if not path.is_file():
            fail(f"{platform_key} artifact not found: {path}")
        if path.name != expected[platform_key]:
            fail(
                f"{platform_key} artifact name {path.name!r} does not match the canonical "
                f"versioned name {expected[platform_key]!r}"
            )
        if not path.name.endswith(spec["ext"]):
            fail(f"{platform_key} artifact does not end with {spec['ext']}: {path.name}")

    bold("Scanning RAW archive + member bytes (Phase 49 Sec.31)")
    findings: list[Finding] = []
    inspect_windows_zip(artifacts[WINDOWS], version, findings)
    inspect_linux_tar(artifacts[LINUX], version, findings)

    report_findings(findings)

    # ---- checksums + sizes ----------------------------------------------- #
    bold("Computing SHA-256 and sizes from final archive bytes")
    checksum_lines: list[str] = []
    manifest_artifacts: list[dict[str, Any]] = []
    for platform_key in (WINDOWS, LINUX):
        spec = ARCHIVE_SPEC[platform_key]
        path = artifacts[platform_key]
        digest = sha256_file(path)
        size = path.stat().st_size
        checksum_lines.append(f"{digest}  {path.name}")
        manifest_artifacts.append(
            {
                "platform": platform_key,
                "arch": spec["arch"],
                "filename": path.name,
                "sha256": digest,
                "size": size,
            }
        )
        print(f"  {path.name}: {size} bytes  {digest}")

    # ---- write SHA256SUMS.txt -------------------------------------------- #
    out_dir.mkdir(parents=True, exist_ok=True)
    sums_path = out_dir / "SHA256SUMS.txt"
    sums_path.write_text("\n".join(checksum_lines) + "\n", encoding="utf-8", newline="\n")
    bold(f"Wrote {sums_path}")
    print("\n".join(checksum_lines))

    # ---- build + write release-manifest.json ----------------------------- #
    released_at = args.released_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest: dict[str, Any] = {
        "version": version,
        "gitCommit": provenance["gitCommitShort"],
        "releasedAt": released_at,
        "artifacts": manifest_artifacts,
        # Honest additional provenance (frontend parser ignores unknown keys).
        "gitCommitFull": provenance["gitCommitFull"],
        "dirty": provenance["dirty"],
        "buildTimestamp": released_at,
        "packager": f"tools/build_release.py (Python {platform_mod.python_version()})",
    }

    manifest_path = out_dir / "release-manifest.json"
    manifest_text = json.dumps(manifest, indent=2, sort_keys=False) + "\n"
    manifest_path.write_text(manifest_text, encoding="utf-8", newline="\n")
    bold(f"Wrote {manifest_path}")
    print(manifest_text)

    # ---- copy manifest to the website (only generated file in Git) ------- #
    if args.manifest_out is not None:
        target = Path(args.manifest_out)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(manifest_text, encoding="utf-8", newline="\n")
        bold(f"Copied manifest to {target}")

    print(
        "\nRelease assembly complete:"
        f"\n  version : {version}"
        f"\n  commit  : {provenance['gitCommitFull']} (dirty={provenance['dirty']})"
        f"\n  out     : {out_dir}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
