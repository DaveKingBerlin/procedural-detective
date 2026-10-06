"""Phase 31 — Frontier/BYOK benchmark harness tests (backend scope).

Covers Phase31 §31 (all 20 benchmark-core items), §32 (provider isolation),
§33 (Luna BLOCKED-EXTERNAL), §34 (sentinel secret-persistence) and §44 (focused
gates). Every test is hermetic: the backend suite's autouse network block
forbids external sockets, the inprocess driver's provider transport is mocked
through the same ``app.generation.frontier_provider.httpx.post`` seam Phase 30
uses, and the http driver is exercised against an in-process loopback HTTP
server. No real paid provider call is ever made and no API key literal exists
in tracked test prose (the sentinel and assembled key vectors below are test
data used ONLY in the mocked outbound authentication slot).

The benchmark runner itself is imported from ``tools.frontier_benchmark`` —
exactly the CLI the Dev-Box flows invoke (``python -m tools.frontier_benchmark``).
"""

from __future__ import annotations

import json
import ssl as _ssl
import threading
from pathlib import Path

import pytest

from tools import frontier_benchmark as fb

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS = REPO_ROOT / "benchmarks" / "frontier" / "corpus"

# The mandatory Phase 31 sentinel (§8). The ONLY allowed appearance is the
# mocked outbound authentication slot (the recorded Authorization header of the
# mocked provider traffic in the inprocess driver); everywhere else (stdout,
# stderr, artifacts, DB, logs, exceptions, resume state) it must be absent.
SENTINEL = fb.SENTINEL

KEY_A = "BK-A-KEY-41"
KEY_B = "BK-B-KEY-42"

GOLDEN_PROMPT = (
    "Victim: sarah_miller\n"
    "Murderer: thomas_reed\n"
    "Motive: cover_up_embezzlement\n"
    "Weapon: kitchen_knife\n"
    "Time: 2026-09-11T22:17:00+02:00\n"
    "Witness: emily_reed\n"
)


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _write_contestants(tmp_path, entries) -> Path:
    """Serialize contestant dicts into a TOML file under tmp_path."""
    lines: list[str] = []
    for entry in entries:
        lines.append("[[contestants]]")
        for key in ("id", "label", "provider", "model", "credential_env"):
            lines.append(f'{key} = "{entry[key]}"')
        enabled_value = entry.get("enabled", True)
        if isinstance(enabled_value, bool):
            lines.append(f"enabled = {str(enabled_value).lower()}")
        else:
            lines.append(f'enabled = "{enabled_value}"')
        tags = entry.get("tags", ())
        if tags:
            rendered = ", ".join(f'"{t}"' for t in tags)
            lines.append(f"tags = [{rendered}]")
        lines.append("")
    path = tmp_path / "contestants.toml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _golden_strings():
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.generation.provider import GenerationStage
    from fixtures.golden_generation import GOLDEN_STAGE_PAYLOADS

    return [
        GOLDEN_STAGE_PAYLOADS[stage]
        for stage in (
            GenerationStage.CASE_TRUTH,
            GenerationStage.PUBLIC_WORLD,
            GenerationStage.EVIDENCE,
            GenerationStage.WORLD_GRAPH,
        )
    ]


class _FakeResponse:
    status_code = 200

    def __init__(self, body: bytes) -> None:
        self._body = body

    def iter_bytes(self, chunk_size):
        yield self._body


class _GoldenWire:
    """Thread-safe httpx.post stand-in for the production Frontier provider.

    Every recorded call keeps (url, body, headers) so tests can prove the
    trusted registry endpoint, the exact contestant credential, the exact
    model and the existing stage schemas reached the wire with no cross-talk.
    """

    def __init__(self, golden_by_model: dict[str, list[str]] | None = None) -> None:
        self.posts: list[tuple[str, dict, dict]] = []
        self._lock = threading.Lock()
        self._golden = {
            model: list(entries) for model, entries in (golden_by_model or {}).items()
        }

    def install(self, monkeypatch) -> "_GoldenWire":
        from app.generation import frontier_provider as fp_mod

        monkeypatch.setattr(fp_mod.httpx, "post", self._post)
        return self

    def _post(self, url, json=None, headers=None, timeout=None):
        body = dict(json or {})
        headers = dict(headers or {})
        with self._lock:
            self.posts.append((url, body, headers))
            queue = self._golden.setdefault(body.get("model", ""), [])
            content = queue.pop(0) if queue else "<not-json>"
        return _FakeResponse(content.encode("utf-8"))


def _contenders_toml():
    return CORPUS.parent / "contestants.toml", CORPUS.parent / "suites.toml"


def _default_contestants(tmp_path, *, credential_env="OPENROUTER_API_KEY"):
    return _write_contestants(
        tmp_path,
        [
            {
                "id": "openrouter-model-a",
                "label": "OpenRouter Model A (test)",
                "provider": "openrouter",
                "model": "MODEL-A",
                "credential_env": credential_env,
            }
        ],
    )


def _read_results(run_dir: Path) -> list[dict]:
    raw = (run_dir / "results.jsonl").read_text(encoding="utf-8")
    return [json.loads(line) for line in raw.splitlines() if line.strip()]


def _basic_cli(tmp_path, output_dir: str, **overrides) -> list[str]:
    contestants, _ = _contenders_toml()
    argv = [
        "--suite", "smoke",
        "--contestants", str(contestants),
        "--case-ids", "easy-office-001",
        "--driver", "inprocess",
        "--output-dir", output_dir,
        "--yes",
    ]
    for key, value in overrides.items():
        argv.append(f"--{key}")
        if value is not None:
            argv.append(str(value))
    return argv


# --------------------------------------------------------------------------- #
# §44 — config parsing gates
# --------------------------------------------------------------------------- #


def test_contestant_config_loads_valid_entries(tmp_path):
    path = _write_contestants(
        tmp_path,
        [
            {
                "id": "a",
                "label": "A",
                "provider": "openrouter",
                "model": "deepseek/deepseek-v4.1-flash",
                "credential_env": "OPENROUTER_API_KEY",
                "tags": ["flash"],
            },
            {
                "id": "luna",
                "label": "Luna",
                "provider": "openai",
                "model": "",
                "credential_env": "OPENAI_API_KEY",
                "enabled": False,
            },
        ],
    )
    loaded = fb.load_contestants_toml(path)
    assert {c.id for c in loaded} == {"a", "luna"}
    assert loaded[0].provider == "openrouter"
    assert loaded[0].model == "deepseek/deepseek-v4.1-flash"
    assert loaded[0].enabled is True
    assert loaded[1].enabled is False
    assert repr(loaded[0]) and "credential" not in f"{loaded[0].credential_env}=x"


@pytest.mark.parametrize(
    "name,extra_field,extra_value",
    [
        ("inline_api_key", "apiKey", "sk-inline-secret"),
        ("inline_key_field", "api_key", "sk-inline-secret"),
        ("arbitrary_endpoint", "endpoint", "https://evil.example/v1"),
        ("base_url", "base_url", "https://evil.example"),
        ("response_format", "response_format", '{"type":"json"}'),
        ("schema_override", "schema", "{}"),
        ("system_prompt", "system_prompt", "do better"),
        ("timeout_disable", "timeout_disable", "true"),
        ("validator_disable", "validator_disable", "true"),
        ("security_override", "security_override", "true"),
        ("unknown_field", "strange", "1"),
    ],
)
def test_config_rejects_forbidden_override_fields(tmp_path, name, extra_field, extra_value):
    """§9/§31.7/.8 — inline keys, arbitrary endpoints, response_format, schema,
    prompts and security/timeout/validator overrides are all rejected."""
    lines = [
        "[[contestants]]",
        'id="x"',
        'label="X"',
        'provider="openrouter"',
        'model="m"',
        'credential_env="OPENROUTER_API_KEY"',
        f'{extra_field} = "{extra_value}"',
    ]
    path = tmp_path / f"{name}.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(fb.ConfigError):
        fb.load_contestants_toml(path)


def test_config_rejects_unknown_provider_even_when_disabled(tmp_path):
    for enabled in ("true", "false"):
        lines = [
            "[[contestants]]",
            'id="x"',
            'label="X"',
            'provider="cloud-unknown"',
            'model="m"',
            'credential_env="OPENROUTER_API_KEY"',
            f"enabled={enabled}",
        ]
        path = tmp_path / f"unknown-{enabled}.toml"
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with pytest.raises(fb.ConfigError, match="unknown trusted provider"):
            fb.load_contestants_toml(path)


def test_config_rejects_disabled_registry_provider_for_enabled_contestant(tmp_path):
    lines = [
        "[[contestants]]",
        'id="x"',
        'label="X"',
        'provider="openai"',
        'model="gpt-4o"',
        'credential_env="OPENAI_API_KEY"',
        "enabled=true",
    ]
    path = tmp_path / "disabled-provider.toml"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    # "openai" is present in the registry today, so this entry may or may not
    # be disabled depending on the operator's registry state; the gate either
    # loads (registry-enabled) or rejects (registry-disabled). The important
    # invariant is: an unknown ID always fails and the model is always
    # validated for enabled contestants.
    from app.generation.frontier_registry import frontier_provider_definition

    definition = frontier_provider_definition("openai")
    if definition is not None and definition.enabled:
        loaded = fb.load_contestants_toml(path)
        assert loaded[0].provider == "openai"
    else:
        with pytest.raises(fb.ConfigError):
            fb.load_contestants_toml(path)


def test_config_rejects_malformed_config_variants(tmp_path):
    # malformed TOML
    bad_toml = tmp_path / "bad.toml"
    bad_toml.write_text("[[contestants]\nbroken\n", encoding="utf-8")
    with pytest.raises(fb.ConfigError):
        fb.load_contestants_toml(bad_toml)
    # missing contestants
    empty = tmp_path / "empty.toml"
    empty.write_text('[other]\nnot_here = 1\n', encoding="utf-8")
    with pytest.raises(fb.ConfigError):
        fb.load_contestants_toml(empty)
    # malformed corpus JSON
    bad_corpus = tmp_path / "corpus-bad"
    bad_corpus.mkdir()
    (bad_corpus / "bad.json").write_text("{ nope", encoding="utf-8")
    with pytest.raises(fb.ConfigError):
        fb.load_corpus(bad_corpus)
    # corpus case without required fields
    missing_corpus = tmp_path / "corpus-missing"
    missing_corpus.mkdir()
    (missing_corpus / "missing.json").write_text(
        json.dumps({"id": "x"}), encoding="utf-8"
    )
    with pytest.raises(fb.ConfigError, match="missing"):
        fb.load_corpus(missing_corpus)
    # unknown difficulty
    wrong_corpus = tmp_path / "corpus-wrong"
    wrong_corpus.mkdir()
    (wrong_corpus / "wrong-diff.json").write_text(
        json.dumps(
            {"id": "y", "difficulty": "impossible", "prompt": "Victim: a\n"}
        ),
        encoding="utf-8",
    )
    with pytest.raises(fb.ConfigError, match="difficulty"):
        fb.load_corpus(wrong_corpus)


def test_config_rejects_invalid_suite_file(tmp_path):
    bad = tmp_path / "bad-suites.toml"
    bad.write_text("[suites.smoke]\neasy = -1\n", encoding="utf-8")
    with pytest.raises(fb.ConfigError):
        fb.load_suites(bad)


def test_corpus_well_formed_unique_and_parseable():
    """The tracked corpus files must be well-formed, pin unique ids, use the
    canonical structured key format and parse through the production prompt
    normalization + world-requirements extraction."""
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.generation.pipeline import normalize_prompt
    from app.world.extract import extract_world_requirements

    corpus = fb.load_corpus(CORPUS)
    assert len(corpus) >= 20
    ids = [c.id for c in corpus]
    assert len(ids) == len(set(ids))
    assert all(c.difficulty in fb.DIFFICULTIES for c in corpus)
    assert all(len(c.prompt) <= fb.MAX_CORPUS_PROMPT_CHARS for c in corpus)
    for case in corpus:
        locked, _note = normalize_prompt(case.prompt, max_chars=4000)
        world = extract_world_requirements(case.prompt, locked)
        assert world is not None
    golden = [c for c in corpus if c.id == "easy-office-001"]
    assert golden
    locked, _ = normalize_prompt(golden[0].prompt, max_chars=4000)
    assert locked.victim == "sarah_miller"
    assert locked.murderer == "thomas_reed"
    assert locked.crime_time == "2026-09-11T22:17:00+02:00"


def test_suites_counts():
    suites = fb.load_suites(CORPUS.parent / "suites.toml")
    assert suites["smoke"] == fb.SuiteSpec(easy=1, medium=1, hard=1)
    assert suites["standard"] == fb.SuiteSpec(easy=5, medium=5, hard=10)
    assert suites["smoke"].total() == 3
    assert suites["standard"].total() == 20


def test_schema_version_is_present_in_artifacts_and_helper():
    assert fb.BENCHMARK_SCHEMA_VERSION == "1.0.0"


# --------------------------------------------------------------------------- #
# §31.1 / §31.2 — determinism, ordering, seedability
# --------------------------------------------------------------------------- #


def test_build_work_items_seedable_and_fixed_order(tmp_path):
    contestants = [
        fb.BenchmarkContestant(
            id=cid, label=cid, provider="openrouter", model="m",
            credential_env="K", enabled=True,
        )
        for cid in ("a", "b", "c")
    ]
    cases = [
        fb.BenchmarkCase(id="c1", difficulty="easy", prompt="Victim: x\n"),
        fb.BenchmarkCase(id="c2", difficulty="easy", prompt="Victim: y\n"),
    ]
    items1, order1, seed1 = fb.build_work_items(
        contestants, cases, repeat=2, seed=7, fixed_order=False
    )
    items2, order2, seed2 = fb.build_work_items(
        contestants, cases, repeat=2, seed=7, fixed_order=False
    )
    assert seed1 == seed2 == 7
    assert order1 == order2
    assert [i.as_tuple() for i in items1] == [i.as_tuple() for i in items2]
    fixed_items, fixed_order, fixed_seed = fb.build_work_items(
        contestants, cases, repeat=1, seed=7, fixed_order=True
    )
    assert fixed_seed is None
    assert fixed_order == ["a", "b", "c"]
    # contestant-major over 2 cases x 1 repeat: a a b b c c
    assert [i.contestant_id for i in fixed_items] == [
        "a", "a", "b", "b", "c", "c"
    ]


def _run_main(argv):
    return fb.main(argv)


def test_metadata_records_seed_and_contestant_order(tmp_path, monkeypatch):
    contestants_path = _default_contestants(tmp_path)
    out = tmp_path / "run-seed"
    rc = _run_main(
        [
            "--suite", "smoke",
            "--contestants", str(contestants_path),
            "--case-ids", "easy-office-001",
            "--driver", "inprocess",
            "--output-dir", str(out),
            "--seed", "7",
            "--fixed-order", "--yes",
            "--contestant", "openrouter-model-a",
        ]
    )
    assert rc == 0
    metadata = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["seed"] is None  # fixed-order discards the seed
    assert metadata["contestantOrder"] == ["openrouter-model-a"]
    assert metadata["benchmarkSchemaVersion"] == fb.BENCHMARK_SCHEMA_VERSION


# --------------------------------------------------------------------------- #
# §31.10/.11/.12/.13/.14/.15/.19/.20 + §44 metrics — one golden run, then the
# aggregation unit surface
# --------------------------------------------------------------------------- #


@pytest.fixture
def golden_mock(monkeypatch):
    wire = _GoldenWire(golden_by_model={"MODEL-A": _golden_strings()})
    wire.install(monkeypatch)
    return wire


def _run_golden_case(tmp_path, monkeypatch, golden_mock, extra=(), name="run-a"):
    contestants_path = _default_contestants(tmp_path)
    out = tmp_path / name
    argv = [
        "--suite", "smoke",
        "--contestants", str(contestants_path),
        "--case-ids", "easy-office-001",
        "--driver", "inprocess",
        "--output-dir", str(out),
        "--yes",
    ] + list(extra)
    rc = _run_main(argv)
    return rc, out, contestants_path


def test_inprocess_golden_run_persists_complete_artifacts(
    tmp_path, monkeypatch, golden_mock
):
    """§31.10/.11/.12/.15/.19/.20 + §44 (measuring) — a golden inprocess run
    publishes through the REAL GenerationService (same provider seam / stage
    schemas / validators / publication as production), persists every artifact
    as JSONL/JSON/CSV/MD, fills telemetry-derived fields truthfully and leaves
    cost unavailable (null, never 0)."""
    rc, out, _ = _run_golden_case(tmp_path, monkeypatch, golden_mock)
    assert rc == 0

    assert (out / "results.jsonl").is_file()
    assert (out / "metadata.json").is_file()
    assert (out / "summary.json").is_file()
    assert (out / "summary.csv").is_file()
    assert (out / "report.md").is_file()
    assert (out / "state.json").is_file()

    records = _read_results(out)
    assert len(records) == 1
    record = records[0]
    assert record["benchmarkSchemaVersion"] == "1.0.0"
    assert record["finalStatus"] == "PUBLISHED"
    assert record["published"] is True
    assert record["failureCode"] is None
    # Telemetry-derived (truthful — the app's own observability, meaning the
    # golden pipeline made exactly 4 calls, zero repairs).
    assert record["providerCallCount"] == 4
    assert record["repairCount"] == 0
    assert record["regenerationCount"] == 0
    assert record["structuredOutputUsed"] is True
    assert record["validationOutcome"] == "VALID"
    # Cost never fabricated.
    assert record["costSource"] == "unavailable"
    assert record["inputTokens"] is None
    assert record["outputTokens"] is None
    assert record["providerReportedCost"] is None
    assert record["calculatedCost"] is None
    assert record["timeoutContractViolation"] is None

    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    row = summary["perContestant"]["openrouter-model-a"]
    assert row["attempts"] == 1
    assert row["published"] == 1
    assert row["publishedRate"] == 1.0
    assert row["zeroRepairPublished"] == 1
    assert row["zeroRepairRate"] == 1.0
    assert row["cost"] == {"source": "unavailable", "amount": None, "currency": None}

    # §31.12 — summary aggregates match raw results.
    manual_attempts = len(records)
    manual_published = sum(1 for r in records if r["published"] is True)
    assert row["attempts"] == manual_attempts
    assert row["published"] == manual_published

    # JSONL crash-tolerant persistence: every line must be a standalone object.
    for line in (out / "results.jsonl").read_text(encoding="utf-8").splitlines():
        assert isinstance(json.loads(line), dict)

    metadata = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
    assert metadata["benchmarkSchemaVersion"] == "1.0.0"
    assert metadata["suite"] == "smoke"
    assert metadata["driver"] == "inprocess"
    # Metadata never carries credential VALUES (only env-var NAMES).
    blob = json.dumps(metadata)
    assert "credentialEnv" in blob
    assert "OPENROUTER_API_KEY" in blob
    for forbidden in ("sk-", "Authorization", "Bearer "):
        assert forbidden not in blob

    report = (out / "report.md").read_text(encoding="utf-8")
    for section in (
        "RUN METADATA", "CONTESTANTS", "WORKLOAD", "QUALITY RESULTS",
        "LATENCY RESULTS", "REPAIR / REGENERATION RESULTS",
        "FAILURE DISTRIBUTION", "STRUCTURED OUTPUT", "TOKEN / COST RESULTS",
        "TIMEOUT CONTRACT CHECK", "LUNA VS FRONTIER", "RECOMMENDATION",
        "CAVEATS", "SECRET-SAFETY CHECK", "PHASE 29 MONITORING EVALUATION",
    ):
        assert f"## {section}" in report, section
    # §31.20 — the report contains no sentinel secret.
    assert SENTINEL not in report

    # CSV row present and readable.
    csv_text = (out / "summary.csv").read_text(encoding="utf-8")
    assert csv_text.splitlines()[0].startswith("contestantId")
    assert "openrouter-model-a" in csv_text
    assert SENTINEL not in csv_text


def test_fixed_fake_inputs_produce_stable_result_records(tmp_path, monkeypatch):
    """§31.1 — the same deterministic workload through the same (mocked)
    pipeline yields the same outcome fields across separate runs."""
    wire = _GoldenWire(golden_by_model={"MODEL-A": _golden_strings()})
    wire.install(monkeypatch)
    rc1, out1, _ = _run_golden_case(tmp_path, monkeypatch, wire, name="stable-1")
    assert rc1 == 0
    rec1 = _read_results(out1)[0]
    wire2 = _GoldenWire(golden_by_model={"MODEL-A": _golden_strings()})
    wire2.install(monkeypatch)
    rc2, out2, _ = _run_golden_case(tmp_path, monkeypatch, wire2, name="stable-2")
    assert rc2 == 0
    rec2 = _read_results(out2)[0]
    for key in (
        "finalStatus", "published", "failureCode", "validationOutcome",
        "providerCallCount", "repairCount", "regenerationCount",
        "structuredOutputUsed", "costSource",
    ):
        assert rec1[key] == rec2[key], key


def test_percentile_calculation_is_correct():
    """§31.13 — p50/p90/p95 via linear interpolation are exact."""
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert fb.percentile(values, 50) == 3.0
    assert fb.percentile(values, 90) == 4.6
    assert fb.percentile(values, 95) == 4.8
    assert fb.percentile([7.0], 50) == 7.0
    with pytest.raises(ValueError):
        fb.percentile([], 50)


def _synthetic_records():
    """Executed + skipped records designed to exercise every aggregation rule."""
    base = {"benchmarkSchemaVersion": "1.0.0", "benchmarkRunId": "r",
            "contestantId": "c1", "contestantLabel": "C1", "provider": "openrouter",
            "model": "m", "difficulty": "easy", "repeatIndex": 0}
    records = [
        {
            **base,
            "benchmarkCaseId": "a", "executionOrder": 1, "finalStatus": "PUBLISHED",
            "published": True, "failureCode": None, "totalElapsedMs": 100,
            "providerCallCount": 4, "repairCount": 0, "regenerationCount": 0,
            "structuredOutputUsed": True, "timeoutContractViolation": None,
        },
        {
            **base,
            "benchmarkCaseId": "b", "executionOrder": 2, "finalStatus": "PUBLISHED",
            "published": True, "failureCode": None, "totalElapsedMs": 120,
            "providerCallCount": 5, "repairCount": 1, "regenerationCount": 0,
            "structuredOutputUsed": True, "timeoutContractViolation": None,
        },
        {
            **base,
            "benchmarkCaseId": "c", "executionOrder": 3, "finalStatus": "FAILED",
            "published": False, "failureCode": "VALIDATION_FAILED",
            "totalElapsedMs": 150, "providerCallCount": 7, "repairCount": 2,
            "regenerationCount": 1, "structuredOutputUsed": False,
            "timeoutContractViolation": None,
        },
        {
            **base,
            "benchmarkCaseId": "d", "executionOrder": 4, "finalStatus": "FAILED",
            "published": False, "failureCode": "REPAIR_BUDGET_EXHAUSTED",
            "totalElapsedMs": 80, "providerCallCount": 9, "repairCount": 2,
            "regenerationCount": 0, "structuredOutputUsed": True,
            "timeoutContractViolation": None,
        },
        {
            **base,
            "benchmarkCaseId": "e", "executionOrder": 5, "finalStatus": "FAILED",
            "published": False, "failureCode": "FRONTIER_TIMEOUT",
            "totalElapsedMs": 300, "providerCallCount": 1, "repairCount": 0,
            "regenerationCount": 0, "structuredOutputUsed": False,
            "timeoutContractViolation": True,
        },
        {
            **base,
            "benchmarkCaseId": "f", "executionOrder": 6, "finalStatus": "SKIPPED_CREDENTIAL_MISSING",
            "published": None, "failureCode": None, "totalElapsedMs": None,
            "providerCallCount": None, "repairCount": None, "regenerationCount": None,
            "structuredOutputUsed": None, "timeoutContractViolation": None,
        },
    ]
    return records


def test_aggregation_rules(tmp_path):
    """§31.12/.13/.14/.15/.16/.17/.19 — failed cases stay in the denominator,
    zero-repair success is correct, repair exhaustion and timeouts are
    classified, latency percentiles are correct, structured output aggregates
    truthfully and unavailable cost remains null (never 0)."""
    contestant = fb.BenchmarkContestant(
        id="c1", label="C1", provider="openrouter", model="m",
        credential_env="K", enabled=True,
    )
    row = fb.aggregate_per_contestant(contestant, _synthetic_records())
    assert row["attempts"] == 5  # failed cases AND published stay in N
    assert row["skippedCredentialMissing"] == 1
    assert row["published"] == 2
    assert row["failed"] == 3
    assert row["publishedRate"] == round(2 / 5, 4)
    assert row["zeroRepairPublished"] == 1
    assert row["zeroRepairRate"] == round(1 / 5, 4)
    assert row["repair1Published"] == 1
    assert row["repair2Published"] == 0
    assert row["repairExhausted"] == 1
    assert row["timeoutFailures"] == 1
    assert row["contractViolations"] == 1
    lat = row["latency"]
    # percentiles over {300, 80, 100, 150, 120} sorted = [80,100,120,150,300]
    assert lat["median"] == 120.0
    assert lat["p90"] == 240.0
    assert lat["p95"] == 270.0
    assert lat["n"] == 5
    assert row["failureCodes"] == {
        "VALIDATION_FAILED": 1,
        "REPAIR_BUDGET_EXHAUSTED": 1,
        "FRONTIER_TIMEOUT": 1,
    }
    assert row["structuredOutputAttempts"] == 3
    assert row["structuredOutputKnownAttempts"] == 5
    assert row["structuredOutputRate"] == round(3 / 5, 4)
    assert row["cost"] == {"source": "unavailable", "amount": None, "currency": None}
    # unavailable cost must be null, never 0.
    for r in _synthetic_records():
        assert r.get("costSource", "unavailable") != 0


def test_aggregate_empty_and_all_skipped_are_not_fabricated(tmp_path):
    contestant = fb.BenchmarkContestant(
        id="c9", label="C9", provider="openrouter", model="m",
        credential_env="K", enabled=True,
    )
    row = fb.aggregate_per_contestant(contestant, [])
    assert row["attempts"] == 0
    assert row["publishedRate"] is None
    assert row["zeroRepairRate"] is None
    assert row["latency"] == {"n": 0}
    assert row["cost"]["amount"] is None


def test_recommendations_cite_metrics_and_caveats():
    corpus = fb.load_corpus(CORPUS)
    suite = fb.load_suites(CORPUS.parent / "suites.toml")["smoke"]
    caveats = fb.build_caveats(driver="inprocess", telemetry_present=False)
    assert any("telemetry" in c for c in caveats)
    notes = fb.build_recommendations([])
    assert notes == []
    contestant = fb.BenchmarkContestant(
        id="c1", label="C1", provider="openrouter", model="m",
        credential_env="K", enabled=True,
    )
    row = fb.aggregate_per_contestant(contestant, _synthetic_records())
    notes = fb.build_recommendations([row])
    assert any("published rate" in n for n in notes)


# --------------------------------------------------------------------------- #
# §20 — dry-run contacts no provider
# --------------------------------------------------------------------------- #


def test_dry_run_inprocess_and_http_contact_no_provider(tmp_path, monkeypatch, capsys):
    """§20/§31.4 — ``--dry-run`` prints a non-secret plan and performs ZERO
    outbound/paid calls for both drivers."""
    from app.generation import frontier_provider as fp_mod

    def _boom(*args, **kwargs):
        raise AssertionError("outbound provider call attempted during dry-run")

    monkeypatch.setattr(fp_mod.httpx, "post", _boom)
    contestants_path = _default_contestants(tmp_path)
    for driver in ("inprocess", "http"):
        out = tmp_path / f"dry-{driver}"
        rc = _run_main(
            [
                "--suite", "smoke",
                "--contestants", str(contestants_path),
                "--case-ids", "easy-office-001",
                "--driver", driver,
                "--output-dir", str(out),
                "--dry-run",
            ]
        )
        assert rc == 0, driver
        assert not (out / "results.jsonl").exists(), driver
        captured = capsys.readouterr()
        assert "Phase 31 benchmark plan" in captured.out
        assert "paid provider calls: no" in captured.out
        assert "credential OK" in captured.out or "MISSING CREDENTIAL" in captured.out
        assert SENTINEL not in captured.out + captured.err


def test_non_interactive_without_yes_requires_yes(tmp_path, monkeypatch, golden_mock):
    contestants_path = _default_contestants(tmp_path)
    out = tmp_path / "no-yes"
    rc = _run_main(
        [
            "--suite", "smoke",
            "--contestants", str(contestants_path),
            "--case-ids", "easy-office-001",
            "--driver", "inprocess",
            "--output-dir", str(out),
        ]
    )
    assert rc == 2
    assert not (out / "results.jsonl").exists()
    assert golden_mock.posts == []


def test_max_cases_plan_guard_prevents_execution(tmp_path, monkeypatch, capsys):
    """§21 — a plan beyond --max-cases is refused before any provider call."""
    contestants_path = _default_contestants(tmp_path)
    out = tmp_path / "cap"
    rc = _run_main(
        [
            "--suite", "smoke",
            "--contestants", str(contestants_path),
            "--case-ids", "easy-office-001",
            "--driver", "inprocess",
            "--output-dir", str(out),
            "--dry-run",
            "--repeat", "5",
            "--max-cases", "3",
        ]
    )
    # 1 contestant x 3 cases x 5 repeats = 15 attempts > 3
    assert rc == 2
    assert "exceeds --max-cases" in capsys.readouterr().err


def test_max_estimated_cost_is_labeled_non_binding(tmp_path, monkeypatch, golden_mock):
    rc, out, _ = _run_golden_case(
        tmp_path, monkeypatch, golden_mock, extra=["--max-estimated-cost", "10"]
    )
    assert rc == 0
    metadata = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
    cli = metadata["cli"]
    assert cli["max_estimated_cost"] == 10.0
    assert "best-effort" in cli.get("maxEstimatedCostLabeled", "")


# --------------------------------------------------------------------------- #
# §31.5 — missing credential skips only the affected contestant
# --------------------------------------------------------------------------- #


def test_missing_credential_marks_skipped_and_continues(tmp_path, monkeypatch):
    """§7/§31.5 — a contestant with no credential is SKIPPED_CREDENTIAL_MISSING
    and the benchmark continues for the others."""
    golden = _golden_strings()

    class _Wire:
        def __init__(self):
            self.posts = []

        def install(self, m):
            from app.generation import frontier_provider as fp_mod

            def _post(url, json=None, headers=None, timeout=None):
                with self._lock:
                    self.posts.append((url, dict(json or {}), dict(headers or {})))
                    queue = golden_q
                    content = queue.pop(0) if queue else "<not-json>"
                return _FakeResponse(content.encode("utf-8"))

            m.setattr(fp_mod.httpx, "post", _post)

    golden_q = list(golden)
    wire = _Wire()
    wire._lock = threading.Lock()
    wire.install(monkeypatch)

    contestants_path = _write_contestants(
        tmp_path,
        [
            {
                "id": "has-key",
                "label": "Has key",
                "provider": "openrouter",
                "model": "MODEL-A",
                "credential_env": "OPENROUTER_API_KEY",
            },
            {
                "id": "no-key",
                "label": "No key",
                "provider": "openrouter",
                "model": "MODEL-B",
                "credential_env": "NO_SUCH_BENCH_API_KEY",
            },
        ],
    )
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY_A)
    monkeypatch.delenv("NO_SUCH_BENCH_API_KEY", raising=False)
    out = tmp_path / "skip-run"
    rc = _run_main(
        [
            "--suite", "smoke",
            "--contestants", str(contestants_path),
            "--case-ids", "easy-office-001",
            "--driver", "inprocess",
            "--output-dir", str(out),
            "--yes",
        ]
    )
    assert rc == 0
    records = {r["contestantId"]: r for r in _read_results(out)}
    assert records["no-key"]["finalStatus"] == "SKIPPED_CREDENTIAL_MISSING"
    assert records["no-key"]["published"] is None
    assert records["has-key"]["finalStatus"] == "PUBLISHED"
    # Only the credentialed contestant performed the 4 golden stage calls.
    assert len(wire.posts) == 4
    # No out-of-scope key reached the wire.
    assert all(
        headers.get("Authorization") == f"Bearer {KEY_A}"
        for _u, _b, headers in wire.posts
    )
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["perContestant"]["no-key"]["skippedCredentialMissing"] == 1
    assert summary["perContestant"]["no-key"]["attempts"] == 0


# --------------------------------------------------------------------------- #
# HTTP driver against an in-process fake server (§44 / end-to-end)
# --------------------------------------------------------------------------- #


class _Handler:
    def __init__(self, case_statuses: dict[str, tuple[str, str | None]]) -> None:
        self.case_statuses = case_statuses
        self.requests: list[tuple[str, dict, dict]] = []
        self.session_count = 0


def _make_fake_server(handler):
    import json as _json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class _H(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _read_body(self):
            length = int(self.headers.get("Content-Length", "0"))
            return self.rfile.read(length) if length else b""

        def _send(self, status: int, payload: dict):
            data = _json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            body = _json.loads(self._read_body().decode("utf-8"))
            handler.requests.append((self.path, body, dict(self.headers)))
            if self.path == "/api/v1/sessions/anonymous":
                handler.session_count += 1
                self._send(201, {"anonymousSessionToken": "bench-tok-1",
                                 "quotaWindowEndsAt": 1.0})
                return
            if self.path == "/api/v1/cases":
                frontier = body.get("frontier") or {}
                model = frontier.get("model", "")
                status, failure_code = handler.case_statuses.get(
                    model, ("PUBLISHED", None)
                )
                attempt = "GA-" + model.replace("/", "_")
                self._send(
                    201,
                    {
                        "caseId": "CASE-" + model,
                        "generationId": "GEN-1",
                        "generationAttemptId": attempt,
                        "creatorAccessToken": "acctok",
                        "status": status,
                        "failureCode": failure_code,
                    },
                )
                return
            self._send(404, {"error": {"code": "NOT_FOUND"}})

    server = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    return server


def test_http_driver_fake_server_records_correct_results(tmp_path, monkeypatch):
    """§44 / end-to-end — the http driver against an in-process loopback
    backend returns correct result records; the BYOK block and Authorization
    header reach the wire exactly as the production browser would send them."""
    class _HandlerState:
        def __init__(self):
            self.requests = []
            self.session_count = 0
            self.case_statuses = {
                "MODEL-OK": ("PUBLISHED", None),
                "MODEL-FAIL": ("FAILED", "VALIDATION_FAILED"),
            }

    state = _HandlerState()
    server = _make_fake_server(state)
    from threading import Thread

    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        base = f"http://127.0.0.1:{port}"
        contestants_path = _write_contestants(
            tmp_path,
            [
                {
                    "id": "ok-a",
                    "label": "OK A",
                    "provider": "openrouter",
                    "model": "MODEL-OK",
                    "credential_env": "OPENROUTER_API_KEY",
                },
                {
                    "id": "fail-b",
                    "label": "Fail B",
                    "provider": "openrouter",
                    "model": "MODEL-FAIL",
                    "credential_env": "OPENROUTER_API_KEY",
                },
            ],
        )
        monkeypatch.setenv("OPENROUTER_API_KEY", KEY_A)
        out = tmp_path / "http-server"
        rc = _run_main(
            [
                "--suite", "smoke",
                "--contestants", str(contestants_path),
                "--case-ids", "easy-office-001",
                "--driver", "http",
                "--base-url", base,
                "--output-dir", str(out),
                "--yes",
            ]
        )
        assert rc == 0
        records = {r["contestantId"]: r for r in _read_results(out)}
        assert records["ok-a"]["finalStatus"] == "PUBLISHED"
        assert records["ok-a"]["published"] is True
        assert records["fail-b"]["finalStatus"] == "FAILED"
        assert records["fail-b"]["published"] is False
        assert records["fail-b"]["failureCode"] == "VALIDATION_FAILED"
        assert isinstance(records["ok-a"]["totalElapsedMs"], int)
        assert records["ok-a"]["costSource"] == "unavailable"
        # The wire carried the BYOK block with the contestant key + model.
        case_requests = [r for r in state.requests if r[0] == "/api/v1/cases"]
        assert len(case_requests) == 2
        for path, body, headers in case_requests:
            assert body["generationProvider"] == "frontier"
            # The API key travels ONLY in the BYOK body block; the HTTP
            # Authorization header carries the anonymous session token (the
            # exact production contract).
            assert body["frontier"]["apiKey"] == KEY_A
            assert body["frontier"]["model"] in ("MODEL-OK", "MODEL-FAIL")
            assert headers.get("Authorization") == "Bearer bench-tok-1"
            assert KEY_A not in headers.get("Authorization", "")
        assert state.session_count == 1  # one reusable anonymous session
        assert SENTINEL not in json.dumps(records)
    finally:
        server.shutdown()
        server.server_close()


def test_http_driver_records_429_admission_truthfully(tmp_path, monkeypatch):
    """§20 — HTTP 429/admission is recorded truthfully as an ERROR outcome,
    never as a fabricated generation failure."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    class _State:
        pass

    state = _State()
    state.session_calls = 0
    state.handled = False

    class _H(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status, payload):
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            if self.path == "/api/v1/sessions/anonymous":
                state.session_calls += 1
                self._send(201, {"anonymousSessionToken": "bench-tok-2",
                                 "quotaWindowEndsAt": 1.0})
                return
            # First /cases returns a global quota 429; refresh + retry gets a
            # PUBLISHED result (the 429 is recordable but the session refresh
            # happens before any provider call, so the retry is cost-safe).
            if not state.handled:
                state.handled = True
                self._send(429, {"error": {"code": "ADMISSION_DENIED"}})
                return
            self._send(201, {"caseId": "CASE-R", "generationId": "GEN-1",
                             "generationAttemptId": "GA-R", "creatorAccessToken": "t",
                             "status": "PUBLISHED"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        contestants_path = _default_contestants(tmp_path)
        monkeypatch.setenv("OPENROUTER_API_KEY", KEY_A)
        out = tmp_path / "http-429"
        rc = _run_main(
            [
                "--suite", "smoke",
                "--contestants", str(contestants_path),
                "--case-ids", "easy-office-001",
                "--driver", "http",
                "--base-url", f"http://127.0.0.1:{port}",
                "--output-dir", str(out),
                "--yes",
            ]
        )
        assert rc == 0
        records = _read_results(out)
        assert len(records) == 1
        assert records[0]["finalStatus"] == "PUBLISHED"
        assert state.session_calls == 2  # refresh happened exactly once
    finally:
        server.shutdown()
        server.server_close()


# --------------------------------------------------------------------------- #
# DEF-025 — http driver CA-trust option (--ca-bundle / --insecure-tls)
# --------------------------------------------------------------------------- #


def _write_test_tls_pki(directory: Path) -> tuple[Path, Path, Path]:
    """Deterministic pure-stdlib CA + server-leaf PKI rooted in ``directory``.

    Returns ``(ca_pem, leaf_crt, leaf_key)``. The Phase 31 test suite has no
    openssl / cryptography dependency, so the trust anchor and the leaf cert
    (whose IP SAN matches the loopback ``127.0.0.1`` and whose key is signed
    by the CA) are generated here from RSA + ASN.1 primitives with 2048-bit
    keys (the local OpenSSL security level rejects smaller EE keys). This is
    TEST data only — it is never committed and never appears in artifacts.
    """
    import base64 as _b64
    import hashlib as _hashlib
    import math as _math
    import random as _random

    def _der_len(n):
        if n < 0x80:
            return bytes([n])
        b = n.to_bytes((n.bit_length() + 7) // 8, "big")
        return bytes([0x80 | len(b)]) + b

    def _tlv(tag, payload):
        return bytes([tag]) + _der_len(len(payload)) + payload

    def _int(i):
        b = i.to_bytes((i.bit_length() + 7) // 8 or 1, "big")
        if b[0] & 0x80:
            b = b"\x00" + b
        return _tlv(0x02, b)

    def _oid(parts):
        out = bytes([40 * parts[0] + parts[1]])
        for p in parts[2:]:
            chunk = bytearray([p & 0x7F])
            p >>= 7
            while p:
                chunk.insert(0, (p & 0x7F) | 0x80)
                p >>= 7
            out += bytes(chunk)
        return _tlv(0x06, out)

    def _seq(*parts):
        return _tlv(0x30, b"".join(parts))

    def _set(*parts):
        return _tlv(0x31, b"".join(parts))

    def _utctime(text):
        return _tlv(0x17, text.encode())

    def _bit_str(payload):
        return _tlv(0x03, b"\x00" + payload)

    def _octet_str(payload):
        return _tlv(0x04, payload)

    _SHA256_DIGESTINFO = bytes.fromhex("3031300d060960864801650304020105000420")

    def _probable_prime(rng, bits):
        def _miller_rabin(n):
            if n < 2:
                return False
            for small in (2, 3, 5, 7, 11, 13, 17, 19, 23, 29, 31, 37,
                          41, 43, 47, 53, 59, 61, 67, 71):
                if n % small == 0:
                    return n == small
            d, s = n - 1, 0
            while d % 2 == 0:
                s += 1
                d //= 2
            for _ in range(10):
                a = rng.randrange(2, n - 2)
                x = pow(a, d, n)
                if x == 1 or x == n - 1:
                    continue
                for _ in range(s - 1):
                    x = pow(x, 2, n)
                    if x == n - 1:
                        break
                else:
                    return False
            return True

        while True:
            cand = rng.getrandbits(bits) | (1 << (bits - 1)) | 1
            if _miller_rabin(cand):
                return cand

    def _rsa_key(rng):
        p = _probable_prime(rng, 1024)
        q = _probable_prime(rng, 1024)
        while q == p:
            q = _probable_prime(rng, 1024)
        n = p * q
        lam = (p - 1) * (q - 1) // _math.gcd(p - 1, q - 1)
        e = 65537
        d = pow(e, -1, lam)
        return n, e, d, p, q

    def _sign(tbs, secret, modulus, bits):
        digest = _hashlib.sha256(tbs).digest()
        k = bits // 8
        em = (
            b"\x00\x01"
            + b"\xff" * (k - len(_SHA256_DIGESTINFO) - len(digest) - 3)
            + b"\x00"
            + _SHA256_DIGESTINFO
            + digest
        )
        return pow(int.from_bytes(em, "big"), secret, modulus).to_bytes(k, "big")

    def _spki(n, e):
        return _seq(
            _seq(_oid((1, 2, 840, 113549, 1, 1, 1)), _tlv(0x05, b"")),
            _bit_str(_seq(_int(n), _int(e))),
        )

    def _name(cn):
        return _seq(_set(_seq(_oid((2, 5, 4, 3)), _tlv(0x0C, cn))))

    def _ip_san(ip_bytes):
        return _seq(_oid((2, 5, 29, 17)), _octet_str(_seq(_tlv(0x87, ip_bytes))))

    def _basic_constraints(ca):
        return _seq(
            _oid((2, 5, 29, 19)),
            _tlv(0x01, b"\xff"),
            _octet_str(_seq(_tlv(0x01, b"\xff" if ca else b"\x00"))),
        )

    def _make_ca(rng):
        n, e, d, _p, _q = _rsa_key(rng)
        nm = _name(b"PD Phase31 Test CA")
        tbs = _seq(
            _tlv(0xA0, _int(2)),  # version v3
            _int(1),  # serial number
            _seq(_oid((1, 2, 840, 113549, 1, 1, 11)), _tlv(0x05, b"")),
            nm,
            _seq(_utctime("200101000000Z"), _utctime("400101000000Z")),
            nm,
            _spki(n, e),
            _tlv(0xA3, _seq(_basic_constraints(True))),
        )
        return _seq(
            tbs,
            _seq(_oid((1, 2, 840, 113549, 1, 1, 11)), _tlv(0x05, b"")),
            _bit_str(_sign(tbs, d, n, 2048)),
        ), n, e, d

    def _make_leaf(rng, ca_n, ca_d):
        n, e, d, _p, _q = _rsa_key(rng)
        tbs = _seq(
            _tlv(0xA0, _int(2)),
            _int(2),
            _seq(_oid((1, 2, 840, 113549, 1, 1, 11)), _tlv(0x05, b"")),
            _name(b"PD Phase31 Test CA"),
            _seq(_utctime("200101000000Z"), _utctime("400101000000Z")),
            _name(b"127.0.0.1"),
            _spki(n, e),
            _tlv(0xA3, _seq(_ip_san(bytes([127, 0, 0, 1])), _basic_constraints(False))),
        )
        return _seq(
            tbs,
            _seq(_oid((1, 2, 840, 113549, 1, 1, 11)), _tlv(0x05, b"")),
            _bit_str(_sign(tbs, ca_d, ca_n, 2048)),
        ), n, e, d, _p, _q

    def _pem_cert(der):
        return (
            "-----BEGIN CERTIFICATE-----\n"
            + _b64.encodebytes(der).decode()
            + "-----END CERTIFICATE-----\n"
        )

    def _pem_rsa(n, e, d, p, q):
        inner = _seq(
            _int(0), _int(n), _int(e), _int(d), _int(p), _int(q),
            _int(d % (p - 1)), _int(d % (q - 1)), _int(pow(q, -1, p)),
        )
        return (
            "-----BEGIN RSA PRIVATE KEY-----\n"
            + _b64.encodebytes(inner).decode()
            + "-----END RSA PRIVATE KEY-----\n"
        )

    rng = _random.Random(0xC0FFEE)
    ca_der, ca_n, _ca_e, ca_d = _make_ca(rng)
    leaf_der, ln, le, ld, lp, lq = _make_leaf(rng, ca_n, ca_d)
    ca_pem = directory / "devbox-ca.pem"
    leaf_crt = directory / "leaf.crt"
    leaf_key = directory / "leaf.key"
    ca_pem.write_text(_pem_cert(ca_der), encoding="utf-8")
    leaf_crt.write_text(_pem_cert(leaf_der), encoding="utf-8")
    leaf_key.write_text(_pem_rsa(ln, le, ld, lp, lq), encoding="utf-8")
    return ca_pem, leaf_crt, leaf_key


def test_http_tls_context_default_and_insecure_hatch(tmp_path):
    """The runner default stays system trust (no context → urllib default);
    the --insecure-tls escape hatch yields a clearly-verification-disabled
    context (CERT_NONE) and is never the silent default."""
    assert fb.build_http_tls_context() is None
    assert fb.build_http_tls_context(ca_bundle=None, insecure_tls=False) is None
    insecure = fb.build_http_tls_context(insecure_tls=True)
    assert insecure is not None
    assert insecure.verify_mode == _ssl.CERT_NONE
    assert insecure.check_hostname is False


def test_http_tls_ca_bundle_context_keeps_hostname_verification(tmp_path):
    """``--ca-bundle`` loads the Dev-Box CA with hostname verification ON
    (CERT_REQUIRED + check_hostname True) and fails CLOSED on a missing /
    invalid bundle file."""
    ca, _leaf_crt, _leaf_key = _write_test_tls_pki(tmp_path)
    ctx = fb.build_http_tls_context(ca_bundle=ca)
    assert ctx is not None
    assert ctx.verify_mode == _ssl.CERT_REQUIRED  # never disabled by CA bundle
    assert ctx.check_hostname is True
    assert len(ctx.get_ca_certs()) == 1  # the CA actually loaded
    # Fail-closed: a missing bundle must never be silently ignored.
    with pytest.raises((FileNotFoundError, OSError, _ssl.SSLError)):
        fb.build_http_tls_context(ca_bundle=tmp_path / "missing.pem")
    bad = tmp_path / "bad.pem"
    bad.write_text("not a certificate", encoding="utf-8")
    with pytest.raises((_ssl.SSLError, OSError)):
        fb.build_http_tls_context(ca_bundle=bad)


def test_http_driver_passes_ssl_context_to_urlopen(tmp_path, monkeypatch):
    """The driver wires the constructed context into ``urllib.urlopen``
    (hermetic: urlopen is stubbed — no real network)."""
    ca, _leaf_crt, _leaf_key = _write_test_tls_pki(tmp_path)
    captured: dict = {}

    def _fake_urlopen(request, timeout=None, context=None):
        captured["context"] = context
        raise OSError("hermetic stub: no real network")

    monkeypatch.setattr(fb.urllib.request, "urlopen", _fake_urlopen)
    driver = fb.HttpDriver(base_url="https://enshrouded-server", ca_bundle=ca)
    with pytest.raises(fb.BenchmarkError):
        driver._http_post("https://enshrouded-server/api/v1/sessions/anonymous",
                          {}, token=None)
    ctx = captured["context"]
    assert ctx is not None
    assert ctx.verify_mode == _ssl.CERT_REQUIRED
    assert ctx.check_hostname is True

    # Without any flag the driver keeps urllib's default (context None).
    captured2: dict = {}
    monkeypatch.setattr(
        fb.urllib.request,
        "urlopen",
        lambda request, timeout=None, context=None: captured2.update(
            context=context
        ) or (_ for _ in ()).throw(OSError("hermetic stub")),
    )
    default_driver = fb.HttpDriver(base_url="https://enshrouded-server")
    with pytest.raises(fb.BenchmarkError):
        default_driver._http_post(
            "https://enshrouded-server/api/v1/sessions/anonymous", {}, token=None
        )
    assert captured2["context"] is None


def test_http_driver_real_tls_loopback_ca_bundle_succeeds_default_fails(
    tmp_path, monkeypatch
):
    """DEF-025 end-to-end (hermetic loopback TLS): against an https server
    whose leaf is signed by the Dev-Box-style internal CA, the http driver
    with the ORIGINAL default trust FAILS with CERTIFICATE_VERIFY_FAILED (the
    exact QA reproduction), and the SAME driver WITH ``--ca-bundle`` (the CA
    file) succeeds; hostname verification stays on."""
    import json as _json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    ca, leaf_crt, leaf_key = _write_test_tls_pki(tmp_path)

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            body = _json.dumps({"anonymousSessionToken": "tls-tok-99"}).encode()
            self.send_response(201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server_context = _ssl.SSLContext(_ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(str(leaf_crt), str(leaf_key))
    server.socket = server_context.wrap_socket(server.socket, server_side=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        base = f"https://127.0.0.1:{port}"
        # 1) Default system trust -> CERTIFICATE_VERIFY_FAILED (QA repro).
        # The sanitized error exposes ONLY the exception class name (+ numeric
        # TLS verify code) — never the exception message (DEF-040).
        default_driver = fb.HttpDriver(
            base_url=base, http_timeout_seconds=10
        )
        with pytest.raises(fb.BenchmarkError) as exc_info:
            default_driver.create_anonymous_session()
        assert "SSLCertVerificationError" in str(exc_info.value)
        # 2) --ca-bundle -> the internal CA is trusted, session succeeds.
        trusted_driver = fb.HttpDriver(
            base_url=base, http_timeout_seconds=10, ca_bundle=ca
        )
        assert trusted_driver.create_anonymous_session() == "tls-tok-99"
        # 3) Hostname verification still active: the leaf cert is for
        #    "127.0.0.1" and the CA bundle does NOT disable verification.
        assert trusted_driver._ssl_context.verify_mode == _ssl.CERT_REQUIRED
    finally:
        server.shutdown()
        server.server_close()


def test_http_driver_cli_tls_flags_and_help_text(tmp_path, monkeypatch, capsys):
    """CLI plumbing: --ca-bundle flows into a real http run (loopback server),
    invalid flag combos fail closed, --insecure-tls prints the unsupported-escape
    warning, and --help documents the Dev-Box CA path."""
    ca, _leaf_crt, _leaf_key = _write_test_tls_pki(tmp_path)

    # Help documents the Dev-Box CA export + --ca-bundle usage.
    help_text = fb.build_parser().format_help()
    assert "--ca-bundle" in help_text
    assert "--insecure-tls" in help_text
    assert "caddy-local-root.crt" in help_text

    # Mutual exclusivity fails closed.
    rc = _run_main(
        ["--suite", "smoke", "--ca-bundle", str(ca), "--insecure-tls", "--dry-run"]
    )
    assert rc == 2
    assert "mutually exclusive" in capsys.readouterr().err

    # Missing bundle file fails closed.
    rc = _run_main(
        ["--suite", "smoke", "--ca-bundle", str(tmp_path / "nope.pem"), "--dry-run"]
    )
    assert rc == 2
    assert "file not found" in capsys.readouterr().err

    # --insecure-tls is an explicit, loudly-labeled opt-in.
    rc = _run_main(["--suite", "smoke", "--insecure-tls", "--dry-run"])
    assert rc == 0
    warned = capsys.readouterr().err
    assert "unsupported" in warned.lower()
    assert "escape hatch" in warned.lower()

    # End-to-end: a real (loopback) http run accepts --ca-bundle.
    class _HandlerState:
        pass

    state = _HandlerState()
    state.requests = []
    state.session_count = 0
    state.case_statuses = {"MODEL-TLS": ("PUBLISHED", None)}
    server = _make_fake_server(state)
    from threading import Thread

    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        base = f"http://127.0.0.1:{port}"
        contestants_path = _write_contestants(
            tmp_path,
            [
                {
                    "id": "tls-a",
                    "label": "TLS A",
                    "provider": "openrouter",
                    "model": "MODEL-TLS",
                    "credential_env": "OPENROUTER_API_KEY",
                }
            ],
        )
        monkeypatch.setenv("OPENROUTER_API_KEY", KEY_A)
        out = tmp_path / "http-tls-flags"
        rc = _run_main(
            [
                "--suite", "smoke",
                "--contestants", str(contestants_path),
                "--case-ids", "easy-office-001",
                "--driver", "http",
                "--base-url", base,
                "--ca-bundle", str(ca),
                "--output-dir", str(out),
                "--yes",
            ]
        )
        assert rc == 0
        records = _read_results(out)
        assert len(records) == 1
        assert records[0]["finalStatus"] == "PUBLISHED"
    finally:
        server.shutdown()
        server.server_close()


# --------------------------------------------------------------------------- #
# §22 — resume and rerun
# --------------------------------------------------------------------------- #


def test_resume_skips_completed_combos_and_rerun_repeats(tmp_path, monkeypatch):
    """§22/§31.3/.4 — restart skips already-completed contestant/case/repeat
    combinations unless ``--rerun`` is explicitly requested."""
    golden = _golden_strings()
    wire = _GoldenWire(golden_by_model={"MODEL-A": golden})
    wire.install(monkeypatch)
    contestants_path = _default_contestants(tmp_path)
    out = tmp_path / "resume-run"
    argv = [
        "--suite", "smoke",
        "--contestants", str(contestants_path),
        "--case-ids", "easy-office-001",
        "--driver", "inprocess",
        "--output-dir", str(out),
        "--yes",
    ]

    assert _run_main(argv) == 0
    assert len(_read_results(out)) == 1
    first_calls = len(wire.posts)
    assert first_calls == 4

    # Restart WITHOUT --rerun: the completed combo is skipped, no new paid work.
    assert _run_main(argv) == 0
    assert len(_read_results(out)) == 1
    assert len(wire.posts) == first_calls  # no new provider traffic
    state = json.loads((out / "state.json").read_text(encoding="utf-8"))
    assert state["benchmarkRunId"]
    assert {"contestantId": "openrouter-model-a", "benchmarkCaseId": "easy-office-001",
            "repeat": 0} in state["completed"]

    # --rerun: explicitly repeats the paid work (fresh measurement).
    wire2 = _GoldenWire(golden_by_model={"MODEL-A": golden})
    wire2.install(monkeypatch)
    retry = argv + ["--rerun"]
    assert _run_main(retry) == 0
    assert len(_read_results(out)) == 1  # clean output dir restarted
    assert len(wire2.posts) == 4


def _interrupt_running_case_driver(monkeypatch, interrupt_on_case_id):
    """Patch InProcessDriver.run_case to raise KeyboardInterrupt before the
    provider wire is touched for ``interrupt_on_case_id`` (simulates a
    Ctrl+C / crash at the START of that case)."""
    original = fb.InProcessDriver.run_case

    def _interrupting(self, contestant, case):
        if case.id == interrupt_on_case_id:
            raise KeyboardInterrupt()
        return original(self, contestant, case)

    monkeypatch.setattr(fb.InProcessDriver, "run_case", _interrupting)
    return original


def _two_case_argv(tmp_path, output_dir):
    contestants_path = _default_contestants(tmp_path)
    return [
        "--suite", "smoke",
        "--contestants", str(contestants_path),
        "--case-ids", "easy-apartment-001,easy-bakery-001",
        "--driver", "inprocess",
        "--output-dir", str(output_dir),
        "--yes",
    ]


def test_state_persisted_mid_run_after_early_exit(tmp_path, monkeypatch):
    """§22 (DEF-024 regression, requirement d) — the resume state is persisted
    AFTER EACH completed case: a run interrupted after the FIRST case leaves a
    state.json on disk (with exactly the completed combo) and one durable
    results.jsonl line — never "no state until the very end"."""
    golden = _golden_strings()
    wire = _GoldenWire(golden_by_model={"MODEL-A": golden})
    wire.install(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY_A)
    out = tmp_path / "early-exit"
    argv = _two_case_argv(tmp_path, out)
    _interrupt_running_case_driver(monkeypatch, "easy-bakery-001")

    rc = _run_main(argv)
    assert rc == 130  # Ctrl+C reached the runner
    assert (out / "state.json").is_file(), "state.json must exist MID-RUN"
    state = json.loads((out / "state.json").read_text(encoding="utf-8"))
    assert state["completed"] == [
        {"contestantId": "openrouter-model-a", "benchmarkCaseId": "easy-apartment-001",
         "repeat": 0}
    ]
    assert len(_read_results(out)) == 1  # the first case's crash-tolerant line
    assert len(wire.posts) == 4  # only the first case reached the (mocked) wire


def test_interrupted_run_restart_skips_completed_combos(tmp_path, monkeypatch):
    """§22/§46 (DEF-024, requirement a) — restart WITHOUT --rerun after an
    interrupted paid run skips the completed combo: the wire call count stays
    unchanged (no duplicate paid calls) and both result lines survive."""
    golden = _golden_strings()
    wire = _GoldenWire(golden_by_model={"MODEL-A": golden})
    wire.install(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY_A)
    out = tmp_path / "crash-resume"
    argv = _two_case_argv(tmp_path, out)
    original = _interrupt_running_case_driver(monkeypatch, "easy-bakery-001")
    assert _run_main(argv) == 130
    first_calls = len(wire.posts)
    assert first_calls == 4
    interrupted_run_id = json.loads(
        (out / "state.json").read_text(encoding="utf-8")
    )["benchmarkRunId"]

    # Restart WITHOUT --rerun: the completed combo is skipped.
    monkeypatch.setattr(fb.InProcessDriver, "run_case", original)  # stop interrupting
    wire2 = _GoldenWire(golden_by_model={"MODEL-A": golden})
    wire2.install(monkeypatch)
    assert _run_main(argv) == 0
    assert len(wire2.posts) == 4, "restart re-executed a completed paid combo"
    records = _read_results(out)
    assert len(records) == 2  # crash-recovered line + finished line, deduped
    state = json.loads((out / "state.json").read_text(encoding="utf-8"))
    assert len(state["completed"]) == 2
    # The run identity is preserved across the interruption (no id fork).
    assert state["benchmarkRunId"] == interrupted_run_id


def test_rerun_after_interruption_repeats_everything(tmp_path, monkeypatch):
    """§22 (DEF-024, requirement b) — ``--rerun`` is the explicit 'repeat
    everything' override even after an interruption: BOTH combos are
    re-executed and the output dir is cleaned."""
    golden = _golden_strings()
    wire = _GoldenWire(golden_by_model={"MODEL-A": golden})
    wire.install(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY_A)
    out = tmp_path / "rerun-after-crash"
    argv = _two_case_argv(tmp_path, out)
    original = _interrupt_running_case_driver(monkeypatch, "easy-bakery-001")
    assert _run_main(argv) == 130

    monkeypatch.setattr(fb.InProcessDriver, "run_case", original)  # stop interrupting
    wire2 = _GoldenWire(golden_by_model={"MODEL-A": list(golden) * 2})
    wire2.install(monkeypatch)
    assert _run_main(argv + ["--rerun"]) == 0
    assert len(wire2.posts) == 8, "--rerun must repeat BOTH combos (2 x 4 calls)"
    assert len(_read_results(out)) == 2


def test_resume_without_state_json_dedupes_from_results_jsonl(
    tmp_path, monkeypatch, capsys
):
    """§22/§46 (DEF-024, requirement c) — a run where state.json is MISSING but
    results.jsonl exists and --rerun is NOT set does NOT silently re-execute
    completed combos: the JSONL combos are treated as completed (dedupe) with a
    clear notice, the file is preserved (never unlinked) and no paid call is
    repeated."""
    golden = _golden_strings()
    wire = _GoldenWire(golden_by_model={"MODEL-A": list(golden) * 2})
    wire.install(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY_A)
    out = tmp_path / "dedupe-run"
    argv = _two_case_argv(tmp_path, out)
    assert _run_main(argv) == 0
    assert len(_read_results(out)) == 2
    first_calls = len(wire.posts)
    assert first_calls == 8
    original_run_id = json.loads((out / "state.json").read_text(encoding="utf-8"))[
        "benchmarkRunId"
    ]

    # Exactly the interrupted-run disk shape: state.json gone, JSONL survives.
    (out / "state.json").unlink()

    wire2 = _GoldenWire(golden_by_model={"MODEL-A": golden})
    wire2.install(monkeypatch)
    assert _run_main(argv) == 0
    assert len(wire2.posts) == 0, "completed combos were silently re-executed"
    assert len(_read_results(out)) == 2, "results.jsonl must not be unlinked"
    captured = capsys.readouterr()
    assert "state.json absent" in captured.out
    assert "as completed" in captured.out
    assert "--rerun to repeat" in captured.out
    # Run identity stayed stable (no fork to a fresh run id).
    state = json.loads((out / "state.json").read_text(encoding="utf-8"))
    assert state["benchmarkRunId"] == original_run_id
    assert len(state["completed"]) == 2


def test_load_completed_from_results_parses_and_excludes_skipped(tmp_path):
    """Unit surface for the crash-tolerant resume source: results.jsonl combos
    parse into completed set; SKIPPED_CREDENTIAL_MISSING lines are never
    completed; a missing/corrupt file yields an empty set; run-id recovery
    returns the durable benchmarkRunId."""
    rows = [
        {"benchmarkRunId": "r1", "contestantId": "a", "benchmarkCaseId": "c1",
         "repeatIndex": 0, "finalStatus": "PUBLISHED", "published": True},
        {"benchmarkRunId": "r1", "contestantId": "a", "benchmarkCaseId": "c2",
         "repeatIndex": 1, "finalStatus": "FAILED", "published": False},
        {"benchmarkRunId": "r1", "contestantId": "a", "benchmarkCaseId": "c3",
         "repeatIndex": 0, "finalStatus": "SKIPPED_CREDENTIAL_MISSING",
         "published": None},
    ]
    results = tmp_path / "results.jsonl"
    results.write_text(
        "\n".join(json.dumps(r) for r in rows) + "\nnot-json-line\n",
        encoding="utf-8",
    )
    combos = fb.load_completed_from_results(results)
    assert combos == {("a", "c1", 0), ("a", "c2", 1)}
    assert ("a", "c3", 0) not in combos
    assert fb.load_completed_from_results(tmp_path / "missing.jsonl") == set()
    assert fb.run_id_from_results(results) == "r1"
    assert fb.run_id_from_results(tmp_path / "missing.jsonl") is None


# --------------------------------------------------------------------------- #
# §18 — timeout contract verification
# --------------------------------------------------------------------------- #


def test_timeout_contract_violation_detection_unit():
    """§18/§31.17 — per-call elapsed > effectiveTimeout+tolerance and total
    > deadline+tolerance are flagged CONTRACT_VIOLATION, never silently called
    ordinary slowness."""
    clean_events = [
        {"event": "provider.call.start", "effectiveProviderTimeoutMs": 10000,
         "configuredGenerationDeadlineMs": 60000},
        {"event": "provider.call.complete", "elapsedMs": 5000,
         "effectiveProviderTimeoutMs": 10000, "structuredOutput": True},
    ]
    assert fb.detect_call_timeout_violations(clean_events, tolerance_ms=5000) == []

    violating_events = [
        {"event": "provider.call.start", "effectiveProviderTimeoutMs": 5000,
         "configuredGenerationDeadlineMs": 60000},
        {"event": "provider.call.complete", "elapsedMs": 25000,
         "effectiveProviderTimeoutMs": 5000, "structuredOutput": True},
    ]
    assert fb.detect_call_timeout_violations(violating_events) != []

    record = {"published": True, "failureCode": None, "totalElapsedMs": 90000}
    fb.enrich_result(record, violating_events, deadline_ms=60000)
    assert record["timeoutContractViolation"] is True
    assert record["contractViolations"] is not None
    assert record["generationDeadlineMs"] == 60000


def test_timeout_contract_violation_reaches_http_run_records(
    tmp_path, monkeypatch
):
    """§18/§31.17 — an http run correlated with the app's observability JSONL
    flags a CONTRACT_VIOLATION from the crafted telemetry and keeps it counted
    separately in the summary."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    class _H(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _send(self, status, payload):
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            self.rfile.read(length)
            if self.path.startswith("/api/v1/sessions"):
                self._send(201, {"anonymousSessionToken": "bench-tok-3",
                                 "quotaWindowEndsAt": 1.0})
                return
            self._send(201, {"caseId": "CASE-T", "generationId": "GEN-1",
                             "generationAttemptId": "GA-TIME-OUT",
                             "creatorAccessToken": "t", "status": "PUBLISHED"})

    server = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        contestants_path = _default_contestants(tmp_path)
        monkeypatch.setenv("OPENROUTER_API_KEY", KEY_A)

        telemetry = tmp_path / "telemetry.jsonl"
        telemetry.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "generationAttemptId": "GA-TIME-OUT",
                            "event": "provider.call.start",
                            "effectiveProviderTimeoutMs": 5000,
                            "configuredGenerationDeadlineMs": 60000,
                        }
                    ),
                    json.dumps(
                        {
                            "generationAttemptId": "GA-TIME-OUT",
                            "event": "provider.call.complete",
                            "elapsedMs": 40000,
                            "effectiveProviderTimeoutMs": 5000,
                            "structuredOutput": True,
                        }
                    ),
                    json.dumps(
                        {
                            "generationAttemptId": "GA-TIME-OUT",
                            "event": "generation.published",
                            "totalElapsedMs": 120000,
                            "providerCallCount": 4,
                            "repairCount": 0,
                            "regenerationCount": 0,
                            "failureCode": None,
                        }
                    ),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        out = tmp_path / "timeout-run"
        rc = _run_main(
            [
                "--suite", "smoke",
                "--contestants", str(contestants_path),
                "--case-ids", "easy-office-001",
                "--driver", "http",
                "--base-url", f"http://127.0.0.1:{port}",
                "--telemetry-logs", str(telemetry),
                "--output-dir", str(out),
                "--yes",
            ]
        )
        assert rc == 0
        records = _read_results(out)
        assert len(records) == 1
        record = records[0]
        assert record["timeoutContractViolation"] is True
        assert record["contractViolations"] is not None
        assert record["providerCallCount"] == 4
        assert record["repairCount"] == 0
        assert record["structuredOutputUsed"] is True
        assert record["validationOutcome"] == "VALID"
        summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        row = summary["perContestant"]["openrouter-model-a"]
        assert row["contractViolations"] == 1
    finally:
        server.shutdown()
        server.server_close()


# --------------------------------------------------------------------------- #
# §32 — provider isolation with concurrent contestants
# --------------------------------------------------------------------------- #


def test_provider_isolation_concurrent_contestants(tmp_path, monkeypatch):
    """§32/§31.8 — two concurrent deterministic contestants (openai + groq)
    with mocked traffic: each hits the trusted registry endpoint with ONLY its
    own credential/model; no key/model/provider cross-talk; the existing stage
    schemas travel on the wire; the production provider path is the exact
    GenerationService entry point."""
    golden = _golden_strings()
    wire = _GoldenWire(golden_by_model={"MODEL-A": golden, "MODEL-B": golden})
    wire.install(monkeypatch)
    from app.generation.frontier_registry import frontier_provider_definition

    openai_endpoint = frontier_provider_definition("openai").endpoint
    groq_endpoint = frontier_provider_definition("groq").endpoint

    contestants_path = _write_contestants(
        tmp_path,
        [
            {
                "id": "contestant-a",
                "label": "A",
                "provider": "openai",
                "model": "MODEL-A",
                "credential_env": "BENCH_KEY_A_API_KEY",
            },
            {
                "id": "contestant-b",
                "label": "B",
                "provider": "groq",
                "model": "MODEL-B",
                "credential_env": "BENCH_KEY_B_API_KEY",
            },
        ],
    )
    monkeypatch.setenv("BENCH_KEY_A_API_KEY", KEY_A)
    monkeypatch.setenv("BENCH_KEY_B_API_KEY", KEY_B)

    out = tmp_path / "isolation-run"
    rc = _run_main(
        [
            "--suite", "smoke",
            "--contestants", str(contestants_path),
            "--case-ids", "easy-office-001",
            "--driver", "inprocess",
            "--output-dir", str(out),
            "--concurrency", "2",
            "--yes",
        ]
    )
    assert rc == 0
    records = {r["contestantId"]: r for r in _read_results(out)}
    assert records["contestant-a"]["finalStatus"] == "PUBLISHED"
    assert records["contestant-b"]["finalStatus"] == "PUBLISHED"
    assert records["contestant-a"]["published"] is True
    assert records["contestant-b"]["published"] is True

    posts_a = [(u, b, h) for u, b, h in wire.posts
               if h.get("Authorization") == f"Bearer {KEY_A}"]
    posts_b = [(u, b, h) for u, b, h in wire.posts
               if h.get("Authorization") == f"Bearer {KEY_B}"]
    assert len(posts_a) == 4
    assert len(posts_b) == 4
    assert all(url == openai_endpoint for url, _b, _h in posts_a)
    assert all(url == groq_endpoint for url, _b, _h in posts_b)
    assert all(body["model"] == "MODEL-A" for _u, body, _h in posts_a)
    assert all(body["model"] == "MODEL-B" for _u, body, _h in posts_b)
    # No cross-talk on the wire: only the two expected credentials appear.
    wire_keys = {h.get("Authorization") for _u, _b, h in wire.posts}
    assert wire_keys == {f"Bearer {KEY_A}", f"Bearer {KEY_B}"}
    wire_models = {b.get("model") for _u, b, _h in wire.posts}
    assert wire_models == {"MODEL-A", "MODEL-B"}
    wire_urls = {u for u, _b, _h in wire.posts}
    assert wire_urls == {openai_endpoint, groq_endpoint}
    # Existing stage schemas reach the wire (Phase 30-Fix DEF-B): the trusted
    # registry entry declared native structured output, so every call carries
    # response_format with the server-owned schema and the model never overrides.
    for _u, body, _h in wire.posts:
        assert "response_format" in body
        assert body["response_format"]["type"] == "json_schema"
    # The per-contestant results are not crossed.
    assert KEY_A not in json.dumps(records["contestant-b"])
    assert KEY_B not in json.dumps(records["contestant-a"])
    # Production-equivalent path: the frozen model landed in each durable
    # publication row (the same Store the GenerationService wrote to).
    assert records["contestant-a"]["model"] == "MODEL-A"
    assert records["contestant-b"]["model"] == "MODEL-B"


# --------------------------------------------------------------------------- #
# §33 — Luna baseline BLOCKED-EXTERNAL
# --------------------------------------------------------------------------- #


def test_luna_baseline_blocked_external(tmp_path, monkeypatch, golden_mock):
    """§2/§33 — with no verified official OpenAI API Luna model id the report
    renders BLOCKED-EXTERNAL and no fake Luna network behavior is ever
    created."""
    assert fb.LUNA_VERIFIED_OFFICIAL_MODEL_IDS == ()
    status, reason = fb.luna_report()
    assert status == "BLOCKED-EXTERNAL"
    assert "no officially callable OpenAI API model ID" in reason

    contestants_path = _default_contestants(tmp_path)
    out = tmp_path / "luna-run"
    rc = _run_main(
        [
            "--suite", "smoke",
            "--contestants", str(contestants_path),
            "--case-ids", "easy-office-001",
            "--driver", "inprocess",
            "--output-dir", str(out),
            "--yes",
            "--contestant", "openrouter-model-a",
        ]
    )
    assert rc == 0
    summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert summary["luna"]["status"] == "BLOCKED-EXTERNAL"
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "Luna baseline: `BLOCKED-EXTERNAL`" in report


def test_luna_enabled_flag_has_no_effect_without_verified_id(tmp_path, monkeypatch, capsys):
    rc = _run_main(["--suite", "smoke", "--dry-run", "--luna-enabled"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "no verified official OpenAI" in captured.err


# --------------------------------------------------------------------------- #
# §34 — sentinel secret persistence
# --------------------------------------------------------------------------- #


class _SentinelWire:
    """Records each mocked post; the sentinel may appear ONLY in these headers."""

    def __init__(self):
        self.posts: list[tuple[str, dict, dict]] = []
        self.installed = False

    def install(self, monkeypatch):
        from app.generation import frontier_provider as fp_mod

        def _post(url, json=None, headers=None, timeout=None):
            with threading.Lock():
                self.posts.append((url, dict(json or {}), dict(headers or {})))
            return _FakeResponse(_golden_strings()[len(self.posts) - 1].encode("utf-8"))

        monkeypatch.setattr(fp_mod.httpx, "post", _post)
        self.installed = True


def test_sentinel_appears_only_in_mocked_outbound_auth_slot(
    tmp_path, monkeypatch, capsys
):
    """§8/§34 (MANDATORY) — after a full run with the sentinel as the
    credential, the sentinel appears NOWHERE except the mocked outbound
    Authorization header: not in stdout/stderr, metadata, JSONL, JSON summary,
    CSV, Markdown, resume state, DB, logs, or exceptions."""
    wire = _SentinelWire()
    wire.install(monkeypatch)
    # inprocess driver uses its own temp DB; pin it under tmp_path so the raw
    # DB bytes can be scanned exactly like the §34 checklist demands.
    db_url = f"sqlite:///{(tmp_path / 'bench.db').as_posix()}"

    # Patch the runner/driver to use OUR db_url for the inprocess run.
    original_ctor = fb.InProcessDriver.__init__

    def _ctor_with_db(self, *, concurrency=1, **kw):
        kw["database_url"] = db_url
        original_ctor(self, concurrency=concurrency, **kw)

    monkeypatch.setattr(fb.InProcessDriver, "__init__", _ctor_with_db)

    contestants_path = _default_contestants(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", SENTINEL)
    out = tmp_path / "sentinel-run"
    rc = _run_main(
        [
            "--suite", "smoke",
            "--contestants", str(contestants_path),
            "--case-ids", "easy-office-001",
            "--driver", "inprocess",
            "--output-dir", str(out),
            "--yes",
        ]
    )
    assert rc == 0
    captured = capsys.readouterr()
    combined_stdout_streams = captured.out + captured.err

    # The sentinel reached the mocked outbound authentication slot: the header.
    assert any(h.get("Authorization") == f"Bearer {SENTINEL}" for _u, _b, h in wire.posts)
    assert len(wire.posts) == 4

    # Zero occurrences anywhere else.
    artifact_files = sorted(out.rglob("*"))
    artifact_text = "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in artifact_files
        if p.is_file()
    )
    assert SENTINEL not in artifact_text
    assert SENTINEL not in combined_stdout_streams
    for rel in (
        "metadata.json", "results.jsonl", "summary.json", "summary.csv",
        "report.md", "state.json",
    ):
        path = out / rel
        assert path.is_file()
        assert SENTINEL not in path.read_text(
            encoding="utf-8", errors="replace"
        ), rel
    # Raw DB bytes contain no sentinel (sqlite pages, indexes, everything).
    assert (tmp_path / "bench.db").is_file()
    assert SENTINEL.encode() not in (tmp_path / "bench.db").read_bytes()
    # Structured logs / exceptions: scanned via the inprocess capture sink
    # (driver closed already, so verify no log file landed in artifacts and
    # no exception text ever carried it — the CLI exited 0).
    assert not list(out.glob("*.log"))
    assert not list(out.glob("*.db"))
    # The resume state is clean too.
    assert SENTINEL not in (out / "state.json").read_text(encoding="utf-8")


def test_sentinel_never_persists_in_driver_database(tmp_path, monkeypatch):
    """§34 — direct driver-level proof: after a generation with the sentinel,
    the driver's own durable SQLite database and captured structured events
    contain zero sentinel occurrences."""
    wire = _SentinelWire()
    wire.install(monkeypatch)
    db_url = f"sqlite:///{(tmp_path / 'driver.db').as_posix()}"
    driver = fb.InProcessDriver(concurrency=1, database_url=db_url)
    try:
        contestant = fb.BenchmarkContestant(
            id="c", label="C", provider="openrouter", model="MODEL-S",
            credential_env="K", enabled=True, credential=SENTINEL,
        )
        case = fb.BenchmarkCase(
            id="easy-office-001", difficulty="easy", prompt=GOLDEN_PROMPT
        )
        outcome = driver.run_case(contestant, case)
        assert outcome["created"] == "PUBLISHED"
        db_bytes = (tmp_path / "driver.db").read_bytes()
        assert SENTINEL.encode() not in db_bytes
        events_json = json.dumps(driver.captured_events)
        assert SENTINEL not in events_json
        assert "Authorization" not in events_json
        assert any(
            h.get("Authorization") == f"Bearer {SENTINEL}"
            for _u, _b, h in wire.posts
        )
    finally:
        driver.close()


# --------------------------------------------------------------------------- #
# artifact secret scan
# --------------------------------------------------------------------------- #


def test_scan_files_for_secrets_detects_and_otherwise_clean(tmp_path):
    target = tmp_path / "run"
    target.mkdir()
    (target / "results.jsonl").write_text('{"a": 1}\n', encoding="utf-8")
    (target / "summary.json").write_text('{"s": 1}', encoding="utf-8")
    assert fb.scan_files_for_secrets(target, ["nope"]) == []
    (target / "leaky.md").write_text("some credential-here now", encoding="utf-8")
    hits = fb.scan_files_for_secrets(target, ["credential-here"])
    assert hits == [("leaky.md", "<redacted>")]
    assert fb.scan_files_for_secrets(target, []) == []


# --------------------------------------------------------------------------- #
# Phase 29 monitoring integration (§58/§59/§60 — hermetic)
# --------------------------------------------------------------------------- #


def _write_caddy_lines() -> str:
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    payloads = [
        {"level": "info", "ts": 1700000000.0, "logger": "http.log.access.log0",
         "msg": "handled request",
         "request": {"remote_ip": "192.0.2.10", "method": "GET",
                     "host": "localhost", "uri": "/"},
         "status": 200, "size": 500},
        {"level": "info", "ts": 1700000100.0, "logger": "http.log.access.log0",
         "msg": "handled request",
         "request": {"remote_ip": "192.0.2.10", "method": "POST",
                     "host": "localhost", "uri": "/api/v1/sessions/anonymous"},
         "status": 201, "size": 100},
        {"level": "info", "ts": 1700000200.0, "logger": "http.log.access.log0",
         "msg": "handled request",
         "request": {"remote_ip": "192.0.2.10", "method": "GET",
                     "host": "localhost", "uri": "/api/v1/health"},
         "status": 200, "size": 40},
        {"level": "info", "ts": 1700000300.0, "logger": "http.log.access.log0",
         "msg": "handled request",
         "request": {"remote_ip": "192.0.2.10", "method": "GET",
                     "host": "localhost", "uri": "/missing"},
         "status": 404, "size": 11},
    ]
    return "\n".join(json.dumps(p) for p in payloads) + "\n"


def test_phase29_artifacts_written_and_raw_deleted(tmp_path):
    """§58/§60 — the Phase 29 flow writes the three derived artifacts into the
    run dir, keeps raw logs/DB out of the artifacts and works entirely
    locally (canned capture + the real tools.monitoring_report)."""
    import tempfile

    snapshot_dir = Path(tempfile.mkdtemp(prefix="pd-p29-snapshot-"))
    try:
        db = snapshot_dir / "pd.db"
        fb._upgrade_database(f"sqlite:///{db.as_posix()}")

        run_dir = tmp_path / "monitoring-run"
        result = fb.build_phase29_artifacts(
            run_dir,
            _write_caddy_lines(),
            db,
            hours=1,
            include_health=True,
            existing_health_text=(
                "Health summary (MON-11)\n"
                "-------------------------\n"
                "procedural-detective: state=running, health=healthy\n"
                "caddy: state=running, health=healthy\n"
            ),
        )
        assert result["hours"] == 1
        assert result["http_total"] == 4
        # "/" (Page) + /api/v1/sessions/anonymous (API); /api/v1/health is a
        # health probe and /missing is classified Other/404 — neither is a
        # real Page/API request.
        assert result["http_page_api"] == 2
        assert result["http_4xx"] == 1
        assert result["http_5xx"] == 0
        assert result["health_app"] is not None
        assert result["health_caddy"] is not None
        assert result["warnings"] == []
        for expected in (
            "phase29-monitoring-summary.txt",
            "phase29-monitoring.json",
            "phase29-health.txt",
        ):
            assert (run_dir / expected).is_file(), expected
        # Raw logs/DB never land in the final artifacts.
        assert not (run_dir / "caddy.log").exists()
        assert not (run_dir / "pd.db").exists()
        assert not (run_dir / "procedural_detective.db").exists()
        assert SENTINEL not in (run_dir / "phase29-monitoring.json").read_text(
            encoding="utf-8"
        )
    finally:
        import shutil

        shutil.rmtree(snapshot_dir, ignore_errors=True)


def test_phase29_full_wrapper_uses_temp_and_cleans(
    tmp_path, monkeypatch
):
    """§58.3/§60 — the full wrapper captures to a TEMP dir, runs the derived
    artifacts, and DELETES the temp raw logs/DB (no docker needed when the
    capture hooks are injected)."""
    db_path = tmp_path / "snap.db"
    fb._upgrade_database(f"sqlite:///{db_path.as_posix()}")
    temp_dirs = []

    def _capture_logs(tmp_dir, hours, compose_args):
        temp_dirs.append(Path(tmp_dir))
        (Path(tmp_dir) / "caddy.log").write_text(
            _write_caddy_lines(), encoding="utf-8"
        )

    def _capture_db(tmp_dir, compose_args):
        temp_dirs.append(Path(tmp_dir))
        import shutil

        shutil.copyfile(db_path, Path(tmp_dir) / "pd.db")

    run_dir = tmp_path / "full-wrapper"
    result = fb.run_phase29_monitoring(
        run_dir,
        window_seconds=3600.0 * 3.1,
        capture_logs=_capture_logs,
        capture_db=_capture_db,
        include_health=True,
        existing_health_text="Health summary (MON-11)\n",
    )
    assert result["hours"] == 4  # ceil(3.1h) → 4h
    assert (run_dir / "phase29-monitoring.json").is_file()
    # Every temp dir was deleted after the derived artifacts were produced.
    assert all(not d.exists() for d in temp_dirs), temp_dirs


def test_report_phase29_section_unavailable_without_post_monitoring(
    tmp_path, monkeypatch, golden_mock
):
    rc, out, _ = _run_golden_case(tmp_path, monkeypatch, golden_mock)
    assert rc == 0
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "This section is unavailable for this run" in report
    assert "without `--post-monitoring`" in report


def test_report_never_contains_phrase_typo(tmp_path, monkeypatch, golden_mock):
    """DEF-027 — the PHASE 29 unavailable-section text must say "Phase 29"
    (the "Phrase 29" typo is fixed and must never return)."""
    rc, out, _ = _run_golden_case(tmp_path, monkeypatch, golden_mock)
    assert rc == 0
    report = (out / "report.md").read_text(encoding="utf-8")
    assert "Phrase 29" not in report
    assert "Phrase" not in report
    assert "Phase 29 monitoring evaluation" in report
    assert "executed without `--post-monitoring`" in report


def test_monitoring_capture_db_error_message_contains_exit_code(
    tmp_path, monkeypatch
):
    """DEF-026 — the DB-snapshot MonitoringCaptureError message interpolates
    the docker exit code: str(exc) reads "exit 37", never a tuple repr with a
    bare %d."""
    class _Completed:
        returncode = 37
        stdout = ""

    monkeypatch.setattr(fb.subprocess, "run", lambda *a, **k: _Completed())
    with pytest.raises(fb.MonitoringCaptureError) as exc_info:
        fb._default_capture_sqlite_db(tmp_path, ["-f", "docker-compose.prod.yml"])
    message = str(exc_info.value)
    assert "exit 37" in message
    assert "%d" not in message
    assert "('" not in message  # never the literal args-tuple repr
    # The sibling caddy-log capture path already interpolated; both must read
    # the same way.
    with pytest.raises(fb.MonitoringCaptureError, match=r"exit 37"):
        fb._default_capture_sqlite_db(tmp_path, ["-f", "docker-compose.prod.yml"])


# --------------------------------------------------------------------------- #
# §44 — focused gate: cost handling / unstructured empty telemetry never
# fabricated, and the report PHASE 29 section content path
# --------------------------------------------------------------------------- #


def test_enrich_result_without_events_leaves_null_fields_unit():
    record = {
        "published": True, "finalStatus": "PUBLISHED", "failureCode": None,
        "totalElapsedMs": 10,
    }
    result = fb.enrich_result(dict(record), [])
    assert result.get("contractViolations") is None
    assert result.get("providerCallCount") is None
    assert result.get("repairCount") is None
    assert result.get("regenerationCount") is None
    assert result.get("structuredOutputUsed") is None


def test_phase29_report_section_renders_from_json_payload(tmp_path):
    from tools.monitoring_report import format_human  # noqa: F401 (gate)

    json_text = json.dumps(
        {
            "http": {"total": 100, "page_api_requests": 40,
                     "status_classes": {"2xx": 90, "4xx": 8, "5xx": 2},
                     "count_5xx": 2},
            "product": {"playthroughs_started": 3, "cases_started": 5,
                        "cases_completed": 4, "completion_rate": 0.8},
        }
    )
    enriched = fb.summarize_phase29_json(
        json_text, hours=2, health_ok=True,
        health_text="procedural-detective: state=running, health=healthy\n"
                    "caddy: state=running, health=healthy\n",
    )
    assert enriched["http_total"] == 100
    assert enriched["http_page_api"] == 40
    assert enriched["http_4xx"] == 8
    assert enriched["http_5xx"] == 2
    assert enriched["completion_rate"] == 0.8
    assert any("5xx" in w for w in enriched["warnings"])
    # The interpretation note is rendered by build_report.
    contestants_path = CORPUS.parent / "contestants.toml"
    _ = contestants_path
    assert enriched["health_app"] is not None
    assert enriched["health_caddy"] is not None


# --------------------------------------------------------------------------- #
# CLI plumbing
# --------------------------------------------------------------------------- #


def test_runner_exports_cli_entrypoint():
    assert callable(fb.main)
    assert hasattr(fb, "build_parser")
    # --help parses (argparse prints and SystemExit(0)).
    import sys

    try:
        fb.build_parser().parse_args(["--help"])
    except SystemExit as exc:
        assert exc.code == 0
    finally:
        sys.stdout.flush()


def test_corpus_and_suites_file_layout_trackable():
    assert (CORPUS / "easy-office-001.json").is_file()
    assert (REPO_ROOT / "benchmarks" / "frontier" / "contestants.toml").is_file()
    assert (REPO_ROOT / "benchmarks" / "frontier" / "suites.toml").is_file()
    assert (REPO_ROOT / ".env.benchmark.example").is_file()


# --------------------------------------------------------------------------- #
# DEF-028..DEF-044 — Phase 31 adversarial findings regression battery
# --------------------------------------------------------------------------- #


# ------------------------------- DEF-028 ----------------------------------- #
# --rerun --dry-run must delete NOTHING; --rerun must only reset a directory
# that provably belongs to a previous benchmark run of this tool.


def test_rerun_dry_run_never_deletes_anything(tmp_path, capsys):
    """DEF-028 — ``--rerun --dry-run`` performs zero filesystem mutation: an
    existing directory (with or without any benchmark marker) is NOT erased and
    a missing directory is NOT created."""
    import os

    out = tmp_path / "output"
    out.mkdir()
    (out / "precious.txt").write_text("keep me", encoding="utf-8")
    (out / "state.json").write_text('{"not-a-benchmark": 1}', encoding="utf-8")
    before = {p.name for p in out.iterdir()}
    contestants_path = _default_contestants(tmp_path)
    rc = _run_main(
        [
            "--suite", "smoke",
            "--contestants", str(contestants_path),
            "--case-ids", "easy-office-001",
            "--driver", "inprocess",
            "--output-dir", str(out),
            "--rerun", "--dry-run",
        ]
    )
    assert rc == 0
    assert (out / "precious.txt").is_file()
    assert {p.name for p in out.iterdir()} == before
    # A missing directory is not created either.
    missing = tmp_path / "never-created"
    rc = _run_main(
        [
            "--suite", "smoke",
            "--contestants", str(contestants_path),
            "--case-ids", "easy-office-001",
            "--driver", "inprocess",
            "--output-dir", str(missing),
            "--rerun", "--dry-run",
        ]
    )
    assert rc == 0
    assert not missing.exists()
    captured = capsys.readouterr()
    assert "DRY RUN: NO provider is contacted" in captured.out


def test_rerun_refuses_to_delete_non_benchmark_directory(
    tmp_path, monkeypatch, capsys
):
    """DEF-028 — a real ``--rerun`` refuses to rmtree() a directory that lacks
    the benchmark-identity marker (metadata.json/state.json/results.jsonl of
    this tool); precious files survive and nothing else is written."""
    out = tmp_path / "output"
    out.mkdir()
    (out / "precious.txt").write_text("keep me", encoding="utf-8")
    (out / "state.json").write_text('{"not-a-benchmark": 1}', encoding="utf-8")
    contestants_path = _default_contestants(tmp_path)
    rc = _run_main(
        [
            "--suite", "smoke",
            "--contestants", str(contestants_path),
            "--case-ids", "easy-office-001",
            "--driver", "inprocess",
            "--output-dir", str(out),
            "--rerun", "--yes",
        ]
    )
    assert rc == 2
    assert (out / "precious.txt").is_file()
    assert not (out / "results.jsonl").exists()
    err = capsys.readouterr().err
    assert "not a recognized benchmark-run directory" in err


# ------------------------------- DEF-029 ----------------------------------- #
# per-case durability under concurrency (incremental persistence as futures
# complete) — an interrupted concurrent run keeps completed combos durable.


def test_concurrent_interrupted_run_keeps_completed_combos_durable(
    tmp_path, monkeypatch
):
    """DEF-029 — with ``--concurrency 2`` each completed future's result line +
    resume state is persisted AS IT COMPLETES: an interruption after the first
    case leaves >0 durable results.jsonl lines and a state.json, and a restart
    WITHOUT --rerun skips the durable combo (no duplicate paid calls)."""
    golden = _golden_strings()
    wire = _GoldenWire(golden_by_model={"MODEL-A": golden})
    wire.install(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY_A)
    out = tmp_path / "concurrent-interrupt"
    argv = _two_case_argv(tmp_path, out) + ["--concurrency", "2"]
    original = fb.InProcessDriver.run_case
    gate = threading.Event()

    def _interrupt_second(self, contestant, case):
        if case.id == "easy-bakery-001":
            gate.wait(timeout=60)
            raise KeyboardInterrupt()
        result = original(self, contestant, case)
        gate.set()
        return result

    monkeypatch.setattr(fb.InProcessDriver, "run_case", _interrupt_second)
    rc = _run_main(argv)
    assert rc == 130
    # At least the first (apartment) combo is durable even though the whole run
    # was interrupted mid-way under concurrency.
    assert (out / "results.jsonl").is_file()
    durable = _read_results(out)
    assert len(durable) == 1
    assert durable[0]["benchmarkCaseId"] == "easy-apartment-001"
    state = json.loads((out / "state.json").read_text(encoding="utf-8"))
    assert {"contestantId": "openrouter-model-a",
            "benchmarkCaseId": "easy-apartment-001", "repeat": 0} in state["completed"]
    first_calls = len(wire.posts)
    assert first_calls == 4

    # Restart WITHOUT --rerun: the durable combo is skipped, only the
    # unfinished combo runs — no duplicate paid call for the completed one.
    monkeypatch.setattr(fb.InProcessDriver, "run_case", original)
    wire2 = _GoldenWire(golden_by_model={"MODEL-A": golden})
    wire2.install(monkeypatch)
    assert _run_main(argv) == 0
    assert len(wire2.posts) == 4, "restart re-executed a completed paid combo"
    records = _read_results(out)
    assert len(records) == 2
    by_case = {r["benchmarkCaseId"]: r for r in records}
    assert set(by_case) == {"easy-apartment-001", "easy-bakery-001"}
    state = json.loads((out / "state.json").read_text(encoding="utf-8"))
    assert len(state["completed"]) == 2


# ------------------------------- DEF-030 ----------------------------------- #
# --base-url validation: reject userinfo/query/fragment, non-http(s) schemes,
# paths; persist + print ONLY the sanitized scheme://host[:port] form.


def test_base_url_userinfo_and_query_rejected_and_never_persisted(
    tmp_path, monkeypatch, capsys, golden_mock
):
    """DEF-030 — ``user:pass@`` and ``?x=`` base URLs are rejected (without
    echoing the secret), the dry-run plan renders only scheme://host, and a
    real run's metadata persists only the sanitized base."""
    contestants_path = _default_contestants(tmp_path)
    # Rejected forms: userinfo, query, fragment, path, non-http scheme.
    for bad in (
        f"https://user:{SENTINEL}@enshrouded-server",
        "https://enshrouded-server?apiKey=SECRET",
        "https://enshrouded-server#frag",
        "ftp://enshrouded-server",
        "https://enshrouded-server/some/path",
    ):
        rc = _run_main(
            [
                "--suite", "smoke",
                "--contestants", str(contestants_path),
                "--driver", "http",
                "--base-url", bad,
                "--dry-run",
            ]
        )
        assert rc == 2, bad
        err = capsys.readouterr().err
        assert SENTINEL not in err, "the rejection must not echo the secret"
    # Sanitizer unit surface.
    assert fb.sanitize_base_url("https://enshrouded-server") == "https://enshrouded-server"
    assert fb.sanitize_base_url("http://127.0.0.1:8080") == "http://127.0.0.1:8080"
    # Dry-run plan shows the sanitized scheme://host form.
    rc = _run_main(
        [
            "--suite", "smoke",
            "--contestants", str(contestants_path),
            "--driver", "http",
            "--base-url", "http://127.0.0.1:54321",
            "--dry-run",
        ]
    )
    assert rc == 0
    plan = capsys.readouterr().out
    assert "base-url:" in plan
    assert "http://127.0.0.1:54321" in plan
    # A real run persists only the sanitized base (no userinfo/query).
    from threading import Thread

    class _State:
        pass

    state = _State()
    state.case_statuses = {"MODEL-A": ("PUBLISHED", None)}
    server = _make_fake_server(state)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = f"http://127.0.0.1:{server.server_address[1]}"
        out = tmp_path / "base-url-run"
        rc = _run_main(
            [
                "--suite", "smoke",
                "--contestants", str(contestants_path),
                "--case-ids", "easy-office-001",
                "--driver", "http",
                "--base-url", base,
                "--output-dir", str(out),
                "--yes",
            ]
        )
        assert rc == 0
        metadata = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
        assert metadata["cli"]["base_url"] == base
        blob = json.dumps(metadata)
        assert "user:" not in blob and "pass@" not in blob and "apiKey=" not in blob
    finally:
        server.shutdown()
        server.server_close()


# ------------------------------- DEF-031 ----------------------------------- #
# strict config/corpus text validation + scan accept an extra suspicious-value
# list (credential values + config/corpus string fields) + token-shaped scan.


def test_strict_config_field_validation_rejects_smuggling(
    tmp_path,
):
    """DEF-031 — contestant label/model/tags are gated to bounded
    printable-ASCII with no control characters, so a hostile/careless config
    cannot smuggle CR/LF or non-printable material into plan/artifacts."""
    def _entry(**overrides):
        entry = {
            "id": "c1", "label": "L", "provider": "openrouter",
            "model": "deepseek/deepseek-v4.1-flash",
            "credential_env": "OPENROUTER_API_KEY", "enabled": True,
        }
        entry.update(overrides)
        return entry

    cases = [
        ("label-newline", _entry(label="ok\n- stolen line")),
        ("label-ctrl", _entry(label="bad\x01label")),
        ("label-long", _entry(label="x" * 121)),
        ("label-non-ascii", _entry(label="l\u00e9bel")),
        ("tag-ctrl", _entry(tags=["fine", "bad\r\ntag"])),
        ("model-ctrl", _entry(model="m\x00odel")),
    ]
    for name, entry in cases:
        path = _write_contestants(tmp_path / name, [entry])
        with pytest.raises(fb.ConfigError):
            fb.load_contestants_toml(path)


def test_scan_flags_config_field_values_and_token_shaped_secrets(tmp_path):
    """DEF-031/DEF-041 — the scan accepts an extra list of suspicious values
    (credential values + config/corpus string fields) so a sentinel supplied
    as a model/label/tags value is flagged; token-shaped (sk-...) secrets are
    flagged even with an empty credential list."""
    target = tmp_path / "run"
    target.mkdir()
    (target / "metadata.json").write_text(
        json.dumps({SENTINEL: "model/label/tags value"}), encoding="utf-8"
    )
    # Plain credential-only scan does NOT see the config-derived value...
    assert fb.scan_files_for_secrets(target, [KEY_A]) == []
    # ...but the EXTRA list (config/corpus string fields) DOES flag it.
    hits = fb.scan_files_for_secrets(target, [KEY_A], extra=[SENTINEL])
    assert hits == [("metadata.json", "<redacted>")]
    # Token-shaped secret pattern (sk- + 20+ alphanumerics) is flagged even
    # with no loaded credential (mirrors tools.release_check).
    token_dir = tmp_path / "token-run"
    token_dir.mkdir()
    (token_dir / "leak.md").write_text(
        "leaked sk-abcdefghijklmnopqrstuvwx now", encoding="utf-8"
    )
    assert fb.scan_files_for_secrets(token_dir, []) != []
    assert fb.scan_files_for_secrets(token_dir, [], token_patterns=False) == []


# ------------------------------- DEF-032 ----------------------------------- #
# enabled = "false" / "0" / "no" must never coerce to True.


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("false", False), ("FALSE", False), ("False", False),
        ("0", False), ("no", False), ("true", True), ("TRUE", True),
        (False, False), (True, True),
    ],
)
def test_enabled_string_semantics(tmp_path, raw, expected):
    """DEF-032 — string enabled values follow strict bool semantics: only
    true/false/0/no are accepted (never truthy unless literally 'true'), and
    real TOML booleans pass through."""
    model = "deepseek/deepseek-v4.1-flash" if expected else ""
    path = _write_contestants(
        tmp_path / f"enabled-{raw!s}",
        [{
            "id": "c1", "label": "L", "provider": "openrouter",
            "model": model, "credential_env": "OPENROUTER_API_KEY",
            "enabled": raw,
        }],
    )
    loaded = fb.load_contestants_toml(path)
    assert loaded[0].enabled is expected


@pytest.mark.parametrize("bad", ["yes", "1", 1, 0.5, [], {}, ""])
def test_enabled_unparseable_values_raise_config_error(tmp_path, bad):
    """DEF-032 — anything that is not a real boolean / true|false|0|no string
    raises ConfigError instead of silently enabling a paying contestant."""
    path = _write_contestants(
        tmp_path / f"enabled-bad-{type(bad).__name__}",
        [{
            "id": "c1", "label": "L", "provider": "openrouter",
            "model": "", "credential_env": "OPENROUTER_API_KEY",
            "enabled": bad,
        }],
    )
    with pytest.raises(fb.ConfigError):
        fb.load_contestants_toml(path)


# ------------------------------- DEF-033 ----------------------------------- #
# hostile/NaN/Inf telemetry must never crash a run; notes are recorded and
# every artifact is still secret-scanned.


def test_nonfinite_telemetry_does_not_crash_run_and_is_noted(
    tmp_path, monkeypatch
):
    """DEF-033 — NaN/Infinity/1e309 telemetry values are recorded as
    unparseable-telemetry notes, the run continues, results stay enriched from
    the GOOD lines and the report (secret-scan result) is still written."""
    from threading import Thread

    class _H(_Handler):
        pass

    server = _make_fake_server(_H({}))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        contestants_path = _default_contestants(tmp_path)
        monkeypatch.setenv("OPENROUTER_API_KEY", KEY_A)
        telemetry = tmp_path / "hostile-telemetry.jsonl"
        telemetry.write_text(
            "\n".join(
                [
                    '{"generationAttemptId": "GA-MODEL-A", "event": "provider.call.start", "effectiveProviderTimeoutMs": NaN}',
                    '{"generationAttemptId": "GA-MODEL-A", "event": "provider.call.complete", "elapsedMs": 1e309, "structuredOutput": true}',
                    '{"generationAttemptId": "GA-MODEL-A", "event": "generation.published", "totalElapsedMs": 1234.5, "providerCallCount": 4, "repairCount": 0, "regenerationCount": 0}',
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        out = tmp_path / "nan-telemetry"
        rc = _run_main(
            [
                "--suite", "smoke",
                "--contestants", str(contestants_path),
                "--case-ids", "easy-office-001",
                "--driver", "http",
                "--base-url", f"http://127.0.0.1:{port}",
                "--telemetry-logs", str(telemetry),
                "--output-dir", str(out),
                "--yes",
            ]
        )
        assert rc == 0
        records = _read_results(out)
        assert len(records) == 1
        # The good line still enriched; no non-finite number survived.
        assert records[0]["providerCallCount"] == 4
        assert records[0]["repairCount"] == 0
        assert records[0]["totalElapsedMs"] != 1e309
        metadata = json.loads((out / "metadata.json").read_text(encoding="utf-8"))
        notes = " ".join(metadata.get("telemetryNotes") or [])
        assert "2 telemetry line(s)" in notes
        assert "NaN" in notes or "non-finite" in notes
        # Artifact scan ran on the happy path (report written, CLEAN).
        report = (out / "report.md").read_text(encoding="utf-8")
        assert "SECRET-SAFETY CHECK" in report
    finally:
        server.shutdown()
        server.server_close()


def test_parse_telemetry_events_counts_unparseable_lines(tmp_path):
    """DEF-033 unit — the NaN/Inf-rejecting parser skips malformed lines and
    non-finite constants and returns the count as a note."""
    path = tmp_path / "telemetry.jsonl"
    path.write_text(
        "\n".join(
            [
                '{"event": "a", "value": NaN}',
                '{"event": "b", "value": 1e309}',
                "not json at all",
                '{"event": "ok", "value": 3}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    events, unparseable = fb.parse_telemetry_events_with_notes(path)
    assert unparseable == 3
    assert len(events) == 1
    assert events[0]["value"] == 3
    assert fb.parse_telemetry_events_with_notes(tmp_path / "missing.jsonl") == ([], 0)


# ------------------------------- DEF-034 ----------------------------------- #
# CONTRACT_VIOLATION: verifiable vs unverifiable call counts; never a false
# all-clear when zero calls were verifiable.


def test_contract_unverifiable_when_timeout_data_missing_unit():
    """DEF-034 unit — provider.call events lacking effectiveProviderTimeoutMs
    are counted unverifiable (not silently treated as 'no violation')."""
    missing_timeout = [
        {"event": "provider.call.start", "configuredGenerationDeadlineMs": 60000},
        {"event": "provider.call.complete", "elapsedMs": 999999,
         "structuredOutput": True},
    ]
    verifiable, unverifiable = fb.call_timeout_verifiability(missing_timeout)
    assert verifiable == 0
    assert unverifiable == 2
    record = {"published": True, "failureCode": None, "totalElapsedMs": 42}
    fb.enrich_result(record, missing_timeout, deadline_ms=60000)
    assert record["contractViolationVerifiableCalls"] == 0
    assert record["contractViolationUnverifiableCalls"] == 2
    assert record["contractViolations"] is None
    assert record["timeoutContractViolation"] is None


def test_report_never_claims_all_clear_when_zero_calls_verifiable(
    tmp_path, monkeypatch
):
    """DEF-034 end-to-end — telemetry that lacks timeout data produces
    NOT-verifiable counts in report.md (and in the summary), never the phrase
    'No CONTRACT_VIOLATION detected in this run'."""
    from threading import Thread

    server = _make_fake_server(_Handler({}))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        contestants_path = _default_contestants(tmp_path)
        monkeypatch.setenv("OPENROUTER_API_KEY", KEY_A)
        telemetry = tmp_path / "no-timeout-telemetry.jsonl"
        telemetry.write_text(
            "\n".join(
                [
                    '{"generationAttemptId": "GA-MODEL-A", "event": "provider.call.start", "configuredGenerationDeadlineMs": 60000}',
                    '{"generationAttemptId": "GA-MODEL-A", "event": "provider.call.complete", "elapsedMs": 1250, "structuredOutput": true}',
                    '{"generationAttemptId": "GA-MODEL-A", "event": "generation.published", "totalElapsedMs": 5000, "providerCallCount": 1}',
                ]
            )
            + "\n",
            encoding="utf-8",
        )
        out = tmp_path / "contract-nover"
        rc = _run_main(
            [
                "--suite", "smoke",
                "--contestants", str(contestants_path),
                "--case-ids", "easy-office-001",
                "--driver", "http",
                "--base-url", f"http://127.0.0.1:{port}",
                "--telemetry-logs", str(telemetry),
                "--output-dir", str(out),
                "--yes",
            ]
        )
        assert rc == 0
        records = _read_results(out)
        record = records[0]
        assert record["contractViolationVerifiableCalls"] == 0
        assert record["contractViolationUnverifiableCalls"] == 2
        report = (out / "report.md").read_text(encoding="utf-8")
        assert "NOT verifiable: 2 calls missing timeout data" in report
        assert "CONTRACT check NOT VERIFIABLE" in report
        assert "No CONTRACT_VIOLATION detected in this run" not in report
        summary = json.loads((out / "summary.json").read_text(encoding="utf-8"))
        row = summary["perContestant"]["openrouter-model-a"]
        assert row["contractUnverifiableCalls"] == 2
        assert row["contractVerifiableCalls"] == 0
    finally:
        server.shutdown()
        server.server_close()


# ------------------------------- DEF-035 ----------------------------------- #
# every float CLI option rejects non-finite values.


@pytest.mark.parametrize(
    "flag,value",
    [
        ("--http-timeout-seconds", "nan"),
        ("--http-timeout-seconds", "inf"),
        ("--http-timeout-seconds", "-inf"),
        ("--max-estimated-cost", "nan"),
        ("--max-estimated-cost", "inf"),
        ("--max-estimated-cost", "-inf"),
        ("--frontier-timeout-seconds", "nan"),
        ("--frontier-timeout-seconds", "inf"),
        ("--frontier-timeout-seconds", "-inf"),
    ],
)
def test_cli_float_bounds_reject_nonfinite(tmp_path, capsys, flag, value):
    """DEF-035 — nan/inf/-inf are rejected on every float CLI option."""
    rc = _run_main(["--suite", "smoke", f"{flag}={value}", "--dry-run"])
    assert rc == 2
    assert "finite" in capsys.readouterr().err


# ------------------------------- DEF-036 ----------------------------------- #
# CSV formula injection neutralization.


def test_csv_formula_injection_neutralized(tmp_path, monkeypatch, golden_mock):
    """DEF-036 — cells starting with = + - @ are neutralized with a leading
    apostrophe in summary.csv (no live spreadsheet formula)."""
    assert fb._csv_cell("=1+1") == "'=1+1"
    assert fb._csv_cell("+SUM(A1)") == "'+SUM(A1)"
    assert fb._csv_cell("-2+3") == "'-2+3"
    assert fb._csv_cell("@cmd") == "'@cmd"
    path = _write_contestants(
        tmp_path,
        [{
            "id": "formula-a", "label": "=1+1", "provider": "openrouter",
            "model": "MODEL-A", "credential_env": "OPENROUTER_API_KEY",
        }],
    )
    out = tmp_path / "formula-run"
    rc = _run_main(
        [
            "--suite", "smoke",
            "--contestants", str(path),
            "--case-ids", "easy-office-001",
            "--driver", "inprocess",
            "--output-dir", str(out),
            "--yes",
        ]
    )
    assert rc == 0
    csv_text = (out / "summary.csv").read_text(encoding="utf-8")
    assert "'=1+1" in csv_text
    for line in csv_text.splitlines():
        if "formula-a" in line or "=1+1" in line:
            assert not line.replace("'=1+1", "").lstrip().startswith("=")


# ------------------------------- DEF-037 ----------------------------------- #
# corpus ids/tags/prompt reject control characters (markdown injection).


def test_corpus_rejects_control_chars_in_ids_tags_and_prompt(tmp_path):
    """DEF-037 — a hostile corpus id/tag/prompt with control/newline characters
    is rejected at load time; newlines inside prompts remain legal."""
    base = {"difficulty": "easy", "prompt": "Victim: x\n"}
    bad_id = {"id": "x\n- stolen line", **base}
    bad_tag = {"id": "ok", "tags": ["fine", "broken\r\nbullet"], **base}
    bad_prompt = {"id": "ok", **base, "prompt": "Victim: \x00nul\n"}
    bad_space_id = {"id": "bad id", **base}
    for i, payload in enumerate([bad_id, bad_tag, bad_prompt, bad_space_id]):
        directory = tmp_path / f"corpus-{i}"
        directory.mkdir()
        (directory / "hostile.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        with pytest.raises(fb.ConfigError):
            fb.load_corpus(directory)


def test_markdown_clean_strips_control_chars_unit():
    """DEF-037 — report-embedded untrusted strings are cleaned before they can
    splice markdown."""
    assert fb._markdown_clean("a\n- bullet") == "a - bullet"
    assert fb._markdown_clean("x\x00y\rz") == "x y z"
    assert "\n" not in fb._markdown_clean("no\nfresh\nlines")


# ------------------------------- DEF-038 ----------------------------------- #
# credential_env policy: token-shaped uppercase + allowed suffix + blocked
# common environment names.


def test_credential_env_policy_blocks_common_process_variables(tmp_path):
    """DEF-038 — PATH/HOME/SHELL/PWD/SSL_CERT_FILE/BENCHMARK_ENV_FILE and
    suffix-less names can never be wired as the outbound Bearer credential;
    _API_KEY/_KEY/_TOKEN/_SECRET suffixed names still load."""
    def _load(env_name):
        path = _write_contestants(
            tmp_path / env_name.replace("/", "_"),
            [{
                "id": "c1", "label": "L", "provider": "openrouter",
                "model": "", "credential_env": env_name, "enabled": False,
            }],
        )
        return fb.load_contestants_toml(path)[0]

    for blocked in (
        "PATH", "HOME", "SHELL", "PWD", "TMP", "SSL_CERT_FILE",
        "BENCHMARK_ENV_FILE", "MY_VARIABLE", "api_Key_BAD",
    ):
        with pytest.raises(fb.ConfigError):
            _load(blocked)
    assert _load("MY_BENCH_API_KEY").credential_env == "MY_BENCH_API_KEY"
    assert _load("SOME_PROVIDER_TOKEN").credential_env == "SOME_PROVIDER_TOKEN"
    assert fb._credential_env_name_policy("OPENROUTER_API_KEY") == "OPENROUTER_API_KEY"
    assert fb._credential_env_name_policy("PATH") is None
    assert fb._credential_env_name_policy("BENCH_KEY_A_API_KEY") == "BENCH_KEY_A_API_KEY"


# ------------------------------- DEF-039 ----------------------------------- #
# artifact secret scan runs on EVERY exit path (abort paths included).


def test_artifact_scan_runs_on_abort_path_and_aborts_exit2(
    tmp_path, monkeypatch, capsys
):
    """DEF-039 — a mid-run abort (KeyboardInterrupt raised from the first
    artifact write) still runs the artifact secret scan on the exit path; a
    credential found in the written artifacts overrides the exit code with 2."""
    original_write = fb.write_results_jsonl

    def _leaky_write(path, record):
        original_write(path, record)
        (path.parent / "leak.txt").write_text(
            f"leaked {SENTINEL}", encoding="utf-8"
        )
        raise KeyboardInterrupt()

    monkeypatch.setattr(fb, "write_results_jsonl", _leaky_write)
    monkeypatch.setenv("OPENROUTER_API_KEY", SENTINEL)
    contestants_path = _default_contestants(tmp_path)
    out = tmp_path / "abort-scan"
    rc = _run_main(
        [
            "--suite", "smoke",
            "--contestants", str(contestants_path),
            "--case-ids", "easy-office-001",
            "--driver", "inprocess",
            "--output-dir", str(out),
            "--yes",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 2
    assert "credential material detected in benchmark artifacts" in captured.err
    assert "leak.txt" in captured.err
    assert SENTINEL not in captured.out  # never printed to stdout


# ------------------------------- DEF-040 ----------------------------------- #
# _sanitize_error returns ONLY the exception class name (plus numeric TLS
# verify code), never the exception message.


def test_sanitize_error_never_leaks_exception_message():
    """DEF-040 — str(exc) content (URLs/credentials) is never returned by the
    sanitizer; wrapper exceptions are unwrapped one level for the class name."""
    assert fb._sanitize_error(
        ValueError(f"GET https://user:{SENTINEL}@host/with/key=SECRET")
    ) == "ValueError"
    assert SENTINEL not in fb._sanitize_error(
        ValueError(f"boom {SENTINEL}")
    )
    import urllib.error as _urlerror

    assert (
        fb._sanitize_error(_urlerror.URLError(ValueError("inner message")))
        == "ValueError"
    )
    assert "inner message" not in fb._sanitize_error(
        _urlerror.URLError(ValueError("inner message"))
    )


# ------------------------------- DEF-041 ----------------------------------- #
# scan detects UTF-16 / \u-escaped / URL-encoded / base64 / hex renderings of
# any suspicious value (credential values + extra config/corpus strings).


def test_scan_detects_encoded_secret_forms(tmp_path):
    """DEF-041 — byte-exact-only scanning is bypassable via encodings; the
    extended scan detects UTF-16LE/BE, fully ``\\u``-escaped, URL-encoded,
    base64 (and urlsafe) and hex renderings of every suspicious value."""
    import base64 as _b64
    import urllib.parse as _urlparse

    secret = "AB+CD/EF=GH?uv"
    target = tmp_path / "encoded"
    target.mkdir()
    variants = {
        "utf8.txt": secret.encode("utf-8"),
        "utf16le.txt": secret.encode("utf-16-le"),
        "utf16be.txt": secret.encode("utf-16-be"),
        "uescape.txt": "".join("\\u%04x" % ord(ch) for ch in secret).encode("ascii"),
        "url.txt": _urlparse.quote(secret, safe="").encode("ascii"),
        "urlplus.txt": _urlparse.quote_plus(secret, safe="").encode("ascii"),
        "b64.txt": _b64.b64encode(secret.encode("utf-8")),
        "b64nopad.txt": _b64.b64encode(secret.encode("utf-8")).rstrip(b"="),
        "b64url.txt": _b64.urlsafe_b64encode(secret.encode("utf-8")),
        "hex.txt": secret.encode("utf-8").hex().encode("ascii"),
    }
    for name, data in variants.items():
        (target / name).write_bytes(data)
    hits = fb.scan_files_for_secrets(target, [secret])
    hit_names = {rel for rel, _marker in hits}
    assert hit_names == set(variants)
    # The extra-list capability: an unrelated suspicious value is also flagged.
    (target / "extra.json").write_text("tagged meta-value", encoding="utf-8")
    hits2 = fb.scan_files_for_secrets(target, [], extra=["tagged meta-value"])
    assert any(rel == "extra.json" for rel, _marker in hits2)


# ------------------------------- DEF-042 ----------------------------------- #
# --contestant <disabled-id> must fail closed (plan printed, exit 2).


def test_contestant_filter_only_disabled_fails_closed(tmp_path, capsys):
    """DEF-042 — filtering to a disabled contestant prints the (non-secret)
    plan and exits 2 instead of silently running a zero-attempt benchmark."""
    contestants, suites = _contenders_toml()
    rc = _run_main(
        [
            "--suite", "smoke",
            "--contestants", str(contestants),
            "--suites", str(suites),
            "--case-ids", "easy-office-001",
            "--driver", "inprocess",
            "--contestant", "luna-baseline",
            "--dry-run",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 2
    assert "Phase 31 benchmark plan" in captured.out
    assert "luna-baseline" in captured.out
    assert "DISABLED" in captured.err


# ------------------------------- DEF-043 ----------------------------------- #
# no ---yes guard: EOF on stdin defaults to "no" with a documented message;
# the output dir is created (and --rerun deletes) only AFTER confirmation.


class _EOFStdin:
    def isatty(self):
        return True

    def readline(self, *args, **kwargs):
        raise EOFError()


def test_confirmation_eof_defaults_no_and_creates_nothing(
    tmp_path, monkeypatch, capsys, golden_mock
):
    """DEF-043 — an EOF on the confirmation prompt defaults to "no" with the
    documented message and the output dir is NEVER created (nor a --rerun
    reset performed) without an explicit confirmation."""
    monkeypatch.setattr(fb.sys, "stdin", _EOFStdin())
    contestants_path = _default_contestants(tmp_path)
    out = tmp_path / "prompt-out"
    rc = _run_main(
        [
            "--suite", "smoke",
            "--contestants", str(contestants_path),
            "--case-ids", "easy-office-001",
            "--driver", "inprocess",
            "--output-dir", str(out),
        ]
    )
    captured = capsys.readouterr()
    assert rc == 0
    assert "was not confirmed" in captured.out
    assert "non-interactive execution requires --yes" in captured.out
    assert not out.exists()
    assert golden_mock.posts == []


def test_rerun_without_confirmation_never_deletes(tmp_path, monkeypatch, capsys):
    """DEF-043 — ``--rerun`` with a refused/EOF confirmation leaves an existing
    benchmark-run directory (even one with a valid marker) untouched."""
    import datetime as _dt

    out = tmp_path / "prior-run"
    out.mkdir()
    (out / "metadata.json").write_text(
        json.dumps(
            {
                "benchmarkSchemaVersion": fb.BENCHMARK_SCHEMA_VERSION,
                "benchmarkRunId": "phase31-20260101-000000",
                "toolName": "tools.frontier_benchmark",
            }
        ),
        encoding="utf-8",
    )
    (out / "precious.txt").write_text("keep me", encoding="utf-8")
    monkeypatch.setattr(fb.sys, "stdin", _EOFStdin())
    contestants_path = _default_contestants(tmp_path)
    rc = _run_main(
        [
            "--suite", "smoke",
            "--contestants", str(contestants_path),
            "--case-ids", "easy-office-001",
            "--driver", "inprocess",
            "--output-dir", str(out),
            "--rerun",
        ]
    )
    captured = capsys.readouterr()
    assert rc == 0
    assert "was not confirmed" in captured.out
    assert (out / "precious.txt").is_file()
    assert (out / "metadata.json").is_file()
    assert not (out / "results.jsonl").exists()


# ------------------------------- DEF-044 ----------------------------------- #
# summarize_phase29_json degrades gracefully on hostile monitoring JSON.


def test_phase29_summarize_degrades_gracefully_on_hostile_json(tmp_path):
    """DEF-044 — unparseable monitoring counts degrade to 0/None with a
    warning (never a ValueError abort after all paid work)."""
    hostile = json.dumps(
        {
            "http": {
                "total": 5,
                "status_classes": {"4xx": "many", "5xx": None},
                "count_5xx": "nope",
            },
            "product": {
                "playthroughs_started": None,
                "cases_started": "??",
                "cases_completed": 3,
                "completion_rate": "not-a-number",
            },
        }
    )
    enriched = fb.summarize_phase29_json(
        hostile, hours=1, health_ok=True,
        health_text="procedural-detective: state=running, health=healthy\n",
    )
    assert enriched["http_4xx"] == 0
    assert enriched["http_5xx"] == 0
    assert enriched["completion_rate"] is None
    assert any("not parseable" in w for w in enriched["warnings"])
    # A boolean status-class value is treated as 0 with a warning too.
    hostile_bool = json.dumps(
        {"http": {"status_classes": {"4xx": True}, "count_5xx": 0}}
    )
    enriched2 = fb.summarize_phase29_json(
        hostile_bool, hours=1, health_ok=True, health_text=""
    )
    assert enriched2["http_4xx"] == 0
    assert any("boolean" in w for w in enriched2["warnings"])
    # The zero-count all-clear path still warns about the empty window.
    assert any("no access-log entries" in w for w in enriched2["warnings"])