"""Phase 24 — bounded CI readiness poll (GitLab docker-smoke helper).

Polls the deterministic stack's readable REST readiness endpoint until it
reports 200 (or the bounded budget expires). NO arbitrary fixed sleeps: every
probe is a real HTTP request spaced by a short interval, always bounded by
``--timeout``. On failure it prints useful diagnostics (last status, last
sanitized body, transport error) so a job can quickly tell a startup/migration
defect from a runner problem.

The Phase 24 gitlab ``docker-smoke`` job calls this before the deterministic
smoke journey (``python -m tools.ci_wait_ready --base-url ...``). The smoke
harness also exposes the same wait inline (``python -m tools.docker_smoke
--wait-ready``); the standalone primitive keeps the wait reusable and gives the
pipeline a bounded, diagnostically-rich readiness gate.

Exit code 0 when ready; 1 when the bounded budget is exhausted (never before).
Stdlib only. The repository-owned ``scripts/`` wrapper location is reserved by
the RAD governance permission map; this tools/-level module is the canonical
helper the pipeline consumes.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

API_PREFIX = "/api/v1"


def _probe(
    base_url: str,
    timeout_seconds: float,
    *,
    insecure_tls: bool = False,
) -> tuple[int, object | None, str | None]:
    url = base_url.rstrip("/") + API_PREFIX + "/readiness"
    try:
        context = None
        if insecure_tls:
            import ssl

            # Explicit operator opt-in for the LOCAL/TEST Caddy edge whose
            # internal CA is not in the runner's trust store (prod-like smoke
            # §39). NEVER passed for a public topology: the default stays
            # full certificate verification.
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout_seconds, context=context) as resp:
            raw = resp.read()
            try:
                parsed: object = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                parsed = {"_bodyPreview": raw.decode("utf-8", errors="replace")[:200]}
            return int(resp.status), parsed, None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw) if raw else {"_status": exc.code}
        except json.JSONDecodeError:
            parsed = {"_status": exc.code}
        return int(exc.code), parsed, None
    except urllib.error.URLError as exc:
        return -1, None, str(exc.reason)


def wait_ready(
    base_url: str,
    *,
    timeout_seconds: float = 180,
    interval_seconds: float = 2,
    request_timeout_seconds: float = 10,
    insecure_tls: bool = False,
) -> int:
    deadline = time.monotonic() + float(timeout_seconds)
    last_status: int | None = None
    last_body: object | None = None
    last_error: str | None = None
    started = time.monotonic()
    while time.monotonic() < deadline:
        status, body, error = _probe(
            base_url, request_timeout_seconds, insecure_tls=insecure_tls
        )
        if status == 200:
            elapsed = int(time.monotonic() - started)
            print(
                f"ci-wait-ready: /api/v1/readiness -> 200 ready "
                f"(waited {elapsed}s, budget {int(timeout_seconds)}s)"
            )
            return 0
        last_status, last_body, last_error = status, body, error
        time.sleep(interval_seconds)
    print(
        f"ci-wait-ready: NOT ready after {int(timeout_seconds)}s — "
        f"last status={last_status}"
        + (f" body={json.dumps(last_body, sort_keys=True)[:300]}" if last_body is not None else "")
        + (f" transport={last_error}" if last_error else ""),
        file=sys.stderr,
    )
    print(
        "diagnostics: check `docker compose logs` for migration/startup "
        "failures, `docker compose ps` for the healthcheck state, and the "
        "uvicorn entrypoint output. Do NOT increase the budget without "
        "investigating first.",
        file=sys.stderr,
    )
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tools/ci_wait_ready.py",
        description="Bounded poll of /api/v1/readiness with useful diagnostics.",
    )
    parser.add_argument(
        "--base-url", default="http://127.0.0.1:8000",
        help="Stack base URL (default: http://127.0.0.1:8000).",
    )
    parser.add_argument(
        "--timeout", type=float, default=180,
        help="Bounded readiness wait budget in seconds (default 180).",
    )
    parser.add_argument(
        "--interval", type=float, default=2,
        help="Poll interval in seconds (default 2).",
    )
    parser.add_argument(
        "--request-timeout", type=float, default=10,
        help="Per-request timeout in seconds (default 10).",
    )
    parser.add_argument(
        "--insecure-tls", action="store_true",
        help=(
            "Skip TLS certificate verification — ONLY for the local/TEST Caddy "
            "edge (prod-like smoke §39) whose internal CA is not in the runner "
            "trust store. Never use on a public topology; the default keeps "
            "full certificate verification."
        ),
    )
    args = parser.parse_args(argv)
    return wait_ready(
        args.base_url,
        timeout_seconds=args.timeout,
        interval_seconds=args.interval,
        request_timeout_seconds=args.request_timeout,
        insecure_tls=args.insecure_tls,
    )


if __name__ == "__main__":
    sys.exit(main())