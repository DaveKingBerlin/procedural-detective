"""FAIL-CLOSED production preflight CLI.

One command that validates Docker Compose's rendered production deployment,
including current shell overrides. It runs the same check suite as
``tools.release_check`` with the strict ready-to-host verdict on
``CADDY_DOMAIN``:

    python -m tools.prod_preflight [--allow-local] [--env-file PATH]

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
       - CADDY_DOMAIN is a plausible non-reserved FQDN — local, IP, malformed
         and reserved/example names FAIL the ready-to-host verdict. DNS and
         certificate issuance remain part of the public TLS smoke;
       - production frontend bundle is same-origin/clean when present;
  3. compose logging bounds (F-04) — repeated as its own finding.

The preflight invokes ``docker compose config --format json`` with the current
process environment and project directory. Compose automatically uses ``.env``
just as the documented startup command does. If startup uses a different
``--env-file``, pass that exact path here too. The rendered model, configured
endpoints and credentials are never printed.

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
            "Accept an explicit localhost/.localhost/.local hostname as a "
            "local-smoke REPORT. Empty, malformed, IP and reserved/example "
            "names still FAIL. Do NOT pass this for public certification."
        ),
    )
    parser.add_argument(
        "--ingress-profile",
        choices=("caddy", "alternate"),
        default="caddy",
        help=(
            "Ingress being certified. The shipped Caddy profile requires "
            "TRUST_PROXY=true. An alternate ingress defaults to "
            "TRUST_PROXY=false and fails closed until a separate deployment "
            "configuration and edge verification exist."
        ),
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help=(
            "Use the same explicit Compose --env-file as the real startup "
            "command. Omit it for the documented automatic .env behavior."
        ),
    )
    args = parser.parse_args(argv)

    findings: list[release_check.Finding] = []
    findings.extend(release_check.check_prod_env_profile(_REPO_ROOT))
    findings.extend(
        release_check.check_prod_effective_config(
            _REPO_ROOT,
            allow_local=args.allow_local,
            ingress_profile=args.ingress_profile,
            compose_env_file=args.env_file,
        )
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
    verdict = (
        "local-smoke configuration validated; NOT a ready-to-host verdict"
        if args.allow_local
        else "ready-to-host configuration validated"
    )
    print(f"production preflight: ALL {len(unique)} findings OK "
          f"({verdict})" +
          (f" with {len(reports)} REPORT-only notes" if reports else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
