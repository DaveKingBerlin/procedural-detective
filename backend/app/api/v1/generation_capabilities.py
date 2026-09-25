"""Player-safe generation capability metadata (Phase16 D / I / J).

Public (NO auth) capability advertisement for the frontend's generation-mode
selector. The response is an explicit allowlist and NEVER contains:

- the Ollama base URL / host / port,
- API credentials,
- raw prompts,
- network details,
- internal errors.

EXACT top-level shape: ``{"modes": [...], "configuredProvider": <enum>}``.

- ``configuredProvider`` is the backend-AUTHORITATIVE "what will actually run"
  signal: the exact raw operator setting string from ``Settings``.
  ``generation_provider`` (a ``Literal["fake", "ollama", "live"]``), emitted
  VERBATIM and INDEPENDENT of probe availability — never a URL, host, IP,
  model token or credential. A defensive allowlist fails closed to ``"fake"``
  for a non-enum value (matching the provider factory's runtime fallback
  branch), so a hostile settings object can never change this field into a
  non-enum string.
- ``demo.available`` is TRUTHFUL (Phase21B Finding 3 / DEF-096): True ONLY
  when ``configuredProvider == "fake"`` — the deterministic demo pipeline is
  actually server-enforced on that profile alone. An ollama/live backend
  reports ``demo.available:false`` EVEN while its probe is down, so the DTO
  can never collapse to the fake-only shape and mislabel an Ollama request as
  deterministic demo.

``model`` is only the operator-configured display name (``OLLAMA_MODEL``) —
public-safe by definition. Availability failures degrade to ``available:false``
with no reason detail beyond the sanitized probe result. The local mode is
probed ONLY when ``GENERATION_PROVIDER == "ollama"`` so a misconfigured
unselected provider can never block this endpoint (or anything else).
The probe itself is BOUNDED at the service seam (``app.services.generation_capabilities``):
short-TTL cache + single-flight + a dedicated small probe timeout independent of
``OLLAMA_TIMEOUT_SECONDS`` + negative caching of failures (Phase21B Finding 4).
``POST /sessions/anonymous``-style admission (PD-SEC-02) is mirrored here with a
small per-IP sliding window (``CAPABILITY_REQUESTS_PER_IP_PER_MIN``) keyed on the
TRUST_PROXY-aware resolved client IP — a spoofed ``X-Forwarded-For`` never bypasses
it while ``TRUST_PROXY=false``.
"""

from __future__ import annotations

from fastapi import APIRouter, Request

from app.api.v1.errors import http_error
from app.core.ratelimit import resolve_client_ip
from app.services.generation_capabilities import ollama_available

router = APIRouter(tags=["generation-capabilities"])

_LOCAL_LABEL = "Local AI"

# The CLOSED enum of provider values the DTO may ever advertise. The Settings
# field is already a Literal of exactly these three (+ the Phase 22
# "remote_client" bridge mode), so it cannot be malformed; the allowlist is a
# defensive fail-closed guard so a hostile injected settings object can never
# turn ``configuredProvider`` into a non-enum string.
_PROVIDER_ALLOWLIST: tuple[str, ...] = ("fake", "ollama", "live")


def _configured_provider(settings: object) -> str:
    """Backend-authoritative, sanitized provider enum for the DTO.

    The value is the EXACT raw operator setting (``Settings.generation_provider``,
    a ``Literal["fake","ollama","live","remote_client"]``) — emitted verbatim,
    never a URL, host/IP, model token or credential. Defensive allowlist (fail
    closed): a value outside the Phase 21B closed set {fake, ollama, live}
    resolves to ``"fake"`` — the one provider that is always server-enforced
    and needs no network.

    Phase 22: ``remote_client`` (the BYO-Ollama bridge) is a NEW separate
    concept and is NOT a ``configuredProvider`` value (Phase 21B contract). It
    projects onto the closed set as ``"fake"`` here (the documented fail-closed
    default), and its TRUTHFUL signal lives in the top-level ``remoteLocalAi``
    block instead. ``demo.available`` never becomes true for it (see
    ``_demo_available``), so a bridge deployment is never mislabelled as the
    deterministic demo.
    """
    value = getattr(settings, "generation_provider", None)
    if value in _PROVIDER_ALLOWLIST:
        return value
    return "fake"


def _demo_available(configured_provider: str, settings: object) -> bool:
    """Truthful Phase 21B rule: the deterministic demo pipeline is server-
    enforced ONLY on the fake profile. A remote_client (bridge) backend does
    NOT run the fake pipeline (its closed-set projection is ``"fake"`` for the
    untouchable Phase 21B enum), so demo.available stays false there; any
    OTHER value (including a hostile injected string) keeps the historical
    fail-closed rule ``configuredProvider == "fake"``."""
    if getattr(settings, "generation_provider", None) == "remote_client":
        return False
    return configured_provider == "fake"


def _current_session_scope(request: Request) -> str | None:
    """Best-effort anonymous-session detection for the PUBLIC capability
    endpoint: a VALID ``anonymousSessionToken`` bearer scopes the truthful
    ``remoteLocalAi.connected`` value to its own session. An absent/garbled/
    expired bearer simply leaves ``connected=false`` for the anonymous caller —
    the endpoint stays public and never hard-fails on a header it did not
    require."""
    authorization = request.headers.get("authorization")
    if not authorization:
        return None
    try:
        from app.auth.tokens import parse_bearer, verifier

        token = parse_bearer(authorization)
        row = request.app.state.store.get_session_by_verifier(verifier(token))
        if row is None:
            return None
        if request.app.state.clock.now() >= row.quota_window_end:
            return None
        return row.session_id
    except Exception:  # noqa: BLE001 - public endpoint never hard-fails
        return None


def _remote_local_ai(request: Request, settings: object) -> dict | None:
    """Truthful top-level ``remoteLocalAi`` block (Phase 22), or None when the
    bridge feature is disabled (the DTO then omits the key entirely).

    NEVER includes the bridge token, secret, IP or Ollama URL — only booleans
    and the sanitized model label, scoped to the requester's session."""
    if not bool(getattr(settings, "enable_bridge", False)):
        return None
    scope = _current_session_scope(request)
    registry = getattr(request.app.state, "bridge_registry", None)
    state = {"connected": False, "model": None, "ready": False}
    if scope is not None and registry is not None:
        try:
            state = registry.status_for_scope(scope)
        except Exception:  # noqa: BLE001 - sanitized degrade
            state = {"connected": False, "model": None, "ready": False}
    return {
        "available": True,
        "connected": bool(state.get("connected", False)),
        "model": state.get("model"),
        "ready": bool(state.get("ready", False)),
    }


def _live_configured(settings: object) -> bool:
    """The live provider is 'configured' only when all three live settings are
    present (LLM_API_KEY / LIVE_PROVIDER_URL / LLM_MODEL)."""
    return bool(
        getattr(settings, "llm_api_key", None)
        and getattr(settings, "live_provider_url", None)
        and getattr(settings, "llm_model", None)
    )


def _enforce_capability_rate_limit(request: Request) -> None:
    """Phase21B Finding 4 — small per-IP sliding window on the PUBLIC endpoint.

    The endpoint is unauthenticated and now serves from a short-TTL cache; the
    per-IP window (``CAPABILITY_REQUESTS_PER_IP_PER_MIN``) still bounds scripted
    request volume. Identity is the Phase 20 ``resolve_client_ip``
    (direct socket peer by default; forwarded headers honored ONLY when
    ``TRUST_PROXY=true``). Denial raises the sanitized 429 envelope with no
    internal detail.
    """
    limiter = getattr(request.app.state, "capability_ip_limiter", None)
    if limiter is None:  # defensive: create_app always wires it
        return
    trust_proxy = bool(request.app.state.settings.trust_proxy)
    ip = resolve_client_ip(request, trust_proxy=trust_proxy)
    if not limiter.allow(ip, request.app.state.clock.now()):
        raise http_error(
            429,
            "TOO_MANY_REQUESTS",
            "Too many capability requests; please try again later",
        )


@router.get(
    "/generation-capabilities",
    summary="Generation provider capabilities",
    description=(
        "Public capability metadata for the generation-mode selector. Reveals "
        "only configured mode ids, availability booleans, the sanitized "
        "backend-authoritative provider enum and the operator-configured model "
        "display name. Never URLs, credentials, prompts, network details or "
        "internal errors. Top-level shape: {\"modes\": [...], "
        "\"configuredProvider\": \"fake\"|\"ollama\"|\"live\"}. "
        "demo.available is TRUE only when configuredProvider==\"fake\" (the "
        "deterministic demo is server-enforced on that profile alone); an "
        "ollama/live backend reports demo.available:false even while its probe "
        "is down."
    ),
)
def generation_capabilities(request: Request) -> dict:
    """Player-safe mode list (exact shape: a top-level ``modes`` array plus the
    ``configuredProvider`` enum).

    ``configuredProvider`` is the backend-authoritative ''what will actually
    run'' signal (sanitized enum, independent of probe availability).
    ``demo.available`` is truthful (Phase21B Finding 3 / DEF-096): the demo
    mode is offered ONLY when the deterministic fake provider is actually the
    configured provider — an ollama/live backend advertises NO demo even while
    its probe is down, so the DTO never looks fake-only.
    """
    _enforce_capability_rate_limit(request)
    settings = request.app.state.settings
    configured_provider = _configured_provider(settings)
    modes: list[dict] = [
        {"id": "demo", "available": _demo_available(configured_provider, settings)}
    ]

    # Local AI: only the SELECTED provider is probed.
    local_available = False
    if getattr(settings, "generation_provider", None) == "ollama":
        try:
            local_available, _detail = ollama_available(settings)
        except Exception:  # noqa: BLE001 - availability failures degrade
            local_available = False
    local_entry: dict = {"id": "local", "available": local_available}
    local_entry["label"] = _LOCAL_LABEL
    local_entry["model"] = str(getattr(settings, "ollama_model", "") or "")
    modes.append(local_entry)

    # Cloud AI: revealed only when the live provider is actually configured.
    if _live_configured(settings):
        modes.append(
            {
                "id": "live",
                "available": getattr(settings, "generation_provider", None) == "live",
            }
        )
    body: dict = {"modes": modes, "configuredProvider": configured_provider}
    remote_local_ai = _remote_local_ai(request, settings)
    if remote_local_ai is not None:
        # Phase 22 — the BYO-Ollama bridge is a NEW top-level concept (never a
        # configuredProvider value). The key is OMITTED entirely when the
        # feature is disabled so the existing byte-identical DTO stays.
        body["remoteLocalAi"] = remote_local_ai
    return body