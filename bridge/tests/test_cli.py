"""CLI-level smoke tests that require no network (fail fast on bad input)."""

from pd_ollama_bridge.cli import main


def test_help_exits_zero(capsys):
    import pytest

    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0


def test_connect_bad_pairing_code_exits_one(capsys):
    assert main(["connect", "not-a-code", "--memory-only"]) == 1
    err = capsys.readouterr().err
    assert "pairing code" in err


def test_connect_remote_http_server_rejected(capsys):
    assert (
        main(
            [
                "connect",
                "PD-A2B3-C4D5",
                "--server",
                "http://detective.example.com",
                "--memory-only",
            ]
        )
        == 1
    )
    err = capsys.readouterr().err
    assert "ws://" in err or "server" in err


def test_connect_remote_ollama_rejected(capsys):
    assert (
        main(
            [
                "connect",
                "PD-A2B3-C4D5",
                "--server",
                "https://detective.example.com",
                "--ollama",
                "http://evil.example.com:11434",
                "--memory-only",
            ]
        )
        == 1
    )
    err = capsys.readouterr().err
    assert "localhost" in err


def test_list_models_bad_url(capsys):
    assert main(["list-models", "--ollama", "ftp://127.0.0.1:11434"]) == 1