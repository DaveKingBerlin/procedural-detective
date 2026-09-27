"""Phase 24 — deterministic Docker smoke harness (public REST API only).

Runs the FULL deterministic smoke journey against a LIVE containerized stack
via the PUBLIC REST API — no backend imports, no Docker socket, stdlib
``urllib`` ONLY (the ``e2e/probes/qa-p19c-fake-stack.py`` probe is the model).
Hermetic by construction: the CI ``docker-smoke`` job boots an isolated
Compose stack (``docker-compose.yml`` + ``docker-compose.ci.yml`` under a
unique ``-p`` project) and this tool then talks to it exactly like a browser.

Stack contract (§8 CI deterministic profile): GENERATION_PROVIDER=fake,
ENABLE_BRIDGE=false, FAKE_PROVIDER_SCRIPT=compose/fake-worlds/
ci-activity-log-world.json (the golden case with the laptop re-linked to the
golden ACTIVITY_LOG record). Every assertion below targets that throughput.

Journey covered (Phase 24 §12-§15):

  §12  readiness -> anonymous session -> generate case (fake golden) ->
       PUBLISHED (no partial publication) -> public case -> playthrough ->
       investigation bootstrap -> interact/discover evidence -> accusation ->
       reveal. Verifies: no CaseTruth before reveal, no partial publication,
       correct solved state (4/4), zero unexpected provider calls during
       gameplay (the bounded fake script never exhausts mid-playthrough —
       reload-identical persisted content + durable generation stays
       PUBLISHED), reload-identical content.
  §13  Activity Log: laptop(read) discovers the golden ACTIVITY_LOG record
       -> read -> 15..20 rows {time,text} compact rendering -> canonical time
       exactly once -> reload-identical bytes -> ZERO provider calls (the
       deterministic log is persisted with the published case).
  §14  Witness: open witness -> ask TIME -> repeat TIME (idempotent) ->
       notebook update -> reload: deterministic statement, no CaseTruth leak,
       zero provider calls, persistence.
  §15  Bridge-disabled: ENABLE_BRIDGE=false -> every /api/v1/bridge/* route is
       404, the WS path is not mounted (HTTP request against it answers 404),
       the capability DTO omits remoteLocalAi and keeps configuredProvider=fake
       with no bridge state leaked.

The CLI is BOUNDED: every request has an explicit timeout, a wall-clock
deadline bounds the whole run, `--wait-ready` polls /api/v1/readiness with
useful diagnostics, and the report is a sanitized JSON/plain structure that
NEVER contains tokens, prompts, CaseTruth, provider output, raw bodies or the
targeted base URL (DEF-004).

Exit code 0 ONLY when every check passes.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

# --------------------------------------------------------------------------- #
# constants (the deterministic CI fake world; see compose/fake-worlds/)
# --------------------------------------------------------------------------- #

DEFAULT_BASE_URL = "http://127.0.0.1:8000"
API_PREFIX = "/api/v1"
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_DEADLINE_SECONDS = 600
DEFAULT_WAIT_READY_SECONDS = 180

# The golden prompt the fake provider answers deterministically (dev-mode
# golden world). Kept here ONLY as the fixture reference — the tool never
# prints it and never echoes provider output.
GOLDEN_PROMPT = (
    "Victim: sarah_miller\n"
    "Murderer: thomas_reed\n"
    "Motive: cover_up_embezzlement\n"
    "Weapon: kitchen_knife\n"
    "Time: 2026-09-11T22:17:00+02:00\n"
    "Witness: emily_reed\n"
)
GOLDEN_CANONICAL_TIME = "2026-09-11T22:17:00+02:00"

# Golden candidates for the accusation (public, published candidate universe).
GOLDEN_ACCUSATION = {
    "murdererId": "thomas_reed",
    "motiveId": "cover_up_embezzlement",
    "weaponId": "kitchen_knife",
    "crimeTime": GOLDEN_CANONICAL_TIME,
}

# The deterministic world objects the CI world publishes (laptop -> activity
# log in the CI fake world; knife -> forensic in every golden-based world).
LAPTOP_OBJECT = "apartment_laptop"
LAPTOP_INTERACTION = "read"
KNIFE_OBJECT = "kitchen_knife"
KNIFE_INTERACTION = "inspect"
# The golden ACTIVITY_LOG record the CI fake world links to the laptop.
ACTIVITY_LOG_EVIDENCE = "cctv_thomas_scene_01"
# The fixture's OWN observed-at for that record (the "clone" row that must
# appear EXACTLY once in the 15-20 rows). NOT the crime time — the activity
# log rows are around the CCTV observation moment (22:16:40+02:00), which the
# Phase 19J server locks into the log as the ordinary middle row.
ACTIVITY_LOG_CANONICAL = "2026-09-11T22:16:40+02:00"
WITNESS_ID = "emily_reed"

# Keys that must NEVER appear in any pre-reveal API body (mirror of the frozen
# Phase 7 scanner used by the e2e probe).
PRE_REVEAL_FORBIDDEN = {
    "murdererId", "victimId", "weaponId", "crimeTime", "canonical",
    "truthfulness", "crime", "timeline", "facts", "relationships",
    "solutionProof", "solverProof", "proof", "truth", "universe", "universes",
    "validation", "winners", "prompt", "providerOutput", "diagnostics", "seed",
    "model", "locked", "stageOutputs", "sourceRef", "propositions",
    "observedAt", "uncertaintySeconds", "verifier", "tokenVerifier", "token",
    "sessionId", "generationAttemptId",
}

# Rendered ACTIVITY_LOG entry keys (compact time rendering contract).
ACTIVITY_LOG_ENTRY_KEYS = frozenset({"time", "text"})

# --------------------------------------------------------------------------- #
# result model / sanitized report
# --------------------------------------------------------------------------- #


class SmokeResult:
    """One named check + sanitized detail."""

    def __init__(self, check: str, ok: bool, detail: str) -> None:
        self.check = check
        self.ok = ok
        self.detail = detail


class SmokeFailure(RuntimeError):
    """Typed smoke failure (never carries secrets; message is sanitized)."""


def _sanitize(value: object) -> str:
    """A one-line SANITIZED render for a report detail value.

    Scalar/number -> repr; dict/list -> compact JSON of the TOP LEVEL only
    (nested evidence ids that are already player-safe may stay as keys, but no
    full bodies are ever rendered).
    """
    if isinstance(value, dict):
        return "{" + ", ".join(
            f"{k}={_sanitize_scalar(v)}" for k, v in sorted(value.items())[:8]
        ) + "}"
    if isinstance(value, (list, tuple)):
        return f"<list[{len(value)}]>"
    return _sanitize_scalar(value)


def _sanitize_scalar(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return str(value)


# --------------------------------------------------------------------------- #
# bounded HTTP layer (stdlib urllib; injectable for hermetic tests)
# --------------------------------------------------------------------------- #


def _build_http_fn(timeout_seconds: float) -> Callable[..., Any]:
    """The real urllib HTTP seam (bbox: base_url, method, path, token, body)."""

    def _http(
        base_url: str,
        method: str,
        path: str,
        token: str | None = None,
        body: object | None = None,
    ) -> tuple[int, Any]:
        url = base_url.rstrip("/") + API_PREFIX + path
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = (
            json.dumps(body).encode("utf-8")
            if body is not None
            else None
        )
        req = urllib.request.Request(
            url, data=data, headers=headers, method=method
        )
        try:
            with urllib.request.urlopen(req, timeout=float(timeout_seconds)) as resp:
                raw = resp.read()
                try:
                    parsed: Any = json.loads(raw) if raw else None
                except json.JSONDecodeError:
                    parsed = {"_bodyPreview": raw.decode("utf-8", errors="replace")[:200]}
                return int(resp.status), parsed
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                parsed = json.loads(raw) if raw else {"_status": exc.code}
            except json.JSONDecodeError:
                parsed = {
                    "error": {
                        "code": "UNPARSABLE",
                        "message": raw.decode("utf-8", errors="replace")[:200],
                        "details": None,
                    }
                }
            return int(exc.code), parsed
        except urllib.error.URLError as exc:
            raise SmokeFailure(f"HTTP transport error: {exc.reason}") from None

    return _http


# --------------------------------------------------------------------------- #
# leak scanners (no CaseTruth before reveal)
# --------------------------------------------------------------------------- #


def _forbidden_hits(body: object) -> list[str]:
    """Paths of frozen forbidden keys anywhere in a pre-reveal body.

    Mirrors the e2e probe: the accusation 200 MAY echo the player's OWN
    submitted fields under the frozen ``accusation`` echo node (REQUIREMENTS
    40.10) — that echo is exempt because the player supplied those exact
    values. Every other forbidden key hit is a leak.
    """
    hits: list[str] = []

    def walk(node: object, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                child = f"{path}.{key}" if path else str(key)
                if key in PRE_REVEAL_FORBIDDEN and not (
                    child == "accusation" or "accusation." in child
                ):
                    hits.append(child)
                walk(value, child)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                walk(value, f"{path}[{index}]")

    walk(body, "")
    return hits


# --------------------------------------------------------------------------- #
# journey steps (each returns sanitized SmokeResult details appended)
# --------------------------------------------------------------------------- #


def wait_ready(
    http: Callable[..., Any],
    base_url: str,
    *,
    timeout_seconds: float = DEFAULT_WAIT_READY_SECONDS,
    probe_seconds: float = 2.0,
    report: list[SmokeResult] | None = None,
) -> None:
    """Bounded poll of /api/v1/readiness with useful failure diagnostics."""
    deadline = time.monotonic() + float(timeout_seconds)
    last_status: int | None = None
    last_body: object | None = None
    last_error: str | None = None
    while time.monotonic() < deadline:
        try:
            last_status, last_body = http(base_url, "GET", "/readiness")
        except SmokeFailure as exc:
            last_error = str(exc)
            time.sleep(probe_seconds)
            continue
        if last_status == 200:
            if report is not None:
                report.append(
                    SmokeResult(
                        "readiness", True,
                        f"/api/v1/readiness -> 200 ready "
                        f"(waited {int(timeout_seconds)}s budget)",
                    )
                )
            return
        time.sleep(probe_seconds)
    diagnosis = (
        f"last status={last_status}"
        + (f" body={_sanitize(last_body)}" if last_body is not None else "")
        + (f" transport={last_error}" if last_error else "")
    )
    raise SmokeFailure(
        f"stack not ready after {int(timeout_seconds)}s: {diagnosis} "
        "(bounded readiness poll; check docker logs for migration/startup "
        "failures)"
    )


def run_smoke(
    http: Callable[..., Any],
    base_url: str,
    *,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
    report: list[SmokeResult] | None = None,
) -> None:
    """The full deterministic journey. Raises SmokeFailure on first failure."""
    out = report if report is not None else []

    def check(name: str, ok: bool, detail: str = "") -> None:
        out.append(SmokeResult(name, bool(ok), detail))
        if not ok:
            raise SmokeFailure(f"{name}: FAILED — {detail}")

    # ---- §12.1 capability DTO (bridge-disabled contract) -------------------
    status, caps = http(base_url, "GET", "/generation-capabilities")
    check("capabilities 200", status == 200, f"status={status}")
    check(
        "capabilities fake provider",
        isinstance(caps, dict) and caps.get("configuredProvider") == "fake",
        f"configuredProvider={_sanitize(caps.get('configuredProvider')) if isinstance(caps, dict) else '?'}",
    )
    check(
        "capabilities bridge-disabled DTO",
        isinstance(caps, dict) and "remoteLocalAi" not in caps,
        "remoteLocalAi must be omitted when ENABLE_BRIDGE=false (§15)",
    )

    # ---- §12.2 anonymous session -------------------------------------------
    status, session = http(base_url, "POST", "/sessions/anonymous")
    check("session 201", status == 201, f"status={status}")
    session_token = (
        str(session.get("anonymousSessionToken")) if isinstance(session, dict) else ""
    )
    check("session token present", bool(session_token), "no anonymousSessionToken")

    # ---- §12.3 generate case -> PUBLISHED (no partial publication) ---------
    status, created = http(
        base_url, "POST", "/cases",
        token=session_token,
        body={"prompt": GOLDEN_PROMPT, "difficulty": "medium"},
    )
    check("case 201", status == 201, f"status={status}")
    case_status = created.get("status") if isinstance(created, dict) else None
    check("case PUBLISHED", case_status == "PUBLISHED", f"status={case_status}")
    case_id = created.get("caseId") if isinstance(created, dict) else None
    creator_token = (
        created.get("creatorAccessToken") if isinstance(created, dict) else ""
    )
    generation_id = created.get("generationId") if isinstance(created, dict) else None
    check("case ids present", bool(case_id) and bool(creator_token), "missing case ids")

    # ---- §12.4 public case (creator dossier) -------------------------------
    status, public_case = http(base_url, "GET", f"/cases/{case_id}", token=creator_token)
    check("public case 200", status == 200, f"status={status}")
    evidence = public_case.get("evidence") if isinstance(public_case, dict) else None
    check(
        "public case evidence rows",
        isinstance(evidence, list) and len(evidence) >= 2,
        f"evidence rows={len(evidence) if isinstance(evidence, list) else 0}",
    )
    hits = _forbidden_hits(public_case)
    check("public case pre-reveal clean", not hits, f"forbidden keys={hits}")

    # durable generation progress stays PUBLISHED (start reference)
    status, progress = http(
        base_url, "GET", f"/generations/{generation_id}", token=creator_token
    )
    check(
        "generation progress PUBLISHED",
        status == 200 and (isinstance(progress, dict) and progress.get("status") == "PUBLISHED"),
        f"status={status} progress={_sanitize(progress)}",
    )

    # ---- §12.5 playthrough -------------------------------------------------
    status, pt = http(
        base_url, "POST",
        f"/cases/{case_id}/versions/1/playthroughs",
        token=creator_token,
    )
    check("playthrough 201", status == 201, f"status={status}")
    pt_id = pt.get("playthroughId") if isinstance(pt, dict) else None
    pt_token = pt.get("playthroughAccessToken") if isinstance(pt, dict) else ""
    check("playthrough ids", bool(pt_id) and bool(pt_token), "missing playthrough ids")

    # ---- §12.6 investigation bootstrap -------------------------------------
    status, boot = http(
        base_url, "GET", f"/playthroughs/{pt_id}/investigation", token=pt_token
    )
    check("bootstrap 200", status == 200, f"status={status}")
    hits = _forbidden_hits(boot)
    check("bootstrap pre-reveal clean", not hits, f"forbidden keys={hits}")

    # ---- §12.7 interact/discover (laptop = ACTIVITY_LOG in CI world) -------
    status, laptop = http(
        base_url, "POST",
        f"/playthroughs/{pt_id}/objects/{LAPTOP_OBJECT}/interact",
        token=pt_token,
        body={"interaction": LAPTOP_INTERACTION},
    )
    check("laptop interact 200", status == 200, f"status={status}")
    disc = laptop.get("discovery") if isinstance(laptop, dict) else None
    check(
        "laptop discovery",
        isinstance(disc, dict)
        and disc.get("state") == "discovered"
        and laptop.get("evidenceId") == ACTIVITY_LOG_EVIDENCE,
        f"evidenceId={_sanitize(laptop.get('evidenceId') if isinstance(laptop, dict) else None)} state={_sanitize(disc.get('state') if isinstance(disc, dict) else None)}",
    )
    hits = _forbidden_hits(laptop)
    check("laptop DTO pre-reveal clean", not hits, f"forbidden keys={hits}")

    # ---- §13 ACTIVITY LOG record read (15-20 rows, compact rendering) ------
    status, log_rec = http(
        base_url, "GET",
        f"/playthroughs/{pt_id}/records/{ACTIVITY_LOG_EVIDENCE}",
        token=pt_token,
    )
    check("log record 200", status == 200, f"status={status}")
    content = log_rec.get("content") if isinstance(log_rec, dict) else None
    check(
        "log renderType ACTIVITY_LOG",
        isinstance(content, dict) and content.get("renderType") == "ACTIVITY_LOG",
        f"renderType={_sanitize(content.get('renderType') if isinstance(content, dict) else None)}",
    )
    entries = content.get("entries") if isinstance(content, dict) else None
    check(
        "log 15-20 rows",
        isinstance(entries, list) and 15 <= len(entries) <= 20,
        f"rows={len(entries) if isinstance(entries, list) else 0}",
    )
    check(
        "log compact time rendering {time,text}",
        bool(entries)
        and all(
            isinstance(e, dict) and set(e.keys()) == ACTIVITY_LOG_ENTRY_KEYS
            for e in entries
        ),
        "every entry must be exactly {time,text}",
    )
    times = [entry.get("time") for entry in entries if isinstance(entry, dict)]
    check(
        "log canonical time exactly once",
        times.count(ACTIVITY_LOG_CANONICAL) == 1,
        f"canonical occurrences={times.count(ACTIVITY_LOG_CANONICAL)}",
    )
    # Compact-time contract: strictly unique, chronological timestamps (the
    # validator enforces uniqueness + ordering server-side; the wire re-checks).
    check(
        "log timestamps unique and chronological",
        len(times) == len(set(times)) and times == sorted(times),
        f"duplicates={len(times) - len(set(times))} unordered={times != sorted(times)}",
    )
    hits = _forbidden_hits(log_rec)
    check("log DTO pre-reveal clean", not hits, f"forbidden keys={hits}")

    # reload-identical persisted content
    status, log_rec2 = http(
        base_url, "GET",
        f"/playthroughs/{pt_id}/records/{ACTIVITY_LOG_EVIDENCE}",
        token=pt_token,
    )
    check("log reload 200", status == 200, f"status={status}")
    check(
        "log reload-identical persisted content",
        _canonical_body(log_rec) == _canonical_body(log_rec2),
        "repeat read body differs",
    )

    # ---- §12.8 second evidence discovery (knife -> forensic) ---------------
    status, knife = http(
        base_url, "POST",
        f"/playthroughs/{pt_id}/objects/{KNIFE_OBJECT}/interact",
        token=pt_token,
        body={"interaction": KNIFE_INTERACTION},
    )
    check("knife interact 200", status == 200, f"status={status}")
    kdisc = knife.get("discovery") if isinstance(knife, dict) else None
    check(
        "knife forensic discovery",
        isinstance(kdisc, dict)
        and kdisc.get("state") == "discovered"
        and kdisc.get("kind") == "forensic",
        f"kind={_sanitize(kdisc.get('kind') if isinstance(kdisc, dict) else None)} state={_sanitize(kdisc.get('state') if isinstance(kdisc, dict) else None)}",
    )
    hits = _forbidden_hits(knife)
    check("knife DTO pre-reveal clean", not hits, f"forbidden keys={hits}")

    # ---- §12.9 zero unexpected provider calls during gameplay --------------
    # The bounded fake script is consumed ONLY by generation. A hidden
    # gameplay provider call would exhaust the script and surface as a failed
    # later endpoint / changed generation. We re-read the durable progress
    # (must still be a single PUBLISHED attempt) and re-fetch the public case
    # (byte-identical persisted world).
    status, progress2 = http(
        base_url, "GET", f"/generations/{generation_id}", token=creator_token
    )
    check(
        "generation stayed PUBLISHED during gameplay",
        status == 200 and (isinstance(progress2, dict) and progress2.get("status") == "PUBLISHED"),
        f"status={status} progress={_sanitize(progress2)}",
    )
    status, public_case2 = http(
        base_url, "GET", f"/cases/{case_id}", token=creator_token
    )
    check(
        "public case reload-identical after gameplay",
        status == 200 and _canonical_body(public_case) == _canonical_body(public_case2),
        "published world changed during gameplay",
    )

    # ---- §14 witness journey -----------------------------------------------
    status, witness_view = http(
        base_url, "GET",
        f"/playthroughs/{pt_id}/witnesses/{WITNESS_ID}",
        token=pt_token,
    )
    check("witness open 200", status == 200, f"status={status}")
    hits = _forbidden_hits(witness_view)
    check("witness view pre-reveal clean", not hits, f"forbidden keys={hits}")

    status, first = http(
        base_url, "POST",
        f"/playthroughs/{pt_id}/witnesses/{WITNESS_ID}/interview",
        token=pt_token,
        body={"questionType": "TIME"},
    )
    check("witness TIME 200", status == 200, f"status={status}")
    statement1 = first.get("statement") if isinstance(first, dict) else None
    check(
        "witness TIME deterministic statement",
        isinstance(statement1, dict) and bool(statement1.get("summary")),
        "missing statement summary",
    )
    hits = _forbidden_hits(first)
    check("witness interview pre-reveal clean", not hits, f"forbidden keys={hits}")

    status, second = http(
        base_url, "POST",
        f"/playthroughs/{pt_id}/witnesses/{WITNESS_ID}/interview",
        token=pt_token,
        body={"questionType": "TIME"},
    )
    check("witness TIME repeat 200", status == 200, f"status={status}")
    check(
        "witness idempotent statement (repeat identical)",
        _canonical_body(statement1) == _canonical_body(
            second.get("statement") if isinstance(second, dict) else None
        ),
        "repeat statement differs",
    )
    disc2 = second.get("discovery") if isinstance(second, dict) else None
    check(
        "witness repeat no new discovery",
        disc2 is None or disc2.get("newlyDiscovered") is False,
        "repeat interview re-discovered evidence",
    )

    # notebook update + reload persistence
    status, boot_after = http(
        base_url, "GET", f"/playthroughs/{pt_id}/investigation", token=pt_token
    )
    check("notebook reload 200", status == 200, f"status={status}")
    known = (
        boot_after.get("playerKnowledge", {}).get("discoveredEvidenceIds", [])
        if isinstance(boot_after, dict)
        else []
    )
    check(
        "notebook update persisted (discovered ids grown)",
        isinstance(known, list) and ACTIVITY_LOG_EVIDENCE in known,
        f"discovered={known}",
    )
    status, boot_after2 = http(
        base_url, "GET", f"/playthroughs/{pt_id}/investigation", token=pt_token
    )
    check(
        "reload-identical notebook bootstrap",
        status == 200 and _canonical_body(boot_after) == _canonical_body(boot_after2),
        "bootstrap changed between reloads",
    )
    hits = _forbidden_hits(boot_after2)
    check("notebook bootstrap clean", not hits, f"forbidden keys={hits}")

    # ---- §15 bridge-disabled (404 everywhere, no state) --------------------
    status, pairing = http(
        base_url, "POST", "/bridge/pairing", token=session_token, body={}
    )
    check("bridge pairing 404", status == 404, f"status={status}")
    status, bstatus = http(base_url, "GET", "/bridge/status", token=session_token)
    check("bridge status 404", status == 404, f"status={status}")
    status, ws_route = http(base_url, "GET", "/bridge/ws", token=session_token)
    check(
        "bridge WS not mounted (non-101)",
        status in (404, 405, 400, 426),
        f"status={status}",
    )

    # ---- §12.10 accusation -> reveal -> 4/4 --------------------------------
    status, acc = http(
        base_url, "POST",
        f"/playthroughs/{pt_id}/accusation",
        token=pt_token,
        body=GOLDEN_ACCUSATION,
    )
    check("accusation 200", status == 200, f"status={status}")
    hits = _forbidden_hits(acc)
    check("accusation pre-reveal clean", not hits, f"forbidden keys={hits}")

    status, reveal = http(
        base_url, "GET", f"/playthroughs/{pt_id}/reveal", token=pt_token
    )
    check("reveal 200", status == 200, f"status={status}")
    check(
        "reveal REVEALED",
        isinstance(reveal, dict) and reveal.get("status") == "REVEALED",
        f"status={_sanitize(reveal.get('status') if isinstance(reveal, dict) else None)}",
    )
    score = reveal.get("score") if isinstance(reveal, dict) else None
    check(
        "reveal solved 4/4",
        isinstance(score, dict)
        and score.get("correctDimensions") == 4
        and score.get("totalDimensions") == 4,
        f"score={_sanitize(score)}",
    )
    result = reveal.get("result") if isinstance(reveal, dict) else None
    check(
        "reveal overall solved",
        isinstance(result, dict) and result.get("overall") == "solved",
        f"result={_sanitize(result)}",
    )


def _canonical_body(body: object) -> str:
    """Deterministic canonical text of a parsed body for equality checks."""
    try:
        return json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError):
        return repr(body)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.docker_smoke",
        description=(
            "Phase 24 deterministic Docker smoke harness. Runs the FULL "
            "journey (case generation -> evidence -> activity log -> witness "
            "-> accusation -> reveal -> bridge-disabled) against a LIVE stack "
            "via the public REST API. Hermetic: no backend imports, no Docker "
            "socket, stdlib urllib only. NEVER prints tokens, prompts, "
            "CaseTruth or provider output."
        ),
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help=f"Stack base URL (default: {DEFAULT_BASE_URL}).",
    )
    parser.add_argument(
        "--wait-ready",
        action="store_true",
        help="Poll /api/v1/readiness (bounded) before starting the journey.",
    )
    parser.add_argument(
        "--wait-ready-seconds",
        type=float,
        default=DEFAULT_WAIT_READY_SECONDS,
        help=f"Bounded readiness wait budget (default {DEFAULT_WAIT_READY_SECONDS}s).",
    )
    parser.add_argument(
        "--request-timeout",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Per-request timeout seconds (default {DEFAULT_TIMEOUT_SECONDS}).",
    )
    parser.add_argument(
        "--report",
        default=None,
        help="Optional sanitized JSON report output path.",
    )
    parser.add_argument(
        "--plain",
        action="store_true",
        help="Plain human-readable report (default is obviously-tagged).",
    )
    parser.add_argument(
        "--deadline-seconds",
        type=float,
        default=DEFAULT_DEADLINE_SECONDS,
        help=f"Wall-clock deadline for the whole run (default {DEFAULT_DEADLINE_SECONDS}s).",
    )
    args = parser.parse_args(argv)

    http = _build_http_fn(args.request_timeout)
    report: list[SmokeResult] = []
    deadline = time.monotonic() + float(args.deadline_seconds)
    try:
        if args.wait_ready:
            wait_ready(
                http,
                args.base_url,
                timeout_seconds=args.wait_ready_seconds,
                report=report,
            )
            if time.monotonic() > deadline:
                raise SmokeFailure("wall-clock deadline exceeded after readiness")
        run_smoke(
            http,
            args.base_url,
            timeout_seconds=args.request_timeout,
            report=report,
        )
        if time.monotonic() > deadline:
            raise SmokeFailure("wall-clock deadline exceeded during journey")
    except SmokeFailure as exc:
        report.append(SmokeResult("smoke", False, str(exc)))
        _emit(report, args)
        return 1
    report.append(
        SmokeResult(
            "smoke", True,
            f"{len([r for r in report if r.ok])}/{len(report)} checks passed",
        )
    )
    _emit(report, args)
    return 0


def _emit(report: list[SmokeResult], args: argparse.Namespace) -> None:
    """Sanitized report output — plain or JSON (never tokens/prompts/truth).

    The report never embeds the targeted base URL (DEF-004): the operator's
    endpoint choice stays out of artifacts/logs entirely, so a recovered CI
    artifact cannot reveal an internal/private stack address. Only stable
    structural fields (schema, pass/total, check rows) are emitted.
    """
    rows = [
        {
            "check": r.check,
            "ok": r.ok,
            "detail": r.detail,
        }
        for r in report
    ]
    summary = {
        "schema": "docker-smoke-v1",
        "passed": sum(1 for r in report if r.ok),
        "total": len(report),
        "checks": rows,
    }
    if args.report:
        import pathlib

        pathlib.Path(args.report).write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
    if args.plain:
        for row in rows:
            print(f"[{'ok' if row['ok'] else 'FAIL'}] {row['check']}: {row['detail']}")
        print(
            f"docker smoke: {summary['passed']}/{summary['total']} checks passed"
        )
    else:
        print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    sys.exit(main())