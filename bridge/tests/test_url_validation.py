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