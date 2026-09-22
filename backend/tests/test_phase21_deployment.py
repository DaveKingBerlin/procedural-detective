"""Phase 21 — deployment/docs closure (deployment track).

F-04 (MEDIUM) — bounded Docker/Uvicorn stdout logs: the PROD compose profile
must carry a json-file logging bound on BOTH public services
(``procedural-detective`` and ``caddy``); the backend's own file-log rotation
constants stay unchanged; the release tool pins the compose block.

P-01 (risk)   — alternate ingress / TRUST_PROXY: the shipped Caddy-first
profile is the AUTHORITATIVE supported production path for ``TRUST_PROXY=true``.
An alternate platform ingress MUST default to ``TRUST_PROXY=false`` and only
enable it after the ingress is verified to strip/overwrite hostile
``X-Forwarded-For``, provide a known hop structure and preserve the client IP;
``TRUST_PROXY=true`` is NOT portable between ingress providers. The forged
multi-hop ``X-Forwarded-For`` operator contract is pinned below against the
real ``resolve_client_ip``.

F-06 (LOW)   — privacy docs: PRIVACY.md states the browser localStorage set
(playthrough bearer token, playthrough ID, notebook hypothesis pins) with the
shared-device/expiry/clearing clarifications; README no longer claims a backend
RESTART is required to renew the generation quota window.

Read-only: no backend or frontend SOURCE is changed here. The only backend
entry points exercised are ``resolve_client_ip`` (pure) and the root
``tools.release_check`` helpers, plus read-only assertions over
``docker-compose.prod.yml`` / repo docs. Hermetic: no network, no database.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _BACKEND_DIR.parent
for _p in (str(_BACKEND_DIR), str(_REPO_ROOT), str(_REPO_ROOT / "tools")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.core import ratelimit as ratelimit_module  # noqa: E402
from app.core.ratelimit import resolve_client_ip  # noqa: E402
from tools import release_check  # noqa: E402

_COMPOSE_PROD = _REPO_ROOT / "docker-compose.prod.yml"


class _FakeRequest:
    """Minimal request-shaped object with a socket peer + raw headers."""

    def __init__(self, client_host: str, headers: dict[str, str] | None = None) -> None:
        self.client = type("Client", (), {"host": client_host})()
        self.headers = headers or {}


def _normalized(text: str) -> str:
    """Collapse runs of whitespace so single-line phrase assertions survive
    markdown line wrapping in the docs."""
    return " ".join(text.split())


# --------------------------------------------------------------------------- #
# F-04 — bounded container stdout logs (json-file) + unchanged file rotation
# --------------------------------------------------------------------------- #


def test_f04_prod_compose_both_public_services_bounded():
    """docker-compose.prod.yml carries the bounded json-file logging block on
    BOTH public services: the backend (uvicorn access logs) AND caddy (Caddy
    access/error logs -> stdout). The max-size/max-file envelope must match
    exactly, verified through the release tool's own stdlib-only parser."""
    text = _COMPOSE_PROD.read_text(encoding="utf-8")
    blocks = release_check._compose_service_blocks(text)
    for name in ("procedural-detective", "caddy"):
        assert name in blocks, f"missing public service {name!r}"
        assert release_check._COMPOSE_LOGGING_BLOCK_RE.search(
            "\n".join(blocks[name])
        ), f"{name} must bound stdout with json-file 10m x 5 (F-04)"


def test_f04_backend_file_log_rotation_unchanged():
    """The application FILE logs stay app-level rotated exactly as before:
    5 MB x 3 backups. Read-only probe of backend/app/core/observability.py
    (never edited) — F-04 only bounds the container stdout layer."""
    from app.core import observability

    assert observability.LOG_MAX_BYTES == 5 * 1024 * 1024
    assert observability.LOG_BACKUP_COUNT == 3
    source = (Path(observability.__file__)).read_text(encoding="utf-8")
    assert "RotatingFileHandler(" in source
    assert "maxBytes=LOG_MAX_BYTES" in source
    assert "backupCount=LOG_BACKUP_COUNT" in source


def test_f04_release_check_pins_compose_logging_bounds_on_real_tree():
    """release_check.check_compose_logging_bounds answers OK on the tracked
    compose file, and run_all wires the gate in."""
    findings = release_check.check_compose_logging_bounds(_REPO_ROOT)
    assert [f.severity for f in findings] == ["ok"], [f.render() for f in findings]
    assert all(f.check == "compose-logging-bounds" for f in findings)

    all_findings = release_check.run_all(_REPO_ROOT, allow_hosted=True)
    bounds = [f for f in all_findings if f.check == "compose-logging-bounds"]
    assert bounds, "run_all must include check_compose_logging_bounds (F-04)"
    assert [f.severity for f in bounds] == ["ok"], [f.render() for f in bounds]


def test_f04_release_check_fails_without_compose_logging_bounds(tmp_path):
    """The gate is load-bearing: a prod compose WITHOUT the bounded logging
    block on a public service MUST fail (and pass once the block is added)."""
    base = (
        "services:\n"
        "  procedural-detective:\n"
        "    image: procedural-detective:latest\n"
        "  caddy:\n"
        "    image: caddy:2-alpine\n"
    )
    unbounded = tmp_path / "docker-compose.prod.yml"
    unbounded.write_text(base, encoding="utf-8")
    fails = release_check.check_compose_logging_bounds(tmp_path)
    assert all(f.severity == "fail" for f in fails), [f.render() for f in fails]
    assert any("procedural-detective" in f.message for f in fails)
    assert any("caddy" in f.message for f in fails)

    bounded = tmp_path / "docker-compose.prod.yml"
    bounded.write_text(
        "services:\n"
        "  procedural-detective:\n"
        "    image: procedural-detective:latest\n"
        "    logging:\n"
        "      driver: json-file\n"
        "      options:\n"
        "        max-size: \"10m\"\n"
        "        max-file: \"5\"\n"
        "  caddy:\n"
        "    image: caddy:2-alpine\n"
        "    logging:\n"
        "      driver: json-file\n"
        "      options:\n"
        "        max-size: \"10m\"\n"
        "        max-file: \"5\"\n"
        "  extra-private-worker:\n"
        "    image: procedural-detective:latest\n",
        encoding="utf-8",
    )
    ok = release_check.check_compose_logging_bounds(tmp_path)
    assert all(f.severity == "ok" for f in ok), [f.render() for f in ok]


def test_f04_no_secret_access_log_rule_restated_in_deployment_docs():
    """The access-log no-secret rule is explicitly restated in deployment docs
    (F-04 doc note) so it cannot silently regress back to token logging."""
    deployment = _normalized((_REPO_ROOT / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8"))
    assert "Access logs must NEVER contain bearer tokens, prompts or secrets" in deployment
    assert "no-secret rule holds at both boundaries" in deployment


# --------------------------------------------------------------------------- #
# P-01 — alternate ingress / TRUST_PROXY operator contract (forged multi-hop)
# --------------------------------------------------------------------------- #


def test_p01_forged_multihop_xff_documented_contract():
    """Forged multi-hop X-Forwarded-For against the REAL resolve_client_ip.

    DOCUMENTED CONTRACT (docs/DEPLOYMENT.md §7 + docker-compose.prod.yml):
    - TRUST_PROXY=false (the default, and the ONLY setting an alternate
      platform ingress may use without proof): the multi-hop forgery is
      IGNORED — the direct socket peer is always the identity.
    - TRUST_PROXY=true: the LEFT-MOST entry is honored. That is safe ONLY
      because the shipped Caddy edge overwrites X-Forwarded-For on its
      private-network hop, so the app sees the true client as left-most. An
      operator who sets TRUST_PROXY=true behind an ingress that does NOT strip
      hostile headers inherits EXACTLY this spoofable result (left-most is the
      attacker's hop) — which is why the phase forbids it until the ingress is
      verified.
    """
    peer = "198.51.100.42"
    forged_multihop = {"x-forwarded-for": "203.0.113.1, 10.0.0.5"}

    # TRUST_PROXY=false: the multi-hop forgery never changes the identity.
    assert (
        resolve_client_ip(_FakeRequest(peer, forged_multihop), trust_proxy=False)
        == peer
    )
    # Same with a single-hop forgery and with no header at all.
    assert (
        resolve_client_ip(_FakeRequest(peer, {"x-forwarded-for": "203.0.113.1"}),
                          trust_proxy=False)
        == peer
    )
    assert resolve_client_ip(_FakeRequest(peer), trust_proxy=False) == peer

    # TRUST_PROXY=true: left-most honored (the Caddy-edge overwrite contract).
    assert (
        resolve_client_ip(_FakeRequest(peer, forged_multihop), trust_proxy=True)
        == "203.0.113.1"
    )
    # A header that Caddy already normalized (true client only) still resolves.
    assert (
        resolve_client_ip(_FakeRequest(peer, {"x-forwarded-for": "203.0.113.7"}),
                          trust_proxy=True)
        == "203.0.113.7"
    )
    # Missing/malformed header falls back to the direct peer even when trusted.
    assert resolve_client_ip(_FakeRequest(peer), trust_proxy=True) == peer
    assert (
        resolve_client_ip(_FakeRequest(peer, {"x-forwarded-for": "   "}),
                          trust_proxy=True)
        == peer
    )


def test_p01_trust_proxy_false_ignores_forgery_and_warns_once(caplog):
    """TRUST_PROXY=false: the forged header triggers the documented ONE-TIME
    operator warning and NEVER changes the identity (multi-hop forgery
    ignored — the exact P-01 vector)."""
    ratelimit_module._forwarded_header_warned = False
    with caplog.at_level(logging.WARNING, logger=ratelimit_module.logger.name):
        assert (
            resolve_client_ip(
                _FakeRequest("198.51.100.42", {"x-forwarded-for": "203.0.113.1, 10.0.0.5"}),
                trust_proxy=False,
            )
            == "198.51.100.42"
        )
        assert (
            resolve_client_ip(
                _FakeRequest("198.51.100.42", {"x-forwarded-for": "203.0.113.9"}),
                trust_proxy=False,
            )
            == "198.51.100.42"
        )
        records = [
            record
            for record in caplog.records
            if record.name == ratelimit_module.logger.name
        ]
        assert len(records) == 1, "the operator warning must fire exactly once"


def test_p01_compose_file_documents_trust_proxy_not_portable():
    """docker-compose.prod.yml explicitly documents the P-01 reinforcement: an
    alternate ingress must keep TRUST_PROXY=false until verified, and
    TRUST_PROXY=true is NOT portable between ingress providers."""
    text = _COMPOSE_PROD.read_text(encoding="utf-8")
    assert "AUTHORITATIVE" in text
    assert "NOT portable between ingress providers" in text
    assert "MUST keep TRUST_PROXY=false" in text
    assert "--no-proxy-headers" in text


def test_p01_deployment_docs_authoritative_caddy_path():
    """docs/DEPLOYMENT.md §7 states the authoritative path + the three
    verification conditions + the multi-hop test reference."""
    deployment = _normalized((_REPO_ROOT / "docs" / "DEPLOYMENT.md").read_text(encoding="utf-8"))
    assert "AUTHORITATIVE supported production path" in deployment
    assert "MUST default to `TRUST_PROXY=false`" in deployment
    assert "strip or overwrite hostile `X-Forwarded-For`" in deployment
    assert "known hop structure" in deployment
    assert "NOT portable between ingress providers" in deployment
    assert "test_phase21_deployment.py" in deployment


# --------------------------------------------------------------------------- #
# F-06 — privacy docs correctness (localStorage) + README quota note
# --------------------------------------------------------------------------- #


def test_f06_privacy_doc_states_browser_local_storage_explicitly():
    """PRIVACY.md explicitly lists the three localStorage items + persistence,
    shared-device, expiry-scope and clearing clarifications."""
    privacy = _normalized((_REPO_ROOT / "docs" / "PRIVACY.md").read_text(encoding="utf-8"))
    for phrase in (
        "scoped playthrough bearer token",
        "playthrough ID",
        "notebook hypothesis pins",
        "persists across page reloads and browser restarts",
        "resume or edit the player's playthrough and see their pinned",
        "PLAYTHROUGH_TOKEN_TTL_SECONDS",
        "Clearing site data",
    ):
        assert phrase in privacy, f"PRIVACY.md must state: {phrase!r}"
    # The old blanket denial is gone (it was the F-06 contradiction).
    assert "None of this is stored in browser storage" not in privacy


def test_f06_readme_quota_renewal_is_automatic_no_restart():
    """README no longer claims the exhausted generation quota needs a backend
    restart: the rolling window renews automatically (Phase 20)."""
    readme = _normalized((_REPO_ROOT / "README.md").read_text(encoding="utf-8"))
    assert "Restart uvicorn for a fresh demo window" not in readme
    assert "renews automatically (Phase 20)" in readme
    assert "a backend RESTART is **not** required" in readme