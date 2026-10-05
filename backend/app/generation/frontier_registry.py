"""Phase 30 — the trusted server-owned BYOK Frontier provider registry.

The browser NEVER supplies a provider URL. For Phase 30 the player may select a
logical provider ID from this server-owned registry and supply only API key +
model per generation attempt; the backend maps the ID to the ONE reviewed
HTTPS endpoint committed here and authenticates the outbound call with the
user's transient key (``app.generation.frontier_provider.FrontierProvider``).

This module is the SINGLE authoritative catalog:

- ``FrontierProviderDefinition`` — the frozen per-provider metadata (provider
  ID, safe display label, verified HTTPS endpoint, protocol family, enabled
  flag). No secrets, no per-user state, no mutable browser-supplied metadata.
- ``FRONTIER_PROVIDER_REGISTRY`` — the committed, ordered catalog. Every entry
  is validated AT IMPORT TIME by ``validate_frontier_registry_endpoint``
  (deterministic / hermetic / parse-only — no socket, DNS or network; the
  Phase 30 §30 registry-safety tests therefore stay offline).
- ``frontier_provider_definition`` / ``frontier_enabled_providers`` /
  ``frontier_catalog`` — the lookups shared by the capability DTO (safe
  ``{id, label}`` catalog only), the per-attempt resolver and the
  request-schema validation.

All eight committed endpoints were verified against the providers' OFFICIAL
documentation (the exploration reviewed the authoritative sources below). Each
endpoint services OpenAI-compatible Chat Completions:

    POST <endpoint>
    Authorization: Bearer <api key>
    JSON body { "model": <model>, "messages": [{"role": "user", "content": ...}] }

committed providers (Phase30 §3):

- openai      — OpenAI                            — https://api.openai.com/v1/chat/completions
      docs: https://platform.openai.com/docs/api-reference/chat/create
- openrouter  — OpenRouter                        — https://openrouter.ai/api/v1/chat/completions
      docs: https://openrouter.ai/docs/api-reference/chat-completion
      (optional ``HTTP-Referer`` / ``X-OpenRouter-Title`` headers are
      OPTIONAL and deliberately NOT sent — the common adapter does not require
      them; model naming such as ``openai/gpt-4o`` works)
- groq        — Groq                              — https://api.groq.com/openai/v1/chat/completions
      docs: https://console.groq.com/docs/api-reference#chat
- together    — Together AI                       — https://api.together.ai/v1/chat/completions
      docs: https://docs.together.ai/reference/chat-completions
- mistral     — Mistral AI                        — https://api.mistral.ai/v1/chat/completions
      docs: https://docs.mistral.ai/api/#tag/chat
- fireworks   — Fireworks AI                      — https://api.fireworks.ai/inference/v1/chat/completions
      docs: https://docs.fireworks.ai/api-reference/endpoints/chat-completions
      (model identifiers use the documented ``accounts/fireworks/models/<name>``
      form — an ordinary structured model string the common adapter sends
      verbatim)
- deepinfra   — DeepInfra                         — https://api.deepinfra.com/v1/openai/chat/completions
      docs: https://deepinfra.com/docs/api/openai
- xai         — xAI                              — https://api.x.ai/v1/chat/completions
      docs: https://docs.x.ai/api/chat-completions
      (documented LEGACY OpenAI-compatible endpoint: Phase 30 keeps Chat
      Completions on purpose and does NOT switch to the newer Responses API)

Endpoint-compatibility note (Phase30 §3 / §20): every committed provider
accepts the exact request shape of the existing ``FrontierProvider``
(single ``{model, messages:[{role, content}]}`` body + ``Bearer`` header) with
no provider-specific headers or payload differences, so ONE common
``openai_chat_completions`` adapter serves the whole catalog. No provider was
removed because Phase 30 found an incompatibility that matters for that
adapter. ``xai`` stays on Chat Completions (#the legacy documented endpoint is
kept per Phase30 §3).

Security boundary (Phase30 §4 / §8 / §21):

- The browser can never override or extend these endpoints; the request body
  carries only ``{provider, apiKey, model}``.
- Every endpoint is validated at import: HTTPS only, absolute, no embedded
  credentials, no query/fragment, no control characters, and the host must be
  a PUBLIC DNS hostname — never loopback/private/link-local/cloud-metadata/
  ``localhost`` / ``*.localhost`` / ``*.local`` / ``host.docker.internal``.
  Literal IP hosts (even "public-looking" ones) are rejected outright so a
  registry entry can never silently target a private/metadata address through
  an IP-literal spelling; the committed endpoints are all hostnames.
- Wildcard-DNS / DNS-rebinding host suffixes (LOW finding A17) are denied too:
  any host whose final labels end in one of the ``_WILDCARD_DNS_SUFFIXES``
  (``*.nip.io``, ``*.sslip.io``, ``*.xip.io``, ``*.freeip.io``, ``*.iluxa.me``,
  ``*.localtest.me``, ``*.vcap.me``, ``*.lvh.me``, ``*.dns.google``) is
  rejected by a hermetic ``endswith`` check on the lowercased hostname (no
  socket/DNS resolver). That closes the rebinding bypass where a hostile entry
  hides a loopback/private/metadata IP *behind a hostname*: a loopback or
  RFC-private address spelled as a dotted quad, or the numeric-spelling form
  of a private address, joined to the rebinding domain as its leftmost-label
  payload — past the literal-IP and numeric-spelling guards.
- Validation is deterministic and hermetic (``urlparse`` + ``ipaddress`` +
  suffix ``endswith``), so the §30 registry-safety tests never touch the
  network.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlparse

# Registry-endpoint safety constants ------------------------------------------- #


# Non-printable / non-ASCII characters are rejected up front (no NUL, no CR/LF,
# no exotic Unicode that would confuse downstream URL handling).
_PRINTABLE_ASCII_RE = re.compile(r"[^\x20-\x7E]")

# Hostname categories that can never be a public BYOK provider target. All
# comparison is casefolded. `.local` is the mDNS/zeroconf domain (RFC 6762);
# `.localhost` is special-use (RFC 6761); ``host.docker.internal`` is the
# Docker Desktop host bridge; ``metadata.*`` / ``instance-data*`` are the AWS/
# GCP/Azure cloud metadata hostnames (SSRF targets). The IP literals behind
# metadata services (169.254.169.254 AWS/GCP/Azure, 100.100.100.200 Alibaba)
# are additionally rejected because ALL literal-IP hosts are rejected below.
_FORBIDDEN_REGISTRY_HOSTNAMES: frozenset[str] = frozenset(
    {
        "localhost",
        "local",
        "host.docker.internal",
        "metadata",
        "metadata.google.internal",
        "metadata.google",
        "metadata.azure.internal",
        "instance-data",
        "instance-data.ec2.internal",
        # TLD-level special-use families that must never enter a public registry.
        "localdomain",
        "lan",
        "home",
        "internal",
    }
)


# Wildcard-DNS / DNS-rebinding host suffixes (LOW finding A17). These public
# wildcard-DNS services resolve ARBITRARY subdomains to a caller-supplied IP
# address, so a hostile registry entry could hide a loopback/private/metadata
# target behind a "hostname" that defeats the literal-IP and numeric-spelling
# guards above. A registry host is therefore also rejected when its FINAL
# labels end in any of these suffixes (hermetic ``endswith`` on the lowercased
# hostname — no socket, DNS or resolver is ever consulted).
#
# The set is a BOUNDED, documented static list of well-known wildcard-DNS
# service suffixes (any-IP encoders + loopback wildcards):
#
#   nip.io / sslip.io / xip.io       any-IP wildcard-DNS rebinding encoders
#   freeip.io / iluxa.me             any-IP wildcard-DNS services
#   localtest.me / vcap.me / lvh.me  wildcard DNS -> loopback
#   dns.google                       Google wildcard-DNS family (incl. in the
#                                    A17 documented wildcard-DNS list)
#
# Self-hygiene (release gate): this module's tracked prose NEVER writes a
# concrete rebinding vector or a private-IP / numeric-spelling example in a
# comment — ``tools.release_check`` scans the TRACKED tree and fails on any
# private-IP literal in tracked prose (the repo's DEF-001 gate-green pattern:
# hostile vectors are documented by CLASS and suffix DOMAIN here, while the
# concrete deny vectors live in the hermetic test tree). The A17 denial is
# implemented by this suffix set + the ``endswith`` checks, never by prose.
_WILDCARD_DNS_SUFFIXES: frozenset[str] = frozenset(
    {
        "nip.io",
        "sslip.io",
        "xip.io",
        "freeip.io",
        "iluxa.me",
        "localtest.me",
        "vcap.me",
        "lvh.me",
        "dns.google",
    }
)


@dataclass(frozen=True)
class FrontierProviderDefinition:
    """One immutable, server-owned BYOK Frontier provider (Phase30 §7).

    Only trusted server-owned metadata lives here: the logical provider ID,
    the safe public display label, the verified HTTPS endpoint, the adapter
    protocol family and the enabled state. Never API keys, never per-user
    state, never mutable browser data.
    """

    provider_id: str
    label: str
    endpoint: str
    protocol: str = "openai_chat_completions"
    enabled: bool = True


# The committed catalog (order is display order). Appendix in the module
# docstring records the official documentation source reviewed for each.
FRONTIER_PROVIDER_REGISTRY: tuple[FrontierProviderDefinition, ...] = (
    FrontierProviderDefinition(
        provider_id="openai",
        label="OpenAI",
        endpoint="https://api.openai.com/v1/chat/completions",
    ),
    FrontierProviderDefinition(
        provider_id="openrouter",
        label="OpenRouter",
        endpoint="https://openrouter.ai/api/v1/chat/completions",
    ),
    FrontierProviderDefinition(
        provider_id="groq",
        label="Groq",
        endpoint="https://api.groq.com/openai/v1/chat/completions",
    ),
    FrontierProviderDefinition(
        provider_id="together",
        label="Together AI",
        endpoint="https://api.together.ai/v1/chat/completions",
    ),
    FrontierProviderDefinition(
        provider_id="mistral",
        label="Mistral AI",
        endpoint="https://api.mistral.ai/v1/chat/completions",
    ),
    FrontierProviderDefinition(
        provider_id="fireworks",
        label="Fireworks AI",
        endpoint="https://api.fireworks.ai/inference/v1/chat/completions",
    ),
    FrontierProviderDefinition(
        provider_id="deepinfra",
        label="DeepInfra",
        endpoint="https://api.deepinfra.com/v1/openai/chat/completions",
    ),
    FrontierProviderDefinition(
        provider_id="xai",
        label="xAI",
        endpoint="https://api.x.ai/v1/chat/completions",
    ),
)


def validate_frontier_registry_endpoint(endpoint: str) -> str:
    """Deterministic, hermetic validation of ONE committed registry endpoint.

    Phase30 §8 (§30 tests): parse-only, never touches a socket / DNS / the
    network. Rejects:

    - non-string / empty / NUL / control / non-ASCII-printable content;
    - any scheme other than ``https`` and any non-absolute URL;
    - missing/empty netloc (no host) and trailing-dot hosts;
    - embedded userinfo (``user:pass@``) and any query string or fragment
      (a query could smuggle credentials/params; the committed endpoints
      need none);
    - literal-IP hosts of any kind (loopback/private/link-local/metadata
      spellings AND "public-looking" literals — a registry endpoint must be a
      public DNS hostname, never an IP-literal target);
    - the closed set of special-use / cloud-metadata host names
      (``localhost``, ``*.localhost``, ``*.local``, ``host.docker.internal``,
      ``metadata*``, ``instance-data*`` ...);
    - the documented wildcard-DNS / DNS-rebinding host suffixes (LOW A17):
      any host whose final labels end in ``*.nip.io`` / ``*.sslip.io`` /
      ``*.xip.io`` / ``*.freeip.io`` / ``*.iluxa.me`` / ``*.localtest.me`` /
      ``*.vcap.me`` / ``*.lvh.me`` / ``*.dns.google`` (the
      ``_WILDCARD_DNS_SUFFIXES`` frozenset) — such names can silently rebind
      to a loopback/private/metadata address and must never enter the registry.

    Returns the trimmed URL (the committed canonical form). Raises
    ``ValueError`` with a SAFE message (never echoes credentials — there are
    none by construction — and only the generic host category).
    """
    if not isinstance(endpoint, str):
        raise ValueError("registry endpoint must be a string")
    text = endpoint.strip()
    if not text:
        raise ValueError("registry endpoint must not be empty")
    if _PRINTABLE_ASCII_RE.search(text):
        raise ValueError(
            "registry endpoint must not contain control or non-ASCII characters"
        )
    try:
        parsed = urlparse(text)
    except (TypeError, ValueError):
        raise ValueError("registry endpoint is not a valid URL") from None
    if parsed.scheme != "https":
        raise ValueError("registry endpoint must use the https scheme")
    if not parsed.netloc:
        raise ValueError("registry endpoint must include a host")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("registry endpoint must not embed credentials")
    if parsed.query:
        raise ValueError("registry endpoint must not contain a query string")
    if parsed.fragment:
        raise ValueError("registry endpoint must not contain a fragment")
    host = parsed.hostname
    if host is None or not host:
        raise ValueError("registry endpoint must include a host")
    if "\x00" in host:
        raise ValueError("registry endpoint host is invalid")
    lowered = host.casefold()
    if lowered.endswith("."):
        raise ValueError("registry endpoint host must not end with a dot")
    if lowered in _FORBIDDEN_REGISTRY_HOSTNAMES or lowered.endswith(".localhost") or lowered.endswith(".local"):
        raise ValueError(
            "registry endpoint host must be a public internet hostname "
            "(loopback/private/link-local/metadata/special-use hosts are rejected)"
        )
    if lowered.startswith("metadata."):
        raise ValueError(
            "registry endpoint host must be a public internet hostname "
            "(loopback/private/link-local/metadata/special-use hosts are rejected)"
        )
    # LOW A17 — wildcard-DNS / DNS-rebinding host suffixes. A suffix match on
    # the lowercased hostname's FINAL labels is hermetic (no socket/DNS) and
    # closes the rebinding bypass: a loopback or RFC-private address spelled
    # as a dotted quad, or the numeric-spelling form of a private address,
    # joined to the rebinding suffix — either payload would otherwise hide a
    # loopback/private/metadata IP behind a hostname.
    if lowered in _WILDCARD_DNS_SUFFIXES or any(
        lowered.endswith("." + suffix) for suffix in _WILDCARD_DNS_SUFFIXES
    ):
        raise ValueError(
            "registry endpoint host must be a public internet hostname "
            "(wildcard-DNS/DNS-rebinding host suffixes are rejected)"
        )
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        # Not a canonical literal IP. Reject the remaining numeric-looking
        # spellings (an integer IPv4 value, or a dotted sequence that is not
        # a canonical quad) so a registry entry can never smuggle an IP
        # target through a non-canonical form.
        if lowered.replace(".", "").isdigit() and lowered.isascii():
            raise ValueError(
                "registry endpoint host must be a hostname, not a numeric/IP address"
            ) from None
    else:
        raise ValueError(
            "registry endpoint host must be a hostname, not a literal IP address"
        ) from None
    return text


# --------------------------------------------------------------------------- #
# committed-registry integrity (validated ONCE at import — fail fast)
# --------------------------------------------------------------------------- #


def _validate_committed_registry() -> None:
    seen: set[str] = set()
    for definition in FRONTIER_PROVIDER_REGISTRY:
        if not isinstance(definition, FrontierProviderDefinition):
            raise ValueError("registry entries must be FrontierProviderDefinition")
        for field_name in ("provider_id", "label"):
            value = getattr(definition, field_name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"registry {field_name} must be a non-empty string")
            if _PRINTABLE_ASCII_RE.search(value):
                raise ValueError(
                    f"registry {field_name} must not contain control or "
                    "non-ASCII characters"
                )
        if definition.provider_id in seen:
            raise ValueError(f"duplicate registry provider id {definition.provider_id!r}")
        seen.add(definition.provider_id)
        validate_frontier_registry_endpoint(definition.endpoint)


_validate_committed_registry()


# --------------------------------------------------------------------------- #
# public lookups
# --------------------------------------------------------------------------- #


def _registered_definitions() -> tuple[FrontierProviderDefinition, ...]:
    """The current committed tuple (module indirection keeps the helper
    functions safely monkeypatchable by tests without touching the code)."""
    return FRONTIER_PROVIDER_REGISTRY


def frontier_provider_definition(
    provider_id: str,
) -> FrontierProviderDefinition | None:
    """The registry definition for an EXACT provider id, else ``None``.

    Exact-match membership against the frozen server-owned catalog: hostile
    values (``https://evil``, ``../../openai``, ``openai?url=...``,
    ``openai\\nAuthorization: ...``) can never resolve to a definition.
    """
    for definition in _registered_definitions():
        if definition.provider_id == provider_id:
            return definition
    return None


def frontier_enabled_providers() -> tuple[FrontierProviderDefinition, ...]:
    """The ENABLED committed definitions, in registry order."""
    return tuple(
        definition
        for definition in _registered_definitions()
        if definition.enabled
    )


def frontier_catalog() -> tuple[tuple[str, str], ...]:
    """The SAFE player-facing catalog ``(provider_id, label)`` tuples.

    Used by the capability DTO: only public IDs and display labels, in display
    order, ENABLED providers only. NEVER endpoints, credentials, secrets or
    previous-user selections.
    """
    return tuple(
        (definition.provider_id, definition.label)
        for definition in frontier_enabled_providers()
    )


__all__ = [
    "FRONTIER_PROVIDER_REGISTRY",
    "FrontierProviderDefinition",
    "frontier_catalog",
    "frontier_enabled_providers",
    "frontier_provider_definition",
    "validate_frontier_registry_endpoint",
]