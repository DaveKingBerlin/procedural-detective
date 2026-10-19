"""Phase 49 §43 — hermetic build-metadata / release-assembly integrity tests.

Verifies the frozen Phase 49 release contract enforced by
``tools/build_release.py`` (the release-tooling owner):

  * the canonical version parses from the SINGLE source
    (``bridge/pd_ollama_bridge/__init__.py::__version__``);
  * the archive filename embeds that version;
  * the generated manifest's ``version`` == archive version == bridge source
    version;
  * the manifest ``gitCommit``/``gitCommitFull`` match the build commit recorded
    at assembly time;
  * the manifest ``sha256``/``size`` match the artifact bytes (so a swapped or
    hand-edited archive/manifest is detected);
  * archive-member validation is EXACT and symmetric for zip + tar
    (no nesting, no traversal, no extra/missing members) — ADV-49-002;
  * the secret scan fails CLOSED on the raw archive bytes, the ZIP comment,
    post-EOCD trailing bytes and compressed member payloads — ADV-49-001.

Fixture archives are built in-test (deterministic; no network, no Docker). Tests
that inspect the REAL ``dist/`` artifacts skip gracefully when those are absent.
Conventions follow ``tests/test_phase46_release_guards.py``.
"""

from __future__ import annotations

import io
import json
import re
import subprocess
import tarfile
import zipfile
import zlib
from pathlib import Path

import pytest

from tools import build_release as br

REPO_ROOT = Path(__file__).resolve().parents[1]
INIT_PY = REPO_ROOT / "bridge" / "pd_ollama_bridge" / "__init__.py"
FRONTEND_MANIFEST = REPO_ROOT / "frontend" / "public" / "releases" / "release-manifest.json"

# The canonical dist directory for the CURRENT source version (resolved lazily
# inside the real-artifact tests so an unusable source tree cannot break import).
def _dist_dir() -> Path:
    return REPO_ROOT / "dist" / "releases" / br.resolve_canonical_version()


# --------------------------------------------------------------------------- #
# Fixture archives (built in-test; no secret literal is stored contiguously)
# --------------------------------------------------------------------------- #
def _fake_openrouter_key() -> bytes:
    """A SYNTHETIC credential-shaped value, assembled at runtime so the test
    source never contains a contiguous key literal."""
    return b"sk-or-" + b"v1-" + b"K" * 24


def _fake_personal_path() -> bytes:
    return b"C:\\Users\\" + b"adv49fixture" + b"\\bridge.toml"


def _write_valid_zip(
    path: Path,
    *,
    exe_payload: bytes = b"MZ fake one-file exe\n",
    comment: bytes = b"",
    trailing: bytes = b"",
) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("pd-ollama-bridge.exe", exe_payload)
        archive.writestr("QUICKSTART.md", b"# quickstart\n")
        archive.writestr("LICENSE", b"MIT\n")
        archive.writestr("THIRD_PARTY_NOTICES.txt", b"notices\n")
        if comment:
            archive.comment = comment
    if trailing:
        with path.open("ab") as handle:
            handle.write(trailing)
    return path


def _write_valid_tar(path: Path, *, binary_payload: bytes = b"#!/bin/sh\necho hi\n") -> Path:
    with tarfile.open(path, "w:gz") as archive:
        for name, data, mode in (
            ("pd-ollama-bridge", binary_payload, 0o755),
            ("QUICKSTART.md", b"# quickstart\n", 0o644),
            ("LICENSE", b"MIT\n", 0o644),
            ("THIRD_PARTY_NOTICES.txt", b"notices\n", 0o644),
        ):
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = mode
            info.type = tarfile.REGTYPE
            archive.addfile(info, io.BytesIO(data))
    return path


def _canonical_names(version: str) -> dict[str, str]:
    return {
        br.WINDOWS: f"pd-ollama-bridge-v{version}-windows-x64{br.ARCHIVE_SPEC[br.WINDOWS]['ext']}",
        br.LINUX: f"pd-ollama-bridge-v{version}-linux-x64{br.ARCHIVE_SPEC[br.LINUX]['ext']}",
    }


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(REPO_ROOT), *args],
        capture_output=True,
        text=True,
        check=False,
    )


# --------------------------------------------------------------------------- #
# Canonical version + archive naming (§4/§6/§38)
# --------------------------------------------------------------------------- #
def test_canonical_version_parses_and_matches_source() -> None:
    version = br.resolve_canonical_version()
    assert re.fullmatch(r"\d+\.\d+\.\d+", version), version
    text = INIT_PY.read_text(encoding="utf-8")
    assert f'__version__ = "{version}"' in text


def test_expected_archive_filenames_embed_the_version() -> None:
    version = br.resolve_canonical_version()
    for platform_key, name in _canonical_names(version).items():
        spec = br.ARCHIVE_SPEC[platform_key]
        assert name.startswith(f"pd-ollama-bridge-v{version}-")
        assert name.endswith(spec["ext"])
        assert platform_key in name
        assert f"-{spec['arch']}{spec['ext']}" in name


def test_required_member_set_is_the_frozen_contract() -> None:
    for platform_key, spec in br.ARCHIVE_SPEC.items():
        assert spec["required"] == {
            spec["binary"],
            "QUICKSTART.md",
            "LICENSE",
            "THIRD_PARTY_NOTICES.txt",
        }, platform_key


# --------------------------------------------------------------------------- #
# End-to-end assembly against in-test fixtures (§19/§20/§36/§38/§43)
# --------------------------------------------------------------------------- #
def _run_assembly(tmp_path: Path) -> tuple[int, Path, Path]:
    version = br.resolve_canonical_version()
    names = _canonical_names(version)
    zip_path = _write_valid_zip(tmp_path / names[br.WINDOWS])
    tar_path = _write_valid_tar(tmp_path / names[br.LINUX])
    out_dir = tmp_path / "out"
    manifest_copy = tmp_path / "website-manifest.json"
    rc = br.main(
        [
            "--windows-zip", str(zip_path),
            "--linux-tar", str(tar_path),
            "--out", str(out_dir),
            "--manifest-out", str(manifest_copy),
            "--allow-dirty",
            "--released-at", "2026-01-01T00:00:00Z",
        ]
    )
    return rc, out_dir, manifest_copy


def test_assembly_generates_consistent_manifest_and_checksums(tmp_path: Path) -> None:
    version = br.resolve_canonical_version()
    names = _canonical_names(version)
    rc, out_dir, manifest_copy = _run_assembly(tmp_path)
    assert rc == 0

    manifest_path = out_dir / "release-manifest.json"
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    # version == archive version == bridge source version
    assert manifest["version"] == version
    assert br.resolve_canonical_version() == version

    # SHA-256 + size must match the REAL artifact bytes, and the filename embeds
    # the version.
    by_platform = {a["platform"]: a for a in manifest["artifacts"]}
    assert set(by_platform) == {br.WINDOWS, br.LINUX}
    for platform_key, entry in by_platform.items():
        artifact = tmp_path / names[platform_key]
        assert entry["filename"] == names[platform_key]
        assert version in entry["filename"]
        assert entry["sha256"] == br.sha256_file(artifact)
        assert entry["size"] == artifact.stat().st_size

    # SHA256SUMS.txt lists exactly those digests.
    sums = (out_dir / "SHA256SUMS.txt").read_text(encoding="utf-8").splitlines()
    for entry in by_platform.values():
        assert f"{entry['sha256']}  {entry['filename']}" in sums

    # The website copy is byte-identical to the generated manifest.
    assert manifest_copy.read_bytes() == manifest_path.read_bytes()


def test_manifest_git_commit_matches_the_build_commit_recorded(tmp_path: Path) -> None:
    rc, out_dir, _ = _run_assembly(tmp_path)
    assert rc == 0
    manifest = json.loads((out_dir / "release-manifest.json").read_text(encoding="utf-8"))

    assert re.fullmatch(r"[0-9a-f]{7,40}", manifest["gitCommit"])
    assert re.fullmatch(r"[0-9a-f]{40}", manifest["gitCommitFull"])
    assert manifest["gitCommitFull"].startswith(manifest["gitCommit"])

    head = _git("rev-parse", "HEAD")
    if head.returncode == 0 and head.stdout.strip():
        # build_release.py records ``git rev-parse HEAD`` at assembly time.
        assert manifest["gitCommitFull"] == head.stdout.strip()


def test_manifest_checksum_detects_a_swapped_artifact(tmp_path: Path) -> None:
    rc, out_dir, _ = _run_assembly(tmp_path)
    assert rc == 0
    manifest = json.loads((out_dir / "release-manifest.json").read_text(encoding="utf-8"))
    version = br.resolve_canonical_version()
    names = _canonical_names(version)
    entry = next(a for a in manifest["artifacts"] if a["platform"] == br.WINDOWS)

    # Rewrite a byte of the archive: the recorded digest must no longer match.
    artifact = tmp_path / names[br.WINDOWS]
    artifact.write_bytes(artifact.read_bytes() + b"\x00tamper")
    assert br.sha256_file(artifact) != entry["sha256"]


# --------------------------------------------------------------------------- #
# Real dist artifacts (§43) — skip gracefully when absent
# --------------------------------------------------------------------------- #
def _load_real_manifest() -> dict:
    manifest_path = _dist_dir() / "release-manifest.json"
    if not manifest_path.is_file():
        pytest.skip(f"real dist manifest absent: {manifest_path}")
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def test_real_dist_manifest_checksums_match_artifact_bytes() -> None:
    manifest = _load_real_manifest()
    version = br.resolve_canonical_version()
    assert manifest["version"] == version
    for entry in manifest["artifacts"]:
        artifact = _dist_dir() / entry["filename"]
        assert artifact.is_file(), artifact
        assert version in entry["filename"]
        assert br.sha256_file(artifact) == entry["sha256"], entry["filename"]
        assert artifact.stat().st_size == entry["size"], entry["filename"]


def test_real_dist_manifest_commit_is_consistent_and_exists() -> None:
    manifest = _load_real_manifest()
    assert re.fullmatch(r"[0-9a-f]{7,40}", manifest["gitCommit"])
    assert re.fullmatch(r"[0-9a-f]{40}", manifest["gitCommitFull"])
    assert manifest["gitCommitFull"].startswith(manifest["gitCommit"])

    if _git("rev-parse", "--is-inside-work-tree").returncode != 0:
        pytest.skip("not a git work tree")
    assert _git("cat-file", "-e", f"{manifest['gitCommitFull']}^{{commit}}").returncode == 0


def test_frontend_manifest_matches_dist_manifest_when_present() -> None:
    dist_manifest = _dist_dir() / "release-manifest.json"
    if not dist_manifest.is_file() or not FRONTEND_MANIFEST.is_file():
        pytest.skip("dist or frontend manifest absent")
    assert FRONTEND_MANIFEST.read_bytes() == dist_manifest.read_bytes()


# --------------------------------------------------------------------------- #
# Exact member validation, symmetric for zip + tar (ADV-49-002)
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "members",
    [
        # nested binary (NOT at the archive root)
        [
            ("sub/pd-ollama-bridge.exe", b"MZ"),
            ("QUICKSTART.md", b"x"),
            ("LICENSE", b"x"),
            ("THIRD_PARTY_NOTICES.txt", b"x"),
        ],
        # windows-style nested binary
        [
            ("sub\\pd-ollama-bridge.exe", b"MZ"),
            ("QUICKSTART.md", b"x"),
            ("LICENSE", b"x"),
            ("THIRD_PARTY_NOTICES.txt", b"x"),
        ],
        # zip-slip traversal
        [
            ("../../../Windows/Temp/pd-ollama-bridge.exe", b"MZ"),
            ("QUICKSTART.md", b"x"),
            ("LICENSE", b"x"),
            ("THIRD_PARTY_NOTICES.txt", b"x"),
        ],
        # absolute path
        [
            ("/tmp/pd-ollama-bridge.exe", b"MZ"),
            ("QUICKSTART.md", b"x"),
            ("LICENSE", b"x"),
            ("THIRD_PARTY_NOTICES.txt", b"x"),
        ],
        # extra member
        [
            ("pd-ollama-bridge.exe", b"MZ"),
            ("QUICKSTART.md", b"x"),
            ("LICENSE", b"x"),
            ("THIRD_PARTY_NOTICES.txt", b"x"),
            ("evil.env", b"x"),
        ],
        # missing member
        [
            ("pd-ollama-bridge.exe", b"MZ"),
            ("QUICKSTART.md", b"x"),
            ("LICENSE", b"x"),
        ],
    ],
)
def test_zip_member_validation_rejects_hostile_layouts(
    tmp_path: Path, members: list[tuple[str, bytes]]
) -> None:
    hostile = tmp_path / "hostile.zip"
    with zipfile.ZipFile(hostile, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in members:
            archive.writestr(name, data)
    with pytest.raises(SystemExit) as exc:
        br.inspect_windows_zip(hostile, br.resolve_canonical_version(), [])
    assert exc.value.code != 0


@pytest.mark.parametrize(
    "kind",
    ["nested", "traversal", "absolute", "extra", "missing", "symlink"],
)
def test_tar_member_validation_rejects_hostile_layouts(tmp_path: Path, kind: str) -> None:
    hostile = tmp_path / "hostile.tar.gz"
    binary_name = "pd-ollama-bridge"
    docs = [("QUICKSTART.md", b"x"), ("LICENSE", b"x"), ("THIRD_PARTY_NOTICES.txt", b"x")]
    with tarfile.open(hostile, "w:gz") as archive:
        def add(name: str, data: bytes, mode: int, ttype: int = tarfile.REGTYPE) -> None:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            info.mode = mode
            info.type = ttype
            archive.addfile(info, io.BytesIO(data))

        if kind == "symlink":
            add(binary_name, b"x", 0o755)
            for name, data in docs:
                add(name, data, 0o644)
            add("evil-link", b"target", 0o777, tarfile.SYMTYPE)
        elif kind == "nested":
            add("sub/" + binary_name, b"x", 0o755)
            for name, data in docs:
                add(name, data, 0o644)
        elif kind == "traversal":
            add("../etc/" + binary_name, b"x", 0o755)
            for name, data in docs:
                add(name, data, 0o644)
        elif kind == "absolute":
            add("/tmp/" + binary_name, b"x", 0o755)
            for name, data in docs:
                add(name, data, 0o644)
        elif kind == "extra":
            add(binary_name, b"x", 0o755)
            for name, data in docs:
                add(name, data, 0o644)
            add("evil.env", b"x", 0o644)
        else:  # missing
            add(binary_name, b"x", 0o755)
            add("QUICKSTART.md", b"x", 0o644)
            add("LICENSE", b"x", 0o644)

    with pytest.raises(SystemExit) as exc:
        br.inspect_linux_tar(hostile, br.resolve_canonical_version(), [])
    assert exc.value.code != 0


def test_valid_fixtures_are_accepted(tmp_path: Path) -> None:
    version = br.resolve_canonical_version()
    names = _canonical_names(version)
    findings: list = []
    br.inspect_windows_zip(_write_valid_zip(tmp_path / names[br.WINDOWS]), version, findings)
    br.inspect_linux_tar(_write_valid_tar(tmp_path / names[br.LINUX]), version, findings)
    assert not [f for f in findings if f["severity"] == "credential"], findings


# --------------------------------------------------------------------------- #
# Secret scan fails CLOSED on every smuggled surface (ADV-49-001)
# --------------------------------------------------------------------------- #
def _assert_fails_closed(archive: Path, *, platform: str) -> None:
    version = br.resolve_canonical_version()
    findings: list = []
    if platform == br.WINDOWS:
        br.inspect_windows_zip(archive, version, findings)
    else:
        br.inspect_linux_tar(archive, version, findings)
    assert any(f["severity"] == "credential" for f in findings), findings
    # The gate (report_findings) fails closed -> non-zero exit.
    with pytest.raises(SystemExit) as exc:
        br.report_findings(findings)
    assert exc.value.code != 0


def test_zip_secret_in_archive_comment_fails_closed(tmp_path: Path) -> None:
    archive = _write_valid_zip(tmp_path / "comment.zip", comment=_fake_openrouter_key())
    _assert_fails_closed(archive, platform=br.WINDOWS)


def test_zip_secret_after_eocd_fails_closed(tmp_path: Path) -> None:
    archive = _write_valid_zip(
        tmp_path / "trailing.zip",
        trailing=b"\nTRAILING-OVERLAY " + _fake_personal_path(),
    )
    _assert_fails_closed(archive, platform=br.WINDOWS)


def test_zip_secret_in_member_payload_fails_closed(tmp_path: Path) -> None:
    archive = _write_valid_zip(tmp_path / "member.zip", exe_payload=_fake_openrouter_key())
    _assert_fails_closed(archive, platform=br.WINDOWS)


def test_zip_secret_in_compressed_member_fails_closed(tmp_path: Path) -> None:
    archive = _write_valid_zip(
        tmp_path / "zlib.zip",
        exe_payload=zlib.compress(_fake_openrouter_key()),
    )
    _assert_fails_closed(archive, platform=br.WINDOWS)


def test_zip_bare_marker_fails_closed(tmp_path: Path) -> None:
    # A bare Sec.31 marker in an out-of-band region is anomalous -> fail closed.
    archive = _write_valid_zip(tmp_path / "marker.zip", trailing=b"OPENROUTER")
    _assert_fails_closed(archive, platform=br.WINDOWS)


def test_tar_secret_in_member_payload_fails_closed(tmp_path: Path) -> None:
    archive = _write_valid_tar(tmp_path / "secret.tar.gz", binary_payload=_fake_openrouter_key())
    _assert_fails_closed(archive, platform=br.LINUX)


def test_scan_does_not_print_the_secret_value(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    secret = _fake_openrouter_key()
    archive = _write_valid_zip(tmp_path / "secret.zip", comment=secret)
    findings: list = []
    br.inspect_windows_zip(archive, br.resolve_canonical_version(), findings)
    with pytest.raises(SystemExit):
        br.report_findings(findings)
    captured = capsys.readouterr()
    assert secret.decode() not in captured.out
    assert secret.decode() not in captured.err
    # It MUST still report the value-free label.
    assert "openrouter-key" in captured.err


# --------------------------------------------------------------------------- #
# Shared scan-only gate used by the platform build scripts (ADV-49-011)
# --------------------------------------------------------------------------- #
def test_scan_only_accepts_a_clean_archive(tmp_path: Path) -> None:
    version = br.resolve_canonical_version()
    names = _canonical_names(version)
    archive = _write_valid_zip(tmp_path / names[br.WINDOWS])
    assert br.main(["--scan-only", "--windows-zip", str(archive)]) == 0


def test_scan_only_rejects_a_planted_secret(tmp_path: Path) -> None:
    version = br.resolve_canonical_version()
    names = _canonical_names(version)
    archive = _write_valid_zip(tmp_path / names[br.WINDOWS], trailing=_fake_openrouter_key())
    with pytest.raises(SystemExit) as exc:
        br.main(["--scan-only", "--windows-zip", str(archive)])
    assert exc.value.code != 0


def test_scan_only_requires_an_artifact() -> None:
    with pytest.raises(SystemExit) as exc:
        br.main(["--scan-only"])
    assert exc.value.code != 0
