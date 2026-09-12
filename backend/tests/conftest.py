"""Shared fixtures for the backend test suite.

Every test gets its own temporary SQLite file under tmp_path, injected through
``Settings`` (the app's single configuration source) and ``TestClient``.
Migrations are applied programmatically via the Alembic command API.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config as AlembicConfig

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"

DEFAULT_CORS = ["http://localhost:5173"]


def make_alembic_config(database_url: str) -> AlembicConfig:
    cfg = AlembicConfig(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def upgrade_db(database_url: str) -> None:
    command.upgrade(make_alembic_config(database_url), "head")


def downgrade_db(database_url: str) -> None:
    command.downgrade(make_alembic_config(database_url), "base")


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.db"


@pytest.fixture
def database_url(db_path):
    return f"sqlite:///{db_path.as_posix()}"


@pytest.fixture
def settings(database_url):
    from app.core.config import Settings

    return Settings(database_url=database_url)


@pytest.fixture
def app(database_url):
    """Unmigrated application (no DB/readiness guarantees)."""
    from app.core.config import Settings
    from app.main import create_app

    application = create_app(
        Settings(database_url=database_url, cors_allowed_origins=DEFAULT_CORS)
    )
    yield application
    application.state.engine.dispose()


@pytest.fixture
def migrated_app(database_url):
    """Application whose database has been migrated to head."""
    upgrade_db(database_url)
    from app.core.config import Settings
    from app.main import create_app

    application = create_app(
        Settings(database_url=database_url, cors_allowed_origins=DEFAULT_CORS)
    )
    yield application
    application.state.engine.dispose()


@pytest.fixture
def client(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def migrated_client(migrated_app):
    from fastapi.testclient import TestClient

    with TestClient(migrated_app) as test_client:
        yield test_client


@pytest.fixture
def scratch_app():
    """Minimal FastAPI with only the structured-error handlers registered.

    Used to exercise 422/500 error paths end-to-end without adding routes to
    the production application surface.
    """
    from fastapi import FastAPI

    from app.main import register_exception_handlers

    application = FastAPI(title="scratch")
    register_exception_handlers(application)
    return application


# ---------------------------------------------------------------------------
# Phase 4, test 30 — ZERO (external) network calls across the mandatory
# backend suite.
# ---------------------------------------------------------------------------


def _host_is_loopback(host: object) -> bool:
    """True for loopback addresses (127.0.0.0/8, ::1).

    In-process plumbing ONLY (e.g. anyio's event-loop self-pipe socketpair on
    Windows) uses loopback; everything else is treated as external network and
    is forbidden.
    """
    if host is None:
        return False
    import ipaddress

    try:
        return ipaddress.ip_address(str(host)).is_loopback
    except ValueError:
        # A hostname can never be proven local -> treat as external.
        return False


def _socket_peer_is_loopback(sock) -> bool:
    try:
        peer = sock.getpeername()
    except OSError:
        return False
    if isinstance(peer, tuple) and peer:
        return _host_is_loopback(peer[0])
    return False


# DNS names the event-loop / in-process plumbing may resolve (ADV-131): any
# other hostname resolution is treated as an external network attempt.
_ALLOWED_DNS_NAMES = frozenset({"localhost", "127.0.0.1", "::1"})


@pytest.fixture(autouse=True)
def _block_network(monkeypatch):
    """Autouse guarantee: no backend test may open an EXTERNAL socket.

    ``connect`` / ``connect_ex`` raise for any non-loopback destination; the
    same applies to ``sendall``/``sendto`` unless the socket's peer is
    loopback; ``getaddrinfo`` raises for any host except the loopback literals
    ``localhost`` / ``127.0.0.1`` / ``::1`` (ADV-131). FastAPI's ``TestClient``
    is in-process ASGI (anyio's Windows proactor self-pipe uses an internal
    LOOPBACK socketpair, which stays allowed); SQLite is plain file I/O. An
    accidental external provider/HTTP call fails with a loud assertion.
    """
    import socket as _socket

    _orig_connect = _socket.socket.connect
    _orig_connect_ex = _socket.socket.connect_ex
    _orig_sendall = _socket.socket.sendall
    _orig_sendto = _socket.socket.sendto
    _orig_getaddrinfo = _socket.getaddrinfo

    def _guarded_getaddrinfo(host, port, family=0, type=0, proto=0, flags=0):
        if host is None or str(host) not in _ALLOWED_DNS_NAMES:
            raise AssertionError("network call attempted in test suite")
        return _orig_getaddrinfo(host, port, family, type, proto, flags)

    def _guarded_connect(sock, address):
        host = address[0] if isinstance(address, tuple) and address else address
        if not _host_is_loopback(host):
            raise AssertionError("network call attempted in test suite")
        return _orig_connect(sock, address)

    def _guarded_connect_ex(sock, address):
        host = address[0] if isinstance(address, tuple) and address else address
        if not _host_is_loopback(host):
            raise AssertionError("network call attempted in test suite")
        return _orig_connect_ex(sock, address)

    def _guarded_sendall(sock, data, flags=0):
        if not _socket_peer_is_loopback(sock):
            raise AssertionError("network call attempted in test suite")
        return _orig_sendall(sock, data, flags)

    def _guarded_sendto(sock, data, address):
        host = address[0] if isinstance(address, tuple) and address else None
        if not _host_is_loopback(host):
            raise AssertionError("network call attempted in test suite")
        return _orig_sendto(sock, data, address)

    monkeypatch.setattr(_socket.socket, "connect", _guarded_connect)
    monkeypatch.setattr(_socket.socket, "connect_ex", _guarded_connect_ex)
    monkeypatch.setattr(_socket.socket, "sendall", _guarded_sendall)
    monkeypatch.setattr(_socket.socket, "sendto", _guarded_sendto)
    monkeypatch.setattr(_socket, "getaddrinfo", _guarded_getaddrinfo)
    yield