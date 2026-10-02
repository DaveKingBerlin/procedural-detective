"""Phase 24 — hermetic tests for ``tools.real_ollama_regression``.

Never a real Ollama call (the backend suite's autouse network block forbids it,
matching the repo rule that real-Ollama calls are NEVER part of pytest). The
runner logic is exercised against the SAME ``MockOllamaTransport`` staged
fixture the Phase 19J backend suite uses, so the full controller/driver path
PUBLISHES hermetically exactly like the backend tests. The env-required guard
and release-gate threshold logic are tested with monkeypatched env / stubbed
items.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _REPO_ROOT / "backend"
_BACKEND_TESTS = _BACKEND / "tests"
for _path in (str(_BACKEND), str(_BACKEND_TESTS)):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from tools import real_ollama_regression as ror  # noqa: E402


@pytest.mark.parametrize(
    ("env", "expected_provider"),
    [
        (
            {"GENERATION_PROVIDER": "", "OLLAMA_BASE_URL": "", "OLLAMA_MODEL": ""},
            "",
        ),
        (
            {"GENERATION_PROVIDER": "fake"},
            "fake",
        ),
        (
            {"GENERATION_PROVIDER": "ollama"},
            "ollama",  # base/model missing -> fails too
        ),
        (
            {"GENERATION_PROVIDER": "OLLAMA"},
            "OLLAMA",
        ),
    ],
)
def test_require_real_ollama_env_fails_truthfully(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], expected_provider: str
) -> None:
    monkeypatch.delenv("OLLAMA_BASE_URL", raising=False)
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    with pytest.raises(ror.RealOllamaConfigError) as excinfo:
        ror._require_real_ollama_env()
    assert "ollama" in str(excinfo.value).lower()
    # NEVER a silent fallback: the error is a typed config failure.
    assert isinstance(excinfo.value, ror.RealOllamaConfigError)


def test_require_real_ollama_env_accepts_operator_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GENERATION_PROVIDER", "ollama")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
    monkeypatch.setenv("OLLAMA_MODEL", "hermes3:8b")
    ror._require_real_ollama_env()  # must not raise


# --------------------------------------------------------------------------- #
# real provider path, mocked transport (full controller/driver PUBLISHED)
# --------------------------------------------------------------------------- #


def _staged_settings() -> object:
    from app.core.config import Settings

    return Settings(generation_provider="ollama", ollama_model="llama3.2:3b")


def _mock_provider_factory():
    """A provider factory whose OllamaProvider runs over the staged mock
    transport (the Phase 19J driver fixture) — hermetic, zero network."""
    from test_ollama_driver import MockOllamaTransport, _staged
    from app.generation.ollama_provider import OllamaProvider

    OLLAMA_BASE = "http://127.0.0.1:11434"
    OLLAMA_MODEL = "llama3.2:3b"

    def _factory():
        posts = _staged()
        transport = MockOllamaTransport(posts=posts)
        return OllamaProvider(
            base_url=OLLAMA_BASE,
            model=OLLAMA_MODEL,
            timeout_seconds=5,
            structured_output=False,
            transport=transport,
        )

    return _factory


def test_matrix_runs_published_hermetically(monkeypatch: pytest.MonkeyPatch) -> None:
    from backend.tests.test_ollama_driver import PROMPT as DRIVER_PROMPT

    settings = _staged_settings()
    factory = _mock_provider_factory()
    # The driver fixture is the SINGLE showcase world (Anna Weiss / Paul
    # Becker / bronze ice pick / 23:42). Drive all four matrix items with the
    # fixture-compatible prompt so locked constraints never fail the run; each
    # item still gets a FRESH provider+transport (a real full chain per item).
    monkeypatch.setattr(
        ror,
        "_MATRIX_ITEMS",
        (
            ("easy", DRIVER_PROMPT),
            ("medium", DRIVER_PROMPT),
            ("procedural_arbitrary_object", DRIVER_PROMPT),
            ("activity_log", DRIVER_PROMPT),
        ),
    )
    report = ror.run_matrix(
        settings,
        factory,
        {"probeAvailable": True, "structuredOutputProbe": {"supported": False, "probeUrl": None}},
    )
    assert report["mode"] == "matrix"
    items = report["items"]
    assert len(items) == 4
    # The staged fixture world is the driver golden equivalent; the driver
    # must PUBLISH every item (the controller gate allows it).
    assert all(item["state"] == "PUBLISHED" for item in items), items
    summary = report["summary"]
    assert summary["published"] == 4
    assert summary["failed"] == 0


def test_activity_log_live_validation_reports_pass() -> None:
    from backend.tests.test_ollama_driver import _alog, _j

    settings = _staged_settings()

    def factory():
        from backend.tests.test_ollama_driver import MockOllamaTransport
        from app.generation.ollama_provider import OllamaProvider

        # The activity-log round-trip provider only needs the alog responses:
        # an initial valid 17-row log (passes on the FIRST attempt).
        posts = [_j(_alog("2026-09-11T23:42:00+02:00"))]
        transport = MockOllamaTransport(posts=posts)
        return OllamaProvider(
            base_url="http://127.0.0.1:11434",
            model="llama3.2:3b",
            timeout_seconds=5,
            structured_output=False,
            transport=transport,
        )

    activity = ror._activity_log_live_validation(settings, factory)
    assert activity["pass"] is True
    assert activity["entryCounts"]
    assert all(15 <= count <= 20 for count in activity["entryCounts"])


def test_report_never_contains_prompt_url_or_truth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from backend.tests.test_ollama_driver import PROMPT as DRIVER_PROMPT

    monkeypatch.setattr(
        ror,
        "_MATRIX_ITEMS",
        (
            ("easy", DRIVER_PROMPT),
            ("medium", DRIVER_PROMPT),
            ("procedural_arbitrary_object", DRIVER_PROMPT),
            ("activity_log", DRIVER_PROMPT),
        ),
    )
    settings = _staged_settings()
    factory = _mock_provider_factory()
    report = ror.run_matrix(
        settings,
        factory,
        {"probeAvailable": True, "structuredOutputProbe": {"supported": False, "probeUrl": None}},
    )
    blob = json.dumps(report)
    for forbidden in (
        "OLLAMA_BASE_URL",
        "http://127.0.0.1:11434",
        "solverProof",
        "caseTruth",
        "murdererId",
        "prompt",
        "promptText",
        "rawOutput",
    ):
        assert forbidden not in blob, forbidden
    # The base URL is NEVER reported even structurally:
    assert report["probe"]["structuredOutputProbe"]["probeUrl"] is None


# --------------------------------------------------------------------------- #
# release gate threshold logic (stubbed items — pure decision math)
# --------------------------------------------------------------------------- #


def _stub_matrix_item(state: str, outcome: str = "valid"):
    def _stub(settings, provider_factory, name, prompt):
        return {
            "item": name,
            "state": state,
            "failureCode": None,
            "failedStage": "none",
            "validationOutcome": outcome,
            "providerCalls": 3,
            "elapsedSeconds": 1.0,
            "reportElapsedSeconds": 1.0,
            "repairCount": 0,
            "procPlacementCount": 0,
        }

    return _stub


def test_release_gate_requires_ten_generations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        ror, "_matrix_item", _stub_matrix_item("PUBLISHED", "valid")
    )
    monkeypatch.setattr(
        ror, "_activity_log_live_validation",
        lambda settings, factory: {"pass": True, "maxRepairPasses": 2,
                                   "passCount": 1, "elapsedSeconds": 0.1,
                                   "entryCounts": [17], "validatorCodes": [[]]},
    )
    report = ror.run_release_gate(
        _staged_settings(), lambda: None, {"probeAvailable": True},
        max_generations=5,
        # This test pins the NUMERIC threshold; journey-evidence policy is
        # covered by the DEF-007 tests below, so the operator opt-in keeps
        # the numeric gate the focus here.
        allow_skip_journeys=True,
    )
    assert report["generations"] == 10  # minimum enforced
    assert report["gatePassed"] is True
    assert report["summary"]["published"] == 10
    assert report["summary"]["incorrect"] == 0
    assert report["summary"]["partial"] == 0


def test_release_gate_fails_on_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def _stub(settings, provider_factory, name, prompt):
        calls["n"] += 1
        if calls["n"] <= 3:
            return _stub_matrix_item("FAILED", "valid")(settings, provider_factory, name, prompt)
        return _stub_matrix_item("PUBLISHED", "valid")(settings, provider_factory, name, prompt)

    monkeypatch.setattr(ror, "_matrix_item", _stub)
    monkeypatch.setattr(
        ror, "_activity_log_live_validation",
        lambda settings, factory: {"pass": True, "maxRepairPasses": 2,
                                   "passCount": 1, "elapsedSeconds": 0.1,
                                   "entryCounts": [17], "validatorCodes": [[]]},
    )
    report = ror.run_release_gate(
        _staged_settings(), lambda: None, {"probeAvailable": True},
        max_generations=10,
        allow_skip_journeys=True,  # numeric policy only (DEF-007 tests below)
    )
    # 7/10 published = 70% < 90% -> gate must FAIL truthfully.
    assert report["gatePassed"] is False
    assert report["summary"]["published"] == 7


def test_release_gate_fails_on_activity_log_pass_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ror, "_matrix_item", _stub_matrix_item("PUBLISHED", "valid"))
    monkeypatch.setattr(
        ror, "_activity_log_live_validation",
        lambda settings, factory: {"pass": False, "maxRepairPasses": 2,
                                   "passCount": 1, "elapsedSeconds": 0.1,
                                   "entryCounts": [9], "validatorCodes": [
                                       ["ENTRY_COUNT_TOO_LOW"]]},
    )
    report = ror.run_release_gate(
        _staged_settings(), lambda: None, {"probeAvailable": True},
        max_generations=10,
        allow_skip_journeys=True,  # activity-log policy only (DEF-007 tests)
    )
    assert report["gatePassed"] is False
    assert report["summary"]["activityLogPass"] is False


def test_operator_journey_reports_loaded(tmp_path: Path) -> None:
    witness = tmp_path / "witness.json"
    witness.write_text(json.dumps({"ok": True, "checks": 4}), encoding="utf-8")
    loaded = ror._load_operator_report(str(witness), "witness")
    assert loaded is not None and loaded["loaded"] and loaded["ok"]
    missing = ror._load_operator_report(str(tmp_path / "nope.json"), "browser")
    assert missing is not None and missing["loaded"] is False


# --------------------------------------------------------------------------- #
# DEF-007 — §36 witness/browser journey evidence is REQUIRED for the gate
# --------------------------------------------------------------------------- #


def _gate_items_all_published(n: int = 10) -> list[dict[str, object]]:
    return [
        {
            "item": f"r{i + 1}",
            "state": "PUBLISHED",
            "failureCode": None,
            "failedStage": "none",
            "validationOutcome": "valid",
            "providerCalls": 3,
            "elapsedSeconds": 1.0,
            "reportElapsedSeconds": 1.0,
            "repairCount": 0,
            "procPlacementCount": 0,
        }
        for i in range(n)
    ]


def _run_gate(
    monkeypatch: pytest.MonkeyPatch,
    *,
    witness_report: str | None = None,
    browser_report: str | None = None,
    allow_skip_journeys: bool = False,
) -> dict[str, object]:
    monkeypatch.setattr(ror, "_matrix_item", _stub_matrix_item("PUBLISHED", "valid"))
    monkeypatch.setattr(
        ror, "_activity_log_live_validation",
        lambda settings, factory: {"pass": True, "maxRepairPasses": 2,
                                   "passCount": 1, "elapsedSeconds": 0.1,
                                   "entryCounts": [17], "validatorCodes": [[]]},
    )
    return ror.run_release_gate(
        _staged_settings(), lambda: None, {"probeAvailable": True},
        max_generations=10,
        witness_report=witness_report,
        browser_report=browser_report,
        allow_skip_journeys=allow_skip_journeys,
    )


def test_release_gate_fails_without_journey_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEF-007 default: every numeric criterion is green but the witness and
    browser journeys are MISSING -> gatePassed MUST be False."""
    report = _run_gate(monkeypatch)
    assert report["gatePassed"] is False
    assert report["summary"]["published"] == 10  # numeric side is green
    journeys = report["operatorJourneys"]
    assert journeys["witness"]["skipped"] is True
    assert journeys["browser"]["skipped"] is True
    # and _journey_passed never fabricates evidence
    assert ror._journey_passed(None) is False
    assert ror._journey_passed({"loaded": False, "error": "nope"}) is False
    assert ror._journey_passed({"loaded": True, "ok": False}) is False


def test_release_gate_passes_with_both_journey_reports(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """DEF-007: supplied reports that parse AND report ok:true -> gate passes."""
    witness = tmp_path / "witness.json"
    witness.write_text(json.dumps({"ok": True, "checks": 4}), encoding="utf-8")
    browser = tmp_path / "browser.json"
    browser.write_text(json.dumps({"ok": True, "summary": {"passed": 8, "total": 8}}), encoding="utf-8")
    report = _run_gate(monkeypatch, witness_report=str(witness), browser_report=str(browser))
    assert report["gatePassed"] is True
    assert report["summary"]["journeyEvidence"] == "present"
    assert report["operatorJourneys"]["witness"]["ok"] is True
    assert report["operatorJourneys"]["browser"]["ok"] is True


def test_release_gate_fails_when_a_journey_report_is_missing_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """DEF-007: one supplied report that fails to load MUST fail the gate."""
    witness = tmp_path / "witness.json"
    witness.write_text(json.dumps({"ok": True, "checks": 4}), encoding="utf-8")
    report = _run_gate(
        monkeypatch,
        witness_report=str(witness),
        browser_report=str(tmp_path / "missing-browser.json"),
    )
    assert report["gatePassed"] is False
    assert report["summary"]["journeyEvidence"] == "MISSING/FAILED"
    assert report["operatorJourneys"]["browser"]["loaded"] is False


def test_release_gate_fails_when_a_journey_report_is_failed_journey(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """DEF-007: a loaded journey that reports a FAILED journey must fail gate."""
    witness = tmp_path / "witness.json"
    witness.write_text(json.dumps({"ok": True, "checks": 4}), encoding="utf-8")
    browser = tmp_path / "browser.json"
    browser.write_text(json.dumps({"ok": False, "checks": {"failed": 2}}), encoding="utf-8")
    report = _run_gate(monkeypatch, witness_report=str(witness), browser_report=str(browser))
    assert report["gatePassed"] is False


def test_release_gate_allow_skip_journeys_requires_operator_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DEF-007: --allow-skip-journeys is the EXPLICIT operator opt-in; the
    skipped journeys are still REPORTED in the JSON, never fabricated."""
    report = _run_gate(monkeypatch, allow_skip_journeys=True)
    assert report["gatePassed"] is True
    assert report["allowSkipJourneys"] is True
    assert report["threshold"]["requiredOperatorJourneys"] is False
    assert report["summary"]["journeyEvidence"] == "skipped (operator opted in)"
    assert report["operatorJourneys"]["witness"]["skipped"] is True
    assert report["operatorJourneys"]["browser"]["skipped"] is True


# --------------------------------------------------------------------------- #
# DEF-006 — matrix exit code reflects the matrix result (CLI level)
# --------------------------------------------------------------------------- #


def _cli_stage(monkeypatch: pytest.MonkeyPatch, run_result: dict[str, object]) -> None:
    monkeypatch.setattr(ror, "_require_real_ollama_env", lambda: None)
    monkeypatch.setattr(ror, "_build_settings", lambda: object())
    monkeypatch.setattr(
        ror,
        "_probe_and_provider_factory",
        lambda settings: (lambda: None, {"probeAvailable": True}, True),
    )
    monkeypatch.setattr(ror, "run_matrix", lambda settings, factory, probe: run_result)


def _matrix_result(*states: str) -> dict[str, object]:
    return {
        "mode": "matrix",
        "items": [
            {
                "item": f"item{i + 1}",
                "state": state,
                "failureCode": "PROVIDER_TIMEOUT" if state != "PUBLISHED" else None,
            }
            for i, state in enumerate(states)
        ],
        "summary": {
            "items": len(states),
            "published": sum(1 for s in states if s == "PUBLISHED"),
            "failed": sum(1 for s in states if s == "FAILED"),
        },
    }


def test_matrix_cli_exit_zero_only_when_every_item_published(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _cli_stage(monkeypatch, _matrix_result("PUBLISHED", "PUBLISHED", "PUBLISHED", "PUBLISHED"))
    assert ror.main(["--matrix"]) == 0
    err = capsys.readouterr().err
    assert "matrix FAILED" not in err


def test_matrix_cli_exit_one_on_red_matrix(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """DEF-006: a matrix with ANY non-PUBLISHED item must exit 1 so the gitlab
    `real-ollama-regression` job fails truthfully (never a green job on a red
    matrix), while the report JSON keeps every per-item status + typed code."""
    _cli_stage(monkeypatch, _matrix_result("PUBLISHED", "FAILED"))
    assert ror.main(["--matrix"]) == 1
    captured = capsys.readouterr()
    assert "matrix FAILED" in captured.err
    assert '"state": "FAILED"' in captured.out
    assert '"failureCode": "PROVIDER_TIMEOUT"' in captured.out


def test_release_gate_cli_exit_code_reflects_gate_result(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(ror, "_require_real_ollama_env", lambda: None)
    monkeypatch.setattr(ror, "_build_settings", lambda: object())
    monkeypatch.setattr(
        ror,
        "_probe_and_provider_factory",
        lambda settings: (lambda: None, {"probeAvailable": True}, True),
    )
    monkeypatch.setattr(
        ror,
        "run_release_gate",
        lambda *a, **k: {"mode": "release-gate", "gatePassed": False, "summary": {}},
    )
    assert ror.main(["--release-gate", "--max-generations", "10"]) == 1
    assert "release gate FAILED" in capsys.readouterr().err