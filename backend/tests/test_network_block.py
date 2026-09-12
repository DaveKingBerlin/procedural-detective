"""Network-block proofs (Phase4 K item 30).

The autouse fixture in ``conftest.py`` patches ``socket.socket.connect`` /
``connect_ex`` / ``sendall`` / ``sendto`` and ``socket.create_connection`` to
raise ``AssertionError("network call attempted in test suite")`` for EVERY
backend test. These tests prove the block is live and that neither the fake
provider nor an attempted live HTTP call can touch the network.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.generation.fake_provider import FakeProvider  # noqa: E402
from app.generation.live_provider import LiveHttpProvider  # noqa: E402
from app.generation.provider import (  # noqa: E402
    GenerateRequest,
    GenerationStage,
)

DENY_MESSAGE = "network call attempted in test suite"

# TEST-NET-1 (RFC 5737): never routed on the real internet; any connect to it
# in the test suite is by definition an unintended external network attempt.
BLOCKED_HOST = "192.0.2.1"


def test_socket_connect_is_blocked_by_autouse_fixture():
    with pytest.raises(AssertionError, match=DENY_MESSAGE):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.connect((BLOCKED_HOST, 80))
        sock.close()


def test_socket_connect_ex_and_sendall_are_blocked():
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with pytest.raises(AssertionError, match=DENY_MESSAGE):
        sock.connect_ex((BLOCKED_HOST, 80))
    with pytest.raises(AssertionError, match=DENY_MESSAGE):
        sock.sendall(b"x")  # unconnected socket -> never loopback -> blocked
    sock.close()


def test_create_connection_to_external_host_is_blocked():
    # IP literal so getaddrinfo needs no DNS; the socket.connect inside is the
    # blocked, guarded step.
    with pytest.raises(AssertionError, match=DENY_MESSAGE):
        socket.create_connection((BLOCKED_HOST, 80), timeout=0.1)


def test_fake_provider_makes_no_network_calls():
    """A fully scripted FakeProvider generation never touches sockets."""
    provider = FakeProvider(
        {
            GenerationStage.CASE_TRUTH: ['{"crime": null}'],
        }
    )
    result = provider.generate(
        GenerateRequest(
            attempt_id="GA-net",
            stage=GenerationStage.CASE_TRUTH,
            prompt_context="no network here",
        )
    )
    assert result.content == '{"crime": null}'
    assert len(provider.calls) == 1


def test_def044_getaddrinfo_external_host_is_blocked():
    """DEF-044 (ADV-131): DNS resolution of an external host is blocked too."""
    with pytest.raises(AssertionError, match=DENY_MESSAGE):
        socket.getaddrinfo("example.com", 80)
    with pytest.raises(AssertionError, match=DENY_MESSAGE):
        socket.getaddrinfo("203.0.113.7", 443)  # TEST-NET-3 literal


def test_def044_getaddrinfo_loopback_still_resolves():
    """DEF-044: in-process loopback plumbing (localhost) keeps resolving."""
    results = socket.getaddrinfo("localhost", 80)
    assert results
    assert any(
        info[4][0] in ("127.0.0.1", "::1") for info in results
    )


def test_def044_dns_and_socket_remain_blocked_together():
    """DEF-044: a URL-ish flow (getaddrinfo then connect) is blocked at DNS."""
    with pytest.raises(AssertionError, match=DENY_MESSAGE):
        socket.getaddrinfo("api.openrouter.ai", 443)
    # And even with a canned resolution the socket connect is still blocked.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with pytest.raises(AssertionError, match=DENY_MESSAGE):
        sock.connect((BLOCKED_HOST, 443))
    sock.close()


def test_live_provider_http_call_cannot_execute(monkeypatch):
    """A LiveHttpProvider call is guaranteed to hit the network block.

    ``getaddrinfo`` is stubbed to a canned TEST-NET address so the outcome is
    deterministic regardless of the host's DNS/network state: the transport
    MUST open a socket to a non-loopback peer and the autouse block raises.
    """
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (BLOCKED_HOST, 443))
        ],
    )
    provider = LiveHttpProvider(
        endpoint_url="https://provider.invalid/api",
        api_key="test-only-key",
        model="fake-model",
        timeout_seconds=0.5,
    )
    request = GenerateRequest(
        attempt_id="GA-net",
        stage=GenerationStage.CASE_TRUTH,
        prompt_context="context",
    )
    with pytest.raises(AssertionError, match=DENY_MESSAGE):
        provider.generate(request)