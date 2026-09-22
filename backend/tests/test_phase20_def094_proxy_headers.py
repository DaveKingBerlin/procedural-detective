"""DEF-094 (HIGH, PD-SEC-02) — forwarded-header authority on REAL uvicorn.

The app-level ``resolve_client_ip(request, trust_proxy=False)`` is correct, but
uvicorn's platform default ``--proxy-headers`` (trusting loopback ``127.0.0.1``)
rewrites ``scope["client"]`` from a hostile ``X-Forwarded-For`` BEFORE the ASGI
app runs — so every spoofed forwarded value used to mint its own per-IP budget
even with ``TRUST_PROXY=false`` (anonymous-session AND generation per-IP abuse).

The fix is the launcher flag ``--no-proxy-headers`` on EVERY documented uvicorn
launch path, making the app the SOLE authority over forwarded-header trust.
This suite pins that contract at three levels:

  1. launcher-flag presence — a future edit cannot drop ``--no-proxy-headers``
     from any documented launch artifact without failing here;
  2. LIVE real-uvicorn subprocess (exactly the documented dev command +
     ``--no-proxy-headers``) — 20 distinct hostile ``X-Forwarded-For`` values
     bind to ONE socket-peer identity: [[201 x4, 429 x16]] and a per-IP
     generation budget of 2/hour answers [[201, 201, 429]] under the same
     rotation;
  3. LIVE sensitivity control — the SAME battery WITHOUT the flag reproduces
     the DEF-094 vector (each spoofed value mints a fresh budget), proving the
     flag is load-bearing and the positive test is not vacuous.

Plus an in-process check that ``resolve_client_ip`` keeps the peer identity
and warns (exactly once per process) when a forwarded header arrives while
``TRUST_PROXY=false``.

Hermetic: scratch SQLite, GENERATION_PROVIDER=fake, subprocess env scrubbed of
operator keys; only loopback network (the suite's standing network policy).
"""

from __future__ import annotations

import copy
import http.client
import json
import logging
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core import ratelimit as ratelimit_module  # noqa: E402
from app.core.ratelimit import resolve_client_ip  # noqa: E402
from conftest import upgrade_db  # noqa: E402
from phase5_helpers import assert_sanitized_error  # noqa: E402

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent

# --------------------------------------------------------------------------- #
# level 1 — the flag must never disappear from a documented launch path
# --------------------------------------------------------------------------- #

# Every artifact that documents or executes a uvicorn launch must carry
# --no-proxy-headers. `docker-compose.prod.yml` launches uvicorn only through
# the image entrypoint (docker/entrypoint.sh), which is covered here; the
# compose file documents the TRUST_PROXY=true model separately.
_LAUNCH_ARTIFACTS = (
    "scripts/start-demo.ps1",
    "docker/entrypoint.sh",
    "README.md",
    "docs/DEPLOYMENT.md",
    "backend/app/main.py",  # documented run command in the module comment
)


@pytest.mark.parametrize("relative", _LAUNCH_ARTIFACTS)
def test_def094_documented_launch_paths_carry_no_proxy_headers(relative: str):
    """DEF-094 launcher-flag guard: every documented uvicorn launch path runs
    with ``--no-proxy-headers`` so uvicorn NEVER rewrites request.client from
    forwarded headers before the app's identity gate."""
    text = (REPO_ROOT / relative).read_text(encoding="utf-8", errors="replace")
    assert "--no-proxy-headers" in text, (
        f"{relative} must launch/document uvicorn with --no-proxy-headers "
        f"(DEF-094: without it, uvicorn's platform default --proxy-headers "
        f"rewrites request.client from a hostile X-Forwarded-For before the "
        f"app runs)"
    )


# --------------------------------------------------------------------------- #
# helpers — real uvicorn subprocess lifecycle on a free loopback port
# --------------------------------------------------------------------------- #


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _uvicorn_loopback_proxy_default() -> bool:
    """True when the installed uvicorn's PLATFORM DEFAULT would rewrite
    loopback ``request.client`` from forwarded headers (the DEF-094 hazard).
    The sensitivity control only makes sense while that hazard exists."""
    from uvicorn.config import Config

    config = Config(app="app.main:app")
    ips = config.forwarded_allow_ips
    if isinstance(ips, str):
        ips = [part.strip() for part in ips.split(",") if part.strip()]
    return bool(config.proxy_headers) and any(
        ip in ("127.0.0.1", "::1", "*") for ip in ips
    )


class _UvicornSubprocess:
    """A real ``python -m uvicorn app.main:app`` child on a free loopback
    port, launched exactly like the documented dev command (plus the
    ``--no-proxy-headers`` flag unless intentionally omitted for the control).
    """

    def __init__(
        self,
        database_url: str,
        log_path: Path,
        *,
        with_no_proxy_headers: bool = True,
    ) -> None:
        self.port = _free_port()
        self.log_path = log_path
        command = [
            sys.executable,
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
        ]
        if with_no_proxy_headers:
            command.append("--no-proxy-headers")
        env = copy.deepcopy(dict(os.environ))
        # Hermetic child: never read the operator .env; pin the fake provider
        # and the DEF-094 test limits; scrub values that could leak operator
        # config or redirect file logs into the repo.
        env["ENV_FILE"] = os.devnull
        env["GENERATION_PROVIDER"] = "fake"
        env["TRUST_PROXY"] = "false"
        env["DATABASE_URL"] = database_url
        env["ANON_SESSION_LIMIT_PER_IP_PER_10_MIN"] = "4"
        env["ANON_SESSION_GLOBAL_LIMIT_PER_MIN"] = "100"
        env["GENERATION_LIMIT_PER_IP_PER_HOUR"] = "2"
        env["ENVIRONMENT"] = "development"
        env["PD_DEV_TRACE"] = "false"
        env["PD_GENERATION_DEBUG_LOGS"] = "false"
        for key in ("STATIC_DIR", "PD_FILE_LOGS", "PD_LOG_FILE", "FORWARDED_ALLOW_IPS"):
            env.pop(key, None)
        self._log_handle = open(log_path, "wb")
        self._proc = subprocess.Popen(
            command,
            cwd=str(REPO_ROOT),
            env=env,
            stdout=self._log_handle,
            stderr=subprocess.STDOUT,
        )

    def wait_ready(self, deadline_seconds: float = 30.0) -> None:
        deadline = time.monotonic() + deadline_seconds
        while time.monotonic() < deadline:
            if self._proc.poll() is not None:
                raise AssertionError(
                    f"uvicorn exited early (rc={self._proc.returncode}); "
                    f"see {self.log_path}"
                )
            try:
                conn = http.client.HTTPConnection(
                    "127.0.0.1", self.port, timeout=0.5
                )
                try:
                    conn.request("GET", "/api/v1/health")
                    resp = conn.getresponse()
                    resp.read()
                    if resp.status == 200:
                        return
                finally:
                    conn.close()
            except (OSError, http.client.HTTPException, ValueError):
                pass
            time.sleep(0.05)
        raise AssertionError(
            f"uvicorn did not serve /health 200 within {deadline_seconds}s; "
            f"see {self.log_path}"
        )

    def stop(self) -> None:
        try:
            self._proc.terminate()
            self._proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=10)
        finally:
            self._log_handle.close()


def _post(
    port: int,
    path: str,
    *,
    xff: str | None = None,
    token: str | None = None,
    body: dict | None = None,
):
    headers: dict[str, str] = {}
    if xff is not None:
        headers["X-Forwarded-For"] = xff
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    try:
        conn.request(
            "POST",
            path,
            body=json.dumps(body) if body is not None else None,
            headers=headers,
        )
        resp = conn.getresponse()
        payload = resp.read()
        return resp.status, json.loads(payload or b"null")
    finally:
        conn.close()


def _port_free(port: int, timeout_seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind(("127.0.0.1", port))
                sock.listen(1)
            return True
        except OSError:
            time.sleep(0.1)
    return False


def _scratch_url(tmp_path: Path, name: str) -> str:
    return f"sqlite:///{(tmp_path / name).as_posix()}"


# --------------------------------------------------------------------------- #
# level 2 — LIVE positive regression: the fixed launch path binds hostile XFF
# --------------------------------------------------------------------------- #


def test_def094_live_uvicorn_no_proxy_headers_binds_hostile_xff_to_one_identity(
    tmp_path,
):
    """The documented command + ``--no-proxy-headers``: 20 DIFFERENT hostile
    ``X-Forwarded-For`` values are all bound to the ONE socket-peer identity —
    exactly 4 sessions in the window then 429, and the per-IP generation budget
    (2/hour) is equally un-rotatable. Sanitized 429 envelope on the live wire.
    """
    database_url = _scratch_url(tmp_path, "def094_live.db")
    upgrade_db(database_url)

    server = _UvicornSubprocess(
        database_url, tmp_path / "uvicorn-live-no-proxy-headers.log"
    )
    try:
        server.wait_ready()

        # Anonymous-session admission: 20 requests, each with a DIFFERENT
        # hostile forwarded identity. The pattern must be exactly
        # [201 x4, 429 x16] — a spoofed header can never mint a new quota.
        statuses: list[int] = []
        token: str | None = None
        for index in range(20):
            status, body = _post(
                server.port,
                "/api/v1/sessions/anonymous",
                xff=f"203.0.113.{index + 1}",
            )
            statuses.append(status)
            if status == 201 and token is None:
                token = body["anonymousSessionToken"]
        assert statuses == [201] * 4 + [429] * 16, statuses
        assert token is not None

        # Even a request WITHOUT a forwarded header is the same peer identity.
        status, _ = _post(server.port, "/api/v1/sessions/anonymous")
        assert status == 429

        # THE 429 envelope stays sanitized at the live boundary: shared error
        # envelope, no internal markers, no-store + CSP headers (carried by
        # every response including 4xx).
        status, body = _post(
            server.port, "/api/v1/sessions/anonymous", xff="203.0.113.99"
        )
        assert status == 429
        assert body["error"]["code"] == "TOO_MANY_REQUESTS"
        flattened = json.dumps(body).lower()
        for leaked in ("per_ip", "global", "window", "limiter", "quota", "reservation"):
            assert leaked not in flattened, f"429 leaks internal marker {leaked!r}"
        assert_sanitized_error(json.dumps(body))
        conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
        try:
            conn.request(
                "POST", "/api/v1/sessions/anonymous", headers={"X-Forwarded-For": "203.0.113.98"}
            )
            resp = conn.getresponse()
            resp.read()
            assert str(resp.getheader("cache-control", "")) == "no-store"
            assert "content-security-policy" in {h.lower() for h in resp.headers.keys()}
        finally:
            conn.close()

        # Generation per-IP budget uses the SAME authority: 2/hour bound to the
        # socket peer — three attempts with three DIFFERENT hostile XFF values
        # answer [201, 201, 429] (the third spoofed identity cannot reset it).
        generation_statuses: list[int] = []
        for index in range(3):
            gen_status, gen_body = _post(
                server.port,
                "/api/v1/cases",
                xff=f"203.0.113.{60 + index}",
                token=token,
                body={
                    "prompt": "Victim: sarah_miller\nMurderer: thomas_reed\n",
                    "difficulty": "medium",
                },
            )
            generation_statuses.append(gen_status)
            if gen_status == 429:
                assert gen_body["error"]["code"] == "TOO_MANY_REQUESTS"
        assert generation_statuses == [201, 201, 429], generation_statuses
    finally:
        server.stop()
        assert _port_free(server.port), "live uvicorn socket must be released"


# --------------------------------------------------------------------------- #
# level 3 — LIVE sensitivity control: without the flag the vector is open
# --------------------------------------------------------------------------- #


def test_def094_sensitivity_uvicorn_default_proxy_headers_would_rotate_identity(
    tmp_path,
):
    """Proves the positive test is load-bearing: the SAME server launched
    WITHOUT ``--no-proxy-headers`` (uvicorn's platform default, trusting this
    loopback client) rewrites ``request.client`` from each spoofed
    ``X-Forwarded-For`` BEFORE the app's identity gate — the 5th distinct
    forwarded identity gets a fresh budget (201), i.e. the exact DEF-094
    vector. Skipped when a future uvicorn makes the safe default."""
    if not _uvicorn_loopback_proxy_default():
        pytest.skip(
            "installed uvicorn's platform default does not proxy-rewrite "
            "loopback clients; nothing left to guard against"
        )
    database_url = _scratch_url(tmp_path, "def094_sensitivity.db")
    upgrade_db(database_url)

    server = _UvicornSubprocess(
        database_url,
        tmp_path / "uvicorn-sensitivity-default-proxy-headers.log",
        with_no_proxy_headers=False,
    )
    try:
        server.wait_ready()
        statuses: list[int] = []
        for index in range(5):
            status, _ = _post(
                server.port,
                "/api/v1/sessions/anonymous",
                xff=f"198.51.100.{index + 1}",
            )
            statuses.append(status)
        # Five distinct forwarded identities -> five distinct budgets: the
        # per-IP window of 4 never binds (a 5th could never have passed via
        # the true socket peer).
        assert statuses == [201] * 5, statuses
    finally:
        server.stop()
        assert _port_free(server.port), "sensitivity uvicorn socket must be released"


# --------------------------------------------------------------------------- #
# in-process — identity stays the peer and the operator warning fires once
# --------------------------------------------------------------------------- #


class _FakeRequest:
    def __init__(self, client_host: str, headers: dict[str, str] | None = None) -> None:
        self.client = type("Client", (), {"host": client_host})()
        self.headers = headers or {}


def test_def094_resolve_client_ip_keeps_peer_and_warns_once(caplog):
    """TRUST_PROXY=false: a hostile X-Forwarded-For never changes the identity,
    and the first such header emits ONE process-wide operator warning naming
    the --no-proxy-headers remedy (never per-request log spam)."""
    ratelimit_module._forwarded_header_warned = False
    with caplog.at_level(logging.WARNING, logger=ratelimit_module.logger.name):
        assert (
            resolve_client_ip(
                _FakeRequest("127.0.0.1", {"x-forwarded-for": "203.0.113.7"}),
                trust_proxy=False,
            )
            == "127.0.0.1"
        )
        assert (
            resolve_client_ip(
                _FakeRequest("127.0.0.1", {"x-forwarded-for": "203.0.113.8"}),
                trust_proxy=False,
            )
            == "127.0.0.1"
        )
        records = [
            record
            for record in caplog.records
            if record.name == ratelimit_module.logger.name
        ]
        assert len(records) == 1, "the operator warning must fire exactly once"
        assert "--no-proxy-headers" in records[0].message