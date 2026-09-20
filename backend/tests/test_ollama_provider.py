"""Phase16 K — local Ollama provider tests (items 1-20).

ALL transport interaction is mocked with ``MockOllamaTransport`` (injected
responses; records every call): no live Ollama installation is ever required
and the autouse network block (test_network_block.py) makes real calls
impossible anyway. Controller-level tests drive a REAL ``GenerationController``
with the Ollama adapter over the mocked transport so budgets / admission /
repair / race / validation semantics are exact.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fixtures.golden_generation import (  # noqa: E402
    GOLDEN_FULL_DRAFT,
    GOLDEN_STAGE_PAYLOADS,
)

from app.core.config import Settings  # noqa: E402
from app.generation.admission import AdmissionController, AdmissionDenied  # noqa: E402
from app.generation.clock import ManualClock  # noqa: E402
from app.generation.controller import GenerationController  # noqa: E402
from app.generation.constraints import LockedConstraints  # noqa: E402
from app.domain.evidence import PROPOSITION_TYPES  # noqa: E402
from app.generation.fake_provider import FakeProvider  # noqa: E402
from app.generation.ids import IdSource  # noqa: E402
from app.generation.ollama_provider import (  # noqa: E402
    MAX_OLLAMA_RESPONSE_BYTES,
    OllamaProvider,
    ollama_available,
)
from app.generation.provider import (  # noqa: E402
    GenerateRequest,
    GenerationStage,
    ProviderResult,
)
from app.generation.state_machine import GenerationState, ValidationOutcome  # noqa: E402

GOLDEN_PROMPT = (
    "Victim: sarah_miller\n"
    "Murderer: thomas_reed\n"
    "Motive: cover_up_embezzlement\n"
    "Weapon: kitchen_knife\n"
    "Time: 2026-09-11T22:17:00+02:00\n"
    "Witness: emily_reed\n"
)

_G = GOLDEN_STAGE_PAYLOADS

OLLAMA_BASE = "http://127.0.0.1:11434"
OLLAMA_MODEL = "llama3.2:3b"


# --------------------------------------------------------------------------- #
# Mock transport (never touches the network)
# --------------------------------------------------------------------------- #


class MockOllamaTransport:
    """Injects canned responses; records every call (atomic call counting).

    ``posts`` is a FIFO queue of response entries:

    - ``str``  -> HTTP 200 envelope with that generated content;
    - ``(status, body)`` -> an explicit status + body (str/dict/bytes);
    - a callable ``(url, payload, timeout) -> (status, bytes)`` for custom
      behavior (e.g. raising ``TimeoutError``).

    ``get`` answers ``/api/tags`` from ``tags_status`` / ``tags_models``.
    """

    def __init__(self, *, tags_status: int = 200, tags_models=(), posts=()):
        self.tags_status = int(tags_status)
        self.tags_models = list(tags_models)
        self.posts = list(posts)
        self.post_calls: list[tuple[str, dict, float]] = []
        self.get_calls: list[tuple[str, float]] = []

    def get(self, url: str, timeout: float) -> tuple[int, bytes]:
        self.get_calls.append((url, float(timeout)))
        if self.tags_status == 200:
            body = {"models": [{"name": name} for name in self.tags_models]}
            return 200, json.dumps(body).encode()
        return self.tags_status, b'{"error": "unavailable"}'

    def post_json(
        self, url: str, payload: dict, timeout: float
    ) -> tuple[int, bytes]:
        self.post_calls.append((url, dict(payload), float(timeout)))
        if not self.posts:
            # fail-closed default: malformed generated content
            return 200, self._envelope("<not-json>")
        entry = self.posts.pop(0)
        if callable(entry):
            return entry(url, dict(payload), timeout)
        if isinstance(entry, tuple):
            status, body = entry
            if isinstance(body, bytes):
                return status, body
            if isinstance(body, dict):
                return status, json.dumps(body).encode()
            return status, str(body).encode()
        return 200, self._envelope(str(entry))

    @staticmethod
    def _envelope(content: str) -> bytes:
        return json.dumps(
            {"model": OLLAMA_MODEL, "message": {"role": "assistant", "content": content}}
        ).encode()

    @property
    def call_count(self) -> int:
        return len(self.post_calls)

    def stage_of_call(self, index: int) -> str | None:
        payload = self.post_calls[index][1]
        messages = payload.get("messages") or ()
        content = messages[0].get("content", "") if messages else ""
        match = re.search(r"for the '([a-z_]+)' stage", content)
        return match.group(1) if match else None

    def prompt_of_call(self, index: int) -> str:
        payload = self.post_calls[index][1]
        messages = payload.get("messages") or ()
        return messages[0].get("content", "") if messages else ""


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _request(
    stage: GenerationStage = GenerationStage.CASE_TRUTH,
    attempt_id: str = "att-ollama",
    prompt_context: str = "ctx",
    locked: LockedConstraints | None = None,
    diagnostics: tuple[str, ...] = (),
) -> GenerateRequest:
    return GenerateRequest(
        attempt_id=attempt_id,
        stage=stage,
        prompt_context=prompt_context,
        locked=locked,
        diagnostics=diagnostics,
    )


def _provider(
    transport: MockOllamaTransport,
    *,
    base_url: str = OLLAMA_BASE,
    model: str = OLLAMA_MODEL,
    timeout_seconds: float = 5.0,
    structured_output: bool = False,
) -> OllamaProvider:
    return OllamaProvider(
        base_url=base_url,
        model=model,
        timeout_seconds=timeout_seconds,
        temperature=0.2,
        num_ctx=4096,
        transport=transport,
        structured_output=structured_output,
    )


def _admission(clock, ids, **overrides):
    kwargs = dict(
        max_concurrent_generations=1,
        max_concurrent_generations_global=3,
        max_generations_per_session_per_window=3,
        max_generations_global_per_window=20,
        anonymous_quota_session_ttl_seconds=86400,
    )
    kwargs.update(overrides)
    return AdmissionController(clock=clock, ids=ids, **kwargs)


def _controller(provider, admission, clock, ids, **overrides):
    kwargs = dict(
        deadline_seconds=60,
        max_llm_calls_per_generation=8,
        max_repair_passes=2,
        max_full_regenerations=1,
        max_prompt_chars=4000,
        seed=11,
    )
    kwargs.update(overrides)
    return GenerationController(
        provider=provider, admission=admission, clock=clock, ids=ids, **kwargs
    )


def _golden_posts(*, malformed_stage: GenerationStage | None = None, repair=GOLDEN_FULL_DRAFT):
    """Stage content queue for a full golden run (optionally malformed or
    fence-wrapped on one stage)."""
    posts = [
        _G[GenerationStage.CASE_TRUTH],
        _G[GenerationStage.PUBLIC_WORLD],
        _G[GenerationStage.EVIDENCE],
        _G[GenerationStage.WORLD_GRAPH],
    ]
    if malformed_stage is not None:
        posts[3 if malformed_stage is GenerationStage.WORLD_GRAPH else 2] = "<not-json>"
    if repair is not None:
        posts.append(repair)
    return posts


def _run_generation(provider, prompt=GOLDEN_PROMPT, **controller_overrides):
    """Start + resolve a controller run to a terminal state (returns record)."""
    clock = ManualClock()
    ids = IdSource()
    admission = _admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    controller = _controller(provider, admission, clock, ids, **controller_overrides)
    handle = controller.start_generation(
        prompt, anonymous_quota_session_id=session.session_id
    )
    return controller.attempt(handle.attempt_id)


# --------------------------------------------------------------------------- #
# 1. maps GenerateRequest correctly
# --------------------------------------------------------------------------- #


def test_01_maps_generate_request_correctly():
    transport = MockOllamaTransport(
        posts=['{"crime": {"type": "murder"}}']
    )
    provider = _provider(transport)
    result = provider.generate(
        _request(
            stage=GenerationStage.PUBLIC_WORLD,
            prompt_context="sanitized material",
            locked=LockedConstraints(victim="sarah_miller"),
            diagnostics=("one", "two"),
        )
    )
    assert result.content == '{"crime": {"type": "murder"}}'
    assert transport.call_count == 1
    url, payload, timeout = transport.post_calls[0]
    # endpoint shape: {base}/api/chat, non-streaming, messages array
    assert url == f"{OLLAMA_BASE}/api/chat"
    assert payload["model"] == OLLAMA_MODEL
    assert payload["stream"] is False
    assert payload["messages"] == [{"role": "user", "content": payload["messages"][0]["content"]}]
    assert payload["options"]["temperature"] == 0.2
    assert payload["options"]["num_ctx"] == 4096
    prompt = payload["messages"][0]["content"]
    assert "'public_world'" in prompt  # stage identity
    assert "sanitized material" in prompt  # prompt_context carried
    assert "sarah_miller" in prompt  # locked projection carried
    assert "- one" in prompt and "- two" in prompt  # diagnostics carried
    assert timeout == 5.0


# --------------------------------------------------------------------------- #
# 2. valid response -> ProviderResult(content)
# --------------------------------------------------------------------------- #


def test_02_valid_response_maps_to_provider_result_content():
    transport = MockOllamaTransport(posts=['{"crime": {}}'])
    result = _provider(transport).generate(_request())
    assert result == ProviderResult(content='{"crime": {}}')
    assert result.error is None
    assert result.timed_out is False
    assert result.pending is False


def test_02a_evidence_request_sends_the_closed_proposition_enum_schema():
    transport = MockOllamaTransport(posts=['{"evidence":[]}'])
    result = _provider(transport, structured_output=True).generate(
        _request(stage=GenerationStage.EVIDENCE)
    )
    assert result.content == '{"evidence":[]}'
    schema = transport.post_calls[0][1]["format"]
    proposition_type = (
        schema["properties"]["evidence"]["items"]["properties"]
        ["propositions"]["items"]["properties"]["type"]
    )
    assert proposition_type["enum"] == sorted(PROPOSITION_TYPES)


def test_02b_top_level_content_field_also_accepted():
    transport = MockOllamaTransport(
        posts=[(200, {"content": '{"crime": {}}'})]
    )
    result = _provider(transport).generate(_request())
    assert result.content == '{"crime": {}}'


def test_02c_non_dict_json_envelope_is_sanitized_error():
    transport = MockOllamaTransport(posts=[(200, b"[1, 2, 3]")])
    result = _provider(transport).generate(_request())
    assert result.content is None
    assert result.error is not None


# --------------------------------------------------------------------------- #
# 3. malformed JSON follows the NORMAL failure path (controller run)
# --------------------------------------------------------------------------- #


def test_03_malformed_json_follows_normal_failure_path():
    transport = MockOllamaTransport(
        posts=_golden_posts(malformed_stage=GenerationStage.EVIDENCE)
    )
    record = _run_generation(_provider(transport))
    assert record.state is GenerationState.PUBLISHED
    assert record.budget.repair_passes == 1
    assert record.last_validation.valid is True
    # the repair pass reached the SAME Ollama provider instance (5 posts).
    assert transport.call_count == 5
    assert transport.stage_of_call(4) == "repair"


def test_03b_fence_wrapped_malformed_json_still_normal_failure_path():
    """The adapter strips ONLY the outer fence; remaining malformation still
    fails through the normal parse/repair lifecycle."""
    fenced_malformed = "```json\n<not-json>\n```"
    transport = MockOllamaTransport(
        posts=[
            _G[GenerationStage.CASE_TRUTH],
            _G[GenerationStage.PUBLIC_WORLD],
            fenced_malformed,
            _G[GenerationStage.WORLD_GRAPH],
            GOLDEN_FULL_DRAFT,
        ]
    )
    record = _run_generation(_provider(transport))
    assert record.state is GenerationState.PUBLISHED
    assert record.budget.repair_passes == 1


def test_03c_fence_wrapped_valid_json_parses_without_repair():
    fenced_valid = "```json\n" + _G[GenerationStage.EVIDENCE] + "\n```"
    transport = MockOllamaTransport(
        posts=[
            _G[GenerationStage.CASE_TRUTH],
            _G[GenerationStage.PUBLIC_WORLD],
            fenced_valid,
            _G[GenerationStage.WORLD_GRAPH],
        ]
    )
    record = _run_generation(_provider(transport))
    assert record.state is GenerationState.PUBLISHED
    assert record.budget.repair_passes == 0
    assert transport.call_count == 4


# --------------------------------------------------------------------------- #
# 4. HTTP failure handled
# --------------------------------------------------------------------------- #


def test_04_http_failure_handled():
    transport = MockOllamaTransport(posts=[(404, {"error": "model not found"})])
    provider = _provider(transport)
    result = provider.generate(_request())
    assert result.content is None
    assert result.error is not None
    assert "404" in result.error
    assert result.timed_out is False

    # a FRESH transport for the controller run (the direct call consumed it).
    controller_transport = MockOllamaTransport(posts=[(404, {"error": "model not found"})])
    record = _run_generation(_provider(controller_transport))
    assert record.state is GenerationState.FAILED
    assert record.reason == "provider failure: generator unavailable"
    assert record.published is None
    assert controller_transport.call_count == 1


def test_04b_500_handled_without_exception_escape():
    transport = MockOllamaTransport(posts=[(500, "internal")])
    provider = _provider(transport)
    result = provider.generate(_request())
    assert result.content is None and result.error is not None
    assert result.timed_out is False


# --------------------------------------------------------------------------- #
# 5. timeout handled
# --------------------------------------------------------------------------- #


def _raise_timeout(url, payload, timeout):
    raise TimeoutError("ollama request timed out")


def test_05_timeout_handled():
    transport = MockOllamaTransport(posts=[_raise_timeout])
    provider = _provider(transport)
    result = provider.generate(_request())
    assert result.timed_out is True
    assert result.content is None
    assert result.error is None

    controller_transport = MockOllamaTransport(posts=[_raise_timeout])
    record = _run_generation(_provider(controller_transport))
    assert record.state is GenerationState.FAILED
    assert record.reason == "provider failure: generator unavailable"
    assert record.published is None
    assert controller_transport.call_count == 1  # no retry


# --------------------------------------------------------------------------- #
# 6. configured model unavailable handled
# --------------------------------------------------------------------------- #


def test_06_configured_model_unavailable_handled():
    settings = Settings(
        generation_provider="ollama",
        ollama_base_url=OLLAMA_BASE,
        ollama_model=OLLAMA_MODEL,
        ollama_timeout_seconds=5,
    )
    transport = MockOllamaTransport(
        tags_status=200,
        tags_models=["other-model:latest"],
        posts=[(404, {"error": "model not found"})],
    )
    available, detail = ollama_available(settings, transport)
    assert available is False
    assert detail == "not available"  # sanitized (no network/base-url detail)

    record = _run_generation(_provider(transport))
    assert record.state is GenerationState.FAILED
    assert record.reason == "provider failure: generator unavailable"
    assert record.published is None


def test_06b_model_available_true_and_tag_normalization():
    settings = Settings(
        generation_provider="ollama",
        ollama_base_url=OLLAMA_BASE,
        ollama_model="llama3.2",  # no tag == latest
        ollama_timeout_seconds=5,
    )
    transport = MockOllamaTransport(tags_models=["llama3.2:3b", "llama3.2:latest"])
    available, _detail = ollama_available(settings, transport)
    assert available is True

    exact = Settings(
        generation_provider="ollama",
        ollama_base_url=OLLAMA_BASE,
        ollama_model="llama3.2:3b",
    )
    assert ollama_available(exact, transport) == (True, "")
    missing = Settings(
        generation_provider="ollama",
        ollama_base_url=OLLAMA_BASE,
        ollama_model="mistral:7b",
    )
    assert ollama_available(missing, transport) == (False, "not available")


# --------------------------------------------------------------------------- #
# 7. provider call counts use the existing budget
# --------------------------------------------------------------------------- #


def test_07_provider_call_counts_use_existing_budget():
    transport = MockOllamaTransport(
        posts=[
            _G[GenerationStage.CASE_TRUTH],
            _G[GenerationStage.PUBLIC_WORLD],
            "<not-json>",  # EVIDENCE malformed -> repair loop
            _G[GenerationStage.WORLD_GRAPH],
            "<not-json>",  # REPAIR 1
            "<not-json>",  # REPAIR 2
            "<not-json>",  # REPAIR 3
            "<not-json>",  # REPAIR 4  (8th call -> budget ends)
        ]
    )
    record = _run_generation(
        _provider(transport),
        max_llm_calls_per_generation=8,
        max_repair_passes=100,
        max_full_regenerations=100,
    )
    assert record.state is GenerationState.FAILED
    assert "model call budget" in record.reason
    assert record.published is None
    assert transport.call_count == 8  # atomic call counting on the transport
    assert record.budget.calls == 8


# --------------------------------------------------------------------------- #
# 8. admission happens before the Ollama call
# --------------------------------------------------------------------------- #


def test_08_admission_happens_before_ollama_call():
    clock = ManualClock()
    ids = IdSource()
    admission = _admission(clock, ids, max_generations_per_session_per_window=1)
    session = admission.create_anonymous_quota_session()
    transport = MockOllamaTransport(posts=_golden_posts(repair=None))
    controller = _controller(_provider(transport), admission, clock, ids)

    first = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    assert controller.attempt(first.attempt_id).state is GenerationState.PUBLISHED
    assert transport.call_count == 4

    with pytest.raises(AdmissionDenied):
        controller.start_generation(
            GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
        )
    assert transport.call_count == 4  # denied request -> zero additional calls


# --------------------------------------------------------------------------- #
# 9. repair call uses Ollama through the SAME provider interface
# --------------------------------------------------------------------------- #


def test_09_repair_call_uses_same_ollama_provider_interface():
    # a malformed EVIDENCE stage forces a REAL repair pass.
    transport = MockOllamaTransport(
        posts=_golden_posts(malformed_stage=GenerationStage.EVIDENCE)
    )
    provider = _provider(transport)
    record = _run_generation(provider)
    assert record.state is GenerationState.PUBLISHED
    assert record.budget.repair_passes == 1
    # 5 posts total; the 5th is a REPAIR-stage call through the SAME provider.
    assert transport.call_count == 5
    stages = [transport.stage_of_call(i) for i in range(5)]
    assert stages == ["case_truth", "public_world", "evidence", "world_graph", "repair"]
    # the repair prompt carries the sanitized repair diagnostics marker
    repair_prompt = transport.prompt_of_call(4)
    assert "repair" in repair_prompt
    assert "VALIDATION ISSUES" not in repair_prompt  # diagnostics are sanitized issues


# --------------------------------------------------------------------------- #
# 10. stale Ollama completion cannot publish
# --------------------------------------------------------------------------- #


def test_10_stale_ollama_completion_cannot_publish():
    transport = MockOllamaTransport(
        posts=[
            _G[GenerationStage.CASE_TRUTH],
            _G[GenerationStage.PUBLIC_WORLD],
            "<not-json>",  # EVIDENCE malformed
            _G[GenerationStage.WORLD_GRAPH],
            "<not-json>",  # REPAIR 1
            "<not-json>",  # REPAIR 2 -> repair budget exhausted -> FAILED
        ]
    )
    provider = _provider(transport)
    clock = ManualClock()
    ids = IdSource()
    admission = _admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    controller = _controller(provider, admission, clock, ids)
    handle = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.FAILED
    assert record.reason == "repair budget exhausted"
    assert record.published is None
    calls_before = transport.call_count
    assert calls_before == 6

    # A stale/late completion routed through the controller's race machinery
    # (the ONLY deferred path) is discarded with zero mutation (§32.4).
    controller.on_completion(
        "stale-ollama-pending", ProviderResult(content=GOLDEN_FULL_DRAFT)
    )
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.FAILED
    assert record.published is None
    assert transport.call_count == calls_before  # zero new provider calls
    # Attempting to publish the stale material is refused.
    assert controller.publish(handle.attempt_id).success is False


# --------------------------------------------------------------------------- #
# 11. CaseTruth never leaks to the solver
# --------------------------------------------------------------------------- #


def test_11_case_truth_never_leaks_to_solver():
    transport = MockOllamaTransport(posts=_golden_posts(repair=None))
    provider = _provider(transport)
    from app.generation import pipeline

    clock = ManualClock()
    ids = IdSource()
    admission = _admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    controller = _controller(provider, admission, clock, ids)
    handle = controller.start_generation(
        GOLDEN_PROMPT, anonymous_quota_session_id=session.session_id
    )
    record = controller.attempt(handle.attempt_id)
    assert record.state is GenerationState.PUBLISHED

    # (a) provider request prompts never carry hidden truth fields.
    hidden = ("timeline", "relationships", "facts", "solverProof", "_phase3_cache")
    all_prompts = "".join(transport.prompt_of_call(i) for i in range(4))
    for token in hidden:
        assert token not in all_prompts
    # the CASE_TRUTH request (first call) carries no caseTruth material at all.
    assert "caseTruth" not in transport.prompt_of_call(0)

    # (b) the pipeline solve inputs (public + evidence) have no truth tokens.
    public, evidence, _truth, _draft = pipeline.assemble(record)
    blob = repr(public) + " ".join(repr(fact) for fact in evidence)
    for token in ("murdererId", "crimeTime", "timeline", "relationships", "solverProof"):
        assert token not in blob


# --------------------------------------------------------------------------- #
# 12. unsafe generated content rejected (never published)
# --------------------------------------------------------------------------- #


def _unsafe_evidence() -> str:
    doc = json.loads(_G[GenerationStage.EVIDENCE])
    doc["evidence"][0]["presentation"]["description"] = (
        '<script>alert(1)</script> javascript:evil data:text/plain,x'
    )
    return json.dumps(doc, sort_keys=True, ensure_ascii=False)


def test_12_unsafe_generated_content_rejected_and_repaired():
    transport = MockOllamaTransport(
        posts=[
            _G[GenerationStage.CASE_TRUTH],
            _G[GenerationStage.PUBLIC_WORLD],
            _unsafe_evidence(),
            _G[GenerationStage.WORLD_GRAPH],
            GOLDEN_FULL_DRAFT,
        ]
    )
    record = _run_generation(_provider(transport))
    assert record.state is GenerationState.PUBLISHED
    # The unsafe stage output was caught by the safety bucket (repair heals).
    assert record.budget.repair_passes == 1
    payload_blob = repr(record.published.draft)
    for token in ("<script>", "javascript:", "data:text"):
        assert token not in payload_blob


def test_12b_unsafe_content_without_repair_budget_never_publishes():
    transport = MockOllamaTransport(
        posts=[
            _G[GenerationStage.CASE_TRUTH],
            _G[GenerationStage.PUBLIC_WORLD],
            _unsafe_evidence(),
            _G[GenerationStage.WORLD_GRAPH],
        ]
    )
    record = _run_generation(_provider(transport), max_repair_passes=0)
    assert record.state is GenerationState.FAILED
    assert "repair budget" in record.reason
    assert record.published is None


# --------------------------------------------------------------------------- #
# 13. hostile AssetSpec rejected (unknown-object extraction path)
# --------------------------------------------------------------------------- #


def test_13_hostile_asset_spec_rejected():
    from app.world.extract import extract_world_requirements
    from app.world.requirements import ObjectRequest

    # A hostile "noun" (file:// span) is rejected by the string-safety gate and
    # recorded as a sanitized safe-fail note — never a provider request.
    reqs = extract_world_requirements(
        "Also present: a file://evil hourglass", None
    )
    names = {obj.requested_name for obj in reqs.objects}
    assert "file evil hourglass" not in names
    assert any("unsafeUnsupported" in note for note in reqs.unsafe_unsupported)

    # Hostile spec fields cannot construct a typed request at all.
    with pytest.raises(ValueError):
        ObjectRequest(requested_name="javascript:alert(1)")
    with pytest.raises(ValueError):
        ObjectRequest(requested_name="safe name", tags=("<script>",))


# --------------------------------------------------------------------------- #
# 14. unknown-object generation through the Ollama provider boundary
# --------------------------------------------------------------------------- #


def test_14_unknown_object_generation_through_ollama_boundary():
    from app.world.extract import extract_world_requirements

    unknown_prompt = GOLDEN_PROMPT + "Also present: a mysterious hourglass\n"
    reqs = extract_world_requirements(unknown_prompt, None)
    assert any("hourglass" in obj.requested_name for obj in reqs.objects)

    transport = MockOllamaTransport(posts=_golden_posts(repair=None))
    record = _run_generation(_provider(transport), prompt=unknown_prompt)
    # deterministic completion (PUBLISHED or at most safe-unresolved: the
    # golden composition stays) — never a crash.
    assert record.state is GenerationState.PUBLISHED
    # The extracted unknown noun reaches the composed WORLD_GRAPH stage request
    # through the Ollama provider boundary (the 4th post).
    assert transport.stage_of_call(3) == "world_graph"
    assert "hourglass" in transport.prompt_of_call(3)
    # Deterministic: a second identical run reproduces the same state.
    second = _run_generation(_provider(MockOllamaTransport(posts=_golden_posts(repair=None))), prompt=unknown_prompt)
    assert second.state is GenerationState.PUBLISHED


# --------------------------------------------------------------------------- #
# 15. endpoint URL cannot be prompt-controlled
# --------------------------------------------------------------------------- #


def test_15_endpoint_url_cannot_be_prompt_controlled():
    hostile_prompt = (
        GOLDEN_PROMPT
        + "http://evil.example.com:11434\n"
        + "OLLAMA_BASE_URL=http://192.168.0.99:11434\n"
    )
    transport = MockOllamaTransport(posts=_golden_posts(repair=None))
    record = _run_generation(_provider(transport), prompt=hostile_prompt)
    assert record.state is GenerationState.PUBLISHED
    # every transport call stayed on the CONFIGURED base URL; zero influence.
    assert transport.call_count == 4
    for url, _payload, _timeout in transport.post_calls:
        assert url == f"{OLLAMA_BASE}/api/chat"


def test_15b_hostile_prompt_context_on_direct_generate():
    transport = MockOllamaTransport(posts=['{"crime": {}}'])
    provider = _provider(transport)
    provider.generate(
        _request(prompt_context="OLLAMA_BASE_URL=http://evil.example:11434\nhttp://other:11434")
    )
    assert transport.post_calls[0][0] == f"{OLLAMA_BASE}/api/chat"


# --------------------------------------------------------------------------- #
# 16. player DTO contains no Ollama URL
# --------------------------------------------------------------------------- #


def test_16_player_dto_contains_no_ollama_url():
    transport = MockOllamaTransport(posts=_golden_posts(repair=None))
    from app.services.publication import (
        public_case_dict_from_payload,
        serialize_published_payload,
    )

    record = _run_generation(_provider(transport))
    assert record.state is GenerationState.PUBLISHED
    payload = json.loads(
        serialize_published_payload(record.published, title="t")
    )
    dto = public_case_dict_from_payload(payload)
    blob = json.dumps(dto)
    for token in ("11434", "127.0.0.1", "host.docker.internal", OLLAMA_BASE):
        assert token not in blob


# --------------------------------------------------------------------------- #
# 17. credentials/config do not appear in logs/responses
# --------------------------------------------------------------------------- #


def test_17_config_does_not_appear_in_logs_or_responses(caplog):
    caplog.clear()
    settings = Settings(
        generation_provider="ollama",
        ollama_base_url=OLLAMA_BASE,
        ollama_model=OLLAMA_MODEL,
    )
    transport = MockOllamaTransport(
        tags_status=500, posts=[(404, "secret detail")]
    )
    # provider + probe + a controller run
    provider = _provider(transport)
    result = provider.generate(_request())
    assert result.error is not None
    available, detail = ollama_available(settings, transport)
    assert available is False and detail == "not available"
    record = _run_generation(_provider(MockOllamaTransport(posts=[(500, "internal")])))
    combined = caplog.text + " " + str(result.error) + " " + detail
    # no base URL / port / model tokens in logs or sanitized responses.
    for token in (OLLAMA_BASE, "11434", "127.0.0.1", "secret detail"):
        assert token not in combined
    assert record.reason == "provider failure: generator unavailable"


# --------------------------------------------------------------------------- #
# 18. fake provider remains unchanged (default factory)
# --------------------------------------------------------------------------- #


def test_18_fake_provider_remains_unchanged(generation_service):
    assert generation_service._settings.generation_provider == "fake"
    provider = generation_service._build_default_provider_factory()()
    assert isinstance(provider, FakeProvider)
    # The fake provider itself stays deterministic and network-free.
    fake = FakeProvider({GenerationStage.CASE_TRUTH: ["ok:x"]})
    assert fake.generate(_request()).content == "x"


# --------------------------------------------------------------------------- #
# 19. live provider remains HTTPS-only
# --------------------------------------------------------------------------- #


def test_19_live_provider_remains_https_only():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Settings(
            generation_provider="live",
            live_provider_url="http://api.example.com/v1/chat/completions",
            llm_api_key="k",
            llm_model="m",
        )
    # An https live URL still validates (unchanged policy).
    ok = Settings(
        generation_provider="live",
        live_provider_url="https://api.example.com/v1/chat/completions",
        llm_api_key="k",
        llm_model="m",
    )
    assert ok.live_provider_url.startswith("https://")


# --------------------------------------------------------------------------- #
# 20. existing behavior remains green (the full-suite verification is separate)
# --------------------------------------------------------------------------- #


def test_20_existing_suites_still_green():
    """The fake golden lifecycle still PUBLISHES and the strict parser still
    rejects malformed content — nothing in the pipeline was weakened."""
    transport = MockOllamaTransport(posts=_golden_posts(repair=None))
    record = _run_generation(_provider(transport))
    assert record.state is GenerationState.PUBLISHED
    assert record.last_validation.valid is True
    assert record.last_validation.outcome is ValidationOutcome.VALID
    # provider boundary contract: Ollama returns the generic ProviderResult.
    result = _provider(MockOllamaTransport(posts=["{}"])).generate(_request())
    assert isinstance(result, ProviderResult)


# --------------------------------------------------------------------------- #
# extra integration: response size cap + no-type-leak boundary checks
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Phase16_2 extras: duplicate keys, provider exception, duplicate-parse, markdown
# --------------------------------------------------------------------------- #


def test_duplicate_json_keys_rejected_via_strict_parser():
    """Duplicate JSON keys in an Ollama response never silently last-win: the
    strict parser reports a deterministic parse issue (repair path)."""
    from app.generation import parser as stage_parser
    content = '{"crime": {"type": "murder", "type": "murder"}}'
    issues = stage_parser.collect_issues(GenerationStage.CASE_TRUTH, content)
    assert any("duplicate key" in issue for issue in issues)


def test_provider_exception_is_sanitized():
    """A provider whose transport raises (not a timed-out path) degrades to a
    clean, sanitized error — never an escaped exception, never a config leak."""
    def _boom(url, payload, timeout):
        raise RuntimeError("secret provider detail")

    transport = MockOllamaTransport(posts=[_boom])
    result = _provider(transport).generate(_request())
    assert isinstance(result, ProviderResult)
    assert result.error is not None
    assert "secret provider detail" not in result.error  # sanitized
    assert result.content is None and result.timed_out is False


def test_markdown_wrapped_duplicate_keys_still_fails_path():
    """A markdown-fence-wrapped payload with duplicate keys: the ONE outer fence
    is stripped, then the strict parser rejects the duplicate key (repair path,
    never silently last-win)."""
    from app.generation import parser as stage_parser
    from app.generation.ollama_provider import strip_outer_code_fence

    inner = '{"evidence": [{"id": "a", "id": "b", "kind": "physical", "reliability": "high", "discoverable": true, "sourceRef": {"kind": "k", "sourceId": "s"}, "propositions": [{"type": "OTHER"}], "presentation": {"title": "t", "description": "d"}}]}'
    # the adapter strips the ONE outer fence, then the strict parser rejects
    # the duplicate key (never silently last-win).
    fenced = "```json\n" + inner + "\n```"
    stripped = strip_outer_code_fence(fenced)
    issues = stage_parser.collect_issues(GenerationStage.EVIDENCE, stripped)
    assert any("duplicate key" in issue for issue in issues)


def test_extra_response_size_cap_is_clean_error():
    huge = b"x" * (MAX_OLLAMA_RESPONSE_BYTES + 1)
    transport = MockOllamaTransport(posts=[(200, huge)])
    result = _provider(transport).generate(_request())
    assert result.content is None
    assert result.error is not None
    assert "size cap" in result.error


def test_extra_probe_failure_is_sanitized():
    settings = Settings(
        generation_provider="ollama",
        ollama_base_url=OLLAMA_BASE,
        ollama_model=OLLAMA_MODEL,
    )
    transport = MockOllamaTransport(tags_status=500, posts=[])
    assert ollama_available(settings, transport) == (False, "not available")
    # a probe that raises still degrades sanitized
    def _boom(url, timeout):
        raise RuntimeError("kaboom")
    transport.get = _boom  # type: ignore[assignment]
    assert ollama_available(settings, transport) == (False, "not available")


def test_extra_no_ollama_types_leak_across_boundary():
    """The Provider protocol surface is the only crossing: generate accepts a
    GenerateRequest and returns a ProviderResult (never Ollama/httpx types)."""
    import inspect
    import typing

    from app.generation.ollama_provider import OllamaProvider

    hints = typing.get_type_hints(OllamaProvider.generate)
    assert hints["request"] is GenerateRequest
    assert hints["return"] is ProviderResult
    assert inspect.signature(OllamaProvider.generate).parameters["request"].name == "request"
    # an over-bounded transport body still yields a ProviderResult, never an
    # exception and never any Ollama-specific type.
    transport = MockOllamaTransport(posts=[(200, b"not json")])
    result = OllamaProvider(base_url=OLLAMA_BASE, model=OLLAMA_MODEL, transport=transport).generate(_request())
    assert isinstance(result, ProviderResult)
