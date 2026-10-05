"""Phase 29 — monitoring & usage analytics tests (MON-01..MON-15).

Hermetic, deterministic, NO public internet (no sockets are opened; the only
subprocess ever used by the tools is an optional ``docker compose ... config``
client render that the phase24 test family already guards with a skip when the
Compose CLI is unavailable).

Coverage map (§17 acceptance criteria + MON-01/02/03/04/05/08/09/15):

  mon-01  — docker/Caddyfile (and the byte-mirrored Caddyfile.internal LAN
            variant) enables access logging on the HTTPS virtual host.
  mon-02  — access logging uses a structured JSON format on stdout and the
            container logging stays bounded (json-file 10 MB x 5).
  mon-04  — backend publishes no host port (source + rendered model).
  mon-05  — no monitoring service publishes an unprotected host port.
  mon-06  — the production compose stays syntactically valid (client render).
  mon-07  — existing phase24 security/preflight guards stay green with the
            new additive checks wired into ``run_all``.
  mon-08  — the analytics/reporting logic is tested with deterministic canned
            Caddy JSON lines AND with a real migrated SQLite + stdlib DDL;
            the schema contract is pinned against the model sources.
  mon-09  — healthcheck traffic is never counted as user activity.
  mon-03  — query strings / IPs / tokens are never reported; the aggregate
            code strips query strings before the top-path report.
"""

from __future__ import annotations

import io
import json
import re
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from tools import monitoring_report as mr
from tools import release_check

REPO_ROOT = Path(__file__).resolve().parents[1]
CADDY_CANONICAL = REPO_ROOT / "docker" / "Caddyfile"
CADDY_INTERNAL = REPO_ROOT / "docker" / "Caddyfile.internal"
COMPOSE_PROD = REPO_ROOT / "docker-compose.prod.yml"

# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _caddy_line(
    ts: float,
    method: str = "GET",
    uri: str = "/",
    status: int = 200,
    size: int = 1000,
    remote_ip: str = "192.0.2.10",
) -> str:
    """One deterministic Caddy 2 ``format json`` access-log line (fixture).

    Field names match the Caddy documentation exactly (``request.method`` /
    ``request.uri`` / top-level ``ts`` / ``status`` / ``size`` ...). The
    remote_ip value is a documentation-reserved test address and must never
    appear in ANY report output (MON-03).
    """
    payload = {
        "level": "info",
        "ts": ts,
        "logger": "http.log.access.log0",
        "msg": "handled request",
        "request": {
            "remote_ip": remote_ip,
            "remote_port": "51970",
            "client_ip": remote_ip,
            "proto": "HTTP/1.1",
            "method": method,
            "host": "localhost",
            "uri": uri,
        },
        "bytes_read": 0,
        "user_id": "",
        "duration": 0.001,
        "size": size,
        "status": status,
    }
    return json.dumps(payload)


_FIXTURE_TS = 1700000000.0  # 2023-11-14 22:13:20 UTC


def _sample_log_lines() -> list[str]:
    """7 deterministic entries: page, API, static, 2x health, 404, 500,
    plus one non-access line and one garbled line."""
    base = _FIXTURE_TS
    return [
        _caddy_line(base + 0.0, "GET", "/", 200, size=1234),
        # Docker json-file envelope wrapping one Caddy JSON line.
        json.dumps(
            {
                "log": _caddy_line(
                    base + 3600.0, "GET", "/api/v1/cases?prompt=abc", 201, size=789
                ),
                "stream": "stdout",
                "time": "2023-11-14T23:13:20.000Z",
            }
        ),
        # `docker compose logs` prefix.
        "pd-procedural-detective-1  | "
        + _caddy_line(base + 7200.0, "POST", "/api/v1/sessions/anonymous", 201, size=222),
        _caddy_line(base + 10800.0, "GET", "/api/v1/health", 200, size=40),
        _caddy_line(base + 10800.5, "GET", "/api/v1/health", 200, size=40),
        _caddy_line(base + 14400.0, "GET", "/assets/index-abc123.js", 200, size=500),
        _caddy_line(base + 18000.0, "GET", "/scene", 200, size=987),
        _caddy_line(base + 21600.0, "GET", "/missing-page", 404, size=111),
        _caddy_line(base + 25200.0, "GET", "/api/v1/playthroughs/PT-x/reveal", 500, size=222),
        '{"level":"info","ts":1700123456.0,"msg":"certificates are fine"}',
        "not json at all",
    ]


def _aggregated_sample() -> mr.HttpStats:
    entries = [
        e
        for raw in _sample_log_lines()
        if (e := mr.parse_caddy_log_line(raw)) is not None
    ]
    assert len(entries) == 9
    return mr.aggregate(entries, window_hours=24.0)


# =========================================================================== #
# Caddyfile edge — MON-01 / MON-02 (source-level, hermetic)
# =========================================================================== #


def test_mon01_caddyfile_access_logging_enabled() -> None:
    text = CADDY_CANONICAL.read_text(encoding="utf-8")
    log_present, json_stdout, problems = release_check._caddy_https_site_log(text)
    assert log_present, "MON-01: HTTPS virtual host must enable access logging"
    assert json_stdout, problems
    findings = release_check.check_caddy_access_logging(REPO_ROOT)
    assert any(
        f.check == "caddy-access-logging"
        and f.severity == "ok"
        and "JSON on stdout" in f.message
        for f in findings
    ), [f.render() for f in findings]


def test_mon02_access_logging_uses_json_on_stdout() -> None:
    text = CADDY_CANONICAL.read_text(encoding="utf-8")
    site = release_check._extract_braced_block(
        text.splitlines(),
        next(
            i
            for i, line in enumerate(text.splitlines())
            if release_check._normalize_line(line)
            == release_check._CADDY_SITE_HEADER
        ),
    )
    normalized = [
        release_check._normalize_line(line) for line in site
    ]
    assert "log {" in normalized
    assert "output stdout" in normalized, "MON-02: logs must go to stdout"
    assert "format json" in normalized, "MON-02: structured JSON format required"
    # MON-01: no unbounded log file inside the container — the log writer must
    # not target a file; Docker json-file rotation is the single retention
    # bound.
    assert not any(
        line.startswith("output file") for line in normalized
    ), "a Caddy log file inside the container would bypass the Docker rotation"


def test_mon01_internal_lan_variant_mirrors_access_logging() -> None:
    canonical = CADDY_CANONICAL.read_bytes()
    internal = CADDY_INTERNAL.read_bytes()
    assert internal.count(b"tls internal") == 1
    without = internal.replace(b"\ttls internal\n\n", b"", 1)
    without = without.replace(b"\ttls internal\n", b"", 1)
    assert without == canonical, "Caddyfile.internal drifted from the canonical file"
    # The LAN overlay edge must log access the same way (byte-copy invariant).
    findings = release_check.check_caddy_access_logging(REPO_ROOT)
    assert not [f for f in findings if f.severity == "fail"], [
        f.render() for f in findings
    ]


def test_mon02_redirect_block_does_not_carry_the_site_log() -> None:
    """Access logging lives on the HTTPS host (MON-01); the http->https
    redirect block itself must remain a plain redirect (unchanged behavior,
    MON-18)."""
    text = CADDY_CANONICAL.read_text(encoding="utf-8")
    site = release_check._extract_braced_block(
        text.splitlines(),
        next(
            i
            for i, line in enumerate(text.splitlines())
            if release_check._normalize_line(line)
            == release_check._CADDY_SITE_HEADER
        ),
    )
    http_block = text.split("http://{$CADDY_DOMAIN:localhost} {", 1)[0]
    assert "redir / https://{$CADDY_DOMAIN:localhost}{uri} permanent" in text
    assert "intercept" not in site[0]


def test_mon15_caddy_access_logging_fails_closed_when_absent(tmp_path: Path) -> None:
    docker = tmp_path / "docker"
    docker.mkdir()
    (docker / "Caddyfile").write_text(
        "{$CADDY_DOMAIN:localhost} {\n"
        "\trequest_body {\n"
        "\t\tmax_size 70000\n"
        "\t}\n"
        "}\n",
        encoding="utf-8",
    )
    findings = release_check.check_caddy_access_logging(tmp_path)
    fails = [f for f in findings if f.severity == "fail"]
    assert any("Caddy access logging is DISABLED" in f.message for f in fails), [
        f.render() for f in findings
    ]


def test_mon15_caddy_access_logging_fails_closed_without_json(
    tmp_path: Path,
) -> None:
    docker = tmp_path / "docker"
    docker.mkdir()
    (docker / "Caddyfile").write_text(
        "{$CADDY_DOMAIN:localhost} {\n"
        "\tlog {\n"
        "\t\toutput stdout\n"
        "\t}\n"
        "}\n",
        encoding="utf-8",
    )
    findings = release_check.check_caddy_access_logging(tmp_path)
    fails = [f for f in findings if f.severity == "fail"]
    assert any("format json" in f.message for f in fails), [
        f.render() for f in findings
    ]


def test_mon15_caddy_access_logging_skips_without_caddyfile(
    tmp_path: Path,
) -> None:
    findings = release_check.check_caddy_access_logging(tmp_path)
    assert any(f.severity == "skip" and f.check == "caddy-access-logging"
               for f in findings)


def test_p2902_caddy_two_block_stdout_plus_file_log_fails_closed(
    tmp_path: Path,
) -> None:
    """P29-02: a contradictory second access-log block (a file output that
    could win under Caddy's last-wins option merge) FAILS the gate even when
    a conforming stdout+json block is also present."""
    docker = tmp_path / "docker"
    docker.mkdir()
    (docker / "Caddyfile").write_text(
        "{$CADDY_DOMAIN:localhost} {\n"
        "\tlog {\n"
        "\t\toutput stdout\n"
        "\t\tformat json\n"
        "\t}\n"
        "\tlog {\n"
        "\t\toutput file /var/log/caddy/access.log\n"
        "\t\tformat json\n"
        "\t}\n"
        "}\n",
        encoding="utf-8",
    )
    findings = release_check.check_caddy_access_logging(tmp_path)
    fails = [f for f in findings if f.severity == "fail"]
    assert fails, [f.render() for f in findings]
    assert any("output stdout" in f.message for f in fails), [
        f.render() for f in fails
    ]


def test_p2902_caddy_two_block_json_plus_format_access_fails_closed(
    tmp_path: Path,
) -> None:
    """P29-02: an unbounded `format access`-style block (no JSON) fails even
    when a JSON block exists."""
    docker = tmp_path / "docker"
    docker.mkdir()
    (docker / "Caddyfile").write_text(
        "{$CADDY_DOMAIN:localhost} {\n"
        "\tlog {\n"
        "\t\toutput stdout\n"
        "\t\tformat json\n"
        "\t}\n"
        "\tlog {\n"
        "\t\toutput stdout\n"
        "\t\tformat access\n"
        "\t}\n"
        "}\n",
        encoding="utf-8",
    )
    findings = release_check.check_caddy_access_logging(tmp_path)
    fails = [f for f in findings if f.severity == "fail"]
    assert fails, [f.render() for f in findings]
    assert any("format json" in f.message for f in fails), [
        f.render() for f in fails
    ]


def test_p2902_caddy_single_stdout_json_block_ok(tmp_path: Path) -> None:
    """P29-02 baseline: the canonical one-block stdout+json shape stays OK."""
    docker = tmp_path / "docker"
    docker.mkdir()
    (docker / "Caddyfile").write_text(
        "{$CADDY_DOMAIN:localhost} {\n"
        "\tlog {\n"
        "\t\toutput stdout\n"
        "\t\tformat json\n"
        "\t}\n"
        "}\n",
        encoding="utf-8",
    )
    findings = release_check.check_caddy_access_logging(tmp_path)
    assert not [f for f in findings if f.severity == "fail"], [
        f.render() for f in findings
    ]
    assert any(
        f.severity == "ok" and f.check == "caddy-access-logging"
        for f in findings
    )


# =========================================================================== #
# bounded logging + port privacy — MON-02/03/04/05/13 (source + rendered)
# =========================================================================== #


def test_mon03_caddy_service_has_bounded_docker_logging() -> None:
    findings = release_check.check_compose_logging_bounds(REPO_ROOT)
    assert not [f for f in findings if f.severity == "fail"], [
        f.render() for f in findings
    ]
    ok = [f for f in findings if f.severity == "ok"]
    assert ok and "json-file" in ok[0].message


def test_mon04_backend_publishes_no_host_port() -> None:
    text = COMPOSE_PROD.read_text(encoding="utf-8")
    services = release_check._compose_service_blocks(text)
    backend = services["procedural-detective"]
    # Only a NON-COMMENT `ports:` line would publish a host port; the file's
    # own explanatory comments mention the word in prose.
    ports_lines = [
        line for line in backend if re.match(r"^\s*ports:\s*$", line)
    ]
    assert ports_lines == [], (
        "backend must be expose-only (PD-SEC-03): " + repr(ports_lines)
    )
    assert any(re.match(r"^\s*expose:\s*$", line) for line in backend)
    assert any('"8000"' in line for line in backend)


def test_mon04_rendered_backend_stays_private() -> None:
    """Mirrors test_phase24a's canned-render discipline: even a fully rendered
    production model must keep the backend behind expose-only :8000."""
    body = _canonical_prod_body("detective.procedural-game.dev")
    findings = _check_prod_rendered(body)
    assert not [f for f in findings if f.severity == "fail"], [
        f.render() for f in findings
    ]
    backend = body["services"]["procedural-detective"]
    assert release_check._rendered_port_targets(backend) == []
    assert "8000" in {str(item) for item in backend["expose"]}


def test_mon05_no_monitoring_host_port_published() -> None:
    findings = release_check.check_compose_monitoring_safety(REPO_ROOT)
    assert not [f for f in findings if f.severity == "fail"], [
        f.render() for f in findings
    ]
    assert any(
        f.check == "monitoring-safety"
        and f.severity == "ok"
        and "only the Caddy edge" in f.message
        for f in findings
    )


def test_mon05_monitoring_safety_fails_on_extra_port_and_socket(
    tmp_path: Path,
) -> None:
    (tmp_path / "docker-compose.prod.yml").write_text(
        "services:\n"
        "  caddy:\n"
        "    ports:\n"
        '      - "80:80"\n'
        '      - "443:443"\n'
        "  monitoring:\n"
        '    ports:\n'
        '      - "9000:9000"\n'
        "    volumes:\n"
        "      - /var/run/docker.sock:/var/run/docker.sock\n",
        encoding="utf-8",
    )
    findings = release_check.check_compose_monitoring_safety(tmp_path)
    fails = [f.message for f in findings if f.severity == "fail"]
    assert any("monitoring" in m and "host ports" in m for m in fails), fails
    assert any("Docker socket" in m for m in fails), fails


def test_mon13_rendered_model_rejects_docker_socket() -> None:
    body = _canonical_prod_body("detective.procedural-game.dev")
    body["services"]["monitoring"] = {
        "volumes": [
            {
                "type": "bind",
                "source": "/var/run/docker.sock",
                "target": "/var/run/docker.sock",
            }
        ]
    }
    findings = _check_prod_rendered(body)
    fails = [f.message for f in findings if f.severity == "fail"]
    assert any("Docker socket" in m for m in fails), fails


def test_p2903_compose_network_mode_host_fails(tmp_path: Path) -> None:
    """P29-03: `network_mode: host` on any service fails the gate even with
    no `ports:` declaration (the service could bind the host's interfaces)."""
    (tmp_path / "docker-compose.prod.yml").write_text(
        "services:\n"
        "  caddy:\n"
        "    image: caddy:2-alpine\n"
        "    ports:\n"
        '      - "80:80"\n'
        "  monitoring:\n"
        "    image: alpine:3\n"
        "    network_mode: host\n",
        encoding="utf-8",
    )
    findings = release_check.check_compose_monitoring_safety(tmp_path)
    fails = [f.message for f in findings if f.severity == "fail"]
    assert any("network_mode" in m and "host" in m for m in fails), fails


def test_p2903_compose_privileged_true_fails(tmp_path: Path) -> None:
    """P29-03: `privileged: true` on any service fails the gate (host-
    equivalent capabilities outside the compose trust boundary)."""
    (tmp_path / "docker-compose.prod.yml").write_text(
        "services:\n"
        "  caddy:\n"
        "    image: caddy:2-alpine\n"
        "    ports:\n"
        '      - "80:80"\n'
        "  monitoring:\n"
        "    image: alpine:3\n"
        "    privileged: true\n",
        encoding="utf-8",
    )
    findings = release_check.check_compose_monitoring_safety(tmp_path)
    fails = [f.message for f in findings if f.severity == "fail"]
    assert any("privileged" in m for m in fails), fails


def test_p2903_compose_inline_ports_fails(tmp_path: Path) -> None:
    """P29-03: an INLINE `ports: ["9000:9000"]` flow-list declaration must be
    caught (the exact-literal `ports:` block matcher alone would miss it)."""
    (tmp_path / "docker-compose.prod.yml").write_text(
        "services:\n"
        "  caddy:\n"
        "    image: caddy:2-alpine\n"
        "    ports:\n"
        '      - "80:80"\n'
        "  monitoring:\n"
        "    image: alpine:3\n"
        '    ports: ["9000:9000"]\n',
        encoding="utf-8",
    )
    findings = release_check.check_compose_monitoring_safety(tmp_path)
    fails = [f.message for f in findings if f.severity == "fail"]
    assert any("monitoring" in m and "host ports" in m for m in fails), fails


def test_p2903_compose_block_ports_on_monitoring_fails(tmp_path: Path) -> None:
    """P29-03: the classic block-form `ports:` on a monitoring service also
    fails (regression-guarded text path)."""
    (tmp_path / "docker-compose.prod.yml").write_text(
        "services:\n"
        "  monitoring:\n"
        "    image: alpine:3\n"
        "    ports:\n"
        '      - "9000:9000"\n',
        encoding="utf-8",
    )
    findings = release_check.check_compose_monitoring_safety(tmp_path)
    fails = [f.message for f in findings if f.severity == "fail"]
    assert any("monitoring" in m and "host ports" in m for m in fails), fails


def test_p2903_rendered_model_rejects_network_mode_host_and_privileged() -> None:
    """P29-03: the AUTHORITATIVE rendered-model gate also rejects host
    networking and privileged mode on any service (MON-15)."""
    body = _canonical_prod_body("detective.procedural-game.dev")
    body["services"]["monitoring"] = {
        "network_mode": "host",
        "privileged": True,
    }
    findings = _check_prod_rendered(body)
    fails = [f.message for f in findings if f.severity == "fail"]
    assert any("network_mode" in m for m in fails), [f.render() for f in findings]
    assert any("privileged" in m for m in fails), [f.render() for f in findings]


def test_mon07_rendered_model_rejects_extra_host_ports() -> None:
    body = _canonical_prod_body("detective.procedural-game.dev")
    body["services"]["monitoring"] = {
        "ports": [{"target": 9000, "published": "9000"}]
    }
    findings = _check_prod_rendered(body)
    fails = [f.message for f in findings if f.severity == "fail"]
    assert any("publishes a host port" in m or "host port" in m for m in fails), [
        f.render() for f in findings
    ]


def test_mon06_prod_compose_config_stays_valid() -> None:
    """Client-side compose render (no daemon): the production file interpolates
    and the rendered model keeps the documented trust-boundary shape."""
    if not _compose_cli_available():
        pytest.skip("Docker Compose CLI is unavailable")
    empty = Path(__file__).parent / ".tmp-empty-mon06.env"
    empty.write_text("", encoding="utf-8")
    try:
        rendered = release_check._render_prod_compose_config(
            REPO_ROOT, COMPOSE_PROD.resolve(), env_file=empty
        )
    finally:
        empty.unlink(missing_ok=True)
    assert isinstance(rendered.get("services"), dict)
    caddy = release_check._rendered_service(rendered, "caddy")
    backend = release_check._rendered_service(rendered, "procedural-detective")
    assert caddy is not None and backend is not None
    caddy_ports = release_check._rendered_port_targets(caddy)
    assert "80" in caddy_ports and "443" in caddy_ports, caddy_ports
    assert release_check._rendered_port_targets(backend) == []
    raw = json.dumps(rendered)
    assert "/var/run/docker.sock" not in raw
    # Bounded logging renders on both public services.
    assert (
        release_check._rendered_log_bounds_problem("caddy", caddy) is None
    )
    assert (
        release_check._rendered_log_bounds_problem(
            "procedural-detective", backend
        )
        is None
    )


def _canonical_prod_body(domain: str) -> dict:
    """A canned RENDERED production model (mirrors the phase24a fixtures):
    backend expose-only + bounded logs, caddy 80/443 + bounded logs."""
    return {
        "services": {
            "procedural-detective": {
                "environment": {
                    "ENVIRONMENT": "production",
                    "PD_DEV_TRACE": "false",
                    "TRUST_PROXY": "true",
                    "GENERATION_PROVIDER": "ollama",
                    "CASE_GENERATION_DEADLINE_SECONDS": "300",
                    "OLLAMA_TIMEOUT_SECONDS": "180",
                },
                "expose": ["8000"],
                "logging": {
                    "driver": "json-file",
                    "options": {"max-size": "10m", "max-file": "5"},
                },
                "volumes": [
                    {"type": "volume", "source": "pd-data", "target": "/data"}
                ],
            },
            "caddy": {
                "environment": {
                    "CADDY_DOMAIN": domain,
                    "CADDY_EMAIL": "operator@example.com",
                },
                "logging": {
                    "driver": "json-file",
                    "options": {"max-size": "10m", "max-file": "5"},
                },
                "ports": [
                    {"target": 80, "published": "80"},
                    {"target": 443, "published": "443"},
                ],
                "volumes": [
                    {
                        "type": "bind",
                        "source": "./docker/Caddyfile",
                        "target": "/etc/caddy/Caddyfile",
                        "read_only": True,
                    }
                ],
            },
        },
        "volumes": {"pd-data": {"name": "project_pd-data"}},
    }


def _render(body: dict) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["docker", "compose"], 0, json.dumps(body), "")


def _check_prod_rendered(body: dict) -> list[release_check.Finding]:
    def runner(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        return _render(body)

    return release_check.check_prod_effective_config(
        REPO_ROOT,
        allow_local=False,
        compose_runner=runner,
        frontend_dir=REPO_ROOT / "__phase29_missing_dist__",
    )


def _compose_cli_available() -> bool:
    import shutil

    if shutil.which("docker") is None:
        return False
    probe = subprocess.run(
        ["docker", "compose", "version"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    return probe.returncode == 0


# =========================================================================== #
# Caddy JSON access-log parser + aggregation — MON-04/05/08/09
# =========================================================================== #


def test_mon08_parse_direct_caddy_json_line() -> None:
    entry = mr.parse_caddy_log_line(
        _caddy_line(1700000000.25, "GET", "/scene?case=C-1", 200, size=42)
    )
    assert entry is not None
    assert entry.ts == pytest.approx(1700000000.25)
    assert entry.method == "GET"
    assert entry.uri == "/scene?case=C-1"
    assert entry.path == "/scene"  # query stripped for categorization
    assert entry.status == 200
    assert entry.size == 42


def test_mon08_parse_docker_jsonfile_envelope() -> None:
    inner = _caddy_line(1700000000.5, "GET", "/", 200)
    raw = json.dumps({"log": inner, "stream": "stdout", "time": "2023-11-14T22:13:20Z"})
    entry = mr.parse_caddy_log_line(raw)
    assert entry is not None and entry.uri == "/"


def test_mon08_parse_compose_prefixed_line() -> None:
    raw = "caddy-1  | " + _caddy_line(1700000000.5, "GET", "/", 200)
    entry = mr.parse_caddy_log_line(raw)
    assert entry is not None and entry.uri == "/"


def test_mon08_parse_skips_non_access_and_garbage_lines() -> None:
    assert mr.parse_caddy_log_line(
        '{"level":"info","ts":1700000000.5,"msg":"certificates are fine"}'
    ) is None
    assert mr.parse_caddy_log_line("not json") is None


def test_mon04_aggregate_computes_all_http_kpis() -> None:
    stats = _aggregated_sample()
    assert stats.total == 9
    assert stats.methods["GET"] == 8
    assert stats.methods["POST"] == 1
    # status classes
    assert stats.status_classes["2xx"] == 7
    assert stats.status_classes["4xx"] == 1
    assert stats.status_classes["5xx"] == 1
    assert stats.count_404 == 1
    assert stats.count_5xx == 1
    # transferred bytes (response `size`) when determinable
    sizes = [1234, 789, 222, 40, 40, 500, 987, 111, 222]
    assert stats.bytes_transferred == sum(sizes)
    # top paths: query strings stripped, deterministic ordering
    assert stats.top_paths[0] == ("/api/v1/health", 2)
    assert all("?prompt=" not in p for p, _ in stats.top_paths)
    assert ("/api/v1/cases", 1) in stats.top_paths
    assert ("/assets/index-abc123.js", 1) in stats.top_paths
    # time series — fixture spans 2023-11-14 22:13 .. 2023-11-15 05:13 UTC.
    assert stats.per_day["2023-11-14"] == 2
    assert stats.per_day["2023-11-15"] == 7
    assert stats.hourly_series[0][0] == 1699999200  # 22:00 UTC bucket
    assert stats.hourly_series[-1][0] == 1700024400  # 05:00 UTC bucket
    assert sum(count for _, count in stats.hourly_series) == 9
    assert stats.requests_per_hour > 0


def test_mon09_healthcheck_not_counted_as_user_activity() -> None:
    stats = _aggregated_sample()
    # Two /api/v1/health probes are in the log but must NOT count as player
    # activity: Page/API count covers pages + real API routes only.
    assert stats.categories["Health"] == 2
    assert stats.categories["Static"] == 1
    assert stats.categories["Page"] == 2  # "/" + "/scene"
    assert stats.categories["API"] == 3  # cases + sessions + reveal
    assert stats.categories["Other"] == 1  # /missing-page
    assert stats.page_api_requests == 5  # 2 pages + 3 API
    # The full log total still includes them (traffic is traffic).
    assert stats.total == 9


def test_mon05_categorization_uses_actual_repo_routes() -> None:
    cases = [
        ("/", mr.CATEGORY_PAGE),
        ("/new", mr.CATEGORY_PAGE),
        ("/generating", mr.CATEGORY_PAGE),
        ("/scene", mr.CATEGORY_PAGE),
        ("/accuse", mr.CATEGORY_PAGE),
        ("/reveal", mr.CATEGORY_PAGE),
        ("/api/v1/health", mr.CATEGORY_HEALTH),
        ("/api/v1/readiness", mr.CATEGORY_HEALTH),
        ("/api/v1/cases", mr.CATEGORY_API),
        ("/api/v1/sessions/anonymous", mr.CATEGORY_API),
        ("/api/v1/playthroughs/PT-1/reveal", mr.CATEGORY_API),
        ("/api/v1/generation-capabilities", mr.CATEGORY_API),
        ("/assets/index.js", mr.CATEGORY_STATIC),
        ("/static/foo.txt", mr.CATEGORY_STATIC),
        ("/favicon.ico", mr.CATEGORY_OTHER),
        ("/robots.txt", mr.CATEGORY_OTHER),
    ]
    for path, expected in cases:
        assert mr.categorize(path) == expected, path


def test_mon05_page_paths_match_frontend_router_source() -> None:
    """The category vocabulary is pinned against the ACTUAL SPA routes
    (frontend/src/main.tsx) — no invented paths (MON-05)."""
    tsx = (REPO_ROOT / "frontend" / "src" / "main.tsx").read_text(
        encoding="utf-8"
    )
    declared = {"/"}
    for match in re.finditer(r'path="([^"]+)"', tsx):
        route = "/" + match.group(1)
        if route != "/*":  # the NotFound catch-all is not an SPA page
            declared.add(route)
    assert declared == set(mr.PAGE_PATHS), (
        f"monitoring_report.PAGE_PATHS drifted from frontend/src/main.tsx "
        f"(declared {sorted(declared)}, tool {sorted(mr.PAGE_PATHS)})"
    )


def test_mon03_query_strings_and_ips_never_reported(tmp_path: Path) -> None:
    """MON-03 data minimization: query strings (potentially sensitive) are
    stripped before the top-path report, and IP literals never appear in any
    rendered output (the aggregate code prints no IP field at all)."""
    log = tmp_path / "caddy.log"
    log.write_text(
        "\n".join(
            [
                _caddy_line(1700000000.0, "GET",
                            "/api/v1/cases?prompt=hunter2&token=SECRET", 201),
                _caddy_line(1700003600.0, "GET", "/new?rel=C%26C", 200),
            ]
        ),
        encoding="utf-8",
    )
    stats = mr.aggregate(
        [
            e
            for raw in log.read_text(encoding="utf-8").splitlines()
            if (e := mr.parse_caddy_log_line(raw)) is not None
        ]
    )
    assert all("?" not in p for p, _ in stats.top_paths)
    assert all("SECRET" not in p and "hunter2" not in p for p, _ in stats.top_paths)
    report = mr.format_human(stats, None, hours=24.0, skipped_lines=0)
    assert "hunter2" not in report and "SECRET" not in report
    assert "192.0.2.10" not in report and "203.0.113" not in report
    assert "remote_ip" not in report and "client_ip" not in report


# =========================================================================== #
# CLI behaviour — MON-10/03
# =========================================================================== #


def test_mon10_cli_summary_block_and_no_secrets(tmp_path: Path, capsys) -> None:
    log = tmp_path / "caddy.log"
    log.write_text("\n".join(_sample_log_lines()), encoding="utf-8")
    ret = mr.main(["--logs", str(log), "--summary"])
    captured = capsys.readouterr()
    assert ret == 0
    out = captured.out
    assert "Procedural Detective Monitoring" in out
    assert "HTTP requests:          9" in out
    assert "Page/API requests:      5" in out
    assert "5xx responses:          1" in out
    assert "n/a (no --db source)" in out
    # No IP / token / sensitive material in the compact block.
    assert "192.0.2.10" not in out
    assert "remote_ip" not in out and "SECRET" not in out


def test_mon10_cli_full_report_marks_requests_vs_visitors(
    tmp_path: Path, capsys
) -> None:
    log = tmp_path / "caddy.log"
    log.write_text("\n".join(_sample_log_lines()), encoding="utf-8")
    ret = mr.main(["--logs", str(log)])
    out = capsys.readouterr().out
    assert ret == 0
    assert "NOT equivalent to visitors or players" in out
    assert "5xx" in out
    assert "Cases completed" in out


def test_mon10_cli_stdin_source(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        sys, "stdin", io.TextIOWrapper(io.BytesIO("\n".join(_sample_log_lines()).encode()))
    )
    ret = mr.main(["--logs", "-", "--summary"])
    out = capsys.readouterr().out
    assert ret == 0
    assert "HTTP requests:          9" in out


def test_mon10_cli_stdin_source_without_buffer(monkeypatch, capsys) -> None:
    class _FakeStdin(io.StringIO):
        buffer = None  # type: ignore[assignment]

    fake = _FakeStdin("\n".join(_sample_log_lines()))
    monkeypatch.setattr(sys, "stdin", fake)
    ret = mr.main(["--logs", "-", "--summary"])
    out = capsys.readouterr().out
    assert ret == 0
    assert "HTTP requests:          9" in out


def test_mon10_cli_error_on_missing_log_file(tmp_path: Path, capsys) -> None:
    ret = mr.main(["--logs", str(tmp_path / "missing.log")])
    err = capsys.readouterr().err
    assert ret == 1
    assert "access-log file not found" in err


def test_mon10_cli_usage_error(capsys) -> None:
    ret = mr.main(["--hours", "-1"])
    assert ret == 2
    assert "error: --hours must be positive" in capsys.readouterr().err


def test_mon10_cli_no_source_shows_na(capsys) -> None:
    ret = mr.main([])
    out = capsys.readouterr().out
    assert ret == 0
    assert "n/a (no --logs source)" in out
    assert "n/a (no --db source)" in out


# =========================================================================== #
# P29-01 — the report CLI must NEVER crash on hostile/garbled log lines
# =========================================================================== #


def _hostile_line(
    ts: object,
    method: str = "GET",
    uri: str = "/",
    status: object = 200,
    size: object = 5,
) -> str:
    """One Caddy JSON line with a RAW (unserialized) ``ts``/``status`` value
    so JSON floats like NaN/Inf/9e18 and hostile ints reach the parser."""
    payload = {
        "level": "info",
        "ts": ts,
        "logger": "http.log.access.log0",
        "msg": "handled request",
        "request": {
            "remote_ip": "192.0.2.10",
            "remote_port": "1",
            "client_ip": "192.0.2.10",
            "proto": "HTTP/1.1",
            "method": method,
            "host": "localhost",
            "uri": uri,
        },
        "bytes_read": 0,
        "user_id": "",
        "duration": 0.001,
        "size": size,
        "status": status,
    }
    return json.dumps(payload)


def test_p2901_non_finite_and_absurd_timestamps_skip_line() -> None:
    """NaN / +Inf / -Inf / 9e18 timestamps must skip the line — never raise
    ValueError/OverflowError/OSError from the UTC formatter (P29-01)."""
    for raw_ts in (float("nan"), float("inf"), 9e18, float("-inf"), 1e13, -1e13):
        assert mr.parse_caddy_log_line(_hostile_line(raw_ts)) is None, raw_ts
    # The aggregate-level defensive guard also skips a manually-built entry.
    stats = mr.aggregate([_hostile_entry(float("nan"))])
    assert stats.total == 0 and stats.skipped_lines == 1


def _hostile_entry(ts: float) -> mr.CaddyLogEntry:
    return mr.CaddyLogEntry(
        ts=ts, method="GET", uri="/", path="/", status=200, size=5
    )


def test_p2901_deep_nesting_json_never_raises() -> None:
    """A pathologically-nested JSON line must be skipped (RecursionError is
    caught explicitly), not crash the report (P29-01)."""
    deep = '{"log":' + "[" * 5000 + "]" * 5000 + "}"
    assert mr.parse_caddy_log_line(deep) is None


def test_p2901_absurd_status_and_size_skipped_or_clamped() -> None:
    # status outside [100, 599] (and a bool) skip the line.
    assert mr.parse_caddy_log_line(_hostile_line(1700000000.0, status=99999)) is None
    assert mr.parse_caddy_log_line(_hostile_line(1700000000.0, status=True)) is None
    assert mr.parse_caddy_log_line(_hostile_line(1700000000.0, status=99)) is None
    # absurd / negative sizes are dropped from byte accounting, never added.
    huge = mr.parse_caddy_log_line(_hostile_line(1700000000.0, size=10**18))
    assert huge is not None and huge.size is None
    negative = mr.parse_caddy_log_line(_hostile_line(1700000001.0, size=-5))
    assert negative is not None and negative.size is None
    stats = mr.aggregate([huge, negative])
    assert stats.total == 2
    assert stats.bytes_transferred == 0


def test_p2901_hostile_long_uri_truncated() -> None:
    """A hostile 131 KB URI must be truncated for the path echo / top-path
    collections so the report never prints or stores the full string."""
    long_uri = "/" + "x" * 131000
    entry = mr.parse_caddy_log_line(_hostile_line(1700000000.0, uri=long_uri))
    assert entry is not None
    assert entry.path.endswith("...")
    assert len(entry.path) <= 200 + 3
    stats = mr.aggregate([entry])
    assert stats.top_paths and len(stats.top_paths[0][0]) <= 200 + 3
    out = mr.format_human(stats, None, hours=24.0, skipped_lines=0)
    assert ("x" * 1000) not in out  # the full hostile URI never reaches output


def test_p2901_sub_second_span_floor() -> None:
    """Two entries 1 us apart must NOT fabricate ~7.5e9 req/h; the observed
    span is floored at 1 s (P29-01)."""
    e1 = mr.parse_caddy_log_line(_hostile_line(1700000000.0))
    e2 = mr.parse_caddy_log_line(_hostile_line(1700000000.000001))
    stats = mr.aggregate([e1, e2])
    # 2 requests / floored 1 s -> 7200 req/h (never 7.5e9).
    assert stats.requests_per_hour == pytest.approx(7200.0)
    # A single entry (zero span) keeps the window fallback, not the floor.
    single = mr.aggregate([e1], window_hours=24.0)
    assert single.requests_per_hour == pytest.approx(1.0 / 24.0)


def test_p2901_hours_nan_inf_rejected(capsys) -> None:
    """`--hours nan|inf` must be rejected with the usage-error path (exit 2),
    never poison the window arithmetic."""
    for bad in ("nan", "inf", "1e999"):
        assert mr.main(["--hours", bad]) == 2, bad
        err = capsys.readouterr().err
        assert "error: --hours must be positive" in err


def test_p2901_garbled_lines_are_skipped_not_crashing(
    tmp_path: Path, capsys
) -> None:
    """A capture mixing every hostile class with valid lines exits 0 across
    the default / --summary / --json modes, reports the valid aggregates and
    the safe skipped-lines counter (P29-01)."""
    lines = [
        _hostile_line(float("nan")),
        _hostile_line(float("inf")),
        _hostile_line(9e18),
        _hostile_line(float("-inf")),
        '{"log":' + "[" * 5000 + "]" * 5000 + "}",
        _hostile_line(1700000000.0, status=700),
        _hostile_line(1700000001.0, size=10**18, uri="/" + "x" * 2000),
        _caddy_line(1700000002.0, "GET", "/api/v1/health", 200, size=40),
        _caddy_line(1700000003.0, "GET", "/", 200, size=1000),
    ]
    log = tmp_path / "hostile.log"
    log.write_text("\n".join(lines), encoding="utf-8")

    # default (full human) mode
    capsys.readouterr()
    assert mr.main(["--logs", str(log)]) == 0
    out = capsys.readouterr().out
    assert "HTTP requests:          3" in out
    assert "Lines skipped (malformed): 6" in out
    assert "PRIVATE" not in out  # no traceback / leaked path

    # --summary compact mode
    assert mr.main(["--logs", str(log), "--summary"]) == 0
    out = capsys.readouterr().out
    assert "HTTP requests:          3" in out
    assert "Lines skipped (malformed): 6" in out

    # --json mode
    assert mr.main(["--logs", str(log), "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["http"]["total"] == 3
    assert doc["http"]["count_5xx"] == 0
    assert doc["skipped_lines"] == 6


# =========================================================================== #
# product metrics — MON-08 (existing schema, stdlib + real migrations)
# =========================================================================== #


def _sample_log_file(tmp_path: Path) -> Path:
    log = tmp_path / "caddy.log"
    log.write_text("\n".join(_sample_log_lines()), encoding="utf-8")
    return log


def _stdlib_product_db(path: Path) -> Path:
    """Create a temp SQLite with the exact columns the monitoring tool reads
    (real `created_at`/`published_at` epoch-float semantics)."""
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE cases (
            case_id TEXT PRIMARY KEY,
            quota_session_id TEXT NOT NULL,
            title TEXT NOT NULL,
            difficulty TEXT,
            next_version INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL
        );
        CREATE TABLE case_versions (
            case_id TEXT NOT NULL,
            version INTEGER NOT NULL,
            state TEXT NOT NULL,
            state_reason TEXT,
            generation_id TEXT NOT NULL,
            created_at REAL NOT NULL,
            PRIMARY KEY (case_id, version)
        );
        CREATE TABLE playthroughs (
            playthrough_id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL,
            case_version INTEGER NOT NULL,
            token_verifier TEXT NOT NULL UNIQUE,
            state TEXT NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL
        );
        CREATE TABLE published_versions (
            case_id TEXT NOT NULL,
            case_version INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            published_at REAL NOT NULL,
            PRIMARY KEY (case_id, case_version)
        );
        CREATE TABLE anonymous_quota_sessions (
            session_id TEXT PRIMARY KEY,
            token_verifier TEXT NOT NULL UNIQUE,
            created_at REAL NOT NULL,
            quota_window_end REAL NOT NULL,
            generations_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE generation_attempts (
            attempt_id TEXT PRIMARY KEY,
            case_id TEXT NOT NULL,
            case_version INTEGER NOT NULL,
            status TEXT NOT NULL,
            stage TEXT,
            progress INTEGER NOT NULL DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        """
    )
    conn.commit()
    conn.close()
    return path


def _seed_product_rows(conn: sqlite3.Connection, start: float) -> None:
    ver = "%064x"
    conn.executemany(
        "INSERT INTO cases (case_id, quota_session_id, title, difficulty, "
        "next_version, created_at) VALUES (?,?,?,?,?,?)",
        [
            ("C-1", "s1", "Murder at the Villa", "Easy", 2, start + 1),
            ("C-2", "s1", "The Missing Ledger", "Hard", 1, start + 2),
            ("C-3", "s2", "Old case (outside window)", "Medium", 1, start - 90000),
        ],
    )
    conn.executemany(
        "INSERT INTO case_versions (case_id, version, state, state_reason, "
        "generation_id, created_at) VALUES (?,?,?,?,?,?)",
        [
            ("C-1", 1, "PUBLISHED", None, "GEN-1", start + 1),
            ("C-1", 2, "FAILED", None, "GEN-2", start + 600),
            ("C-2", 1, "PUBLISHED", None, "GEN-1", start + 700),
        ],
    )
    conn.executemany(
        "INSERT INTO published_versions (case_id, case_version, payload_json, "
        "published_at) VALUES (?,?,?,?)",
        [
            ("C-1", 1, "{}", start + 900),
            ("C-2", 1, "{}", start + 1000),
        ],
    )
    conn.executemany(
        "INSERT INTO playthroughs (playthrough_id, case_id, case_version, "
        "token_verifier, state, created_at, expires_at) VALUES (?,?,?,?,?,?,?)",
        [
            ("PT-1", "C-1", 1, ver % 2, "REVEALED", start + 500, start + 5000),
            ("PT-2", "C-1", 1, ver % 3, "PLAYING", start + 600, start + 5000),
            ("PT-3", "C-2", 1, ver % 4, "CREATED", start - 90000, start - 80000),
        ],
    )
    conn.executemany(
        "INSERT INTO anonymous_quota_sessions (session_id, token_verifier, "
        "created_at, quota_window_end, generations_count) VALUES (?,?,?,?,?)",
        [
            ("s1", ver % 10, start + 1, start + 86400, 2),
            ("s2", ver % 11, start - 90000, start - 86400, 1),
        ],
    )
    conn.executemany(
        "INSERT INTO generation_attempts (attempt_id, case_id, case_version, "
        "status, stage, progress, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        [
            ("a1", "C-1", 1, "PUBLISHED", "done", 100, start + 1, start + 900),
            ("a2", "C-1", 2, "FAILED", None, 0, start + 600, start + 601),
        ],
    )
    conn.commit()


def test_mon08_product_metrics_stdlib_schema(tmp_path: Path) -> None:
    now = 1700092800.0  # 2023-11-16 00:00 UTC
    db = _stdlib_product_db(tmp_path / "pd.db")
    conn = sqlite3.connect(db)
    _seed_product_rows(conn, start=now - 24 * 3600)
    conn.close()
    stats = mr.query_product_metrics(db, now_ts=now, hours=24)
    assert stats.playthroughs_started == 2
    assert stats.cases_started == 2  # C-1 + C-2 (old C-3 outside window)
    assert stats.case_versions_started == 3
    assert stats.published_versions == 2
    assert stats.cases_completed == 2  # distinct case_id with a PUBLISHED row
    assert stats.completion_rate == 1.0
    assert stats.generation_failed_attempts == 1
    assert stats.anonymous_quota_sessions == 1
    assert stats.first_usage_ts == pytest.approx(now - 24 * 3600 + 1)
    assert stats.last_usage_ts == pytest.approx(now - 24 * 3600 + 1000)
    assert stats.playthroughs_per_day["2023-11-15"] == 2
    assert len(stats.playthroughs_per_day) == 1


def test_mon08_product_metrics_real_migrated_schema(tmp_path: Path) -> None:
    """The SAME queries over a database built by the repository's real Alembic
    migration chain to head (the migration list declares the authoritative
    schema; the ORM metadata is not connected)."""
    alembic = pytest.importorskip("alembic")
    import logging

    from alembic import command
    from alembic.config import Config

    logging.getLogger("alembic").setLevel(logging.CRITICAL)
    db = tmp_path / "migrated.db"
    cfg = Config(str(REPO_ROOT / "backend" / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", "sqlite:///" + db.as_posix())
    command.upgrade(cfg, "head")

    now = 1700092800.0
    start = now - 24 * 3600
    conn = sqlite3.connect(db)
    _seed_product_rows(conn, start=start)
    conn.close()

    stats = mr.query_product_metrics(db, now_ts=now, hours=24)
    assert stats.playthroughs_started == 2
    assert stats.cases_started == 2
    assert stats.published_versions == 2
    assert stats.cases_completed == 2
    assert stats.completion_rate == 1.0
    assert stats.generation_failed_attempts == 1


def test_mon08_product_metrics_wrong_database(tmp_path: Path) -> None:
    db = tmp_path / "not-pd.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE something_else (id INTEGER)")
    conn.commit()
    conn.close()
    with pytest.raises(ValueError, match="missing tables"):
        mr.query_product_metrics(db, now_ts=1700092800.0, hours=24)


def test_mon08_product_metrics_missing_database_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="not found"):
        mr.query_product_metrics(tmp_path / "nope.db", now_ts=1700092800.0, hours=24)


def test_mon08_schema_contract_pinned_to_models_source() -> None:
    """The tool's expected tables/columns must be declared in the ACTUAL model
    sources (backend/app/models/*.py) — a schema drift fails this test."""
    for table, columns in mr._PRODUCT_TABLE_COLUMNS.items():
        model = REPO_ROOT / "backend" / "app" / "models"
        candidates = list(model.glob("*.py"))
        matches = []
        for path in candidates:
            text = path.read_text(encoding="utf-8", errors="replace")
            declared = set(
                re.findall(
                    r"^\s*([a-z][a-z0-9_]*):\s*Mapped\b", text, re.M
                )
            )
            if f'__tablename__ = "{table}"' in text:
                matches.append((path.name, declared))
        assert matches, f"{table} is not declared by any model source"
        for _name, declared in matches:
            missing = [c for c in columns if c not in declared]
            assert not missing, (
                f"monitoring tool reads columns {missing} of {table} which the "
                f"model source does not declare — schema contract drift"
            )


def test_mon08_sqlite_url_to_path(tmp_path: Path) -> None:
    # POSIX semantics (the deployment host): `sqlite:////data/pd.db` -> /data/pd.db.
    absolute = mr.sqlite_url_to_path("sqlite:////data/procedural_detective.db")
    assert absolute.as_posix().replace("//", "/").rstrip("/") == (
        "/data/procedural_detective.db"
    )
    assert mr.sqlite_url_to_path(str(tmp_path / "pd.db")) == tmp_path / "pd.db"
    assert mr.sqlite_url_to_path("sqlite:///relative.db") == Path("relative.db")
    with pytest.raises(ValueError):
        mr.sqlite_url_to_path("sqlite:///:memory:")


def test_mon08_session_duration_is_documented_not_fabricated() -> None:
    note = mr.product_session_duration_note()
    assert "NOT REPORTED" in note
    assert "expires_at" in note and "token TTL" in note
    # The human report surfaces the documented reasoning.
    report = mr.format_human(mr.HttpStats(), None, hours=24.0, skipped_lines=0)
    assert "NOT REPORTED" in report


# =========================================================================== #
# deterministic JSON + full-report checks
# =========================================================================== #


def test_mon08_json_output_deterministic(tmp_path: Path) -> None:
    log = _sample_log_file(tmp_path)
    first = subprocess.run(
        [sys.executable, "-m", "tools.monitoring_report", "--logs", str(log), "--json"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    second = subprocess.run(
        [sys.executable, "-m", "tools.monitoring_report", "--logs", str(log), "--json"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert first.returncode == 0, first.stderr
    assert first.stdout == second.stdout
    doc = json.loads(first.stdout)
    assert doc["http"]["total"] == 9
    assert doc["http"]["count_5xx"] == 1
    assert doc["http"]["categories"]["Health"] == 2
    assert doc["http"]["page_api_requests"] == 5


def test_cli_rejects_bad_hours(capsys) -> None:
    assert mr.main(["--hours", "0"]) == 2


def test_mon11_health_summary_parses_per_line_ps_json(
    monkeypatch, capsys,
) -> None:
    """`docker compose ps --format json` emits ONE json object PER LINE (not an
    array); the health summary must parse each line (regression for the live
    `--health` smoke that reported every service NOT RUNNING)."""
    two_lines = (
        '{"Service":"caddy","State":"running","Health":"",'
        '"Name":"pd-caddy-1"}'
        + "\n"
        + '{"Service":"procedural-detective","State":"running",'
        '"Health":"healthy","Name":"pd-backend-1"}'
        + "\n"
    )

    def fake_run(command, **_kwargs):
        return subprocess.CompletedProcess(
            command, 0, two_lines, ""
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    summary = mr.health_summary()
    assert "caddy: state=running, health=n/a" in summary, summary
    assert "procedural-detective: state=running, health=healthy" in summary, summary
    assert "NOT RUNNING" not in summary


def test_mon11_health_summary_orders_docker_socket_free_and_rootless(
    monkeypatch,
) -> None:
    """MON-13/MON-10: the health aid is a local read-only `docker compose ps` —
    the summary docstring and output never require root and the tool never
    mounts /var/run/docker.sock."""
    source = (REPO_ROOT / "tools" / "monitoring_report.py").read_text(
        encoding="utf-8"
    )
    health_section = source.split("def health_summary")[1].split(
        "_HEALTH_CHECKLIST"
    )[0]
    assert "/var/run/docker.sock" not in health_section
    assert "no root" in health_section


# =========================================================================== #
# regression wiring — MON-07 (additive, existing guards stay green)
# =========================================================================== #


def test_mon07_run_all_includes_phase29_checks_and_stays_green() -> None:
    findings = release_check.run_all(
        REPO_ROOT, allow_hosted=True, frontend_dir=None,
    )
    assert any(f.check == "caddy-access-logging" for f in findings)
    assert any(f.check == "monitoring-safety" for f in findings)
    assert any(f.check == "compose-logging-bounds" for f in findings)
    assert not [f for f in findings if f.severity == "fail"], [
        f.render() for f in findings
    ]


def test_mon07_prod_preflight_wiring_includes_monitoring_checks(
    monkeypatch, tmp_path: Path,
) -> None:
    """The preflight CLI (canned render via the client, no daemon) runs the
    new MON-15 checks and stays green for the canonical local-smoke profile."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("PD_DEV_TRACE", "false")
    monkeypatch.setenv("TRUST_PROXY", "true")
    monkeypatch.setenv("CADDY_DOMAIN", "localhost")
    empty = tmp_path / "empty.env"
    empty.write_text("", encoding="utf-8")
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tools.prod_preflight",
            "--allow-local",
            "--env-file",
            str(empty),
        ],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert "caddy-access-logging" in completed.stdout
    assert "monitoring-safety" in completed.stdout
    assert "ALL" in completed.stdout