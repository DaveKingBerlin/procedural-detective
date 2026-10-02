"""Phase 25 — per-attempt provider selection resolution (backend core).

Central, immutable, concurrency-safe provider selection for a single case
generation attempt.

Design rules (locked for this module — see Phase25 §5 / §14 / §17 / §18):

- ``GenerationSelection`` is an IMMUTABLE (frozen) description of ONE attempt:
  the logical provider id + the optional Ollama transport and model. It is the
  ONLY thing a generation attempt may depend on: the service freezes it at
  attempt start and never re-reads a mutable global after that.
- ``resolve()`` produces a ``ResolvedGeneration`` — a frozen bundle holding a
  stateless provider FACTORY (never a shared mutable provider), the canonical
  provider id (for lifecycle events), the frozen model, the provider timeout
  and whether the attempt must run through the phase-16_2 stage driver. The
  factory returns a FRESH provider instance per call so two overlapping
  attempts can never share request/session state.
- NO global settings mutation: ``resolve()`` only reads ``settings``; a caller
  may select fake and construct a real Ollama server path in the SAME process
  concurrently without either touching the other (Phase25 §1.5 / §14).
- The browser value is UNTRUSTED input and is validated centrally here
  (``validate_ollama_model_string``) — the SAME validator feeds the direct
  Ollama path and the bridge path (Phase25 §1.3 / §4.1). The operator config
  validator (``Settings._validate_ollama_model``) is untouched.
- Only SAFE logical values are accepted: provider ``fake|ollama|frontier`` and
  transport ``server|bridge`` for browser-supplied selection. Legacy defaults
  ``live`` and ``remote_client`` are config-only (never browser-selectable).
- Errors are typed: ``InvalidProviderError`` (400 INVALID_GENERATION_PROVIDER),
  ``ProviderUnavailableError`` (400 PROVIDER_UNAVAILABLE — an EXPLICIT request
  for a not-configured provider, never a silent fallback),
  ``InvalidOllamaModelError`` (400 INVALID_OLLAMA_MODEL) and
  ``SelectionConfigError`` (operator misconfiguration -> translated to the
  service's ``ProviderConfigError`` for the config-default path).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from app.core.config import DEFAULT_OLLAMA_BASE_URL
from app.generation.bridge_protocol import MAX_MODEL_LABEL_LENGTH
from app.generation.fake_provider import FakeProvider
from app.generation.live_provider import LiveHttpProvider
from app.generation.provider import Provider
from app.generation.remote_client_provider import RemoteClientProvider

# --------------------------------------------------------------------------- #
# the closed logical vocabulary (Phase25 §1.3 / §4.1)
# --------------------------------------------------------------------------- #

# Browser-selectable providers (the ONLY values the request body may carry).
BROWSER_SELECTABLE_PROVIDERS: frozenset[str] = frozenset({"fake", "ollama", "frontier"})
# All logical provider ids the resolver understands (including the legacy
# config-only defaults live / remote_client — they can never come from the
# request body but ARE valid configured defaults).
ALL_PROVIDER_IDS: frozenset[str] = frozenset(
    {"fake", "ollama", "frontier", "live", "remote_client"}
)
# Valid Ollama transports.
OLLAMA_TRANSPORTS: frozenset[str] = frozenset({"server", "bridge"})

# --------------------------------------------------------------------------- #
# the central user-input Ollama model-string validator (Phase25 §1.3)
# --------------------------------------------------------------------------- #

# The contract's maximum for a USER-SUPPLIED model string (the operator config
# validator keeps its own 80-char bound and is untouched).
MAX_USER_MODEL_LENGTH = 256
# Allow [A-Za-z0-9._:\-+/] — letters, digits, '.', '_', '-', ':', '/' plus '+'
# (documented decision: '+' is permitted for quantized/build-tag variants such
# as ``model+q8_0``; it is structurally inert because the value is never
# shell-interpolated and only ever travels as a structured JSON field).
_ALLOWED_MODEL_CHARS_RE = re.compile(r"^[A-Za-z0-9._:\-+/]+$")
# Obvious URL schemes (case-insensitive substring scan).
_URL_SCHEME_TOKENS: tuple[str, ...] = ("http://", "https://", "ws://", "wss://")
# A general scheme-looking prefix (e.g. ``ftp://``, ``file://``).
_SCHEME_PREFIX_RE = re.compile(r"^[a-z][a-z0-9+.\-]*://", re.IGNORECASE)
# F1 (adversarial, accepted): known URL scheme NAMES also rejected as a leading
# ``<name>:`` prefix WITHOUT ``//`` (``http:evil.example``, ``https:evil``,
# ``http:/evil``, ``ws:``). A purely syntactic ``^[A-Za-z][A-Za-z0-9+.-]*:``
# scheme test would ALSO reject legitimate Ollama tags such as ``qwen2.5:1.5b``
# (that release prefix is a valid RFC-3986 scheme token) — so the scheme
# rejection is anchored to DOCUMENTED scheme names only. This is the minimal
# fix that covers every mandated REJECT while preserving every mandated/live
# ACCEPT (``qwen2.5:1.5b``, ``library/model:1.2.3``, ``model+q8_0``,
# ``mymodel``, ``a/b:c``). Path-like shapes (``//`` separators, leading /
# trailing / embedded ``..``) are rejected separately below.
_FORBIDDEN_SCHEME_NAMES: frozenset[str] = frozenset(
    {
        "http", "https", "ws", "wss", "ftp", "ftps", "file", "data",
        "javascript", "vbscript", "mailto", "tel", "telnet", "ssh", "sftp",
        "gopher", "ldap", "ldaps", "irc", "ircs", "mms", "rtsp", "nntp",
        "news", "smtp", "pop", "imap", "imaps", "sip", "sips", "xmpp",
        "jdbc", "mqtt", "about", "chrome", "view-source", "intent",
    }
)
# Any printable non-ASCII or C0/C1 control character is rejected outright.
_CTRL_OR_NON_ASCII_RE = re.compile(r"[^\x20-\x7E]")


def validate_ollama_model_string(value: object) -> str | None:
    """Central Phase 25 user-supplied Ollama model validator.

    Returns the trimmed, validated model string, or ``None`` for an absent
    field. Raises ``InvalidOllamaModelError`` with a SANITIZED message (the
    offending value is never echoed) for:

    - empty after trimming surrounding whitespace;
    - any CR/LF/control or non-ASCII-printable character;
    - obvious URL schemes (``http://`` ``https://`` ``ws://`` ``wss://``), a
      general ``^[a-z][a-z0-9+.-]*://`` scheme-looking prefix, OR a leading
      DOCUMENTED scheme name + ``:`` without ``//`` (``http:evil.example``,
      ``ws:`` — F1);
    - ``//`` separators anywhere and dot-dot path/traversal shapes
      (``/../``, ``..\\``, a value that starts with ``..`` or ends with
      ``..``) while a single ``/`` or single ``.`` inside a legitimate Ollama
      tag stays allowed (``library/model:1.2.3``, ``qwen2.5:1.5b`` — F1);
    - length > ``MAX_USER_MODEL_LENGTH`` (256);
    - a character outside ``[A-Za-z0-9._:\\-+/]``.

    The value is NEVER shell-interpolated anywhere; it only travels as
    structured data (``OllamaProvider(model=...)`` or the bridge job ``model``
    field). No hard-coded model allowlist is applied (custom/local Ollama model
    names must work) — this is a pure shape/transport-safety gate.
    """
    if value is None:
        return None
    if not isinstance(value, str):
        raise InvalidOllamaModelError("model must be a string")
    text = value.strip()
    if not text:
        raise InvalidOllamaModelError("model must not be empty")
    if _CTRL_OR_NON_ASCII_RE.search(text):
        raise InvalidOllamaModelError("model contains a control or non-ASCII character")
    lowered = text.casefold()
    if any(token in lowered for token in _URL_SCHEME_TOKENS):
        raise InvalidOllamaModelError("model must not contain a URL scheme")
    if _SCHEME_PREFIX_RE.match(lowered):
        raise InvalidOllamaModelError("model must not contain a URL scheme")
    # F1 — a leading DOCUMENTED scheme name + ``:`` (without needing ``//``).
    # e.g. http:evil.example / https:evil / http:/evil / ws:
    _colon = text.find(":")
    if _colon > 0 and text[:_colon].casefold() in _FORBIDDEN_SCHEME_NAMES:
        raise InvalidOllamaModelError("model must not contain a URL scheme")
    # F1 — ``//`` separator shape (http//evil) and dot-dot path/traversal
    # shapes (model/../pwn, ../x, x/..). Single ``/`` and single ``.`` inside
    # legitimate tags (library/model:1.2.3, qwen2.5:1.5b) stay allowed.
    if "//" in text:
        raise InvalidOllamaModelError("model must not contain a URL or path shape")
    if (
        "/../" in text
        or "..\\" in text
        or text.startswith("..")
        or text.endswith("..")
    ):
        raise InvalidOllamaModelError("model must not contain a path-traversal shape")
    if len(text) > MAX_USER_MODEL_LENGTH:
        raise InvalidOllamaModelError("model is too long")
    if not _ALLOWED_MODEL_CHARS_RE.fullmatch(text):
        raise InvalidOllamaModelError("model contains an invalid character")
    return text


# --------------------------------------------------------------------------- #
# configuration-presence predicates (shared by the resolver and the capability
# DTO — availability is derived independently per provider, Phase25 §18)
# --------------------------------------------------------------------------- #


def ollama_server_configured(settings: object) -> bool:
    """True when the operator exposed a server/direct Ollama endpoint.

    OLLAMA_BASE_URL explicitly set (not None), OR the operator explicitly
    selected ``ollama`` as the default (which then uses ``DEFAULT_OLLAMA_BASE_URL``).
    OLLAMA_MODEL always has a documented default, so it never binds.
    """
    return (
        getattr(settings, "ollama_base_url", None) is not None
        or getattr(settings, "generation_provider", None) == "ollama"
    )


def ollama_bridge_available(settings: object, bridge_registry: object) -> bool:
    """True when the bridge feature flag is on AND a registry is wired.

    This is the CONFIG-level availability of the bridge transport. The actual
    session-scoped ``connected`` state is reported separately by the capability
    DTO (from the registry, scoped to the requesting session).
    """
    return bool(getattr(settings, "enable_bridge", False)) and bridge_registry is not None


def frontier_configured(settings: object) -> bool:
    """True when every FRONTIER_* member is present (Phase25 §3.1 Frontier)."""
    return bool(
        getattr(settings, "frontier_enabled", False)
        and getattr(settings, "frontier_base_url", None)
        and getattr(settings, "frontier_api_key", None)
        and getattr(settings, "frontier_model", None)
    )


def frontier_display_model(settings: object) -> str | None:
    """The PUBLIC-SAFE configured Frontier model display name (never the key)."""
    value = getattr(settings, "frontier_model", None)
    return str(value) if value else None


# --------------------------------------------------------------------------- #
# immutable selection / resolution bundles
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class GenerationSelection:
    """One immutable, frozen-per-attempt provider selection (Phase25 §5).

    ``provider`` is a logical provider id (``fake``/``ollama``/``frontier`` for
    browser selections; ``live``/``remote_client`` for legacy configured
    defaults). ``ollama_transport`` (``server``/``bridge``) and ``ollama_model``
    apply to Ollama selections only.
    """

    provider: str
    ollama_transport: str | None = None
    ollama_model: str | None = None

    def __post_init__(self) -> None:
        if self.provider not in ALL_PROVIDER_IDS:
            raise InvalidProviderError("unknown generation provider")
        if (
            self.ollama_transport is not None
            and self.ollama_transport not in OLLAMA_TRANSPORTS
        ):
            raise InvalidProviderError("unknown ollama transport")
        if self.ollama_transport is not None and self.provider not in (
            "ollama",
            "remote_client",
        ):
            raise InvalidProviderError(
                "ollama transport is only valid for an ollama selection"
            )


@dataclass(frozen=True)
class ResolvedGeneration:
    """Frozen per-attempt provider bundle (Phase25 §5).

    ``provider_factory`` returns a FRESH, stateless provider instance for the
    frozen selection (never a shared mutable provider, so concurrent requests
    stay isolated). ``provider_id`` is the canonical lifecycle id ("fake",
    "live", "ollama", "frontier", "remote_client") used for events/logging.
    ``model`` is the frozen model for the attempt (publication record / events).
    ``timeout_seconds`` is the frozen provider timeout carried to the
    controller. ``needs_driver`` is True only for the driver-run providers
    (ollama server / ollama bridge / legacy remote_client).
    """

    provider_factory: Callable[[], Provider]
    provider_id: str
    model: str | None
    timeout_seconds: float | None
    needs_driver: bool
    selection: GenerationSelection


# --------------------------------------------------------------------------- #
# typed selection errors (translated to canonical HTTP codes at the boundary)
# --------------------------------------------------------------------------- #


class GenerationSelectionError(Exception):
    """Base class for selection/validation failures."""


class InvalidProviderError(GenerationSelectionError):
    """Unknown browser-supplied provider id or Ollama transport id."""


class ProviderUnavailableError(GenerationSelectionError):
    """An EXPLICITLY requested but not-configured/unavailable provider."""


class InvalidOllamaModelError(GenerationSelectionError):
    """A user-supplied Ollama model string failed the central validator."""


class SelectionConfigError(GenerationSelectionError):
    """Operator configuration is invalid for the selected provider (fail fast)."""


# --------------------------------------------------------------------------- #
# the resolver
# --------------------------------------------------------------------------- #


def resolve(
    selection: GenerationSelection,
    settings: object,
    session: str | None,
    *,
    bridge_registry: object = None,
    ollama_structured_output: bool = False,
    fake_script: Mapping[Any, Any] | None = None,
    strict_unavailable: bool = False,
) -> ResolvedGeneration:
    """Resolve a frozen selection to a concrete provider bundle.

    ``session`` is the generation attempt's creator-session scope (used to bind
    a remote-client provider to its bridge). ``strict_unavailable`` selects the
    failure mode for a missing provider configuration:

    - False (the CONFIG-DEFAULT path, e.g. ``GENERATION_PROVIDER=frontier``
      without the full trio) -> ``SelectionConfigError`` — fail-fast at app
      construction like the legacy live/remote_client branches;
    - True (an EXPLICIT browser-supplied selection) -> ``ProviderUnavailableError``
      — the canonical 400 PROVIDER_UNAVAILABLE with NO silent fallback.

    NEVER mutates ``settings`` or any global state; safe for concurrent use.
    """
    provider = selection.provider

    if provider == "fake":
        if fake_script is None:
            raise SelectionConfigError("fake provider script is empty")
        script = fake_script

        def _fake() -> Provider:
            return FakeProvider(script=script)

        return ResolvedGeneration(
            provider_factory=_fake,
            provider_id="fake",
            model=None,
            timeout_seconds=None,
            needs_driver=False,
            selection=selection,
        )

    if provider == "live":
        url = getattr(settings, "live_provider_url", None)
        key = getattr(settings, "llm_api_key", None)
        model = getattr(settings, "llm_model", None)
        if not (url and key and model):
            raise SelectionConfigError(
                "generation_provider=live requires LIVE_PROVIDER_URL, "
                "LLM_API_KEY and LLM_MODEL"
            )
        url_s = str(url)
        key_s = str(key)
        model_s = str(model)

        def _live() -> Provider:
            return LiveHttpProvider(endpoint_url=url_s, api_key=key_s, model=model_s)

        return ResolvedGeneration(
            provider_factory=_live,
            provider_id="live",
            model=model_s,
            timeout_seconds=30.0,
            needs_driver=False,
            selection=selection,
        )

    if provider == "frontier":
        if not frontier_configured(settings):
            if strict_unavailable:
                raise ProviderUnavailableError("frontier provider is not configured")
            raise SelectionConfigError(
                "generation_provider=frontier requires FRONTIER_ENABLED=true, "
                "FRONTIER_BASE_URL, FRONTIER_API_KEY and FRONTIER_MODEL"
            )
        endpoint = str(settings.frontier_base_url)
        api_key = str(settings.frontier_api_key)
        model = str(settings.frontier_model)
        timeout = float(
            getattr(settings, "frontier_timeout_seconds", 60.0) or 60.0
        )
        from app.generation.frontier_provider import FrontierProvider

        def _frontier() -> Provider:
            return FrontierProvider(
                endpoint_url=endpoint,
                api_key=api_key,
                model=model,
                timeout_seconds=timeout,
            )

        return ResolvedGeneration(
            provider_factory=_frontier,
            provider_id="frontier",
            model=model,
            timeout_seconds=timeout,
            needs_driver=False,
            selection=selection,
        )

    if provider in ("ollama", "remote_client"):
        transport = selection.ollama_transport or "server"
        if transport == "bridge":
            if not ollama_bridge_available(settings, bridge_registry):
                if strict_unavailable:
                    raise ProviderUnavailableError("bridge transport is not available")
                raise SelectionConfigError(
                    "generation_provider=remote_client requires ENABLE_BRIDGE="
                    "true and a configured bridge registry"
                )
            model = selection.ollama_model
            # Phase 25: a user-supplied bridge model must fit the strict bridge
            # job-frame protocol label bound (the wire carries ``model`` as a
            # job field validated by ``_validate_model_label``). The central
            # validator allows up to 256 chars; the protocol allows 80 — clamp
            # at the protocol bound so the job frame can never be rejected
            # mid-flight (fail clean at request time instead).
            if model is not None and len(model) > MAX_MODEL_LABEL_LENGTH:
                raise InvalidOllamaModelError("model is too long for the bridge job")
            timeout = float(
                getattr(settings, "bridge_job_deadline_seconds", 120.0) or 120.0
            )

            def _remote() -> Provider:
                return RemoteClientProvider(
                    registry=bridge_registry,
                    settings=settings,
                    session_scope=session,
                    model=model,
                )

            return ResolvedGeneration(
                provider_factory=_remote,
                provider_id="remote_client",
                model=model,
                timeout_seconds=timeout,
                needs_driver=True,
                selection=selection,
            )

        # server / direct transport
        if not ollama_server_configured(settings):
            if strict_unavailable:
                raise ProviderUnavailableError("ollama provider is not configured")
            raise SelectionConfigError(
                "generation_provider=ollama requires OLLAMA_BASE_URL or an "
                "explicit OLLAMA_BASE_URL setting"
            )
        from app.generation.ollama_provider import OllamaProvider

        base_url = str(
            getattr(settings, "ollama_base_url", None) or DEFAULT_OLLAMA_BASE_URL
        ).rstrip("/")
        model = selection.ollama_model or str(
            getattr(settings, "ollama_model", "") or ""
        )
        if not model:
            raise SelectionConfigError("generation_provider=ollama requires a model")
        timeout_seconds = float(
            getattr(settings, "ollama_timeout_seconds", 60.0) or 60.0
        )

        def _ollama() -> Provider:
            return OllamaProvider(
                base_url=base_url,
                model=model,
                timeout_seconds=timeout_seconds,
                temperature=float(getattr(settings, "ollama_temperature", 0.2) or 0.2),
                num_ctx=int(getattr(settings, "ollama_num_ctx", 4096) or 4096),
                structured_output=bool(ollama_structured_output),
            )

        return ResolvedGeneration(
            provider_factory=_ollama,
            provider_id="ollama",
            model=model,
            timeout_seconds=timeout_seconds,
            needs_driver=True,
            selection=selection,
        )

    raise InvalidProviderError("unknown generation provider")


__all__ = [
    "ALL_PROVIDER_IDS",
    "BROWSER_SELECTABLE_PROVIDERS",
    "GenerationSelection",
    "GenerationSelectionError",
    "InvalidOllamaModelError",
    "InvalidProviderError",
    "MAX_USER_MODEL_LENGTH",
    "OLLAMA_TRANSPORTS",
    "ProviderUnavailableError",
    "ResolvedGeneration",
    "SelectionConfigError",
    "frontier_configured",
    "frontier_display_model",
    "ollama_bridge_available",
    "ollama_server_configured",
    "resolve",
    "validate_ollama_model_string",
]