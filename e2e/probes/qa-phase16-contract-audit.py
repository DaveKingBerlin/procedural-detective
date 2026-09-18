"""QA-owned Phase 16 CONTRACT AUDIT (independent; e2e/probes series).

Proves the Phase 16 gate tasks 2a/2b/2c + the in-process Phase16-N security
focus against the REAL repo modules:

  2a. CONFIG — ``GENERATION_PROVIDER=ollama`` accepted; ``fake`` default
      unchanged; OLLAMA bounds enforced (model charset, timeout 5..300,
      temperature 0..2, num_ctx 512..32768); the URL matrix (loopback /
      private / LAN / host.docker.internal accepted; public hosts, javascript:,
      file:, data:, embedded credentials, malformed URLs rejected); the LIVE
      provider stays HTTPS-only.

  2b. PROVIDER — a QA-owned mocked Ollama transport records every call; the
      adapter maps ``GenerateRequest`` correctly (model + stage + bounded
      prompt); valid response -> ``ProviderResult(content)``; malformed /
      markdown-wrapped output is stripped ONCE and then drives the REAL
      ``GenerationController`` through the NORMAL failure path (repair
      classification; the raw malformed output is never published; without a
      repair budget the attempt FAILS and nothing is published); HTTP 5xx/4xx
      -> sanitized error; timeout -> ``timed_out=True``; model-unavailable ->
      ``ollama_available`` False + provider-unavailable FAILED when selected;
      the 8-call budget ends in "model call budget exhausted"; admission-denied
      -> ZERO transport calls; repair goes through the SAME provider instance;
      a stale completion after FAILED is discarded (no payload); unsafe
      generated content (<script>) lands in the safety bucket and is never
      published; a hostile AssetSpec request is rejected at the typed-object
      gate; an unknown-object prompt reaches the Ollama boundary (WORLD_GRAPH
      stage content) and completes deterministically; the endpoint URL cannot
      be prompt-controlled; no URL / config / prompt / key appears in public
      DTOs (incl. the capability response) or in captured logs.

  2c. CAPABILITIES — GET /api/v1/generation-capabilities on a REAL migrated
      scratch DB + TestClient: provider=fake shape (demo available, local
      unavailable, live NOT revealed unless configured), provider=ollama with
      a mocked-available probe (local available + model label), provider=ollama
      with a failing probe (local false, NO reason detail), public/no-auth,
      global readiness unchanged and the unselected provider NEVER probed,
      and no base URL anywhere in the response.

Run:  python e2e/probes/qa-phase16-contract-audit.py [out.json]
Output JSON: argv[1] or e2e/artifacts/qa-phase16-contract-audit.json
Exit: 0 = all PASS, 1 = any FAIL.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR / "tests"))

results: list[dict[str, object]] = []


def record(name: str, ok: bool, detail: object) -> None:
    results.append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'}: {name} :: {json.dumps(detail, ensure_ascii=False)[:560]}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


def _fresh_migrated_db() -> Path:
    scratch = Path(tempfile.mkdtemp(prefix="qa_p16_audit_"))
    db = scratch / "audit.db"
    env = os.environ.copy()
    env["DATABASE_URL"] = f"sqlite:///{db.as_posix()}"
    env.pop("ENV_FILE", None)
    env.pop("STATIC_DIR", None)
    env.pop("GENERATION_PROVIDER", None)
    proc = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
        cwd=str(BACKEND_DIR), env=env, capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, proc.stderr
    con = sqlite3.connect(db)
    versions = [r[0] for r in con.execute("select version_num from alembic_version")]
    con.close()
    assert versions == ["0004"], versions
    return db


GOLDEN_PROMPT = (
    "Victim: sarah_miller\n"
    "Murderer: thomas_reed\n"
    "Motive: cover_up_embezzlement\n"
    "Weapon: kitchen_knife\n"
    "Time: 2026-09-11T22:17:00+02:00\n"
    "Witness: emily_reed\n"
)

OLLAMA_BASE = "http://127.0.0.1:11434"
OLLAMA_MODEL = "llama3.2:3b"


# --------------------------------------------------------------------------- #
# QA-owned mock transport (never touches the network)
# --------------------------------------------------------------------------- #
class MockOllamaTransport:
    """Injects canned /api/chat responses and records every call.

    ``posts`` is a FIFO: a ``str`` becomes a 200 envelope with that content; a
    ``(status, body)`` tuple is an explicit status; a callable receives
    ``(url, payload, timeout)`` and returns ``(status, bytes)``. ``get``
    answers /api/tags.
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

    def post_json(self, url: str, payload: dict, timeout: float) -> tuple[int, bytes]:
        self.post_calls.append((url, dict(payload), float(timeout)))
        if not self.posts:
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


def _golden_posts(*, malformed_stage=None, repair="__GOLDEN_FULL_DRAFT__"):
    """FIFO stage contents for a full golden run (one stage may be malformed)."""
    from fixtures.golden_generation import GOLDEN_FULL_DRAFT, GOLDEN_STAGE_PAYLOADS
    from app.generation.provider import GenerationStage

    if repair == "__GOLDEN_FULL_DRAFT__":
        repair = GOLDEN_FULL_DRAFT
    posts = [
        GOLDEN_STAGE_PAYLOADS[GenerationStage.CASE_TRUTH],
        GOLDEN_STAGE_PAYLOADS[GenerationStage.PUBLIC_WORLD],
        GOLDEN_STAGE_PAYLOADS[GenerationStage.EVIDENCE],
        GOLDEN_STAGE_PAYLOADS[GenerationStage.WORLD_GRAPH],
    ]
    if malformed_stage is not None:
        posts[3 if malformed_stage is GenerationStage.WORLD_GRAPH else 2] = "<not-json>"
    posts.append(repair)
    return posts


def _provider(transport, *, timeout_seconds: float = 5.0):
    from app.generation.ollama_provider import OllamaProvider

    return OllamaProvider(
        base_url=OLLAMA_BASE,
        model=OLLAMA_MODEL,
        timeout_seconds=timeout_seconds,
        temperature=0.2,
        num_ctx=4096,
        transport=transport,
    )


def _admission(clock, ids, **overrides):
    from app.generation.admission import AdmissionController

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
    from app.generation.controller import GenerationController

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


def _run_generation(provider, prompt=GOLDEN_PROMPT, **controller_overrides):
    from app.generation.clock import ManualClock
    from app.generation.ids import IdSource

    clock = ManualClock()
    ids = IdSource()
    admission = _admission(clock, ids)
    session = admission.create_anonymous_quota_session()
    controller = _controller(provider, admission, clock, ids, **controller_overrides)
    handle = controller.start_generation(
        prompt, anonymous_quota_session_id=session.session_id
    )
    return controller, handle, clock, admission, controller.attempt(handle.attempt_id)


def _request(stage=None, prompt_context="ctx", locked=None, diagnostics=()):
    from app.generation.constraints import LockedConstraints
    from app.generation.provider import GenerateRequest, GenerationStage

    return GenerateRequest(
        attempt_id="att-qa",
        stage=stage or GenerationStage.CASE_TRUTH,
        prompt_context=prompt_context,
        locked=locked or LockedConstraints(),
        diagnostics=tuple(diagnostics),
    )


# --------------------------------------------------------------------------- #
# 2a. CONFIG
# --------------------------------------------------------------------------- #
def audit_config() -> None:
    section("2a. CONFIG — provider selection, OLLAMA bounds, URL matrix")
    from pydantic import ValidationError

    from app.core.config import Settings, is_allowed_ollama_host

    # provider selection
    default = Settings()
    record("generation_provider default stays fake",
           default.generation_provider == "fake",
           default.generation_provider)
    record("generation_provider=ollama accepted",
           Settings(generation_provider="ollama").generation_provider == "ollama",
           Settings(generation_provider="ollama").generation_provider)
    try:
        Settings(generation_provider="cloud")
        record("unknown provider rejected", False, "accepted 'cloud'")
    except ValidationError:
        record("unknown provider rejected", True, "ValidationError")
    except Exception as exc:  # pragma: no cover
        record("unknown provider rejected", False, repr(exc))

    # bounds
    bound_checks = [
        ("timeout min 5", Settings(ollama_timeout_seconds=5).ollama_timeout_seconds == 5),
        ("timeout max 300", Settings(ollama_timeout_seconds=300).ollama_timeout_seconds == 300),
        ("temp min 0", Settings(ollama_temperature=0.0).ollama_temperature == 0.0),
        ("temp max 2", Settings(ollama_temperature=2.0).ollama_temperature == 2.0),
        ("num_ctx min 512", Settings(ollama_num_ctx=512).ollama_num_ctx == 512),
        ("num_ctx max 32768", Settings(ollama_num_ctx=32768).ollama_num_ctx == 32768),
        ("model keeps safe name", Settings(ollama_model="my-model.latest:tag").ollama_model == "my-model.latest:tag"),
    ]
    for label, ok in bound_checks:
        record(f"OLLAMA bound {label}", ok, "")

    bad_bounds = [
        ("timeout 4.9", dict(ollama_timeout_seconds=4.9)),
        ("timeout 301", dict(ollama_timeout_seconds=301)),
        ("temp -0.01", dict(ollama_temperature=-0.01)),
        ("temp 2.01", dict(ollama_temperature=2.01)),
        ("num_ctx 511", dict(ollama_num_ctx=511)),
        ("num_ctx 32769", dict(ollama_num_ctx=32769)),
        ("model 81 chars", dict(ollama_model="a" * 81)),
        ("model bad charset", dict(ollama_model="bad model!")),
        ("model injection", dict(ollama_model="javascript:alert(1)")),
    ]
    for label, kwargs in bad_bounds:
        try:
            Settings(**kwargs)
            record(f"OLLAMA bound rejected {label}", False, "accepted")
        except ValidationError:
            record(f"OLLAMA bound rejected {label}", True, "ValidationError")
        except Exception as exc:  # pragma: no cover
            record(f"OLLAMA bound rejected {label}", False, repr(exc))

    # URL matrix
    accepted = (
        "http://127.0.0.1:11434",
        "http://localhost:11434",
        "http://host.docker.internal:11434",
        "http://10.0.0.5:11434",
        "http://192.168.1.5:11434",
        "https://127.0.0.1:11434",
        "http://[::1]:11434",
    )
    for url in accepted:
        try:
            s = Settings(ollama_base_url=url)
            record(f"URL accepted {url}", s.ollama_base_url == url, s.ollama_base_url)
        except Exception as exc:  # pragma: no cover
            record(f"URL accepted {url}", False, repr(exc))

    rejected = (
        "http://86.86.86.86:11434",      # public IPv4
        "https://public.example.com",     # public hostname (even https)
        "javascript:alert(1)",
        "file:///etc/passwd",
        "data:text/plain,x",
        "gopher://127.0.0.1:70",
        "http://user:pass@127.0.0.1:11434",  # embedded credentials
        "http://127.0.0.1:11434?x=1",        # query string
        "http://127.0.0.1:11434#frag",       # fragment
        "http://127.0.0.1:11434/api",        # path
        "not a url",                         # malformed
        "http://",                           # no netloc
        "http://8.8.8.8:11434",              # public literal
    )
    for url in rejected:
        try:
            Settings(ollama_base_url=url)
            record(f"URL rejected {url}", False, "accepted")
        except ValidationError:
            record(f"URL rejected {url}", True, "ValidationError")
        except Exception as exc:  # pragma: no cover
            record(f"URL rejected {url}", False, repr(exc))

    # empty-string URL -> None (documented default)
    record("URL empty string -> None",
           Settings(ollama_base_url="").ollama_base_url is None,
           Settings(ollama_base_url="").ollama_base_url)

    # host helper matrix
    record("is_allowed_ollama_host localhost",
           is_allowed_ollama_host("localhost") and is_allowed_ollama_host("LOCALHOST"), "")
    record("is_allowed_ollama_host host.docker.internal",
           is_allowed_ollama_host("host.docker.internal"), "")
    record("is_allowed_ollama_host loopback/private",
           all(is_allowed_ollama_host(h) for h in ("127.0.0.1", "10.1.2.3", "172.20.1.1", "192.168.0.1", "::1")), "")
    record("is_allowed_ollama_host rejects public/.lan",
           not any(is_allowed_ollama_host(h) for h in ("8.8.8.8", "example.com", "peer.lan", "192.0.2.1", "169.254.1.1", "fe80::1")), "")

    # LIVE stays HTTPS-only
    try:
        Settings(generation_provider="live", live_provider_url="http://api.example.com/v1/chat/completions", llm_api_key="k", llm_model="m")
        record("LIVE rejects http://", False, "accepted")
    except ValidationError:
        record("LIVE rejects http://", True, "ValidationError")
    except Exception as exc:  # pragma: no cover
        record("LIVE rejects http://", False, repr(exc))
    ok_live = Settings(
        generation_provider="live",
        live_provider_url="https://api.example.com/v1/chat/completions",
        llm_api_key="k", llm_model="m",
    )
    record("LIVE https accepted unchanged", ok_live.live_provider_url.startswith("https://"), ok_live.live_provider_url)


# --------------------------------------------------------------------------- #
# 2b. PROVIDER
# --------------------------------------------------------------------------- #
def audit_provider() -> None:
    section("2b. PROVIDER — mocked transport, real GenerationController")
    from app.core.config import Settings
    from app.generation.constraints import LockedConstraints
    from app.generation.provider import GenerationStage, ProviderResult

    # (1) generate maps stage/model/messages correctly
    transport = MockOllamaTransport(posts=['{"crime": {"type": "murder"}}'])
    provider = _provider(transport)
    result = provider.generate(
        _request(
            stage=GenerationStage.PUBLIC_WORLD,
            prompt_context="sanitized material",
            locked=LockedConstraints(victim="sarah_miller"),
            diagnostics=("one", "two"),
        )
    )
    url, payload, timeout = transport.post_calls[0]
    prompt = payload["messages"][0]["content"]
    record("maps stage/model/messages",
           (url == f"{OLLAMA_BASE}/api/chat"
            and payload["model"] == OLLAMA_MODEL
            and payload["stream"] is False
            and payload["options"]["temperature"] == 0.2
            and payload["options"]["num_ctx"] == 4096
            and "'public_world'" in prompt
            and "sanitized material" in prompt
            and "sarah_miller" in prompt
            and "- one" in prompt and "- two" in prompt
            and timeout == 5.0),
           {"url": url, "model": payload["model"], "timeout": timeout,
            "stageInPrompt": "'public_world'" in prompt})

    # (2) valid response -> ProviderResult(content)
    transport2 = MockOllamaTransport(posts=['{"crime": {}}'])
    r2 = _provider(transport2).generate(_request())
    record("valid response -> ProviderResult(content)",
           r2 == ProviderResult(content='{"crime": {}}') and r2.error is None and not r2.timed_out,
           {"content": r2.content, "error": r2.error, "timed_out": r2.timed_out})

    # (3) malformed JSON -> NORMAL failure path: repair classification; raw
    # malformed never published; without repair budget -> FAILED, NOT published.
    transport3 = MockOllamaTransport(posts=_golden_posts(malformed_stage=GenerationStage.EVIDENCE))
    _c, _h, _clk, _adm, record3 = _run_generation(_provider(transport3))
    payload_blob = repr(record3.published.draft) if record3.published else ""
    record("malformed JSON -> repair classification",
           record3.state.value == "PUBLISHED"
           and record3.budget.repair_passes == 1
           and transport3.stage_of_call(4) == "repair"
           and "<not-json>" not in payload_blob,
           {"state": record3.state.value, "repairs": record3.budget.repair_passes,
            "calls": transport3.call_count})

    transport3b = MockOllamaTransport(posts=_golden_posts(malformed_stage=GenerationStage.EVIDENCE, repair=None))
    _c, _h, _clk, _adm, record3b = _run_generation(_provider(transport3b), max_repair_passes=0)
    record("malformed JSON without repair budget -> FAILED, NOT published",
           record3b.state.value == "FAILED" and "repair budget" in record3b.reason and record3b.published is None,
           {"state": record3b.state.value, "reason": record3b.reason})

    # (3c) markdown-wrapped VALID JSON -> stripped once -> PUBLISHED without repair
    from fixtures.golden_generation import GOLDEN_STAGE_PAYLOADS
    fenced_valid = "```json\n" + GOLDEN_STAGE_PAYLOADS[GenerationStage.EVIDENCE] + "\n```"
    transport3c = MockOllamaTransport(
        posts=[
            GOLDEN_STAGE_PAYLOADS[GenerationStage.CASE_TRUTH],
            GOLDEN_STAGE_PAYLOADS[GenerationStage.PUBLIC_WORLD],
            fenced_valid,
            GOLDEN_STAGE_PAYLOADS[GenerationStage.WORLD_GRAPH],
        ]
    )
    _c, _h, _clk, _adm, record3c = _run_generation(_provider(transport3c))
    record("markdown-wrapped VALID JSON strips once -> PUBLISHED, zero repairs",
           record3c.state.value == "PUBLISHED" and record3c.budget.repair_passes == 0 and transport3c.call_count == 4,
           {"state": record3c.state.value, "repairs": record3c.budget.repair_passes, "calls": transport3c.call_count})

    # (3d) markdown-wrapped MALFORMED -> repairs through the normal path
    fenced_malformed = "```json\n<not-json>\n```"
    transport3d = MockOllamaTransport(posts=_golden_posts(malformed_stage=GenerationStage.EVIDENCE, repair=fenced_malformed + "\nrepair still malformed"))
    from fixtures.golden_generation import GOLDEN_FULL_DRAFT
    transport3d = MockOllamaTransport(
        posts=[
            GOLDEN_STAGE_PAYLOADS[GenerationStage.CASE_TRUTH],
            GOLDEN_STAGE_PAYLOADS[GenerationStage.PUBLIC_WORLD],
            fenced_malformed,
            GOLDEN_STAGE_PAYLOADS[GenerationStage.WORLD_GRAPH],
            GOLDEN_FULL_DRAFT,
        ]
    )
    _c, _h, _clk, _adm, record3d = _run_generation(_provider(transport3d))
    record("markdown-wrapped MALFORMED -> stripped then repair -> PUBLISHED",
           record3d.state.value == "PUBLISHED" and record3d.budget.repair_passes == 1,
           {"state": record3d.state.value, "repairs": record3d.budget.repair_passes})

    # (4) HTTP failure handled
    transport4 = MockOllamaTransport(posts=[(404, {"error": "model not found"})])
    r4 = _provider(transport4).generate(_request())
    record("HTTP 404 -> sanitized error", r4.content is None and "404" in (r4.error or "") and not r4.timed_out,
           {"error": r4.error})
    transport4b = MockOllamaTransport(posts=[(500, "internal")])
    r4b = _provider(transport4b).generate(_request())
    record("HTTP 500 -> sanitized error", r4b.content is None and r4b.error is not None and not r4b.timed_out,
           {"error": r4b.error})
    transport4c = MockOllamaTransport(posts=[(404, {"error": "model not found"})])
    _c, _h, _clk, _adm, record4c = _run_generation(_provider(transport4c))
    record("HTTP 404 on controller -> provider-unavailable FAILED",
           record4c.state.value == "FAILED" and record4c.reason == "provider failure: generator unavailable" and record4c.published is None,
           {"state": record4c.state.value, "reason": record4c.reason, "calls": transport4c.call_count})

    # (5) timeout handled
    def _raise_timeout(url, payload, timeout):
        raise TimeoutError("ollama request timed out")
    transport5 = MockOllamaTransport(posts=[_raise_timeout])
    r5 = _provider(transport5).generate(_request())
    record("timeout -> timed_out True", r5.timed_out is True and r5.content is None and r5.error is None,
           {"timed_out": r5.timed_out, "content": r5.content, "error": r5.error})
    transport5b = MockOllamaTransport(posts=[_raise_timeout])
    _c, _h, _clk, _adm, record5b = _run_generation(_provider(transport5b))
    record("timeout on controller -> provider-unavailable FAILED, 1 call",
           record5b.state.value == "FAILED" and record5b.reason == "provider failure: generator unavailable" and transport5b.call_count == 1,
           {"state": record5b.state.value, "calls": transport5b.call_count})

    # (6) model-unavailable
    from app.generation.ollama_provider import ollama_available
    settings = Settings(generation_provider="ollama", ollama_base_url=OLLAMA_BASE, ollama_model=OLLAMA_MODEL, ollama_timeout_seconds=5)
    transport6 = MockOllamaTransport(tags_status=200, tags_models=["other-model:latest"], posts=[(404, {"error": "model not found"})])
    available, detail = ollama_available(settings, transport6)
    record("model-unavailable -> ollama_available False + sanitized detail",
           available is False and detail == "not available",
           {"available": available, "detail": detail})
    _c, _h, _clk, _adm, record6b = _run_generation(_provider(transport6))
    record("model-unavailable selected -> provider-unavailable FAILED",
           record6b.state.value == "FAILED" and record6b.reason == "provider failure: generator unavailable",
           {"state": record6b.state.value, "reason": record6b.reason})

    # (7) budget: 8 calls -> "model call budget exhausted"
    transport7 = MockOllamaTransport(
        posts=[
            GOLDEN_STAGE_PAYLOADS[GenerationStage.CASE_TRUTH],
            GOLDEN_STAGE_PAYLOADS[GenerationStage.PUBLIC_WORLD],
            "<not-json>",
            GOLDEN_STAGE_PAYLOADS[GenerationStage.WORLD_GRAPH],
            "<not-json>", "<not-json>", "<not-json>", "<not-json>",
        ]
    )
    _c, _h, _clk, _adm, record7 = _run_generation(
        _provider(transport7), max_llm_calls_per_generation=8, max_repair_passes=100, max_full_regenerations=100
    )
    record("budget: 8 calls -> model call budget exhausted",
           record7.state.value == "FAILED" and "model call budget" in record7.reason
           and transport7.call_count == 8 and record7.budget.calls == 8 and record7.published is None,
           {"state": record7.state.value, "reason": record7.reason, "calls": transport7.call_count})

    # (8) admission happens BEFORE the Ollama call
    from app.generation.admission import AdmissionDenied
    from app.generation.clock import ManualClock
    from app.generation.ids import IdSource
    clock8 = ManualClock()
    ids8 = IdSource()
    admission8 = _admission(clock8, ids8, max_generations_per_session_per_window=1)
    session8 = admission8.create_anonymous_quota_session()
    transport8 = MockOllamaTransport(posts=_golden_posts(repair=None))
    controller8 = _controller(_provider(transport8), admission8, clock8, ids8)
    first = controller8.start_generation(GOLDEN_PROMPT, anonymous_quota_session_id=session8.session_id)
    controller8.attempt(first.attempt_id)
    calls_after_first = transport8.call_count
    denied = False
    try:
        controller8.start_generation(GOLDEN_PROMPT, anonymous_quota_session_id=session8.session_id)
    except AdmissionDenied:
        denied = True
    record("admission-denied -> ZERO additional transport calls",
           denied and transport8.call_count == calls_after_first and calls_after_first == 4,
           {"denied": denied, "calls_after": transport8.call_count, "after_first": calls_after_first})

    # (9) repair goes through the SAME provider instance
    transport9 = MockOllamaTransport(posts=_golden_posts(malformed_stage=GenerationStage.EVIDENCE))
    provider9 = _provider(transport9)
    _c, _h, _clk, _adm, record9 = _run_generation(provider9)
    stages = [transport9.stage_of_call(i) for i in range(transport9.call_count)]
    record("repair stage -> SAME provider instance",
           record9.state.value == "PUBLISHED"
           and stages == ["case_truth", "public_world", "evidence", "world_graph", "repair"],
           {"state": record9.state.value, "stages": stages})

    # (10) stale completion after FAILED -> discarded, no payload
    transport10 = MockOllamaTransport(
        posts=[
            GOLDEN_STAGE_PAYLOADS[GenerationStage.CASE_TRUTH],
            GOLDEN_STAGE_PAYLOADS[GenerationStage.PUBLIC_WORLD],
            "<not-json>",
            GOLDEN_STAGE_PAYLOADS[GenerationStage.WORLD_GRAPH],
            "<not-json>", "<not-json>",
        ]
    )
    provider10 = _provider(transport10)
    clock10 = ManualClock()
    ids10 = IdSource()
    admission10 = _admission(clock10, ids10)
    session10 = admission10.create_anonymous_quota_session()
    controller10 = _controller(provider10, admission10, clock10, ids10)
    handle10 = controller10.start_generation(GOLDEN_PROMPT, anonymous_quota_session_id=session10.session_id)
    record10 = controller10.attempt(handle10.attempt_id)
    calls_before = transport10.call_count
    from fixtures.golden_generation import GOLDEN_FULL_DRAFT as GFD
    controller10.on_completion("stale-ollama-pending", ProviderResult(content=GFD))
    record10 = controller10.attempt(handle10.attempt_id)
    publish10 = controller10.publish(handle10.attempt_id)
    record("stale after FAILED -> discarded (no payload, no publish)",
           record10.state.value == "FAILED" and record10.published is None
           and transport10.call_count == calls_before and publish10.success is False,
           {"state": record10.state.value, "calls_delta": transport10.call_count - calls_before, "publish": publish10.success})

    # (11) unsafe generated content -> safety bucket -> never published
    def _unsafe_evidence() -> str:
        doc = json.loads(GOLDEN_STAGE_PAYLOADS[GenerationStage.EVIDENCE])
        doc["evidence"][0]["presentation"]["description"] = (
            '<script>alert(1)</script> javascript:evil data:text/plain,x'
        )
        return json.dumps(doc, sort_keys=True, ensure_ascii=False)
    transport11 = MockOllamaTransport(
        posts=[
            GOLDEN_STAGE_PAYLOADS[GenerationStage.CASE_TRUTH],
            GOLDEN_STAGE_PAYLOADS[GenerationStage.PUBLIC_WORLD],
            _unsafe_evidence(),
            GOLDEN_STAGE_PAYLOADS[GenerationStage.WORLD_GRAPH],
            GFD,
        ]
    )
    _c, _h, _clk, _adm, record11 = _run_generation(_provider(transport11))
    blob11 = repr(record11.published.draft) if record11.published else ""
    record("unsafe content (<script>) -> safety bucket -> repaired, never published raw",
           record11.state.value == "PUBLISHED" and record11.budget.repair_passes == 1
           and "<script>" not in blob11 and "javascript:" not in blob11 and "data:text" not in blob11,
           {"state": record11.state.value, "repairs": record11.budget.repair_passes})

    # (12) hostile AssetSpec rejection path
    from app.world.extract import extract_world_requirements
    from app.world.requirements import ObjectRequest
    reqs = extract_world_requirements("Also present: a file://evil hourglass", None)
    names = {o.requested_name for o in reqs.objects}
    hostile_rejected = all("file evil hourglass" not in n for n in names)
    unsafe_notes = any("unsafeUnsupported" in note for note in reqs.unsafe_unsupported)
    record("hostile AssetSpec noun -> rejected at extractor (safe-fail note)",
           hostile_rejected and unsafe_notes,
           {"names": list(names)}, )
    o_rejected = True
    for bad in ("javascript:alert(1)", "http://evil/thing"):
        try:
            ObjectRequest(requested_name=bad)
            o_rejected = False
        except ValueError:
            pass
    record("hostile ObjectRequest fields -> ValueError (never a typed request)", o_rejected, "")

    # (13) unknown-object generation through the Ollama provider boundary
    unknown_prompt = GOLDEN_PROMPT + "Also present: a mysterious hourglass\n"
    transport13 = MockOllamaTransport(posts=_golden_posts(repair=None))
    _c, _h, _clk, _adm, record13 = _run_generation(_provider(transport13), prompt=unknown_prompt)
    record("unknown-object -> Ollama boundary (WORLD_GRAPH content) + deterministic completion",
           record13.state.value == "PUBLISHED"
           and transport13.stage_of_call(3) == "world_graph"
           and "hourglass" in transport13.prompt_of_call(3),
           {"state": record13.state.value, "worldGraphCarriesNoun": "hourglass" in transport13.prompt_of_call(3)})

    # (14) endpoint URL cannot be prompt-controlled
    hostile_prompt = GOLDEN_PROMPT + "http://evil.example.com:11434\nOLLAMA_BASE_URL=http://192.168.0.99:11434\n"
    transport14 = MockOllamaTransport(posts=_golden_posts(repair=None))
    _c, _h, _clk, _adm, record14 = _run_generation(_provider(transport14), prompt=hostile_prompt)
    all_urls_ok = all(u == f"{OLLAMA_BASE}/api/chat" for u, _p, _t in transport14.post_calls)
    transport14b = MockOllamaTransport(posts=['{"crime": {}}'])
    _provider(transport14b).generate(
        _request(prompt_context="OLLAMA_BASE_URL=http://evil.example:11434\nhttp://other:11434")
    )
    record("endpoint URL not prompt-controllable",
           record14.state.value == "PUBLISHED" and all_urls_ok
           and transport14b.post_calls[0][0] == f"{OLLAMA_BASE}/api/chat",
           {"calls": transport14.call_count, "allOnConfiguredBase": all_urls_ok})

    # (15) no URL in public DTOs
    from app.services.publication import public_case_dict_from_payload, serialize_published_payload
    transport15 = MockOllamaTransport(posts=_golden_posts(repair=None))
    _c, _h, _clk, _adm, record15 = _run_generation(_provider(transport15))
    payload15 = json.loads(serialize_published_payload(record15.published, title="t"))
    dto15 = public_case_dict_from_payload(payload15)
    blob15 = json.dumps(dto15)
    record("player DTO contains no Ollama URL",
           all(tok not in blob15 for tok in ("11434", "127.0.0.1", "host.docker.internal", OLLAMA_BASE)),
           "")

    # (16) no config/prompt/key in logs (capture during a mocked call)
    log_records: list[str] = []
    handler = logging.Handler()
    handler.emit = lambda record_: log_records.append(record_.getMessage())
    root_logger = logging.getLogger()
    root_logger.addHandler(handler)
    try:
        transport16 = MockOllamaTransport(tags_status=500, posts=[(404, "secret detail")])
        provider16 = _provider(transport16)
        r16 = _provider(MockOllamaTransport(tags_status=500, posts=[(404, "secret detail")])).generate(_request())
        ollama_available(settings, MockOllamaTransport(tags_status=500, posts=[]))
        _c, _h, _clk, _adm, record16 = _run_generation(_provider(MockOllamaTransport(posts=[(500, "internal")])))
    finally:
        root_logger.removeHandler(handler)
    combined = " ".join(log_records) + " " + str(r16.error or "") + " " + detail
    record("no config/prompt/key in captured logs",
           all(tok not in combined for tok in (OLLAMA_BASE, "11434", "127.0.0.1", "secret detail")),
           {"logLines": len(log_records)})

    # (17) budget/deadline: slow response (transport raises timeout) FAILs within deadline
    clock17 = ManualClock()
    ids17 = IdSource()
    admission17 = _admission(clock17, ids17)
    session17 = admission17.create_anonymous_quota_session()
    transport17 = MockOllamaTransport(posts=[_raise_timeout])
    controller17 = _controller(_provider(transport17), admission17, clock17, ids17, deadline_seconds=5)
    begin = clock17.now()
    handle17 = controller17.start_generation(GOLDEN_PROMPT, anonymous_quota_session_id=session17.session_id)
    record17 = controller17.attempt(handle17.attempt_id)
    elapsed = clock17.now() - begin
    record("slow/endless response -> bounded failure within deadline (1 call, FAILED)",
           record17.state.value == "FAILED" and transport17.call_count == 1 and elapsed <= 5,
           {"state": record17.state.value, "elapsed": elapsed, "calls": transport17.call_count})

    # (18) deadline enforcement with a slow/pending provider (ManualClock
    # advanced past the deadline before a deferred result is applied — §32.6 /
    # ADV-124: the deadline is authoritative for deferred completions too).
    class _PendingOnceProvider:
        """First call defers (pending); the controller pauses. The deadline is
        then advanced past by the ManualClock, and the deferred completion must
        be DISCARDED with 'generation deadline exceeded'."""
        def __init__(self):
            self.pending_id = "deadline-pending-01"
            self.calls = 0

        def generate(self, request):
            self.calls += 1
            return ProviderResult(pending=True, pending_id=self.pending_id)

    clock18 = ManualClock()
    ids18 = IdSource()
    admission18 = _admission(clock18, ids18)
    session18 = admission18.create_anonymous_quota_session()
    pending_provider = _PendingOnceProvider()
    controller18 = _controller(pending_provider, admission18, clock18, ids18, deadline_seconds=5)
    handle18 = controller18.start_generation(GOLDEN_PROMPT, anonymous_quota_session_id=session18.session_id)
    record18 = controller18.attempt(handle18.attempt_id)
    paused = record18.state.value  # GENERATING/pending — never terminal yet
    clock18.advance(30)  # well past the 5 s deadline
    controller18.on_completion(pending_provider.pending_id, ProviderResult(content=GFD))
    record18 = controller18.attempt(handle18.attempt_id)
    record("deadline exceeded -> FAILED 'generation deadline exceeded'",
           paused != "FAILED"
           and record18.state.value == "FAILED"
           and "deadline" in record18.reason
           and record18.published is None,
           {"paused_state": paused, "state": record18.state.value, "reason": record18.reason})

    # (19) model-name injection: a prompt cannot alter OLLAMA_MODEL
    inject_prompt = GOLDEN_PROMPT + "OLLAMA_MODEL=mistral:7b\nmodel=jailbreak\n"
    transport19 = MockOllamaTransport(posts=_golden_posts(repair=None))
    _c, _h, _clk, _adm, record19 = _run_generation(_provider(transport19), prompt=inject_prompt)
    models_sent = {p.get("model") for _u, p, _t in transport19.post_calls}
    record("model-name injection rejected (payload model stays configured)",
           record19.state.value == "PUBLISHED" and models_sent == {OLLAMA_MODEL},
           {"models": list(models_sent)})

    # (20) huge model response (512 KiB mock) -> clean truncation, no OOM/hang
    from app.generation.ollama_provider import MAX_OLLAMA_RESPONSE_BYTES
    huge = b"x" * (512 * 1024)  # a full 512 KiB generated body (> the 256 KiB cap)
    transport20 = MockOllamaTransport(posts=[(200, huge)])
    r20 = _provider(transport20).generate(_request())
    record("512 KiB mock response -> clean 'size cap' error, no exception",
           r20.content is None and r20.error is not None and "size cap" in r20.error and r20.timed_out is False,
           {"error": r20.error, "sent": len(huge), "cap": MAX_OLLAMA_RESPONSE_BYTES})

    # (21) truth-token scan over the whole mocked-path public surface
    transport21 = MockOllamaTransport(posts=_golden_posts(repair=None))
    _c, _h, _clk, _adm, record21 = _run_generation(_provider(transport21))
    payload21 = json.loads(serialize_published_payload(record21.published, title="t"))
    dto21 = public_case_dict_from_payload(payload21)
    blob21 = json.dumps(dto21)
    truth_tokens = ("murdererId", "victimId", "weaponId", "crimeTime", "truthfulness",
                    "solverProof", "solutionProof", "diagnostics", "prompt", "providerOutput", "seed", "locked")
    record("truth-token scan 0 over published DTO + all mocked-path prompts",
           all(tok not in blob21 for tok in truth_tokens)
           and all(
               all(tok not in transport21.prompt_of_call(i) for tok in ("solverProof", "relationships", "timeline"))
               for i in range(transport21.call_count)
           ),
           "")


# --------------------------------------------------------------------------- #
# 2c. CAPABILITIES
# --------------------------------------------------------------------------- #
def audit_capabilities() -> None:
    section("2c. CAPABILITIES — real migrated DB + TestClient")
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app

    db = _fresh_migrated_db()
    url = f"sqlite:///{db.as_posix()}"

    # provider=fake default shape
    app_fake = create_app(Settings(database_url=url))
    with TestClient(app_fake) as c:
        res = c.get("/api/v1/generation-capabilities")
        body = res.json()
        ids = [m["id"] for m in body["modes"]]
        record("GET /generation-capabilities 200 (no auth)",
               res.status_code == 200, res.status_code)
        record("provider=fake exact shape (demo available, local unavailable, live unrevealed)",
               set(body.keys()) == {"modes"}
               and ids == ["demo", "local"]
               and body["modes"][0] == {"id": "demo", "available": True}
               and body["modes"][1]["id"] == "local"
               and body["modes"][1]["available"] is False
               and body["modes"][1]["label"] == "Local AI"
               and not any(m["id"] == "live" for m in body["modes"]),
               body)
        # no URL/port anywhere
        text = res.text
        record("capability response contains no URL/port/key tokens",
               all(tok not in text for tok in ("11434", "127.0.0.1", "host.docker.internal", "http://", "llm_api_key", "sekret")),
               "")

    # provider=ollama with a mocked-available probe (no network)
    import app.api.v1.generation_capabilities as cap_module
    original_probe = cap_module.ollama_available
    try:
        cap_module.ollama_available = lambda settings: (True, "")
        app_ollama = create_app(Settings(database_url=url, generation_provider="ollama", ollama_base_url=OLLAMA_BASE, ollama_model=OLLAMA_MODEL))
        with TestClient(app_ollama) as c:
            res = c.get("/api/v1/generation-capabilities")
            body = res.json()
            local = body["modes"][1]
            record("provider=ollama + probe available -> local available + model label",
                   body["modes"][0] == {"id": "demo", "available": True}
                   and local["id"] == "local" and local["available"] is True
                   and local["label"] == "Local AI" and local["model"] == OLLAMA_MODEL,
                   body)
    finally:
        cap_module.ollama_available = original_probe

    try:
        cap_module.ollama_available = lambda settings: (False, "not available")
        app_ollama_down = create_app(Settings(database_url=url, generation_provider="ollama", ollama_base_url=OLLAMA_BASE, ollama_model=OLLAMA_MODEL))
        with TestClient(app_ollama_down) as c:
            res = c.get("/api/v1/generation-capabilities")
            body = res.json()
            local = body["modes"][1]
            record("provider=ollama + probe down -> local false, NO reason detail",
                   local["available"] is False and "not available" not in res.text and "11434" not in res.text,
                   body)
    finally:
        cap_module.ollama_available = original_probe

    # unselected provider NEVER probed
    called = {"count": 0}
    def _unexpected_probe(settings):
        called["count"] += 1
        return (True, "")
    try:
        cap_module.ollama_available = _unexpected_probe
        app_fake2 = create_app(Settings(database_url=url, generation_provider="fake", ollama_base_url=OLLAMA_BASE))
        with TestClient(app_fake2) as c:
            res = c.get("/api/v1/generation-capabilities")
    finally:
        cap_module.ollama_available = original_probe
    record("unselected provider NEVER probed (fake + configured ollama)",
           res.status_code == 200 and called["count"] == 0,
           {"probe_calls": called["count"]})

    # global readiness unchanged + never depends on ollama
    app_ready = create_app(Settings(database_url=url, generation_provider="ollama", ollama_base_url=OLLAMA_BASE))
    with TestClient(app_ready) as c:
        ready = c.get("/api/v1/readiness")
        record("global readiness unchanged (ollama selected, no server)",
               ready.status_code == 200 and ready.json().get("status") == "ready",
               ready.json())

    # live revealed only when fully configured
    app_live = create_app(Settings(
        database_url=url,
        generation_provider="live",
        live_provider_url="https://api.example.com/v1/chat/completions",
        llm_api_key="k", llm_model="m",
    ))
    with TestClient(app_live) as c:
        body = c.get("/api/v1/generation-capabilities").json()
        live = [m for m in body["modes"] if m["id"] == "live"]
        record("live revealed only when configured (available reflects selection)",
               live == [{"id": "live", "available": True}], body)


def main() -> int:
    audit_config()
    audit_provider()
    audit_capabilities()
    passed = sum(1 for r in results if r["ok"])
    total = len(results)
    print(f"\n=== PHASE 16 CONTRACT AUDIT: {passed}/{total} PASS ===")
    out_path = sys.argv[1] if len(sys.argv) > 1 else str(REPO_ROOT / "e2e" / "artifacts" / "qa-phase16-contract-audit.json")
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    Path(out_path).write_text(
        json.dumps({"suite": "qa-phase16-contract-audit", "passed": passed, "total": total, "results": results},
                   indent=2, sort_keys=True), encoding="utf-8")
    print(f"evidence: {out_path}")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())