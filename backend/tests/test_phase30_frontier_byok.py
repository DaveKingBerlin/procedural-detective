"""Phase 30 — Frontier BYOK with the trusted provider registry (backend scope).

Covers the binding contract §§27-31 (mandatory) plus the adversarial probes
§37:

  §30 registry safety: every committed endpoint is https / public-hostname /
      credential-free / fragment-free; the deterministic hermetic validator
      rejects the hostile matrix (no network ever).
  §27 sentinel secret-persistence (MANDATORY): generate with
      ``SECRET-PHASE30-MUST-NOT-PERSIST-123``; after generation the sentinel
      appears NOWHERE except the mocked outbound Authorization header (SQLite,
      case DTO, published-version DTO, playthrough DTO, generation metadata,
      captured logs, structured events, error responses, monitoring inputs).
  §28 billing-key isolation (MANDATORY): two concurrent cases (A=openai/
      KEY-A/MODEL-A, B=groq/KEY-B/MODEL-B) with genuinely overlapping first
      calls; A hits the OpenAI registry endpoint with only KEY-A/MODEL-A, B
      hits the Groq registry endpoint with only KEY-B/MODEL-B.
  §29 registry tampering: browser cannot submit an arbitrary URL, cannot
      override the registry endpoint, unknown/disabled provider IDs fail
      closed, provider ID cannot inject URL/scheme/header content.
  §31 provider behavior: disabled->unavailable; enabled->selectable without
      server credentials; safe catalog; missing/unknown provider/key/model ->
      400; valid->resolves to the registry endpoint with the user key + model;
      key absent from logs/DB/API responses; 401/403,404,429,timeout,5xx
      normalized to the closed FRONTIER_* vocabulary; no silent fallback to
      operator credentials/demo/ollama; provider fixed across every stage;
      concurrent isolation; solver/publication budgets unchanged; Demo and
      Ollama paths untouched.

Every external provider is a mock — CI never needs a real Frontier key or a
public network (the autouse loopback/network block enforces that).
"""

from __future__ import annotations

import itertools
import json
import logging
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.core.config import Settings  # noqa: E402
from app.generation.provider import GenerateRequest, GenerationStage  # noqa: E402
from app.generation.selection import (  # noqa: E402
    GenerationSelection,
    InvalidFrontierConfigError,
    MAX_FRONTIER_API_KEY_LENGTH,
    MAX_FRONTIER_PROVIDER_ID_LENGTH,
    validate_frontier_api_key,
    validate_frontier_model_string,
    validate_frontier_provider_id,
)
from app.main import create_app  # noqa: E402
from conftest import upgrade_db  # noqa: E402
from fixtures.golden_generation import GOLDEN_STAGE_PAYLOADS  # noqa: E402
from phase5_helpers import auth, create_session  # noqa: E402

# The deterministic fake-style pipeline stages (non-driver providers).
_STAGES = (
    GenerationStage.CASE_TRUTH,
    GenerationStage.PUBLIC_WORLD,
    GenerationStage.EVIDENCE,
    GenerationStage.WORLD_GRAPH,
)
_G = GOLDEN_STAGE_PAYLOADS
_GOLDEN_STAGE_STRINGS = [_G[stage] for stage in _STAGES]

# A deterministic prompt (structured key surface recognized by normalize_prompt
# and the golden fixtures). Proven to publish with exactly the 4 stage calls.
_PROMPT = (
    "Victim: sarah_miller\nMurderer: thomas_reed\nMotive: cover_up_embezzlement\n"
    "Weapon: kitchen_knife\nTime: 2026-09-11T22:17:00+02:00\nWitness: emily_reed\n"
)

# §27 / §37 A6-A9 — the mandatory persistence sentinel.
SENTINEL_KEY = "SECRET-PHASE30-MUST-NOT-PERSIST-123"

# The committed registry endpoints the mock asserts outbound calls target.
OPENAI_ENDPOINT = "https://api.openai.com/v1/chat/completions"
GROQ_ENDPOINT = "https://api.groq.com/openai/v1/chat/completions"


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _make_app(database_url, **settings_overrides):
    upgrade_db(database_url)
    kwargs = dict(
        cors_allowed_origins=["http://localhost:5173"],
        max_concurrent_generations=2,
        max_concurrent_generations_global=4,
        max_generations_per_session_per_window=8,
        max_generations_global_per_window=50,
        generation_limit_per_ip_per_hour=1000,  # tamper matrices issue many requests
        frontier_enabled=True,
    )
    kwargs.update(settings_overrides)
    return create_app(Settings(database_url=database_url, **kwargs))


def _dispose(application) -> None:
    application.state.engine.dispose()
    if application.state.store is not None:
        try:
            application.state.store.dispose()
        except Exception:  # noqa: BLE001 - teardown must never mask
            pass


def _post_case(client, token, prompt=_PROMPT, **extra):
    body = {"prompt": prompt}
    body.update(extra)
    return client.post("/api/v1/cases", json=body, headers=auth(token))


class _MockFrontierOutbound:
    """Thread-safe httpx.post stand-in for ``FrontierProvider``.

    Blocking semantics mirror the Phase 25 concurrency harness: the FIRST post
    for every distinct model blocks on ``release`` until the test trips it, so
    genuinely overlapping first-stage calls are guaranteed. Every post is
    recorded as ``(url, body, headers)`` for the wire asserts.
    """

    def __init__(self, golden_by_model: dict[str, list[str]] | None = None) -> None:
        from app.generation import frontier_provider as fp_mod

        self._fp_mod = fp_mod
        self._original_post = fp_mod.httpx.post
        self.posts: list[tuple[str, dict, dict]] = []
        self.first_for_model: set[str] = set()
        self.release = threading.Event()
        self._lock = threading.Lock()
        self._golden = {
            model: list(entries) for model, entries in (golden_by_model or {}).items()
        }

    class _FakeResponse:
        def __init__(self, body: bytes) -> None:
            self.status_code = 200
            self._body = body

        def iter_bytes(self, chunk_size: int):
            yield self._body

    def install(self) -> "_MockFrontierOutbound":
        self._fp_mod.httpx.post = self._post
        return self

    def restore(self) -> None:
        self._fp_mod.httpx.post = self._original_post

    def _post(self, url, json=None, headers=None, timeout=None):
        body = dict(json or {})
        headers = dict(headers or {})
        model = body.get("model", "")
        with self._lock:
            self.posts.append((url, body, headers))
            first = model not in self.first_for_model
            if first:
                self.first_for_model.add(model)
        if first:
            self.release.wait(timeout=120)
        queue = self._golden.get(model, [])
        content = queue.pop(0) if queue else "<not-json>"
        return self._FakeResponse(content.encode("utf-8"))


class _CaptureLog:
    """Deterministic structured-log capture (mirrors test_phase20's helper)."""

    def __init__(self, buffer) -> None:
        import io

        from app.core.observability import SERVICE_LOGGER_NAME, JsonEventFormatter

        self.handler = logging.StreamHandler(buffer)
        self.handler.setLevel(logging.DEBUG)
        self.handler.setFormatter(JsonEventFormatter())
        self.service_logger = logging.getLogger(SERVICE_LOGGER_NAME)
        self._previous_level = self.service_logger.level
        logging.getLogger().addHandler(self.handler)

    def __enter__(self) -> "_CaptureLog":
        self.service_logger.setLevel(logging.DEBUG)
        return self

    def __exit__(self, *_exc) -> None:
        logging.getLogger().removeHandler(self.handler)
        self.service_logger.setLevel(self._previous_level)


def _sqlite_path(database_url: str) -> Path:
    prefix = "sqlite:///"
    assert database_url.startswith(prefix)
    return Path(database_url[len(prefix):])


# --------------------------------------------------------------------------- #
# §30 — registry endpoint safety (deterministic, offline)
# --------------------------------------------------------------------------- #


def test_registry_every_committed_endpoint_is_safe():
    from app.generation.frontier_registry import (
        FRONTIER_PROVIDER_REGISTRY,
        validate_frontier_registry_endpoint,
    )

    assert len(FRONTIER_PROVIDER_REGISTRY) == 8
    for definition in FRONTIER_PROVIDER_REGISTRY:
        endpoint = definition.endpoint
        assert endpoint == validate_frontier_registry_endpoint(endpoint), definition.provider_id
        assert endpoint.startswith("https://"), definition.provider_id
        from urllib.parse import urlparse

        parsed = urlparse(endpoint)
        assert parsed.scheme == "https"
        assert parsed.netloc and parsed.hostname
        assert parsed.username is None and parsed.password is None
        assert not parsed.query and not parsed.fragment
        host = parsed.hostname.lower()
        assert host.count(".") >= 1  # a real public hostname, not a bare label
        assert host not in {"localhost", "host.docker.internal", "metadata"}
        assert not host.endswith(".local") and not host.endswith(".localhost")
        import ipaddress

        with pytest.raises(ValueError):
            ipaddress.ip_address(host)


def test_registry_definitions_are_frozen_unique_and_enabled():
    from app.generation.frontier_registry import (
        FRONTIER_PROVIDER_REGISTRY,
        FrontierProviderDefinition,
        frontier_catalog,
        frontier_enabled_providers,
        frontier_provider_definition,
    )

    ids = [d.provider_id for d in FRONTIER_PROVIDER_REGISTRY]
    assert len(ids) == len(set(ids))
    for definition in FRONTIER_PROVIDER_REGISTRY:
        assert isinstance(definition, FrontierProviderDefinition)
        with pytest.raises(Exception):  # noqa: BLE001 - frozen dataclass
            definition.endpoint = "https://evil.example/v1"  # type: ignore[misc]
        assert definition.protocol == "openai_chat_completions"
        assert definition.enabled is True
        assert definition.provider_id and definition.label
    assert tuple(frontier_enabled_providers()) == FRONTIER_PROVIDER_REGISTRY
    catalog = dict(frontier_catalog())
    assert set(catalog) == set(ids)
    for definition in FRONTIER_PROVIDER_REGISTRY:
        assert frontier_provider_definition(definition.provider_id) is definition


def test_registry_endpoint_validator_rejects_hostile_matrix():
    from app.generation.frontier_registry import (
        validate_frontier_registry_endpoint,
    )

    hostile = (
        "",                       # empty
        "http://api.openai.com/v1/chat/completions",  # not https
        "ftp://api.openai.com/v1",  # not https
        "file:///etc/passwd",
        "//api.openai.com/v1/chat/completions",  # not absolute
        "https:///v1/chat/completions",  # no netloc
        "https://",               # no netloc
        "https://api.openai.com/v1/chat/completions?key=sekret",  # query
        "https://api.openai.com/v1/chat/completions#frag",  # fragment
        "https://user:pass@api.openai.com/v1",  # embedded credentials
        "https://user@api.openai.com/v1",
        "https://localhost/v1",
        "https://api.local/v1",
        "https://api.openai.local/v1",
        "https://foo.localhost/v1",
        "https://host.docker.internal/v1",
        "https://metadata/v1",
        "https://metadata.google.internal/v1",
        "https://instance-data.ec2.internal/v1",
        "https://127.0.0.1/v1",   # loopback literal -> rejected (IP literal)
        "https://10.0.0.5/v1",    # private literal -> rejected (IP literal)
        "https://169.254.169.254/v1",  # cloud metadata literal
        "https://192.168.1.50/v1",
        "https://[::1]/v1",       # IPv6 loopback literal
        "https://8.8.8.8/v1",     # public literal -> still rejected (IP literal)
        # LOW A17 — wildcard-DNS / DNS-rebinding host suffixes must be denied.
        "https://127.0.0.1.nip.io/v1",  # loopback payload behind a rebinding name
        "https://10.0.0.1.nip.io/v1",   # private payload behind a rebinding name
        "https://2130706433.nip.io/v1",  # numeric/hex spelling behind a rebinding name
        "https://metadata.nip.io/v1",  # metadata label behind a rebinding name
        "https://127.0.0.1.sslip.io/v1",  # sslip.io rebinding family
        "https://1.2.3.4.xip.io/v1",  # xip.io rebinding family
        "https://169.254.169.254.nip.io/v1",  # metadata literal behind rebinding
        "https://nip.io/v1",  # bare wildcard-DNS root
        "https://sslip.io/v1",
        "https://xip.io/v1",
        "https://192.168.1.1.localtest.me/v1",  # loopback wildcard DNS
        "https://foo.lvh.me/v1",
        "https://foo.vcap.me/v1",
        "https://sub.dns.google/v1",
        "https://api.openai.com./v1",   # trailing-dot host
        "https://api.openai.com/v1\x00",  # NUL
        "https://api.openai.com\x01/v1",  # mid-URL control character
        "https://api.openai.com/v1\x7f",  # trailing DEL (non-printable)
        "not a url",
    )
    for value in hostile:
        with pytest.raises(ValueError):
            validate_frontier_registry_endpoint(value)
    # The validator is deterministic/hermetic: the committed endpoints round-trip.
    from app.generation.frontier_registry import FRONTIER_PROVIDER_REGISTRY

    for definition in FRONTIER_PROVIDER_REGISTRY:
        assert validate_frontier_registry_endpoint(definition.endpoint) == definition.endpoint


def test_registry_validator_denies_wildcard_dns_rebinding_suffixes():
    """LOW finding A17 — the module docstring promises wildcard-DNS /
    DNS-rebinding hostnames (``*.nip.io`` / ``*.sslip.io`` / ``*.xip.io`` ...)
    are denied, and they MUST be: a rebinding name can silently resolve to a
    loopback/private/metadata IP and defeats the literal-IP and numeric
    guards. Everything here is hermetic (suffix ``endswith`` on the lowercased
    hostname — no socket/DNS)."""
    from app.generation.frontier_registry import (
        FRONTIER_PROVIDER_REGISTRY,
        _WILDCARD_DNS_SUFFIXES,
        validate_frontier_registry_endpoint,
    )

    validate = validate_frontier_registry_endpoint
    # The deny set exists, is static, lowercase and dot-free (bounded list).
    assert isinstance(_WILDCARD_DNS_SUFFIXES, frozenset)
    assert _WILDCARD_DNS_SUFFIXES
    assert {"nip.io", "sslip.io", "xip.io"} <= _WILDCARD_DNS_SUFFIXES
    for suffix in _WILDCARD_DNS_SUFFIXES:
        assert suffix == suffix.lower()
        assert "." in suffix
    # The A17 hostile matrix: rebinding names with loopback/private/numeric/
    # metadata payloads (and the bare wildcard-DNS roots) are ALL rejected.
    for endpoint in (
        # nip.io with loopback/private/numeric/metadata payloads
        "https://127.0.0.1.nip.io/v1",
        "https://10.0.0.1.nip.io/v1",
        "https://2130706433.nip.io/v1",
        "https://metadata.nip.io/v1",
        "https://169.254.169.254.nip.io/v1",
        # sslip.io / xip.io families
        "https://127.0.0.1.sslip.io/v1",
        "https://1.2.3.4.xip.io/v1",
        # bare wildcard-DNS roots
        "https://nip.io/v1",
        "https://sslip.io/v1",
        "https://xip.io/v1",
        # loopback wildcard services + documented wildcard-DNS family
        "https://192.168.1.1.localtest.me/v1",
        "https://foo.lvh.me/v1",
        "https://foo.vcap.me/v1",
        "https://sub.dns.google/v1",
        "https://freeip.io/v1",
        "https://foo.iluxa.me/v1",
    ):
        with pytest.raises(ValueError, match="public internet hostname"):
            validate(endpoint), endpoint
    # Control: an ordinary public-hostname endpoint-style URL still passes.
    assert (
        validate("https://api.example.com/v1/chat/completions")
        == "https://api.example.com/v1/chat/completions"
    )
    assert (
        validate("https://api.example.org/v1")
        == "https://api.example.org/v1"
    )
    # Control: none of the 8 committed registry endpoints uses a forbidden
    # wildcard-DNS suffix — every committed entry still validates.
    for definition in FRONTIER_PROVIDER_REGISTRY:
        assert validate(definition.endpoint) == definition.endpoint
        from urllib.parse import urlparse

        host = urlparse(definition.endpoint).hostname.lower()
        assert host not in _WILDCARD_DNS_SUFFIXES
        assert not any(host.endswith("." + s) for s in _WILDCARD_DNS_SUFFIXES)


# --------------------------------------------------------------------------- #
# central user-input validators (§17/§18 shapes)
# --------------------------------------------------------------------------- #


def test_frontier_provider_id_validator_shapes():
    assert validate_frontier_provider_id(" openai ") == "openai"
    assert validate_frontier_provider_id("openrouter") == "openrouter"
    for hostile in (
        None,
        "",
        "   ",
        " https://evil.example ",
        "https://evil.example",
        "../../openai",
        "openai?url=https://evil.example",
        "openai\nAuthorization: x",
        "openai\rkey: k",
        "gpt\x00x",
        "openai\x7f",
        "x" * (MAX_FRONTIER_PROVIDER_ID_LENGTH + 1),
        "not-a-provider",
        "live",
    ):
        with pytest.raises(InvalidFrontierConfigError):
            validate_frontier_provider_id(hostile)


def test_frontier_api_key_validator_shapes():
    assert validate_frontier_api_key("  sk-abc-123  ") == "sk-abc-123"
    # No sk- prefix requirement.
    assert validate_frontier_api_key("opaque-transient-key") == "opaque-transient-key"
    for hostile in (
        None,
        "",
        "   ",
        "key\nAuthorization: x",   # CRLF / header injection impossible
        "key\r\nX: y",
        "key\x00x",
        "key\x7f",
        "key\tx",
        "k" * (MAX_FRONTIER_API_KEY_LENGTH + 1),
    ):
        with pytest.raises(InvalidFrontierConfigError):
            validate_frontier_api_key(hostile)


def test_frontier_model_validator_uses_hardened_shape():
    # Reuses the Phase 25 hardened model-string validator shape.
    for ok in ("gpt-4o", "deepseek/deepseek-chat", "accounts/fireworks/models/llama-v3p1-70b-instruct", "openai/gpt-4o"):
        assert validate_frontier_model_string(ok) == ok
    for hostile in (
        None,
        "",
        "   ",
        "http://evil/x",
        "https://evil/x",
        "a\nb",
        "a\x00b",
        "model/../pwn",
        "a" * 257,
    ):
        with pytest.raises(InvalidFrontierConfigError):
            validate_frontier_model_string(hostile)


def test_frontier_validators_never_echo_the_offending_value():
    for hostile in (
        "https://evil.example:11434/sekret-value",
        "key\nAuthorization: Bearer sekret",
        "openai?url=https://evil.example/sekret",
    ):
        for validator in (
            validate_frontier_provider_id,
            validate_frontier_api_key,
            validate_frontier_model_string,
        ):
            try:
                validator(hostile)
            except InvalidFrontierConfigError as exc:
                if "sekret" in str(exc) or "evil.example" in str(exc) or "11434" in str(exc):
                    raise AssertionError(f"{validator.__name__} echoed the value: {exc}")


# --------------------------------------------------------------------------- #
# §29 — registry tampering (API level; zero outbound calls on rejection)
# --------------------------------------------------------------------------- #

# A sentinel recorder that FAILS LOUDLY if the outbound adapter is ever called.
_NEVER_CALLED = {"called": False}


@pytest.fixture
def _outbound_must_not_be_called(monkeypatch):
    from app.generation import frontier_provider as fp_mod

    def _boom(*args, **kwargs):
        _NEVER_CALLED["called"] = True
        raise AssertionError("outbound provider call attempted")

    monkeypatch.setattr(fp_mod.httpx, "post", _boom)
    _NEVER_CALLED["called"] = False
    yield _NEVER_CALLED
    _NEVER_CALLED["called"] = False


def test_browser_cannot_submit_arbitrary_url(database_url, _outbound_must_not_be_called):
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            # A URL supplied as the provider id -> 400 INVALID_FRONTIER_CONFIG.
            res = _post_case(
                c, token,
                generationProvider="frontier",
                frontier={"provider": "https://evil.example", "apiKey": "k", "model": "m"},
            )
            assert res.status_code == 400, res.text
            assert res.json()["error"]["code"] == "INVALID_FRONTIER_CONFIG"
            assert "evil.example" not in res.text
            # An unknown endpoint/url/headers/timeout key inside the block is a
            # schema-level rejection (extra="forbid") — never accepted.
            for extra_field in ("url", "endpoint", "baseUrl", "headers", "timeout", "proxy", "tls"):
                res = _post_case(
                    c, token,
                    generationProvider="frontier",
                    frontier={"provider": "openai", "apiKey": "k", "model": "m", extra_field: "x"},
                )
                assert res.status_code in (400, 422), (extra_field, res.text)
            assert _NEVER_CALLED["called"] is False
    finally:
        _dispose(application)


def test_frontier_provider_id_injection_shapes_rejected(database_url, _outbound_must_not_be_called):
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            for hostile in (
                "https://evil.example",
                "http://evil.example/v1",
                "../../openai",
                "..\\openai",
                "openai?url=https://evil.example",
                "openai#frag",
                "openai\nAuthorization: Bearer x",
                "openai\rX: y",
                "openai\x00",
                "gpt",
                "live",
                "remote_client",
                "",
            ):
                res = _post_case(
                    c, token,
                    generationProvider="frontier",
                    frontier={"provider": hostile, "apiKey": "k", "model": "m"},
                )
                assert res.status_code == 400, (hostile, res.text)
                assert res.json()["error"]["code"] == "INVALID_FRONTIER_CONFIG"
                for token_bits in ("evil.example", "openai", "Bearer"):
                    assert token_bits not in res.text, (hostile, res.text)
            assert _NEVER_CALLED["called"] is False
    finally:
        _dispose(application)


def test_crlf_header_injection_through_api_key_rejected(database_url, _outbound_must_not_be_called):
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            for hostile_key in (
                "sk-abc\nAuthorization: Bearer other",
                "sk-abc\rX-Pwn: 1",
                "sk-abc\x00tail",
                "sk-abc\x7f",
            ):
                res = _post_case(
                    c, token,
                    generationProvider="frontier",
                    frontier={"provider": "openai", "apiKey": hostile_key, "model": "m"},
                )
                assert res.status_code == 400, (hostile_key, res.text)
                assert res.json()["error"]["code"] == "INVALID_FRONTIER_CONFIG"
                assert "Authorization" not in res.text
            assert _NEVER_CALLED["called"] is False
    finally:
        _dispose(application)


def test_oversized_provider_key_model_rejected(database_url, _outbound_must_not_be_called):
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            res = _post_case(
                c, token,
                generationProvider="frontier",
                frontier={
                    "provider": "openai",
                    "apiKey": "k" * (MAX_FRONTIER_API_KEY_LENGTH + 1),
                    "model": "m",
                },
            )
            assert res.status_code == 400, res.text
            assert res.json()["error"]["code"] == "INVALID_FRONTIER_CONFIG"
            res = _post_case(
                c, token,
                generationProvider="frontier",
                frontier={"provider": "openai", "apiKey": "k", "model": "m" * 257},
            )
            assert res.status_code == 400, res.text
            assert res.json()["error"]["code"] == "INVALID_FRONTIER_CONFIG"
            res = _post_case(
                c, token,
                generationProvider="frontier",
                frontier={
                    "provider": "p" * (MAX_FRONTIER_PROVIDER_ID_LENGTH + 1),
                    "apiKey": "k",
                    "model": "m",
                },
            )
            assert res.status_code == 400, res.text
            assert res.json()["error"]["code"] == "INVALID_FRONTIER_CONFIG"
            assert _NEVER_CALLED["called"] is False
    finally:
        _dispose(application)


def test_disabled_provider_id_fails_closed(database_url, monkeypatch):
    from app.generation import frontier_provider as fp_mod
    from app.generation import frontier_registry

    # Simulate an operator disabling the openai entry (server-owned registry).
    disabled = tuple(
        frontier_registry.FrontierProviderDefinition(
            provider_id=d.provider_id,
            label=d.label,
            endpoint=d.endpoint,
            protocol=d.protocol,
            enabled=(d.provider_id != "openai"),
        )
        for d in frontier_registry.FRONTIER_PROVIDER_REGISTRY
    )
    monkeypatch.setattr(frontier_registry, "FRONTIER_PROVIDER_REGISTRY", disabled)
    outbound_calls: list[str] = []
    monkeypatch.setattr(
        fp_mod.httpx, "post", lambda url, json=None, headers=None, timeout=None: (outbound_calls.append(url) or _adapter_provider(401, b"{}"))
    )
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            # The capability catalog omits the disabled provider entirely.
            body = c.get("/api/v1/generation-capabilities").json()
            frontier = {p["id"]: p for p in body["providers"]}["frontier"]
            assert frontier["available"] is True  # registry still non-empty
            catalog_ids = {entry["id"] for entry in frontier["providers"]}
            assert "openai" not in catalog_ids
            assert "groq" in catalog_ids
            token, _ = create_session(c)
            res = _post_case(
                c, token,
                generationProvider="frontier",
                frontier={"provider": "openai", "apiKey": "k", "model": "m"},
            )
            assert res.status_code == 400, res.text
            assert res.json()["error"]["code"] == "INVALID_FRONTIER_CONFIG"
            # The disabled provider must fail BEFORE any outbound call.
            assert outbound_calls == []
            # An enabled provider still resolves to a real attempt (401 here
            # only because the mock provider rejects: the SELECTION is valid).
            res = _post_case(
                c, token,
                generationProvider="frontier",
                frontier={"provider": "groq", "apiKey": "k", "model": "m"},
            )
            assert res.status_code == 201, res.text
            assert res.json()["status"] == "FAILED"
            assert res.json()["failureCode"] == "FRONTIER_AUTH_FAILED"
            assert outbound_calls == [GROQ_ENDPOINT]
    finally:
        _dispose(application)


# --------------------------------------------------------------------------- #
# §31 — provider behavior (availability, validation, resolution, no fallback)
# --------------------------------------------------------------------------- #


def test_frontier_disabled_is_unavailable(database_url, _outbound_must_not_be_called):
    application = _make_app(database_url, frontier_enabled=False)
    try:
        with TestClient(application) as c:
            body = c.get("/api/v1/generation-capabilities").json()
            frontier = {p["id"]: p for p in body["providers"]}["frontier"]
            assert frontier["available"] is False
            assert frontier["reason"] == "not_configured"
            token, _ = create_session(c)
            res = _post_case(
                c, token,
                generationProvider="frontier",
                frontier={"provider": "openai", "apiKey": "k", "model": "m"},
            )
            assert res.status_code == 400, res.text
            assert res.json()["error"]["code"] == "PROVIDER_UNAVAILABLE"
            assert _NEVER_CALLED["called"] is False
    finally:
        _dispose(application)


def test_frontier_enabled_selectable_without_server_credentials(database_url):
    # §31.2 — FRONTIER_ENABLED alone (NO FRONTIER_API_KEY/BASE_URL/MODEL) makes
    # the BYOK provider selectable.
    application = _make_app(database_url)
    assert application.state.settings.frontier_base_url is None
    assert application.state.settings.frontier_api_key is None
    assert application.state.settings.frontier_model is None
    try:
        with TestClient(application) as c:
            body = c.get("/api/v1/generation-capabilities").json()
            frontier = {p["id"]: p for p in body["providers"]}["frontier"]
            assert frontier["available"] is True
            assert frontier["reason"] is None
    finally:
        _dispose(application)


def test_capability_catalog_lists_safe_ids_labels_only(database_url):
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            response = c.get("/api/v1/generation-capabilities")
    finally:
        _dispose(application)
    body = response.json()
    frontier = {p["id"]: p for p in body["providers"]}["frontier"]
    assert frontier["requiresUserConfiguration"] is True
    assert frontier["available"] is True
    entries = frontier["providers"]
    assert len(entries) == 8
    for entry in entries:
        assert set(entry) == {"id", "label"}
        assert isinstance(entry["id"], str) and isinstance(entry["label"], str)
    # §9 — NEVER an endpoint/URL/secret in the capability response.
    for token in ("https://", "chat/completions", "api.", "secret", "sk-", "key"):
        assert token not in response.text


def test_missing_unknown_provider_key_model_rejected(database_url, _outbound_must_not_be_called):
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            # missing provider
            res = _post_case(
                c, token,
                generationProvider="frontier",
                frontier={"apiKey": "k", "model": "m"},
            )
            assert res.status_code == 400, res.text
            assert res.json()["error"]["code"] == "INVALID_FRONTIER_CONFIG"
            # missing key
            res = _post_case(
                c, token,
                generationProvider="frontier",
                frontier={"provider": "openai", "model": "m"},
            )
            assert res.status_code == 400, res.text
            assert res.json()["error"]["code"] == "INVALID_FRONTIER_CONFIG"
            # missing model
            res = _post_case(
                c, token,
                generationProvider="frontier",
                frontier={"provider": "openai", "apiKey": "k"},
            )
            assert res.status_code == 400, res.text
            assert res.json()["error"]["code"] == "INVALID_FRONTIER_CONFIG"
            # unknown provider
            res = _post_case(
                c, token,
                generationProvider="frontier",
                frontier={"provider": "cloud-unknown", "apiKey": "k", "model": "m"},
            )
            assert res.status_code == 400, res.text
            assert res.json()["error"]["code"] == "INVALID_FRONTIER_CONFIG"
            # frontier block without generationProvider=frontier -> strict 400
            for provider_label, extra in (
                ("fake", {"generationProvider": "fake", "frontier": {"provider": "openai", "apiKey": "k", "model": "m"}}),
                ("ollama", {"generationProvider": "ollama", "ollamaTransport": "server", "ollamaModel": "qwen2.5:1.5b", "frontier": {"provider": "openai", "apiKey": "k", "model": "m"}}),
                ("omitted", {"frontier": {"provider": "openai", "apiKey": "k", "model": "m"}}),
            ):
                res = _post_case(c, token, **extra)
                assert res.status_code == 400, (provider_label, res.text)
                assert res.json()["error"]["code"] == "INVALID_FRONTIER_CONFIG", provider_label
            assert _NEVER_CALLED["called"] is False
    finally:
        _dispose(application)


def test_valid_byok_resolves_to_registry_and_uses_user_key_and_model(database_url, monkeypatch):
    """§31.8/.9/.10/.11 — a valid provider/key/model publishes through the REAL
    service; the outbound endpoint comes ONLY from the trusted registry; the
    Authorization carries the supplied key; the payload carries the supplied
    model; everything is stable across ALL stages."""
    from app.generation import frontier_provider as fp_mod

    captured = []
    golden = list(_GOLDEN_STAGE_STRINGS)

    class _FakeResponse:
        status_code = 200

        def iter_bytes(self, chunk_size):
            body = golden.pop(0).encode("utf-8")
            yield body

    def _post(url, json=None, headers=None, timeout=None):
        captured.append((url, dict(json or {}), dict(headers or {})))
        return _FakeResponse()

    monkeypatch.setattr(fp_mod.httpx, "post", _post)
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            res = _post_case(
                c, token,
                generationProvider="frontier",
                frontier={"provider": "openai", "apiKey": "sk-user-A-1", "model": "MODEL-A"},
            )
            assert res.status_code == 201, res.text
            assert res.json()["status"] == "PUBLISHED"
            assert len(captured) == 4, captured
            for url, body, headers in captured:
                assert url == OPENAI_ENDPOINT, url
                assert headers["Authorization"] == "Bearer sk-user-A-1"
                assert body["model"] == "MODEL-A"
                assert body["messages"][0]["role"] == "user"
            # The frozen model reached the durable publication record.
            store = application.state.store
            published = json.loads(store.get_published(res.json()["caseId"], 1).payload_json)
            assert published.get("model") == "MODEL-A"
            assert "sk-user-A-1" not in res.text
            assert "sk-user-A-1" not in json.dumps(published)
    finally:
        _dispose(application)


def test_no_silent_operator_key_or_other_provider_fallback(database_url, monkeypatch):
    """§11/§14/§31.19 — even with the legacy operator-funded trio configured the
    browser BYOK attempt uses ONLY the registry endpoint + USER key; a missing
    BYOK block never falls back to the operator key, demo, ollama or bridge."""
    from app.generation import frontier_provider as fp_mod

    captured = []
    golden = list(_GOLDEN_STAGE_STRINGS)

    class _FakeResponse:
        status_code = 200

        def iter_bytes(self, chunk_size):
            body = golden.pop(0).encode("utf-8")
            yield body

    def _post(url, json=None, headers=None, timeout=None):
        captured.append((url, dict(json or {}), dict(headers or {})))
        return _FakeResponse()

    monkeypatch.setattr(fp_mod.httpx, "post", _post)
    application = _make_app(
        database_url,
        generation_provider="fake",
        frontier_base_url="https://operator.example.com/v1/chat/completions",
        frontier_api_key="OPERATOR-SECRET-KEY",
        frontier_model="operator-model",
    )
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            res = _post_case(
                c, token,
                generationProvider="frontier",
                frontier={"provider": "openai", "apiKey": "USER-KEY-9", "model": "user-model"},
            )
            assert res.status_code == 201, res.text
            assert res.json()["status"] == "PUBLISHED"
            for url, _body, headers in captured:
                assert url == OPENAI_ENDPOINT  # never the operator base URL
                assert "OPERATOR-SECRET-KEY" not in headers.get("Authorization", "")
                assert headers["Authorization"] == "Bearer USER-KEY-9"
            # The operator key never leaked into ANY response.
            assert "OPERATOR-SECRET-KEY" not in res.text
            # No global mutation.
            assert application.state.settings.generation_provider == "fake"
            # The default (no provider fields) request is still the fake/demo
            # pipeline (no frontier outbound call, byte-identical behavior).
            fake_res = _post_case(c, token)
            assert fake_res.status_code == 201, fake_res.text
            assert fake_res.json()["status"] == "PUBLISHED"
            assert all(url == OPENAI_ENDPOINT for url, _b, _h in captured)
    finally:
        _dispose(application)


def test_provider_fixed_across_all_stages_and_budgets_intact(database_url, monkeypatch):
    """§31.20 + §23 — one frozen endpoint/key/model drives every stage and the
    publication; the generation budgets/counters still behave (solver +
    publication gate unchanged, provider call counting unchanged)."""
    from app.generation import frontier_provider as fp_mod

    captured = []
    golden = list(_GOLDEN_STAGE_STRINGS)

    class _FakeResponse:
        status_code = 200

        def iter_bytes(self, chunk_size):
            body = golden.pop(0).encode("utf-8")
            yield body

    monkeypatch.setattr(fp_mod.httpx, "post", lambda url, json=None, headers=None, timeout=None: (captured.append((url, dict(json or {}), dict(headers or {}))) or _FakeResponse()))
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            res = _post_case(
                c, token,
                generationProvider="frontier",
                frontier={"provider": "groq", "apiKey": "KEY-STAGE", "model": "MODEL-STAGE"},
            )
            assert res.status_code == 201, res.text
            assert res.json()["status"] == "PUBLISHED"
            assert len(captured) == 4, captured
            for url, body, headers in captured:
                assert url == GROQ_ENDPOINT
                assert headers["Authorization"] == "Bearer KEY-STAGE"
                assert body["model"] == "MODEL-STAGE"
                assert body["messages"][0]["role"] == "user"
            store = application.state.store
            published = json.loads(store.get_published(res.json()["caseId"], 1).payload_json)
            # The solver/publication gate still ran and produced a valid proof.
            assert published["solverProof"]["validation"]["all_true"] is True
            assert published["report"]["valid"] is True
            # The provider-call account stays bounded: exactly the 4 stage
            # calls (no unbounded retries) — the budgets untouched contract.
            assert len(captured) == 4
    finally:
        _dispose(application)


# --------------------------------------------------------------------------- #
# §23 — normalized provider failures (FRONTIER_* vocabulary)
# --------------------------------------------------------------------------- #


def _adapter_provider(status_code: int, body: bytes = b"") -> "_FakeStatusResponse":
    class _R:
        def __init__(self, code, payload):
            self.status_code = code
            self._payload = payload

        def iter_bytes(self, chunk_size):
            yield self._payload

        @property
        def text(self):
            return self._payload.decode("utf-8", errors="replace")

    return _R(status_code, body)


def test_adapter_normalizes_status_bands_and_never_echoes_body(monkeypatch):
    from app.generation.frontier_provider import (
        FrontierHttpError,
        FrontierProvider,
        frontier_failure_code_for_status,
    )

    import app.generation.frontier_provider as fp_mod

    expected = {
        401: "FRONTIER_AUTH_FAILED",
        403: "FRONTIER_AUTH_FAILED",
        404: "FRONTIER_ENDPOINT_OR_MODEL_NOT_FOUND",
        429: "FRONTIER_RATE_LIMITED",
        400: "FRONTIER_PROVIDER_ERROR",
        500: "FRONTIER_PROVIDER_ERROR",
        502: "FRONTIER_PROVIDER_ERROR",
        503: "FRONTIER_PROVIDER_ERROR",
        302: "FRONTIER_PROVIDER_ERROR",
    }
    for status, code in expected.items():
        assert frontier_failure_code_for_status(status).value == code
    provider = FrontierProvider(
        endpoint_url=OPENAI_ENDPOINT, api_key="opaque", model="m"
    )
    for status, code in expected.items():
        monkeypatch.setattr(
            fp_mod.httpx, "post", lambda *a, status=status, **k: _adapter_provider(status, b'{"error":{"message":"SECRET-BODY-TOKEN"}}')
        )
        with pytest.raises(FrontierHttpError) as excinfo:
            provider.generate(
                GenerateRequest(attempt_id="att-x", stage=GenerationStage.CASE_TRUTH, prompt_context="p")
            )
        assert excinfo.value.code.value == code
        assert "SECRET-BODY-TOKEN" not in str(excinfo.value)
        assert "opaque" not in str(excinfo.value)


def test_timeout_normalized_to_frontier_timeout(monkeypatch):
    from app.generation.frontier_provider import (
        FrontierHttpError,
        FrontierProvider,
    )

    import app.generation.frontier_provider as fp_mod

    def _timeout(*a, **k):
        raise httpx.TimeoutException("dialog timeout")

    monkeypatch.setattr(fp_mod.httpx, "post", _timeout)
    provider = FrontierProvider(endpoint_url=OPENAI_ENDPOINT, api_key="k", model="m")
    with pytest.raises(FrontierHttpError) as excinfo:
        provider.generate(
            GenerateRequest(attempt_id="att-t", stage=GenerationStage.CASE_TRUTH, prompt_context="p")
        )
    assert excinfo.value.code.value == "FRONTIER_TIMEOUT"


def test_request_error_and_oversize_normalized(monkeypatch):
    from app.generation.frontier_provider import (
        FrontierHttpError,
        FrontierProvider,
    )

    import app.generation.frontier_provider as fp_mod

    class _ConnectError(httpx.RequestError):
        def __init__(self):
            super().__init__("no route to host", request=httpx.Request("POST", OPENAI_ENDPOINT))
            self.url = OPENAI_ENDPOINT

    monkeypatch.setattr(fp_mod.httpx, "post", lambda *a, **k: (_ for _ in ()).throw(_ConnectError()))
    provider = FrontierProvider(endpoint_url=OPENAI_ENDPOINT, api_key="k", model="m")
    with pytest.raises(FrontierHttpError) as excinfo:
        provider.generate(
            GenerateRequest(attempt_id="att-e", stage=GenerationStage.CASE_TRUTH, prompt_context="p")
        )
    assert excinfo.value.code.value == "FRONTIER_PROVIDER_ERROR"

    monkeypatch.setattr(
        fp_mod.httpx,
        "post",
        lambda *a, **k: _adapter_provider(200, b"A" * (256 * 1024 + 1)),
    )
    with pytest.raises(FrontierHttpError) as excinfo:
        provider.generate(
            GenerateRequest(attempt_id="att-o", stage=GenerationStage.CASE_TRUTH, prompt_context="p")
        )
    assert excinfo.value.code.value == "FRONTIER_PROVIDER_ERROR"


@pytest.mark.parametrize(
    "status,expected_code",
    (
        (401, "FRONTIER_AUTH_FAILED"),
        (403, "FRONTIER_AUTH_FAILED"),
        (404, "FRONTIER_ENDPOINT_OR_MODEL_NOT_FOUND"),
        (429, "FRONTIER_RATE_LIMITED"),
        (500, "FRONTIER_PROVIDER_ERROR"),
        (503, "FRONTIER_PROVIDER_ERROR"),
    ),
)
def test_provider_status_bands_fail_the_attempt_with_normalized_code(
    database_url, monkeypatch, status, expected_code
):
    """§31.15-18 — a non-2xx provider status fails the ATTEMPT with the closed
    FRONTIER_* code and no raw body in the API response."""
    from app.generation import frontier_provider as fp_mod

    body = json.dumps({"error": {"message": "RAW-PROVIDER-SECRET-BODY"}}).encode()
    monkeypatch.setattr(
        fp_mod.httpx,
        "post",
        lambda url, json=None, headers=None, timeout=None: _adapter_provider(status, body),
    )
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            res = _post_case(
                c, token,
                generationProvider="frontier",
                frontier={"provider": "openai", "apiKey": "user-key", "model": "m"},
            )
            assert res.status_code == 201, res.text
            assert res.json()["status"] == "FAILED"
            assert res.json()["failureCode"] == expected_code
            # The raw provider body never reaches the browser.
            assert "RAW-PROVIDER-SECRET-BODY" not in res.text
            assert "user-key" not in res.text
            # The durable attempt records only the safe public code.
            store = application.state.store
            version = store.get_case_version_by_generation_id(
                res.json()["generationId"], case_id=res.json()["caseId"]
            )
            assert version.state_reason == expected_code
    finally:
        _dispose(application)


def test_timeout_fails_attempt_with_frontier_timeout(database_url, monkeypatch):
    from app.generation import frontier_provider as fp_mod

    def _timeout(*a, **k):
        raise httpx.TimeoutException("dialog timeout")

    monkeypatch.setattr(fp_mod.httpx, "post", _timeout)
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            res = _post_case(
                c, token,
                generationProvider="frontier",
                frontier={"provider": "openai", "apiKey": "user-key", "model": "m"},
            )
            assert res.status_code == 201, res.text
            assert res.json()["status"] == "FAILED"
            assert res.json()["failureCode"] == "FRONTIER_TIMEOUT"
            assert "user-key" not in res.text
    finally:
        _dispose(application)


# --------------------------------------------------------------------------- #
# §27 — MANDATORY sentinel secret-persistence test
# --------------------------------------------------------------------------- #


def _scan_db_bytes(database_url: str) -> bytes:
    with open(_sqlite_path(database_url), "rb") as handle:
        return handle.read()


def test_sentinel_secret_never_persists_anywhere(tmp_path, database_url, monkeypatch):
    """§27 MANDATORY — generate with the sentinel key; after generation the
    sentinel appears NOWHERE in SQLite, case/published/playthrough DTOs,
    generation metadata, captured logs, structured events, error responses or
    monitoring inputs — only in the mocked outbound Authorization header."""
    import io

    from app.generation import frontier_provider as fp_mod

    outbound = _MockFrontierOutbound(golden_by_model={"MODEL-SENTINEL": list(_GOLDEN_STAGE_STRINGS)})
    # Single-request test: no overlap needed — never block the first call.
    outbound.release.set()
    outbound.install()
    case_row = version_row = attempt_row = published_row = playthrough_row = None
    try:
        application = _make_app(database_url)
        log_buffer = io.StringIO()
        try:
            with _CaptureLog(log_buffer), TestClient(application) as c:
                token, _ = create_session(c)
                res = _post_case(
                    c, token,
                    generationProvider="frontier",
                    frontier={"provider": "openai", "apiKey": SENTINEL_KEY, "model": "MODEL-SENTINEL"},
                )
                assert res.status_code == 201, res.text
                assert res.json()["status"] == "PUBLISHED"
                case_id = res.json()["caseId"]
                # The sentinel IS present in the (mocked) outbound Authorization
                # header — the single allowed location.
                assert any(
                    headers.get("Authorization") == f"Bearer {SENTINEL_KEY}"
                    for _url, _body, headers in outbound.posts
                )
                assert any(
                    body["model"] == "MODEL-SENTINEL" for _url, body, _h in outbound.posts
                )
                # Playthrough creation + playthrough DTO.
                store = application.state.store
                creator = res.json()["creatorAccessToken"]
                pt_res = c.post(
                    f"/api/v1/cases/{case_id}/versions/1/playthroughs",
                    headers={"Authorization": f"Bearer {creator}"},
                )
                assert pt_res.status_code == 201, pt_res.text
                playthrough_id = pt_res.json()["playthroughId"]
                playthrough_token = pt_res.json()["playthroughAccessToken"]
                public = c.get(
                    f"/api/v1/playthroughs/{playthrough_id}/public-case",
                    headers={"Authorization": f"Bearer {playthrough_token}"},
                )
                assert public.status_code == 200, public.text
                assert SENTINEL_KEY not in public.text
                case_dto = c.get(
                    f"/api/v1/cases/{case_id}",
                    headers={"Authorization": f"Bearer {creator}"},
                )
                assert case_dto.status_code == 200, case_dto.text
                assert SENTINEL_KEY not in case_dto.text

                # GENERATION METADATA row.
                attempt_row = store.get_generation_attempt_by_generation_id(
                    res.json()["generationId"], case_id=case_id
                )
                # CASE/version/credential/published/playthrough rows.
                case_row = store.get_case(case_id)
                version_row = store.get_case_version(case_id, 1)
                published_row = store.get_published(case_id, 1)
                playthrough_row = store.get_playthrough_by_id(playthrough_id)
                assert all(v is not None for v in (attempt_row, case_row, version_row, published_row, playthrough_row))
                log_text = log_buffer.getvalue()
        finally:
            _dispose(application)
    finally:
        outbound.restore()

    # ---- inspect EVERY sink independently ----
    # 1. SQLite raw bytes (every table / column / index page).
    db_bytes = _scan_db_bytes(database_url)
    assert SENTINEL_KEY.encode() not in db_bytes
    # 2. API responses: already scanned above (creation / case DTO /
    #    playthrough DTO / public-case) — none contains the sentinel.
    # 3. published-version payload (the durable DTO source).
    assert SENTINEL_KEY not in (published_row.payload_json or "")
    # 4. structured events + captured logs (the production JSON formatter —
    #    the same projection an operator/monitoring feed would see).
    assert SENTINEL_KEY not in log_text
    assert "Authorization" not in log_text
    # 5. generation metadata representation.
    assert SENTINEL_KEY not in repr(attempt_row.__dict__)
    # 6. every other durable row's stringified content (belt and braces).
    all_rows_text = "\n".join(
        repr(x) for x in (case_row, version_row, attempt_row, published_row, playthrough_row)
    )
    assert SENTINEL_KEY not in all_rows_text


def test_sentinel_absent_from_error_responses_and_db_after_failure(tmp_path, database_url, monkeypatch):
    """§27 — an AUTH failure with the sentinel key surfaces the normalized
    FRONTIER_AUTH_FAILED code; the sentinel never reaches the error response,
    the durable state, logs or the monitoring/DB surface."""
    import io

    from app.generation import frontier_provider as fp_mod

    monkeypatch.setattr(
        fp_mod.httpx,
        "post",
        lambda url, json=None, headers=None, timeout=None: _adapter_provider(401, b'{"error":{"message":"invalid api key"}}'),
    )
    application = _make_app(database_url)
    log_buffer = io.StringIO()
    try:
        with _CaptureLog(log_buffer), TestClient(application) as c:
            token, _ = create_session(c)
            res = _post_case(
                c, token,
                generationProvider="frontier",
                frontier={"provider": "openai", "apiKey": SENTINEL_KEY, "model": "m"},
            )
            assert res.status_code == 201, res.text
            assert res.json()["status"] == "FAILED"
            assert res.json()["failureCode"] == "FRONTIER_AUTH_FAILED"
            assert SENTINEL_KEY not in res.text
            # The safe error-class metadata (provider / frontierProvider /
            # model) IS present.
            assert "FRONTIER_AUTH_FAILED" in res.text
            store = application.state.store
            case_row = store.get_case(res.json()["caseId"])
            version_row = store.get_case_version(res.json()["caseId"], 1)
            assert version_row.state_reason == "FRONTIER_AUTH_FAILED"
            assert SENTINEL_KEY not in (res.text + repr(case_row) + repr(version_row) + log_buffer.getvalue())
    finally:
        _dispose(application)
    # SQLite pristine.
    assert SENTINEL_KEY.encode() not in _scan_db_bytes(database_url)


def test_sentinel_ignored_capability_and_request_logs(database_url, monkeypatch):
    """A tampered frontier block carrying the sentinel key produces the
    normalized FRONTIER_AUTH_FAILED attempt (mocked provider); the sentinel
    never re-appears in the API response."""
    from app.generation import frontier_provider as fp_mod

    monkeypatch.setattr(
        fp_mod.httpx,
        "post",
        lambda url, json=None, headers=None, timeout=None: _adapter_provider(401, b'{"error":{"message":"bad key"}}'),
    )
    application = _make_app(database_url)
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            res = _post_case(
                c, token,
                generationProvider="frontier",
                frontier={"provider": "openai", "apiKey": SENTINEL_KEY, "model": "m"},
            )
            assert res.status_code == 201, res.text
            assert res.json()["status"] == "FAILED"
            assert res.json()["failureCode"] == "FRONTIER_AUTH_FAILED"
            assert SENTINEL_KEY not in res.text
            assert "Authorization" not in res.text
    finally:
        _dispose(application)


# --------------------------------------------------------------------------- #
# §28 — MANDATORY concurrent billing-key isolation test
# --------------------------------------------------------------------------- #


def test_concurrent_byok_keys_and_providers_are_isolated(database_url):
    """§28 MANDATORY — two concurrent cases (A=openai/KEY-A/MODEL-A,
    B=groq/KEY-B/MODEL-B) over mocked providers. A hits the OpenAI registry
    endpoint with ONLY KEY-A/MODEL-A; B hits the Groq registry endpoint with
    ONLY KEY-B/MODEL-B; no cross-over and no global mutation.

    Overlap is genuine: each request's FIRST provider call blocks until both
    have arrived, so the two attempts are simultaneously in-flight with their
    own frozen selections.
    """
    from app.persistence.store import Store
    from app.services.generation import GenerationService

    upgrade_db(database_url)
    settings = Settings(
        database_url=database_url,
        cors_allowed_origins=["http://localhost:5173"],
        generation_provider="fake",
        frontier_enabled=True,
        max_concurrent_generations=2,
        max_concurrent_generations_global=4,
        max_generations_per_session_per_window=8,
        max_generations_global_per_window=50,
        generation_deadline_seconds=60,
    )
    store = Store(database_url)
    service = GenerationService(settings=settings, store=store)
    key_a, model_a = "KEY-A", "MODEL-A"
    key_b, model_b = "KEY-B", "MODEL-B"
    outbound = _MockFrontierOutbound(
        golden_by_model={model_a: list(_GOLDEN_STAGE_STRINGS), model_b: list(_GOLDEN_STAGE_STRINGS)}
    )
    outbound.install()
    try:
        session_a = service.create_anonymous_quota_session()
        session_b = service.create_anonymous_quota_session()
        results: dict[str, object] = {}
        errors: dict[str, Exception] = {}
        import threading as _threading

        def _run(which: str, session_id: str, provider: str, key: str, model: str) -> None:
            try:
                results[which] = service.start_case_generation(
                    _PROMPT,
                    anonymous_quota_session_id=session_id,
                    generation_provider="frontier",
                    frontier_provider=provider,
                    frontier_api_key=key,
                    frontier_model=model,
                )
            except Exception as exc:  # noqa: BLE001 - recorded for the assert
                errors[which] = exc

        thread_a = _threading.Thread(
            target=_run, args=("a", session_a.anonymous_quota_session_id, "openai", key_a, model_a)
        )
        thread_b = _threading.Thread(
            target=_run, args=("b", session_b.anonymous_quota_session_id, "groq", key_b, model_b)
        )
        thread_a.start()
        thread_b.start()
        try:
            started_at = time.monotonic()
            while (
                len(outbound.first_for_model) < 2
                and time.monotonic() - started_at < 30
            ):
                time.sleep(0.01)
            assert len(outbound.first_for_model) == 2, (
                "both requests must reach their first frontier stage"
            )
            # Both requests are now BLOCKED in their first provider call: the
            # only recorded wire traffic is the two first-stage posts.
            assert len(outbound.posts) == 2, outbound.posts
            first_models = [body["model"] for _url, body, _h in outbound.posts]
            assert set(first_models) == {model_a, model_b}, first_models
            # Release both and await.
            outbound.release.set()
            thread_a.join(timeout=120)
            thread_b.join(timeout=120)
            assert not thread_a.is_alive() and not thread_b.is_alive()
            assert not errors, errors
            assert results["a"].status == "PUBLISHED"
            assert results["b"].status == "PUBLISHED"
            assert results["a"].case_id != results["b"].case_id

            # Every post of A: OpenAI registry endpoint + KEY-A/MODEL-A only.
            posts_a = [
                (url, body, headers)
                for url, body, headers in outbound.posts
                if headers.get("Authorization") == f"Bearer {key_a}"
            ]
            posts_b = [
                (url, body, headers)
                for url, body, headers in outbound.posts
                if headers.get("Authorization") == f"Bearer {key_b}"
            ]
            assert len(posts_a) == 4
            assert len(posts_b) == 4
            assert all(url == OPENAI_ENDPOINT for url, _b, _h in posts_a)
            assert all(url == GROQ_ENDPOINT for url, _b, _h in posts_b)
            assert all(body["model"] == model_a for _u, body, _h in posts_a)
            assert all(body["model"] == model_b for _u, body, _h in posts_b)
            assert all(body["model"] != model_a for _u, body, _h in posts_b)
            # No cross-over and no extra key appears anywhere on the wire.
            assert len(outbound.posts) == 8
            wire_keys = {headers.get("Authorization") for _u, _b, headers in outbound.posts}
            assert wire_keys == {f"Bearer {key_a}", f"Bearer {key_b}"}
            wire_urls = {url for url, _b, _h in outbound.posts}
            assert wire_urls == {OPENAI_ENDPOINT, GROQ_ENDPOINT}

            # The frozen model reached each durable publication.
            stored_a = json.loads(store.get_published(results["a"].case_id, 1).payload_json)
            stored_b = json.loads(store.get_published(results["b"].case_id, 1).payload_json)
            assert stored_a.get("model") == model_a
            assert stored_b.get("model") == model_b
            for s in (json.dumps(stored_a), json.dumps(stored_b)):
                assert key_a not in s and key_b not in s
            # No global mutation (never a "current key" anywhere).
            assert settings.generation_provider == "fake"
            assert service._settings.frontier_api_key is None
        finally:
            outbound.release.set()
            thread_a.join(timeout=10)
            thread_b.join(timeout=10)
    finally:
        outbound.restore()
        store.dispose()


# --------------------------------------------------------------------------- #
# Demo / Ollama / budget regressions stay untouched
# --------------------------------------------------------------------------- #


def test_demo_ollama_and_defaults_unchanged_with_frontier_enabled(database_url, monkeypatch):
    """§31.22-26 — with FRONTIER_ENABLED=true (but nothing selected) the
    deterministic demo path, the Ollama-direct and Ollama-bridge paths, the
    solver/publication gate and the budgets behave EXACTLY as before."""
    from test_phase25_provider_selection import _BRIDGE_PROMPT, _mock_ollama_transport
    from test_ollama_driver import _staged

    from app.generation import ollama_provider as ollama_mod

    # Ollama transport mock (never the network).
    transport_cls = _mock_ollama_transport(golden=_staged())
    monkeypatch.setattr(ollama_mod, "_HttpxOllamaTransport", transport_cls)
    application = _make_app(
        database_url,
        generation_provider="fake",
        ollama_base_url="http://127.0.0.1:11434",
        ollama_model="llama3.2:3b",
        enable_bridge=False,
    )
    try:
        with TestClient(application) as c:
            token, _ = create_session(c)
            # 1. demo/fake default -> PUBLISHED with zero provider calls.
            demo = _post_case(c, token)
            assert demo.status_code == 201, demo.text
            assert demo.json()["status"] == "PUBLISHED"
            assert transport_cls.posts == []
            # 2. explicit ollama/server -> publishes through the mocked
            #    transport (unchanged Ollama Direct).
            ollama = _post_case(
                c, token, _BRIDGE_PROMPT,
                generationProvider="ollama",
                ollamaTransport="server",
                ollamaModel="hermes3:8b",
            )
            assert ollama.status_code == 201, ollama.text
            assert ollama.json()["status"] == "PUBLISHED"
            assert transport_cls.posts, "the ollama transport must have been called"
            for _url, payload, _timeout in transport_cls.posts:
                assert payload["model"] == "hermes3:8b"
    finally:
        _dispose(application)


def test_start_case_version_accepts_byok_frontier(database_url, monkeypatch):
    """start_case_version (v2+) also freezes a Phase 30 BYOK frontier config."""
    import json as _json

    from app.generation import frontier_provider as fp_mod
    from app.persistence.store import Store
    from app.services.generation import GenerationService

    captured = []
    golden = itertools.cycle(_GOLDEN_STAGE_STRINGS)

    class _FakeResponse:
        status_code = 200

        def iter_bytes(self, chunk_size):
            yield next(golden).encode("utf-8")

    monkeypatch.setattr(
        fp_mod.httpx, "post", lambda url, json=None, headers=None, timeout=None: (captured.append((url, headers.get("Authorization", ""))) or _FakeResponse())
    )
    upgrade_db(database_url)
    settings = Settings(
        database_url=database_url,
        generation_provider="fake",
        frontier_enabled=True,
        max_concurrent_generations=2,
        max_generations_per_session_per_window=8,
    )
    store = Store(database_url)
    service = GenerationService(settings=settings, store=store)
    try:
        session = service.create_anonymous_quota_session()
        first = service.start_case_generation(
            _PROMPT,
            anonymous_quota_session_id=session.anonymous_quota_session_id,
            generation_provider="frontier",
            frontier_provider="openai",
            frontier_api_key="v2-key",
            frontier_model="v2-model",
        )
        assert first.status == "PUBLISHED"
        captured.clear()
        second = service.start_case_version(
            first.case_id,
            _PROMPT,
            anonymous_quota_session_id=session.anonymous_quota_session_id,
            generation_provider="frontier",
            frontier_provider="openai",
            frontier_api_key="v2-key",
            frontier_model="v2-model",
        )
        assert second.status == "PUBLISHED"
        assert second.case_id == first.case_id
        assert second.generation_id == "GEN-2"
        assert captured, "the v2 attempt must have used the frontier provider"
        for url, authorization in captured:
            assert url == OPENAI_ENDPOINT
            assert authorization == "Bearer v2-key"
        stored = _json.loads(store.get_published(first.case_id, 2).payload_json)
        assert stored.get("model") == "v2-model"
    finally:
        store.dispose()