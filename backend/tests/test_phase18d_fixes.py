"""Phase 18D fixes — regression suite for ADV-204/205/206/207/210.

Covers the five BACKEND-part accepted findings of the Phase 18 adversarial
gate (the sixth, ADV-211, is covered by the frontend kitGeometry suite):

- ADV-204: every HTTP response carries the minimal security headers — INCLUDING
  the sanitized unhandled-exception 500 envelopes emitted by Starlette's
  structurally-outermost ``ServerErrorMiddleware`` (previously they bypassed
  ``SecurityHeadersMiddleware`` entirely while HTTPException-mapped paths did
  not). /boom (unhandled) vs /http500 (HTTPException-mapped) vs 404 vs 422 vs
  CORS preflights vs static/SPA are all asserted.
- ADV-205: ``release_check.git_tracked_files`` fails CLOSED when a repo exists
  but the git index is missing/corrupt (``git ls-files`` exits 0 with empty
  output while the working tree contains files) — the secret / private-endpoint
  scans must never silently probe an unscanned tree.
- ADV-206: the tracked-secret ban covers the whole dotenv family (``.env`` AND
  ``.env.<suffix>``) while ``.env.example`` stays sanctioned.
- ADV-207: private-host detection covers decimal/hex/octal IPv4 spellings of
  private ranges, private/link-local IPv6 (fc00::/7, fe80::/10), userinfo
  wrapping and embedded private literals in longer tokens — allowlist
  (localhost / 127.0.0.1 / ::1 / host.docker.internal / .env.example) intact.
- ADV-210: ``dimensions_of`` DEDUPES ids per dimension, bounds reference lists
  to the published evidence universe (fail closed), rejects garbage types /
  mismatched id sets (fail closed), and a payload published BEFORE Phase 18C
  (``solverProof`` + ``evidence_ids_used`` but NO per-dimension keys) reveals
  200 with a populated board via the deterministic flat-list fallback.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Make ``tools`` importable from the repo root (python -m tools.release_check).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PYTHON_TESTS = str(_REPO_ROOT / "backend" / "tests")

from fastapi.testclient import TestClient  # noqa: E402
from starlette.exceptions import HTTPException as StarletteHTTPException  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.main import SecurityHeadersFastAPI, _CSP_BASELINE, create_app  # noqa: E402
from app.services.reveal import RevealProjectionError, dimensions_of  # noqa: E402
from phase5_helpers import assert_sanitized_error, auth  # noqa: E402
from phase6_helpers import client as phase6_client  # noqa: E402
from test_phase7_helpers import (  # noqa: E402
    create_published_case_and_playthrough,
    get_reveal,
    make_accusation,
    new_playthrough,
    truth_bundle,
    winning_body,
)

for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from tools import release_check  # noqa: E402


# --------------------------------------------------------------------------- #
# ADV-204 — security headers on EVERY response (unhandled 500 included)
# --------------------------------------------------------------------------- #

# Phase 18D ADV-204 assertions. The CSP value is the Phase 20 (PD-SEC-08)
# baseline (imported from main.py); every other header is unchanged.
_SECURITY_HEADER_EXPECT = {
    "content-security-policy": _CSP_BASELINE,
    "x-frame-options": "DENY",
    "x-content-type-options": "nosniff",
    "referrer-policy": "strict-origin-when-cross-origin",
    "permissions-policy": "camera=(), microphone=(), geolocation=()",
}


def _assert_security_headers(res) -> None:
    headers = res.headers
    for name, value in _SECURITY_HEADER_EXPECT.items():
        assert headers.get(name) == value, (
            f"{name!r} missing/wrong on {res.request.method} {res.request.url} "
            f"(status {res.status_code}): got {headers.get(name)!r}"
        )


@pytest.fixture
def header_app(database_url):
    """A full ``create_app`` instance with the /boom / /http500 / /needs-int
    scratch routes (the production middleware + exception-handler wiring)."""
    application = create_app(
        Settings(database_url=database_url, cors_allowed_origins=["http://localhost:5173"])
    )

    @application.get("/boom")
    def boom():
        raise RuntimeError("unhandled-secret-detail")

    @application.get("/http500")
    def http500():
        raise StarletteHTTPException(
            status_code=500,
            detail={"code": "HTTP_ERROR", "message": "Request failed", "details": None},
        )

    @application.get("/needs-int")
    def needs_int(count: int):
        return {"count": count}

    yield application
    application.state.engine.dispose()
    store = application.state.store
    if store is not None:
        store.dispose()


def test_adv204_unhandled_exception_500_carries_security_headers(header_app):
    """The pure-exception /boom path (Starlette ServerErrorMiddleware, which
    sits structurally OUTSIDE every add_middleware registration) must carry the
    SAME headers as the HTTPException-mapped path and every other response."""
    with TestClient(header_app, raise_server_exceptions=False) as c:
        boom = c.get("/boom")
        assert boom.status_code == 500
        error = boom.json()["error"]
        assert error["code"] == "INTERNAL_ERROR"
        assert error["message"] == "Internal server error"
        assert error["details"] is None
        assert "unhandled-secret-detail" not in boom.text
        assert "Traceback" not in boom.text
        _assert_security_headers(boom)


def test_adv204_http_exception_500_carries_security_headers(header_app):
    with TestClient(header_app, raise_server_exceptions=False) as c:
        res = c.get("/http500")
        assert res.status_code == 500
        assert res.json()["error"]["code"] == "HTTP_ERROR"
        _assert_security_headers(res)


def test_adv204_404_422_and_cors_preflights_carry_security_headers(header_app):
    with TestClient(header_app, raise_server_exceptions=False) as c:
        not_found = c.get("/api/v1/does-not-exist")
        assert not_found.status_code == 404
        _assert_security_headers(not_found)

        validation = c.get("/needs-int", params={"count": "abc"})
        assert validation.status_code == 422
        _assert_security_headers(validation)

        allowed = c.options(
            "/api/v1/health",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert allowed.status_code == 200
        _assert_security_headers(allowed)

        rejected = c.options(
            "/api/v1/health",
            headers={
                "Origin": "http://evil.example",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert rejected.status_code == 400
        assert rejected.json()["error"]["code"] == "CORS_ORIGIN_NOT_ALLOWED"
        _assert_security_headers(rejected)


def test_adv204_static_and_spa_serving_carry_security_headers(tmp_path, database_url):
    """Static index / SPA fallback / assets-cache path all carry the headers."""
    build = tmp_path / "dist"
    build.mkdir()
    (build / "index.html").write_text(
        "<!doctype html><html><body>Procedural Detective</body></html>\n",
        encoding="utf-8",
    )
    application = create_app(
        Settings(database_url=database_url, static_dir=str(build))
    )
    try:
        with TestClient(application) as c:
            index = c.get("/")
            assert index.status_code == 200
            assert "Procedural Detective" in index.text
            _assert_security_headers(index)

            spa = c.get("/scene")
            assert spa.status_code == 200
            _assert_security_headers(spa)

            asset = c.get("/assets/missing.js")
            assert asset.status_code == 404
            _assert_security_headers(asset)
    finally:
        application.state.engine.dispose()
        store = application.state.store
        if store is not None:
            store.dispose()


def test_adv204_create_app_uses_transport_boundary_security_app():
    """The app factory builds with the transport-wrapper variant so the header
    guarantee is structural (no reliance on add_middleware ordering)."""
    assert SecurityHeadersFastAPI is not None


# --------------------------------------------------------------------------- #
# ADV-205 — git tracking fails CLOSED on a missing/corrupt index
# --------------------------------------------------------------------------- #


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
    )


@pytest.fixture
def scratch_repo(tmp_path):
    """A real throwaway git repo (committed baseline) under tmp_path."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "adv-test@example.com")
    _git(repo, "config", "user.name", "adv test")
    (repo / "src.py").write_text("LAN_PIN = None\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "baseline")
    return repo


def test_adv205_intact_repo_returns_tracked_files(scratch_repo):
    tracked = release_check.git_tracked_files(scratch_repo)
    assert tracked == ["src.py"]


def test_adv205_non_repo_fails_closed(tmp_path):
    plain = tmp_path / "no-repo"
    plain.mkdir()
    (plain / "src.py").write_text("x = 1\n", encoding="utf-8")
    assert release_check.git_tracked_files(plain) is None


def test_adv205_missing_index_with_files_present_fails_closed(scratch_repo):
    """Delete .git/index: `git ls-files` exits 0 with EMPTY stdout while the
    working tree holds files -> the index is unusable -> the scans must fail
    closed (NEVER report ok over an unscanned tree)."""
    (scratch_repo / ".git" / "index").unlink()
    tracked = release_check.git_tracked_files(scratch_repo)
    assert tracked is None, "missing index must fail closed (None), not []"
    # Both consumers answer fail-closed.
    secrets = release_check.check_tracked_secrets(tracked)
    assert [f.severity for f in secrets] == ["fail"]
    endpoints = release_check.scan_private_endpoints(scratch_repo, tracked)
    assert [f.severity for f in endpoints] == ["fail"]


def test_adv205_genuinely_empty_repo_is_allowed(scratch_repo):
    """A TRULY empty repo (no files at all) is not a failure: nothing to scan,
    nothing to leak."""
    for rel in list(scratch_repo.iterdir()):
        if rel.name != ".git":
            rel.unlink()
    _git(scratch_repo, "rm", "-q", "--cached", "src.py")
    _git(scratch_repo, "commit", "-qm", "empty")
    assert release_check.git_tracked_files(scratch_repo) == []


# --------------------------------------------------------------------------- #
# ADV-206 — the dotenv family is secret-tracked; .env.example stays sanctioned
# --------------------------------------------------------------------------- #


def _configure_scratch_repo(repo: Path) -> None:
    """A release-shaped scratch repo: placeholder-free release docs so the only
    failing check is the one under test. ``.dockerignore`` carries the Phase 20
    (PD-SEC-07) required exclusions so ``run_all``'s dockerignore gate passes
    on synthetic repos too."""
    (repo / "README.md").write_text("# Scratch\n", encoding="utf-8")
    (repo / "SUBMISSION.md").write_text(
        "# Submission\n| Live demo | https://demo.example/\n", encoding="utf-8"
    )
    (repo / "THIRD_PARTY.md").write_text("none\n", encoding="utf-8")
    (repo / ".dockerignore").write_text(
        ".env\n.env.*\n!.env.example\nlogs/\n*.log\n*.db\n*.sqlite\n"
        "*.sqlite3\ntmp/\ntemp/\n.ollama/\n",
        encoding="utf-8",
    )


def test_adv206_tracked_env_production_with_secret_fails_the_tool(scratch_repo):
    """A tracked `.env.production` (the file Vite loads in production builds)
    carrying a secret + a hostname-only provider URL must FAIL the release gate
    on the tracked-secrets check — the private-endpoint scan alone cannot catch
    a hostname-only endpoint (this is the exact ADV-206 leak class)."""
    _configure_scratch_repo(scratch_repo)
    (scratch_repo / ".env.production").write_text(
        "OLLAMA_BASE_URL=http://ollama:11434\nSECRET_KEY=abc\n",
        encoding="utf-8",
    )
    _git(scratch_repo, "add", ".")
    _git(scratch_repo, "commit", "-qm", "add env.production")

    tracked = release_check.git_tracked_files(scratch_repo)
    assert tracked is not None
    assert ".env.production" in tracked
    findings = release_check.run_all(scratch_repo, allow_hosted=True)
    secrets = [f for f in findings if f.severity == "fail" and f.check == "tracked-secrets"]
    assert secrets, [f.render() for f in findings]
    assert any("env.production" in f.message for f in secrets)


def test_adv206_all_dotenv_suffixes_are_banned(scratch_repo):
    suffixes = (".local", ".development", ".test", ".production", ".production.local")
    for suffix in suffixes:
        (scratch_repo / f".env{suffix}").write_text("TOKEN=x\n", encoding="utf-8")
    _git(scratch_repo, "add", ".")
    _git(scratch_repo, "commit", "-qm", "add dotenv variants")
    tracked = release_check.git_tracked_files(scratch_repo)
    assert tracked is not None
    bad = release_check.tracked_secret_entries(tracked)
    assert sorted(bad) == sorted(f".env{s}" for s in suffixes)


def test_adv206_env_example_remains_sanctioned(scratch_repo):
    """The sanctioned `.env.example` may be tracked with example values (even a
    LAN-IP example) without failing either the secret scan or the endpoint
    scan; the REAL `.env.<anything>` and `.env` remain banned."""
    _configure_scratch_repo(scratch_repo)
    (scratch_repo / ".env.example").write_text(
        "OLLAMA_BASE_URL=http://192.168.1.5:11434\nSECRET=replace-me\n",
        encoding="utf-8",
    )
    _git(scratch_repo, "add", ".")
    _git(scratch_repo, "commit", "-qm", "add env.example")
    tracked = release_check.git_tracked_files(scratch_repo)
    assert tracked is not None
    assert release_check.tracked_secret_entries(tracked) == []
    findings = release_check.run_all(scratch_repo, allow_hosted=True)
    fails = [f for f in findings if f.severity == "fail"]
    assert fails == [], [f.render() for f in fails]


# --------------------------------------------------------------------------- #
# ADV-207 — private-host detection closes the obfuscation classes
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "host",
    [
        "192.168.1.1:11434",            # dotted RFC1918 (control, still flagged)
        "10.0.0.7:11434",               # dotted RFC1918 (control, still flagged)
        "172.16.1.1",                   # dotted RFC1918 (control, still flagged)
        "3232235777:11434",             # DECIMAL 192.168.1.1
        "3232235777",                   # DECIMAL form without port
        "0xC0A80101:11434",             # HEX integer 192.168.1.1
        "0xc0a80101",                   # lowercase hex
        "0300.0250.0001.0001:11434",    # OCTAL dotted 192.168.1.1
        "0xC0.0xA8.0x01.0x01:11434",    # HEX dotted 192.168.1.1
        "[fd00::1]:11434",              # ULA IPv6 (lower half)
        "[fc00::abcd]:11434",           # ULA IPv6 (upper half)
        "[fe80::1]",                    # link-local IPv6
        "user:pass@192.168.1.5",        # userinfo-wrapped dotted IP
        "user:pass@[fd00::1]:11434",    # userinfo-wrapped IPv6
        "192.168.1.5.evil.com",         # embedded private literal in longer token
    ],
)
def test_adv207_host_bypass_classes_are_private(host):
    assert release_check._host_is_private(host) is True, host


@pytest.mark.parametrize(
    "host",
    [
        "localhost:11434",                  # sanctioned
        "127.0.0.1:11434",                  # sanctioned loopback
        "[::1]:11434",                      # sanctioned loopback IPv6
        "host.docker.internal",             # sanctioned docker host
        "0.0.0.0",                          # sanctioned any-interface
        "ollama:11434",                     # hostname endpoint (not a literal)
        "example.com",                      # public hostname
        "8.8.8.8",                          # PUBLIC dotted IP -> not private
    ],
)
def test_adv207_sanctioned_and_public_hosts_allowed(host):
    assert release_check._host_is_private(host) is False, host


def test_adv207_tracked_file_with_obfuscated_endpoints_fails_scan(scratch_repo):
    """A tracked source file carrying the whole ADV-207 bypass battery must fail
    the private-endpoint scan (end-to-end through ``run_all``)."""
    _configure_scratch_repo(scratch_repo)
    (scratch_repo / "sample.service.ts").write_text(
        "const endpoints = [\n"
        "  'http://3232235777:11434',\n"
        "  'http://0xC0A80101:11434/',\n"
        "  'http://0300.0250.0001.0001:11434/',\n"
        "  'http://[fd00::1]:11434/',\n"
        "  'http://[fc00::abcd]:11434/call',\n"
        "  'http://[fe80::1]/',\n"
        "  'http://user:pass@192.168.1.5:11434/',\n"
        "  'http://192.168.1.5.evil.com/',\n"
        "];\n",
        encoding="utf-8",
    )
    _git(scratch_repo, "add", ".")
    _git(scratch_repo, "commit", "-qm", "add service sample")
    tracked = release_check.git_tracked_files(scratch_repo)
    findings = release_check.run_all(scratch_repo, allow_hosted=True)
    fails = [f for f in findings if f.severity == "fail" and f.check == "private-endpoint"]
    # Every one of the eight literals is a distinct fail (or at least the scan
    # does not answer ok).
    assert fails, [f.render() for f in findings]
    assert len(fails) >= 6, [f.render() for f in fails]


# --------------------------------------------------------------------------- #
# ADV-210 — dimensions_of: dedupe, bound, fail-closed guards + pre-18C fallback
# --------------------------------------------------------------------------- #


def _facts(ids):
    return [
        {"id": i, "discoverable": True, "presentation": {"title": f"Title {i}"}, "kind": "cctv"}
        for i in ids
    ]


def _proof_payload(dim_refs, flat, evidence_ids):
    return {
        "draft": {"evidence": _facts(evidence_ids)},
        "solverProof": {
            "evidence_ids_used": flat,
            "who_evidence_ids": list(dim_refs.get("who", [])),
            "why_evidence_ids": list(dim_refs.get("why", [])),
            "weapon_evidence_ids": list(dim_refs.get("weapon", [])),
            "time": dict(dim_refs.get("time", {})),
        },
    }


_EVIDENCE = ["e0", "e1", "e2", "e3", "e4", "e5"]


def test_adv210_dimension_ids_are_deduped():
    payload = _proof_payload(
        {"who": ["e0", "e0", "e1", "e1"], "why": ["e2"], "weapon": ["e3"],
         "time": {"critical_evidence_ids": ["e4", "e5"]}},
        _EVIDENCE, _EVIDENCE,
    )
    dims = dimensions_of(payload)
    who = [p["evidenceId"] for p in dims["who"]]
    assert who == ["e0", "e1"], who


def test_adv210_oversized_dimension_ref_fails_closed():
    big = [f"e{i}" for i in range(1000)]
    payload = _proof_payload(
        {"who": big, "why": ["e2"], "weapon": ["e3"],
         "time": {"critical_evidence_ids": ["e4", "e5"]}},
        _EVIDENCE, _EVIDENCE,
    )
    with pytest.raises(RevealProjectionError):
        dimensions_of(payload)


def test_adv210_oversized_flat_ref_fails_closed():
    big = [f"e{i}" for i in range(1000)]
    payload = _proof_payload(
        {"who": [], "why": [], "weapon": [], "time": {}}, big, _EVIDENCE,
    )
    with pytest.raises(RevealProjectionError):
        dimensions_of(payload)


def test_adv210_garbage_element_types_fail_closed():
    payload = _proof_payload(
        {"who": ["e0", {"a": 1}], "why": ["e2"], "weapon": ["e3"],
         "time": {"critical_evidence_ids": ["e4", "e5"]}},
        _EVIDENCE, _EVIDENCE,
    )
    with pytest.raises(RevealProjectionError):
        dimensions_of(payload)


def test_adv210_garbage_time_section_fails_closed():
    payload = {
        "draft": {"evidence": _facts(_EVIDENCE)},
        "solverProof": {
            "evidence_ids_used": _EVIDENCE,
            "who_evidence_ids": [],
            "why_evidence_ids": [],
            "weapon_evidence_ids": [],
            "time": ["not", "an", "object"],
        },
    }
    with pytest.raises(RevealProjectionError):
        dimensions_of(payload)


def test_adv210_mismatched_id_sets_fail_closed():
    payload = _proof_payload(
        {"who": ["e0"], "why": [], "weapon": [], "time": {}},
        _EVIDENCE, _EVIDENCE,
    )
    with pytest.raises(RevealProjectionError):
        dimensions_of(payload)


def test_adv210_pre18c_fallback_populates_all_four_dimensions():
    """solverProof + non-empty evidence_ids_used, NO per-dimension keys -> every
    usable id is distributed to all four dimensions (deterministic fallback)."""
    pre18c = {
        "draft": {"evidence": _facts(_EVIDENCE)},
        "solverProof": {
            "evidence_ids_used": _EVIDENCE,
            "time": {"critical_evidence_ids": ["e4", "e5"]},
        },
    }
    dims = dimensions_of(pre18c)
    for name in ("who", "why", "weapon", "when"):
        ids = [p["evidenceId"] for p in dims[name]]
        assert ids == sorted(_EVIDENCE), name
    # union(dims) == flat (the invariant keeps holding under the fallback).
    union = {
        p["evidenceId"]
        for name in dims
        for p in dims[name]
    }
    assert union == set(_EVIDENCE)


def test_adv210_empty_proof_resilience_unchanged():
    payload = _proof_payload(
        {"who": [], "why": [], "weapon": [], "time": {}}, [], _EVIDENCE,
    )
    dims = dimensions_of(payload)
    assert all(dims[name] == [] for name in dims)


def test_adv210_pre18c_published_case_still_reveals_200_with_populated_board(phase5_app):
    """END-TO-END: a PUBLISHED v2 row whose solverProof is the PRE-18C shape
    (evidence_ids_used + time, no per-dimension keys) reveals 200 with a
    non-empty proof board, and the flat explanation list is unchanged from the
    published source of truth."""
    bundle = create_published_case_and_playthrough(phase5_app)
    case_id = bundle["caseId"]
    creator = bundle["creator"]
    store = phase5_app.state.store
    now = float(phase5_app.state.clock.now())

    v1 = store.get_published(case_id, 1)
    assert v1 is not None
    payload = json.loads(v1.payload_json)
    proof = payload.get("solverProof", {})
    assert proof.get("evidence_ids_used"), "golden payload must carry flat ids"
    # Simulate a case published BEFORE Phase 18C: the per-dimension keys do not
    # exist at all (and the reveal must still have worked back then).
    proof.pop("who_evidence_ids", None)
    proof.pop("why_evidence_ids", None)
    proof.pop("weapon_evidence_ids", None)
    payload["solverProof"] = proof
    payload["caseVersion"] = 2
    payload["publishedAt"] = now

    store.create_case_version(
        case_id=case_id, version=2, state="PUBLISHED",
        generation_id="GEN-2", created_at=now,
    )
    store.insert_published(
        case_id=case_id,
        case_version=2,
        payload_json=json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ),
        published_at=now,
    )

    truth = truth_bundle(phase5_app, case_id, 2)
    pt_id, pt_token = new_playthrough(phase5_app, case_id, creator, version=2)
    with phase6_client(phase5_app) as c:
        res = make_accusation(c, pt_id, pt_token, winning_body(truth))
        assert res.status_code == 200, res.json()
        reveal = get_reveal(c, pt_id, pt_token)
        assert reveal.status_code == 200, reveal.json()
        if reveal.status_code != 200:
            assert_sanitized_error(reveal.text)
        body = reveal.json()

    explanation = body["explanation"]
    dims = explanation["dimensions"]
    assert all(dims[name] for name in ("who", "why", "weapon", "when")), dims
    # Flat evidence list is exactly the published evidence_ids_used (deduped),
    # unchanged by the fallback machinery.
    flat = explanation["evidence"]
    expected_flat_ids = sorted(set(str(i) for i in proof["evidence_ids_used"]))
    assert expected_flat_ids, "flat explainer must be non-empty"
    assert sorted(p["evidenceId"] for p in flat) == expected_flat_ids
    union = {p["evidenceId"] for name in dims for p in dims[name]}
    assert union == set(expected_flat_ids)
