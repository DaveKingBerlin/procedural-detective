import pytest

from pd_ollama_bridge.urls import (
    UrlValidationError,
    resolve_server_ws_url,
    validate_ollama_url,
)


def test_server_https_maps_to_wss():
    assert (
        resolve_server_ws_url("https://detective.example.com")
        == "wss://detective.example.com/api/v1/bridge/ws"
    )


def test_server_with_port():
    assert (
        resolve_server_ws_url("https://detective.example.com:8443")
        == "wss://detective.example.com:8443/api/v1/bridge/ws"
    )


def test_server_localhost_http_allowed():
    assert resolve_server_ws_url("http://127.0.0.1:8000").startswith("ws://127.0.0.1:8000")


def test_server_remote_http_rejected():
    with pytest.raises(UrlValidationError):
        resolve_server_ws_url("http://detective.example.com")


def test_server_remote_ws_url_rejected():
    """A non-local ws:// server is rejected (plain ws is accepted ONLY for
    loopback/development; remote bridge traffic must stay HTTPS/WSS)."""
    with pytest.raises(UrlValidationError):
        resolve_server_ws_url("ws://detective.example.com")
    with pytest.raises(UrlValidationError):
        resolve_server_ws_url("ws://192.168.178.48")


def test_server_url_with_path_rejected():
    with pytest.raises(UrlValidationError):
        resolve_server_ws_url("https://detective.example.com/something")


def test_server_url_with_credentials_rejected():
    with pytest.raises(UrlValidationError):
        resolve_server_ws_url("https://user:pass@detective.example.com")


def test_server_url_scheme_rejected():
    with pytest.raises(UrlValidationError):
        resolve_server_ws_url("ftp://detective.example.com")


def test_ollama_loopback_allowed():
    for url in (
        "http://127.0.0.1:11434",
        "http://localhost:11434",
        "http://localhost",
    ):
        assert validate_ollama_url(url) == url


def test_ollama_ipv6_loopback_allowed():
    assert validate_ollama_url("http://[::1]:11434") == "http://[::1]:11434"


def test_ollama_lan_host_rejected_by_default():
    with pytest.raises(UrlValidationError):
        validate_ollama_url("http://192.168.1.10:11434")


def test_ollama_lan_host_opt_in():
    assert (
        validate_ollama_url("http://192.168.1.10:11434", allow_lan=True)
        == "http://192.168.1.10:11434"
    )


def test_ollama_arbitrary_internet_host_rejected():
    with pytest.raises(UrlValidationError):
        validate_ollama_url("http://evil.example.com:11434")


def test_ollama_bad_scheme_rejected():
    with pytest.raises(UrlValidationError):
        validate_ollama_url("ftp://127.0.0.1:11434")
    with pytest.raises(UrlValidationError):
        validate_ollama_url("file:///etc/passwd")


def test_ollama_credentials_rejected():
    with pytest.raises(UrlValidationError):
        validate_ollama_url("http://user:pass@127.0.0.1:11434")


def test_ollama_path_rejected():
    with pytest.raises(UrlValidationError):
        validate_ollama_url("http://127.0.0.1:11434/api")


def test_ollama_remote_url_never_accepted_from_server():
    for hostile in (
        "http://server.example.com:1234",
        "http://169.254.169.254:11434",
        "wss://evil.example.com/ws",
    ):
        with pytest.raises(UrlValidationError):
            validate_ollama_url(hostile)


# --------------------------------------------------------------------------- #
# F1 — junk/control characters in the netloc/port must fail CLEANLY (the
# typed UrlValidationError), never reach httpx as an unhandled InvalidURL.
# Loopback policy is untouched; only the character/port shape is tightened.
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "junk",
    [
        'http://127.0.0.1:11434"',  # quote in the port
        "http://127.0.0.1:11434;--",  # trailing junk after the port
        "http://127.0.0.1:11434'",
        "http://127.0.0.1:11434`",
    ],
)
def test_ollama_junk_port_rejected(junk):
    with pytest.raises(UrlValidationError):
        validate_ollama_url(junk)


@pytest.mark.parametrize(
    "junk",
    [
        "http://127.0.0.1:11434\nx=1",
        "http://127.0.0.1:11434\r\nx=1",
        "http://127.0.0.1:11434\tx=1",
        "http://127.0.0.1:11434 ",
        "http://127.0.0.1:11434\x0c",
        "http://127.0.0.1:11434\x00",
        'http://127.0.0.1:11434"',
    ],
)
def test_ollama_control_or_whitespace_rejected(junk):
    """Control chars/quotes/whitespace are rejected even where ``str.strip()``
    (or urllib.parse) would previously have hidden them."""
    with pytest.raises(UrlValidationError):
        validate_ollama_url(junk)


@pytest.mark.parametrize(
    "junk",
    [
        "http://127.0.0.1:11434x",
        "http://127.0.0.1:11434abc",
        "http://127.0.0.1:65536",
        "http://127.0.0.1:99999999",
    ],
)
def test_ollama_malformed_port_rejected(junk):
    with pytest.raises(UrlValidationError):
        validate_ollama_url(junk)


def test_ollama_port_zero_rejected():
    with pytest.raises(UrlValidationError):
        validate_ollama_url("http://127.0.0.1:0")


def test_ollama_valid_ports_still_accepted():
    assert validate_ollama_url("http://127.0.0.1:1") == "http://127.0.0.1:1"
    assert validate_ollama_url("http://localhost:65535") == "http://localhost:65535"
    assert validate_ollama_url("http://127.0.0.1:11434") == "http://127.0.0.1:11434"
    assert validate_ollama_url("http://[::1]:11434") == "http://[::1]:11434"


@pytest.mark.parametrize(
    "junk",
    [
        "http://127.0.0.1:8000\x0c",
        "ws://127.0.0.1:8000\x0b",
        "http://127.0.0.1:8000\n",
        'http://127.0.0.1:8000"',
        "http://127.0.0.1:8000 ",
        " https://detective.example.com",
    ],
)
def test_server_url_control_chars_or_whitespace_rejected(junk):
    with pytest.raises(UrlValidationError):
        resolve_server_ws_url(junk)


@pytest.mark.parametrize(
    "junk",
    [
        "ws://127.0.0.1:8000x",
        "https://detective.example.com:65536",
        "https://detective.example.com:notaport",
        "https://detective.example.com:0",
    ],
)
def test_server_url_malformed_port_rejected(junk):
    with pytest.raises(UrlValidationError):
        resolve_server_ws_url(junk)


def test_server_url_valid_ports_still_accepted():
    assert (
        resolve_server_ws_url("https://detective.example.com:8443")
        == "wss://detective.example.com:8443/api/v1/bridge/ws"
    )
    assert resolve_server_ws_url("http://127.0.0.1:8000").startswith("ws://127.0.0.1:8000")


def test_remote_ws_and_http_still_rejected():
    """F1 must NOT loosen the remote plain-ws/http rejection."""
    for hostile in (
        "ws://detective.example.com",
        "http://detective.example.com",
        "ws://192.168.178.48",
    ):
        with pytest.raises(UrlValidationError):
            resolve_server_ws_url(hostile)