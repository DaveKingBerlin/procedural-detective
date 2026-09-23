"""Phase 21B Finding 7 — FAIL-CLOSED production preflight CLI.

One command that validates the EFFECTIVE production deployment — not just the
YAML/example defaults. It runs the SAME stdlib-only check suite as
``tools.release_check`` but with the STRICT ready-to-host verdict on
``CADDY_DOMAIN``:

    python -m tools.prod_preflight [--allow-local]

Checks (all fail-closed; ANY ``fail`` finding exits 1):

  1. prod env profile (check_prod_env_profile) — DEV (.env.example) vs PROD
     (.env.production.example) examples stay a clean split;
  2. effective configuration (check_prod_effective_config) with
     ``allow_local`` from ``--allow-local`` (default False = strict):
       - ENVIRONMENT=production, PD_DEV_TRACE=false, TRUST_PROXY=true (the
         shipped Caddy-edge profile). An operator ``.env`` that sets
         ENVIRONMENT=development / TRUST_PROXY=false FAILS with a clear message
         (Phase21B Finding 2 — dev example must never be copied to prod);
       - P-02 timeout envelope: backend deadline < frontend 360s < proxy 420s;
       - json-file 10m x 5 log bounds on BOTH public services;
       - backend port NEVER publicly published (expose only, no `ports:`) and
         the Ollama port 11434 is never a published host port;
       - CADDY_DOMAIN is a real (non-localhost, non-empty) public domain —
         FAILS the ready-to-host verdict unless --allow-local (local smoke);
       - production frontend bundle is same-origin/clean when present;
  3. compose logging bounds (F-04) — repeated as its own finding.

The preflight reads the working-tree ``.env`` for the OPTIONAL interpolation it
MUST validate, but only the public validation keys (ENVIRONMENT, PD_DEV_TRACE,
TRUST_PROXY, CADDY_DOMAIN, CASE_GENERATION_DEADLINE_SECONDS) — no configured
value is ever printed. When no ``.env`` exists, the documented
``.env.production.example`` is the interpolation source.

Exit code: 0 ONLY when every check passes.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

from tools import release_check  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="prod_preflight",
        description=(
            "FAIL-CLOSED production preflight: validates the EFFECTIVE "
            "docker-compose.prod.yml deployment (post-interpolation), the "
            "DEV/PROD env-example split, timeouts, log bounds, port exposure "
            "and the CADDY_DOMAIN ready-to-host verdict. See "
            "tools/prod_preflight.py for the full check list."
        ),
    )
    parser.add_argument(
        "--allow-local",
        action="store_true",
        help=(
            "Accept CADDY_DOMAIN=localhost/empty as the local-smoke default "
            "(REPORT instead of FAIL). Do NOT pass this for a public hosting "
            "certification."
        ),
    )
    args = parser.parse_args(argv)

    findings: list[release_check.Finding] = []
    findings.extend(release_check.check_prod_env_profile(_REPO_ROOT))
    findings.extend(
        release_check.check_prod_effective_config(_REPO_ROOT, allow_local=args.allow_local)
    )
    findings.extend(release_check.check_compose_logging_bounds(_REPO_ROOT))

    # De-duplicate: the effective-config check already reports log-bound
    # problems (fail-closed reuse) — keep the standalone finding only when the
    # bound blocks pass.
    seen_fails = {f.message for f in findings if f.severity == "fail"}
    for finding in list(findings):
        if (
            finding.check == "compose-logging-bounds"
            and finding.severity == "fail"
            and finding.message in seen_fails
        ):
            findings.remove(finding)
            seen_fails.discard(finding.message)

    seen: set[tuple[str, str, str]] = set()
    unique: list[release_check.Finding] = []
    for finding in findings:
        key = (finding.check, finding.severity, finding.message)
        if key in seen:
            continue
        seen.add(key)
        unique.append(finding)

    for finding in unique:
        print(finding.render())
    failures = [f for f in unique if f.severity == "fail"]
    reports = [f for f in unique if f.severity == "report"]
    print()
    if failures:
        print(f"production preflight: {len(unique)} findings, "
              f"{len(failures)} FAILING (first: {failures[0].message})")
        return 1
    print(f"production preflight: ALL {len(unique)} findings OK "
          "(ready-to-host configuration validated)" +
          (f" with {len(reports)} REPORT-only notes" if reports else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())