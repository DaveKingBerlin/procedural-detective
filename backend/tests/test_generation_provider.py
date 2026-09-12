"""FakeProvider / CountingProvider / ProviderResult contract tests (Phase4 D)."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest  # noqa: E402

from fixtures.golden_generation import (  # noqa: E402
    GOLDEN_STAGE_PAYLOADS,
)

from app.generation.fake_provider import (  # noqa: E402
    CountingProvider,
    FakeProvider,
)
from app.generation.provider import (  # noqa: E402
    GenerateRequest,
    GenerationStage,
    ProviderError,
    ProviderResult,
)

STAGE = GenerationStage.CASE_TRUTH
OTHER_STAGE = GenerationStage.EVIDENCE


def _request(stage=STAGE, attempt_id="att-1") -> GenerateRequest:
    return GenerateRequest(attempt_id=attempt_id, stage=stage, prompt_context="ctx")


class _RecordingSink:
    def __init__(self):
        self.completions: list[tuple[str, ProviderResult]] = []

    def on_completion(self, pending_id: str, result: ProviderResult) -> None:
        self.completions.append((pending_id, result))


# ---------------------------------------------------------------------------
# ProviderResult invariants
# ---------------------------------------------------------------------------


def test_provider_result_default_is_neutral():
    result = ProviderResult()
    assert result.content is None
    assert result.pending is False
    assert result.pending_id is None
    assert result.timed_out is False
    assert result.error is None


def test_provider_result_at_most_one_outcome():
    with pytest.raises(ValueError):
        ProviderResult(content="x", error="boom")
    with pytest.raises(ValueError):
        ProviderResult(timed_out=True, content="x")
    with pytest.raises(ValueError):
        ProviderResult(pending=True, timed_out=True)


def test_provider_result_pending_requires_pending_id():
    with pytest.raises(ValueError):
        ProviderResult(pending=True)


def test_provider_result_pending_id_requires_pending():
    with pytest.raises(ValueError):
        ProviderResult(pending_id="p-1")


def test_generate_request_validation():
    with pytest.raises(ValueError):
        GenerateRequest(attempt_id="", stage=STAGE, prompt_context="c")
    with pytest.raises(ValueError):
        GenerateRequest(attempt_id="a", stage="not-a-stage", prompt_context="c")
    with pytest.raises(ValueError):
        GenerateRequest(attempt_id="a", stage=STAGE, prompt_context="c", diagnostics=("ok", 3))


# ---------------------------------------------------------------------------
# FakeProvider success modes
# ---------------------------------------------------------------------------


def test_fake_provider_raw_string_success():
    provider = FakeProvider({STAGE: ["<json>{'crime': 1}</json>"]})
    result = provider.generate(_request())
    assert result == ProviderResult(content="<json>{'crime': 1}</json>")
    assert provider.pending_ids() == ()


def test_fake_provider_explicit_result_passthrough():
    expected = ProviderResult(content='{"crime": {}}')
    provider = FakeProvider({STAGE: [expected]})
    assert provider.generate(_request()) == expected


def test_fake_provider_ok_directive():
    provider = FakeProvider({STAGE: ["ok:{\"crime\": {}}"]})
    result = provider.generate(_request())
    assert result == ProviderResult(content='{"crime": {}}')


def test_fake_provider_malformed_directive():
    provider = FakeProvider({STAGE: ["malformed"]})
    result = provider.generate(_request())
    assert result.content == "<not-json>"


def test_fake_provider_timeout_directive():
    provider = FakeProvider({STAGE: ["timeout"]})
    result = provider.generate(_request())
    assert result.timed_out is True
    assert result.content is None


def test_fake_provider_exception_directive():
    provider = FakeProvider({STAGE: ["exception"]})
    with pytest.raises(ProviderError, match="scripted failure"):
        provider.generate(_request())


def test_fake_provider_pending_directive_does_not_touch_sink():
    sink = _RecordingSink()
    provider = FakeProvider({STAGE: ["pending"]}, sink=sink)
    result = provider.generate(_request())
    assert result.pending is True
    assert result.pending_id is not None
    assert result.pending_id in provider.pending_ids()
    assert sink.completions == []  # deferred: sink NOT invoked by generate()


def test_fake_provider_logs_calls():
    provider = FakeProvider({STAGE: ["ok:one"], OTHER_STAGE: ["ok:two"]})
    req1 = _request()
    req2 = _request(stage=OTHER_STAGE, attempt_id="att-2")
    provider.generate(req1)
    provider.generate(req2)
    assert provider.calls == [req1, req2]


def test_fake_provider_exhausted_script_raises():
    provider = FakeProvider({STAGE: ["ok:x"]})
    provider.generate(_request())
    with pytest.raises(ProviderError, match="exhausted"):
        provider.generate(_request())


def test_fake_provider_unknown_stage_raises_when_not_scripted():
    provider = FakeProvider({})
    with pytest.raises(ProviderError, match="exhausted"):
        provider.generate(_request())


def test_fake_provider_invalid_entry_type_raises():
    provider = FakeProvider({STAGE: [42]})
    with pytest.raises(ProviderError, match="entry"):
        provider.generate(_request())


# ---------------------------------------------------------------------------
# deferred completion via resolve -> sink
# ---------------------------------------------------------------------------


def test_resolve_routes_to_sink_with_pending_id():
    sink = _RecordingSink()
    provider = FakeProvider({STAGE: ["pending"]}, sink=sink)
    request = _request(attempt_id="att-9")
    pending = provider.generate(request)
    assert pending.pending_id is not None
    completion = ProviderResult(content='{"crime": {}}')
    provider.resolve(pending.pending_id, completion)
    assert sink.completions == [(pending.pending_id, completion)]
    assert pending.pending_id not in provider.pending_ids()


def test_resolve_unknown_pending_id_raises_key_error():
    provider = FakeProvider({STAGE: ["pending"]}, sink=_RecordingSink())
    provider.generate(_request())
    with pytest.raises(KeyError):
        provider.resolve("never-issued", ProviderResult(content="x"))


def test_resolve_after_resolution_raises_key_error():
    sink = _RecordingSink()
    provider = FakeProvider({STAGE: ["pending"]}, sink=sink)
    pending = provider.generate(_request())
    provider.resolve(pending.pending_id, ProviderResult(content="x"))
    with pytest.raises(KeyError):
        provider.resolve(pending.pending_id, ProviderResult(content="y"))


def test_resolve_without_sink_is_noop_for_known_pending():
    provider = FakeProvider({STAGE: ["pending"]}, sink=None)
    pending = provider.generate(_request())
    provider.resolve(pending.pending_id, ProviderResult(content="x"))
    assert provider.pending_ids() == ()


def test_pending_ids_only_outstanding():
    provider = FakeProvider({STAGE: ["pending", "pending"]})
    first = provider.generate(_request())
    second = provider.generate(_request())
    assert provider.pending_ids() == (first.pending_id, second.pending_id)
    provider.resolve(first.pending_id, ProviderResult(content="x"))
    assert provider.pending_ids() == (second.pending_id,)


# ---------------------------------------------------------------------------
# from_golden / seed determinism
# ---------------------------------------------------------------------------


def test_from_golden_builds_success_script():
    provider = FakeProvider.from_golden(GOLDEN_STAGE_PAYLOADS)
    for stage, payload in GOLDEN_STAGE_PAYLOADS.items():
        result = provider.generate(
            GenerateRequest(attempt_id="att", stage=stage, prompt_context="ctx")
        )
        assert result.content == payload


def test_fake_provider_is_seed_deterministic():
    script = {
        STAGE: [
            "raw-{seed}",
            "ok:ok-{seed}",
            "malformed",
            "timeout",
            "exception",
            "pending",
        ]
    }

    def run(seed):
        provider = FakeProvider(script, seed=seed)
        outputs = []
        for _ in range(6):
            try:
                outputs.append(provider.generate(_request()))
            except ProviderError as exc:
                outputs.append(f"raised:{exc}")
        return outputs

    first = run(7)
    second = run(7)
    assert first == second  # the entire result sequence is identical
    assert first[0].content == "raw-7"
    assert first[1].content == "ok-7"


# ---------------------------------------------------------------------------
# CountingProvider
# ---------------------------------------------------------------------------


def test_counting_provider_counts_and_logs():
    inner = FakeProvider({STAGE: ["ok:x"], OTHER_STAGE: ["ok:y"]})
    counting = CountingProvider(inner)
    req1 = _request(attempt_id="att-1")
    req2 = _request(stage=OTHER_STAGE, attempt_id="att-2")
    assert counting.call_count == 0
    counting.generate(req1)
    counting.generate(req2)
    assert counting.call_count == 2
    assert counting.call_log == [req1, req2]


def test_counting_provider_rejected_admission_consumes_zero_calls():
    # Proof helper: an admission gate that never calls generate leaves the
    # counting wrapper at zero even though it wraps a live script.
    inner = FakeProvider({STAGE: ["ok:x"]})
    counting = CountingProvider(inner)
    assert counting.call_count == 0
    # simulate a rejected admission: nothing is invoked
    assert counting.call_log == []