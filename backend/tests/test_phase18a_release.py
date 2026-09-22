"""Phase 18A — submission trust / release hygiene (backend track).

Covers, with hermetic environments (ENV_FILE=os.devnull + operator .env keys
removed by the autouse conftest fixture — the operator LAN host is NEVER read):

1. tools.release_check behaviour as a unit-testable suite (placeholders,
   THIRD_PARTY.md, tracked secrets/logs/dbs, private endpoint leakage,
   Docker build-context hygiene, frontend build scan);
2. no placeholder release links (hosting/video placeholders are REPORT-only
   under the same --allow-hosted-placeholders semantics; the test passes both
   now and on the final post-docs state);
3. a git-based assertion that .env / logs/ / *.db / *.sqlite / *.log are never
   tracked;
4. no provider endpoint / LAN IP in any player-facing DTO (bootstrap /
   discover / read / capabilities / generation status / reveal) and — when the
   gitignored frontend build exists — in the build output either;
5. PD_DEV_TRACE stays OFF by default and never overrides production behavior.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Make ``tools`` importable from the repo root (same layout the release CLI
# uses: python -m tools.release_check).
_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_REPO_ROOT), str(_REPO_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from phase5_helpers import auth, create_case, create_session  # noqa: E402
from phase6_helpers import client as phase6_client  # noqa: E402
from test_phase7_helpers import (  # noqa: E402
    create_published_case_and_playthrough,
    make_accusation,
    truth_bundle,
    winning_body,
)

from tools import release_check  # noqa: E402

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _iter_string_values(node) -> list[str]:
    """Every string value (depth-first) of a JSON-ish response tree."""
    out: list[str] = []
    if isinstance(node, str):
        out.append(node)
    elif isinstance(node, dict):
        for value in node.values():
            out.extend(_iter_string_values(value))
    elif isinstance(node, (list, tuple)):
        for value in node:
            out.extend(_iter_string_values(value))
    return out


def _assert_no_provider_endpoint_texts(bodies: list[dict | str]) -> None:
    """Every player-DTO string must stay free of the Ollama port and URL hosts."""
    for body in bodies:
        for text in _iter_string_values(body):
            assert "11434" not in text, f"provider port leaked in player DTO: {text!r}"
            assert "-11434" not in text, f"provider port leaked in player DTO: {text!r}"
            assert "://" not in text, f"URL host leaked in player DTO: {text!r}"
            assert not release_check._PRIVATE_V4_RE.search(text), (
                f"private/LAN IP leaked in player DTO: {text!r}"
            )


@pytest.fixture
def hermetic_env(monkeypatch):
    """Guarantee the operator .env can never bleed into a check (belt + braces
    on top of the autouse conftest fixture)."""
    monkeypatch.setenv("ENV_FILE", os.devnull)
    return monkeypatch


# --------------------------------------------------------------------------- #
# part 1 — tools.release_check as a testable suite
# --------------------------------------------------------------------------- #


def test_release_docs_placeholder_scan_current_state(hermetic_env):
    """The doc placeholder scan must pass NOW and on the final post-docs state.

    On the CURRENT tree the docs track already landed the judge-facing docs:
    repository links are real; the only remaining placeholders are the explicit
    hosting/video statements ("lands here after hosting is chosen", "TBD after
    recording"), which are REPORT-only under --allow-hosted-placeholders. Once
    hosting/video exist and the docs track fills them in, the scan finds
    nothing and the same assertions still hold (empty is a satisfying state).
    """
    # What must hold NOW (docs track has landed): README + SUBMISSION exist
    # and every remaining placeholder is hosting/video-only (reported).
    findings = release_check.scan_placeholders(_REPO_ROOT, allow_hosted=True)
    hard = [f for f in findings if f.severity == "fail"]
    reports = [f for f in findings if f.severity == "report"]

    # If the docs track were mid-flight, the ONLY acceptable hard findings are
    # the documented repo/asset-table markers the docs track owns.
    docs_owned = ("<owner>", "<repo-url>", "placeholder")
    for finding in hard:
        assert finding.check == "placeholders"
        assert any(token in finding.message for token in docs_owned), (
            f"unexpected hard release blocker: {finding.message}"
        )

    # Hosting/video placeholders are REPORTED (never fail) under the flag, and
    # every report is a hosting/video-dependent statement.
    for finding in reports:
        assert "hosting/video placeholder" in finding.message, finding.message

    # AFTER the docs track lands, the scan must pass cleanly: simulate by
    # asserting the checker's contract — the scanner never FAILS on hosted
    # statements when allow_hosted is set.
    simulated = release_check.scan_placeholders(_REPO_ROOT, allow_hosted=True)
    assert not any(f.severity == "fail" and "hosting/video placeholder" in f.message
                   for f in simulated)


def test_release_tool_hosted_placeholder_tokens_classify_report_only(tmp_path, hermetic_env):
    """Hosting/video placeholder tokens are REPORT when the flag is on and FAIL
    when it is off — the documented --allow-hosted-placeholders semantics."""
    (tmp_path / "README.md").write_text(
        "*Hosted demo URL lands here after hosting is chosen.*\n"
        "*Demo video URL: `TBD after recording`.*\n",
        encoding="utf-8",
    )
    (tmp_path / "SUBMISSION.md").write_text(
        "| Live demo | `https://<demo-host>/` (placeholder) |\n", encoding="utf-8"
    )
    findings_report = [f for f in release_check.scan_placeholders(tmp_path, allow_hosted=True)
                       if f.severity == "fail"]
    findings_strict = [f for f in release_check.scan_placeholders(tmp_path, allow_hosted=False)
                       if f.severity == "fail"]
    assert findings_report == []  # with the flag: never a blocker
    assert findings_strict != []  # without the flag: still a blocker


def test_release_tool_hard_placeholder_token_never_allowed(tmp_path, hermetic_env):
    """Non-hosting placeholder tokens fail with AND without the flag."""
    docs = tmp_path / "README.md"
    docs.write_text("Repository: `https://github.com/<owner>/repo`\nTODO\n", encoding="utf-8")
    for allow_hosted in (False, True):
        fail_messages = [
            f.message for f in release_check.scan_placeholders(tmp_path, allow_hosted=allow_hosted)
            if f.severity == "fail"
        ]
        assert any("<owner>" in m or "TODO" in m for m in fail_messages), fail_messages


def test_release_tool_generic_github_root_link_fails(tmp_path, hermetic_env):
    docs = tmp_path / "README.md"
    docs.write_text("See https://github.com/ for more.\n", encoding="utf-8")
    fail_messages = [
        f.message for f in release_check.scan_placeholders(tmp_path, allow_hosted=True)
        if f.severity == "fail"
    ]
    assert any("generic GitHub-root link" in m for m in fail_messages)


def test_release_tool_third_party_docs_owned(tmp_path, hermetic_env):
    """THIRD_PARTY.md is docs-owned: best-effort assertion that self-skips when
    the docs track has not landed it yet, and passes when it exists."""
    if not (_REPO_ROOT / "THIRD_PARTY.md").is_file():
        pytest.skip("THIRD_PARTY.md is owned by the docs track and not present yet")
    findings = release_check.check_third_party(_REPO_ROOT)
    assert all(f.severity == "ok" for f in findings)


def test_release_tool_tracked_secrets_and_logs_never_tracked(hermetic_env):
    """git ls-files must not list .env / logs/ / *.db / *.sqlite / *.log."""
    tracked = release_check.git_tracked_files(_REPO_ROOT)
    assert tracked is not None, "git ls-files failed — test requires a git repo"
    findings = release_check.check_tracked_secrets(tracked)
    assert all(f.severity == "ok" for f in findings), [f.message for f in findings]
    # Belt-and-braces direct assertion (the tool's own matcher).
    assert release_check.tracked_secret_entries(tracked) == []
    assert all(not rel.endswith(".env") for rel in tracked)
    assert ".env" not in tracked


def test_release_tool_private_endpoint_scan_current_tree_clean(hermetic_env):
    """The tracked release surface carries no LAN IP / private URL / provider
    URL literal (test vectors and the config allowlist are documented
    exceptions)."""
    tracked = release_check.git_tracked_files(_REPO_ROOT)
    assert tracked is not None, "git ls-files failed — test requires a git repo"
    findings = release_check.scan_private_endpoints(_REPO_ROOT, tracked)
    fail_messages = [f.message for f in findings if f.severity == "fail"]
    assert fail_messages == [], fail_messages


def test_release_tool_frontend_build_scan(tmp_path, hermetic_env):
    """Frontend build scan: skips cleanly when absent; fails on private IP /
    provider endpoint literals / the 11434 port; reports gated debug aids."""
    absent = release_check.scan_frontend_build(tmp_path / "does-not-exist")
    assert absent == [f for f in absent if f.severity == "skip"]

    build = tmp_path / "dist"
    build.mkdir()
    (build / "index.html").write_text(
        "<html><body>ok</body></html>\n", encoding="utf-8"
    )
    (build / "leaky.js").write_text(
        "const url = 'http://192.168.1.5:11434/api/chat';\n"
        "const port = ':11434';\n"
        "if (window.location.search.includes('pd-debug-pick=1')) window.__pdDebugScene = 1;\n",
        encoding="utf-8",
    )
    findings = release_check.scan_frontend_build(build)
    fail_messages = [f.message for f in findings if f.severity == "fail"]
    report_messages = [f.message for f in findings if f.severity == "report"]
    assert any("192.168" in m or "private" in m for m in fail_messages)
    assert any(":11434" in m or "11434" in m for m in fail_messages)
    assert any("pd-debug-pick" in m for m in report_messages)
    assert any("__pdDebugScene" in m for m in report_messages)


def test_release_tool_dockerignore_check(tmp_path, hermetic_env):
    """PD-SEC-07: the dockerignore gate fails when the file is missing or when
    a required secret/log/db exclusion is absent, and passes on the real repo
    ``.dockerignore``."""
    # Missing file -> fail.
    missing = release_check.check_dockerignore(tmp_path)
    assert missing == [f for f in missing if f.severity == "fail"]
    assert any("missing" in f.message for f in missing)

    # Valid .dockerignore -> ok.
    (tmp_path / ".dockerignore").write_text(
        ".env\n"
        ".env.*\n"
        "!.env.example\n"
        "\n"
        "logs/\n"
        "*.log\n"
        "*.db\n"
        "*.sqlite\n"
        "*.sqlite3\n"
        "tmp/\n"
        "temp/\n"
        ".ollama/\n",
        encoding="utf-8",
    )
    good = release_check.check_dockerignore(tmp_path)
    assert [f for f in good if f.severity == "ok"] == good, [f.message for f in good]

    # Dropping the .env.* variant -> fail naming the missing pattern.
    (tmp_path / ".dockerignore").write_text(
        ".env\n!.env.example\nlogs/\n*.db\n", encoding="utf-8"
    )
    bad = release_check.check_dockerignore(tmp_path)
    assert [f for f in bad if f.severity == "fail"] == bad, [f.message for f in bad]
    assert any(".env.*" in f.message for f in bad)

    # The actual repo .dockerignore must satisfy every required exclusion.
    repo = release_check.check_dockerignore(_REPO_ROOT)
    assert [f for f in repo if f.severity == "ok"] == repo, [f.message for f in repo]


def test_release_tool_frontend_build_flags_dev_hosts(tmp_path, hermetic_env):
    """Phase20 PD-SEC-04: a production bundle embedding http://localhost:8000
    or a bare 127.0.0.1 must FAIL the frontend-build scan; a clean build must
    pass. (The current checked-out frontend/dist may legitimately still carry
    the audited fallback until the parallel frontend track rebuilds — the gate
    exists NOW and must pass on the FINAL clean build.)"""
    build = tmp_path / "dist"
    build.mkdir()
    (build / "index.html").write_text(
        "<html><body>ok</body></html>\n", encoding="utf-8"
    )
    clean = release_check.scan_frontend_build(build)
    assert [f for f in clean if f.severity == "ok"] == clean, [f.message for f in clean]

    (build / "leaky.js").write_text(
        "const base = 'http://localhost:8000';\n"
        "const alt = ['127.0.0.1'];\n",
        encoding="utf-8",
    )
    findings = release_check.scan_frontend_build(build)
    fail_messages = [f.message for f in findings if f.severity == "fail"]
    assert any("localhost:8000" in m for m in fail_messages), fail_messages
    assert any("127.0.0.1" in m for m in fail_messages), fail_messages


def test_release_tool_exits_nonzero_on_blockers_and_zero_clean(hermetic_env):
    """CLI contract: non-zero when a real blocker exists, zero when the only
    remaining findings are hosted placeholders (--allow-hosted-placeholders)
    plus gated debug aids (reports)."""
    import io
    from unittest import mock

    with mock.patch("tools.release_check.REPO_ROOT", _REPO_ROOT):
        blocked = release_check.main(["--allow-hosted-placeholders"])
        # The current post-docs tree has no non-optional blockers.
        assert blocked == 0

    # A hostile tree (hard placeholder + a tracked secret marker) blocks.
    with mock.patch.object(release_check, "REPO_ROOT", new=_REPO_ROOT):
        with mock.patch.object(
            release_check, "scan_placeholders",
            return_value=[release_check.Finding("placeholders", "fail", "TODO found")],
        ):
            assert release_check.main(["--allow-hosted-placeholders"]) == 1


# --------------------------------------------------------------------------- #
# part 2 — no provider endpoint / LAN IP in player DTOs or the frontend build
# --------------------------------------------------------------------------- #


def test_player_dtos_never_contain_provider_endpoint_or_lan_ip(phase5_app):
    """Every player-facing DTO (bootstrap/discover/read/capabilities/generation
    status/public case/reveal) stays free of the Ollama port, URL hosts and
    private/LAN IPs — verified over REAL API responses."""
    with phase6_client(phase5_app) as c:
        caps = c.get("/api/v1/generation-capabilities")
        assert caps.status_code == 200
        bodies: list[dict | str] = [caps.json()]
        _assert_no_provider_endpoint_texts(bodies)

        session = c.post("/api/v1/sessions/anonymous")
        session_token = session.json()["anonymousSessionToken"]
        bodies.append(session.json())
        _assert_no_provider_endpoint_texts(bodies)

        case = c.post(
            "/api/v1/cases",
            json={"prompt": "Victim: sarah_miller\nMurderer: thomas_reed\n"},
            headers=auth(session_token),
        )
        assert case.status_code == 201, case.json()
        bodies.append(case.json())
        _assert_no_provider_endpoint_texts(bodies)
        case_id = case.json()["caseId"]
        creator = case.json()["creatorAccessToken"]

        gen_status = c.get(
            f"/api/v1/generations/{case.json()['generationId']}", headers=auth(creator)
        )
        assert gen_status.status_code == 200
        bodies.append(gen_status.json())
        _assert_no_provider_endpoint_texts(bodies)

        public_case = c.get(f"/api/v1/cases/{case_id}", headers=auth(creator))
        assert public_case.status_code == 200
        bodies.append(public_case.json())
        _assert_no_provider_endpoint_texts(bodies)

        pt = c.post(
            f"/api/v1/cases/{case_id}/versions/1/playthroughs", headers=auth(creator)
        )
        assert pt.status_code == 201
        bodies.append(pt.json())
        _assert_no_provider_endpoint_texts(bodies)
        pt_id = pt.json()["playthroughId"]
        pt_token = pt.json()["playthroughAccessToken"]

        bootstrap = c.get(f"/api/v1/playthroughs/{pt_id}", headers=auth(pt_token))
        assert bootstrap.status_code == 200
        bodies.append(bootstrap.json())
        _assert_no_provider_endpoint_texts(bodies)

        investigate = c.get(
            f"/api/v1/playthroughs/{pt_id}/investigation", headers=auth(pt_token)
        )
        assert investigate.status_code == 200
        bodies.append(investigate.json())
        _assert_no_provider_endpoint_texts(bodies)

        # interact + read a real record through the API (golden evidence id;
        # PD-SEC-01: the direct discover route was removed — discovery happens
        # through the validated object interaction).
        from phase6_helpers import KNIFE_EVIDENCE, KNIFE_OBJECT, interact, read_record

        discovery = interact(phase5_app, pt_id, pt_token, KNIFE_OBJECT, "inspect")
        assert discovery.status_code == 200, discovery.json()
        bodies.append(discovery.json())
        _assert_no_provider_endpoint_texts(bodies)

        record = read_record(phase5_app, pt_id, pt_token, KNIFE_EVIDENCE)
        assert record.status_code == 200, record.json()
        bodies.append(record.json())
        _assert_no_provider_endpoint_texts(bodies)

        reveal = c.get(f"/api/v1/playthroughs/{pt_id}/reveal", headers=auth(pt_token))
        assert reveal.status_code == 403  # pre-accusation is a clean 403 envelope
        bodies.append(reveal.json())
        _assert_no_provider_endpoint_texts(bodies)

    # reveal after a winning accusation is also clean.
    bundle = create_published_case_and_playthrough(phase5_app)
    truth = truth_bundle(phase5_app, bundle["caseId"], 1)
    with phase6_client(phase5_app) as c:
        res = make_accusation(
            c, bundle["playthroughId"], bundle["playthroughToken"], winning_body(truth)
        )
        assert res.status_code == 200, res.json()
        bodies.append(res.json())
        _assert_no_provider_endpoint_texts(bodies)
        final = c.get(
            f"/api/v1/playthroughs/{bundle['playthroughId']}/reveal",
            headers=auth(bundle["playthroughToken"]),
        )
        assert final.status_code == 200, final.json()
        bodies.append(final.json())
        _assert_no_provider_endpoint_texts(bodies)
    _assert_no_provider_endpoint_texts(bodies)


def test_frontend_build_dist_has_no_provider_endpoint_when_present(hermetic_env):
    """frontend/dist is gitignored; when it exists the release scan must find no
    private host/IP/URL literal, no :11434 and no provider config names."""
    dist = _REPO_ROOT / "frontend" / "dist"
    if not dist.is_dir():
        pytest.skip("frontend build output not present (git-ignored)")
    findings = release_check.scan_frontend_build(dist)
    fail_messages = [f.message for f in findings if f.severity == "fail"]
    assert fail_messages == [], fail_messages


# --------------------------------------------------------------------------- #
# part 3 — the demo must stay byte-identical for fake mode
# --------------------------------------------------------------------------- #


def test_fake_provider_default_path_byte_identical_and_offline(phase5_app):
    """GENERATION_PROVIDER=fake (the default) is deterministic and makes NO
    network calls: two identical prompts publish IDENTICAL golden content
    (the opaque caseId / timestamp shell differs by design). The network-block
    autouse fixture guarantees zero external sockets."""
    import json as _json

    with phase6_client(phase5_app) as c:
        session_token, _ = create_session(c)
        first = create_case(c, session_token)
        second = create_case(c, session_token)
        assert first["status"] == "PUBLISHED"
        assert second["status"] == "PUBLISHED"
        payload_a = phase5_app.state.store.get_published(first["caseId"], 1)
        payload_b = phase5_app.state.store.get_published(second["caseId"], 1)
        draft_a = _json.loads(payload_a.payload_json)["draft"]
        draft_b = _json.loads(payload_b.payload_json)["draft"]
        # Byte-identical golden WORLD material across separate runs.
        assert draft_a == draft_b


# --------------------------------------------------------------------------- #
# part 4 — PD_DEV_TRACE stays OFF by default in production
# --------------------------------------------------------------------------- #


def test_pd_dev_trace_default_off_subprocess():
    """PD_DEV_TRACE is OFF in a clean environment (no env override) and only
    the literal value 'true' ever enables it — never a truthy variant."""
    import json as _json

    backend_dir = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    for key in ("PD_DEV_TRACE", "ENV_FILE", "GENERATION_PROVIDER"):
        env.pop(key, None)

    probes = {
        "(unset)": None,
        "false": "false",
        "1": "1",
        "TRUE": "TRUE",
        "true": "true",
    }
    script = (
        "import json, sys;\n"
        "from app.services import generation as g;\n"
        "from app.generation import controller as c;\n"
        "from app.services import ollama_driver as od;\n"
        "print(json.dumps([g._PD_DEV_TRACE, c._PD_DEV_TRACE, od._PD_DEV_TRACE]))"
    )
    for label, value in probes.items():
        probe_env = dict(env)
        if value is not None:
            probe_env["PD_DEV_TRACE"] = value
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(backend_dir),
            env=probe_env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        states = _json.loads(result.stdout.strip())
        expected = value == "true"
        assert all(state == expected for state in states), (label, states)
        assert states == [expected, expected, expected], (label, states)


def test_pd_dev_trace_never_read_loosely_in_backend_source():
    """The only way the backend reads PD_DEV_TRACE is the exact literal
    ``os.environ.get("PD_DEV_TRACE") == "true"`` gating — never a truthy
    parsing that a hostile value could flip on."""
    backend_dir = Path(__file__).resolve().parents[1]
    for rel in (
        "app/services/generation.py",
        "app/generation/controller.py",
        "app/services/ollama_driver.py",
    ):
        source = (backend_dir / rel).read_text(encoding="utf-8")
        for line in source.splitlines():
            stripped = line.strip()
            if "PD_DEV_TRACE" in stripped and stripped.startswith("_PD_DEV_TRACE") and "=" in stripped:
                assert 'os.environ.get("PD_DEV_TRACE") == "true"' in stripped, (
                    f"{rel} reads PD_DEV_TRACE in a non-strict way: {stripped}"
                )