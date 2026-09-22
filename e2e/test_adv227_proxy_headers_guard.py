"""ADV-227 guard regression (QA-owned, e2e/) — DEF-094 launcher surface.

ADV-227 (LOW, phase-20 final re-audit): the two tracked QA e2e backend
launchers call ``uvicorn.run(...)`` programmatically, so the CLI flag
``--no-proxy-headers`` cannot appear in them; the programmatic equivalent is
the ``proxy_headers=False`` keyword argument. This guard, owned by QA under
``e2e/``, closes the DEF-094 blind spot for the e2e launcher surface:
whenever a tracked e2e backend launcher invokes ``uvicorn.run`` it MUST pass
``proxy_headers=False``, so a future edit cannot silently reopen the
forwarded-header identity-rotation vector.

``backend/tests/test_phase20_def094_proxy_headers.py`` is the canonical home
of the DEF-094 launcher guard, but the QA runtime's permission configuration
denies edits under ``backend/tests/*``; per the ADV-227 handoff note this
guard test body is delivered for the orchestrator to land there verbatim.
This file is the executable copy that runs in the QA-owned tree right now:

    python -m pytest e2e/test_adv227_proxy_headers_guard.py -q
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# The two e2e launchers identified by ADV-227 (git ls-files-verified).
_QA_UVICORN_RUN_LAUNCHERS = (
    "e2e/qa-phase16-backend.py",
    "e2e/qa-phase145-backend.py",
)


def test_adv227_qa_e2e_uvicorn_run_launchers_disable_proxy_headers():
    """Every QA-owned e2e backend launcher that calls ``uvicorn.run`` must
    pass ``proxy_headers=False`` (the no-proxy-headers equivalent for the
    programmatic API). Without it, uvicorn's platform default
    ``--proxy-headers`` (trusting loopback) rewrites request.client from a
    hostile X-Forwarded-For before ``resolve_client_ip`` runs."""
    for relative in _QA_UVICORN_RUN_LAUNCHERS:
        path = REPO_ROOT / relative
        text = path.read_text(encoding="utf-8", errors="replace")
        if "uvicorn.run" not in text:
            continue  # a launcher that stops launching uvicorn has no flag to guard
        assert "proxy_headers=False" in text, (
            f"{relative} calls uvicorn.run without proxy_headers=False — "
            f"uvicorn's platform default --proxy-headers would rewrite "
            f"request.client from a hostile X-Forwarded-For before the app "
            f"runs (DEF-094/ADV-227 identity-rotation vector)"
        )
        # Be precise: the flag must be on the uvicorn.run call itself.
        line = next(
            ln for ln in text.splitlines() if "uvicorn.run" in ln
        )
        assert "proxy_headers=False" in line, (
            f"{relative} uvicorn.run call lacks proxy_headers=False: {line.strip()}"
        )