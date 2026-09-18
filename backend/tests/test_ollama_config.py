"""Phase16 config tests: OLLAMA_* settings, URL validation matrix, bounds and
the unchanged live-provider HTTPS-only policy (Phase16 K items 19 + extras)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import (  # noqa: E402
    DEFAULT_OLLAMA_BASE_URL,
    Settings,
    is_allowed_ollama_host,
)

ACCEPTED_URLS = (
    "http://127.0.0.1:11434",
    "https://127.0.0.1:11434",  # http AND https are both allowed for Ollama
    "http://localhost:11434",
    "http://[::1]:11434",
    "http://[fd00::1]:11434",  # IPv6 ULA (private)
    "http://10.0.0.5:11434",  # 10/8
    "http://172.16.0.1:11434",  # 172.16/12
    "http://172.20.10.2:11434",  # inside 172.16/12 (Docker bridge)
    "http://192.168.1.50:11434",  # 192.168/16
    "http://host.docker.internal:11434",  # Docker Desktop
    "http://192.168.1.50",  # optional port
    "http://127.0.0.1",  # optional port + no trailing slash
)

REJECTED_URLS = (
    "file:///etc/passwd",
    "javascript:alert(1)",
    "data:text/plain,x",
    "gopher://127.0.0.1:70",
    "http://user:pass@127.0.0.1:11434",  # embedded credentials
    "http://127.0.0.1:11434?x=1",  # query
    "http://127.0.0.1:11434#frag",  # fragment
    "http://127.0.0.1:11434/api",  # non-root path
    "http://evil.example.com:11434",  # public host
    "http://192.0.2.1:11434",  # TEST-NET (not private)
    "http://myhost.lan:11434",  # .lan hostname (NOT allowed)
    "http://ollama.local:11434",  # .local hostname (NOT allowed)
    "http://172.32.0.1:11434",  # outside 172.16/12
    "http://169.254.1.1:11434",  # IPv4 link-local (NOT in the private list)
    "http://[fe80::1]:11434",  # IPv6 link-local (explicitly rejected)
    "http:///path",  # no netloc
    "not a url",  # no scheme
    "http://",  # no netloc
    "http://8.8.8.8:11434",  # public literal
)


def test_defaults_are_safe():
    settings = Settings()
    assert settings.generation_provider == "fake"
    assert settings.ollama_base_url is None  # default -> documented local URL
    assert settings.ollama_model == "llama3.2:3b"
    assert settings.ollama_timeout_seconds == 60.0
    assert settings.ollama_temperature == 0.2
    assert settings.ollama_num_ctx == 4096
    assert DEFAULT_OLLAMA_BASE_URL == "http://127.0.0.1:11434"


def test_generation_provider_accepts_ollama():
    settings = Settings(generation_provider="ollama")
    assert settings.generation_provider == "ollama"


def test_generation_provider_rejects_unknown_modes():
    with pytest.raises(ValidationError):
        Settings(generation_provider="cloud")  # type: ignore[arg-type]


def test_ollama_base_url_accepts_local_lan_matrix():
    for url in ACCEPTED_URLS:
        settings = Settings(ollama_base_url=url)
        assert settings.ollama_base_url == url


def test_ollama_base_url_rejects_hostile_matrix():
    for url in REJECTED_URLS:
        with pytest.raises(ValidationError):
            Settings(ollama_base_url=url)


def test_ollama_base_url_empty_string_is_treated_as_none():
    assert Settings(ollama_base_url="").ollama_base_url is None
    assert Settings(ollama_base_url="  ").ollama_base_url is None


def test_allowed_host_helper_matrix():
    for host in (
        "localhost",
        "LOCALHOST",
        "host.docker.internal",
        "127.0.0.1",
        "10.1.2.3",
        "172.20.1.1",
        "192.168.0.1",
        "::1",
        "fd00::1",
    ):
        assert is_allowed_ollama_host(host), host
    for host in (
        "8.8.8.8",
        "1.2.3.4",
        "example.com",
        "peer.lan",
        "peer.local",
        "192.0.2.1",
        "169.254.1.1",
        "fe80::1",
    ):
        assert not is_allowed_ollama_host(host), host


def test_ollama_model_charset_and_bounds():
    Settings(ollama_model="llama3.2:3b")
    Settings(ollama_model="my-model.latest:tag")
    for bad in ("", "a" * 81, "bad model!", "model/name", "javascript:alert(1)"):
        with pytest.raises(ValidationError):
            Settings(ollama_model=bad)


def test_ollama_timeout_bounds():
    assert Settings(ollama_timeout_seconds=5).ollama_timeout_seconds == 5
    assert Settings(ollama_timeout_seconds=300).ollama_timeout_seconds == 300
    for bad in (4.9, 301, -1, 0):
        with pytest.raises(ValidationError):
            Settings(ollama_timeout_seconds=bad)


def test_ollama_temperature_bounds():
    assert Settings(ollama_temperature=0.0).ollama_temperature == 0.0
    assert Settings(ollama_temperature=2.0).ollama_temperature == 2.0
    for bad in (-0.01, 2.01):
        with pytest.raises(ValidationError):
            Settings(ollama_temperature=bad)


def test_ollama_num_ctx_bounds():
    assert Settings(ollama_num_ctx=512).ollama_num_ctx == 512
    assert Settings(ollama_num_ctx=32768).ollama_num_ctx == 32768
    for bad in (511, 32769, "big"):
        with pytest.raises(ValidationError):
            Settings(ollama_num_ctx=bad)  # type: ignore[arg-type]


def test_live_provider_remains_https_only():
    """Phase16 K item 19: the LIVE provider stays HTTPS-only — an http://
    base for LIVE is rejected at configuration time (unchanged policy)."""
    with pytest.raises(ValidationError):
        Settings(
            generation_provider="live",
            live_provider_url="http://api.example.com/v1/chat/completions",
        )
    ok = Settings(
        generation_provider="live",
        live_provider_url="https://api.example.com/v1/chat/completions",
    )
    assert ok.live_provider_url.startswith("https://")


def test_ollama_url_never_affects_live_policy():
    """Configuring the local Ollama URL must not loosen the live https rule."""
    with pytest.raises(ValidationError):
        Settings(
            generation_provider="live",
            ollama_base_url="http://127.0.0.1:11434",
            live_provider_url="http://api.example.com/v1/chat/completions",
        )