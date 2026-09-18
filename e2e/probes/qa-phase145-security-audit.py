"""QA-owned Phase 14_5 SECURITY AUDIT (independent; e2e/probes series).

Phase 14_5 gate task 5 against the REAL modules + a real migrated scratch DB:

  1. HOSTILE NOUN / INJECTION — prompt strings whose "objects" masquerade as
     script/URL/path/executable tokens or quoted-JSON directives are
     IGNORED/REJECTED by the bounded extractor: they never become object
     requests, never reach the AssetSpecProvider, and never produce an asset
     id / URL / path. Known-object words inside quoted-JSON framing may only
     yield their SAFE catalog request (the documented deterministic keyword
     contract) — never a structured directive honored.
  2. BOUNDS — a >120-char requested name is rejected at the ObjectRequest /
     AssetSpecRequest construction gates; the extractor's bounded
     MAX_UNKNOWN_PHRASE_WORDS truncation can never exceed the request-name
     limit; the provider budget caps calls at MAX=6 per attempt.
  3. NO PROVIDER EXEC — the provider only ever hands back raw declarative JSON
     text; a spec carrying a URL / path / script / handler field is REJECTED
     by the strict spec validator (never compiled, never requested to run);
     the whole unknown-object path stays declarative.

Run:  python e2e/probes/qa-phase145-security-audit.py
Exit: 0 = all PASS, 1 = any FAIL.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = REPO_ROOT / "backend"
sys.path.insert(0, str(BACKEND_DIR))
sys.path.insert(0, str(BACKEND_DIR / "tests"))

results: list[dict[str, object]] = []


def record(name: str, ok: bool, detail: object) -> None:
    results.append({"name": name, "ok": bool(ok), "detail": detail})
    print(f"{'PASS' if ok else 'FAIL'}: {name} :: {json.dumps(detail, ensure_ascii=False)[:520]}")


def section(title: str) -> None:
    print(f"\n=== {title} ===")


HOSTILE_PROMPTS = {
    "script noun": (
        "The killer used a script blade and a function razor near the body "
        "in the office."
    ),
    "https url noun": "The killer left a https://evil.example/bin near the body in the office.",
    "data url noun": (
        "A data:text/html,<script>alert(1)</script> weapon was used near the body."
    ),
    "file url noun": "The killer used a file:///etc/passwd to stab the victim.",
    "windows path noun": (
        "The tool was a C:\\windows\\system32\\cmd.exe left near the body."
    ),
    "traversal noun": "The murderer used a ..\\..\\..\\evil payload near the body.",
    "javascript url noun": "The murderer used a javascript:alert(1) near the body.",
    "quoted-json directive": (
        'The killer typed {"objects": "wrench on the wall"} near the body '
        "in the office."
    ),
    "onclick nounce": "The killer used a onclick=alert(1) div near the body.",
}


def audit_hostile() -> None:
    section("5a. HOSTILE NOUN / INJECTION -> ignored/rejected, never a provider request")

    from app.assets.generated_cache import GeneratedAssetCache
    from app.assets.spec_provider import FakeAssetSpecProvider
    from app.environments.resolver import resolve_environment
    from app.world.composer import compose_world
    from app.world.extract import extract_world_requirements

    from fixtures.asset_specs_unseen import UNSEEN_SPEC_CONTENT

    summary: dict[str, object] = {}
    all_clean = True
    for label, prompt in HOSTILE_PROMPTS.items():
        reqs = extract_world_requirements(prompt)
        provider = FakeAssetSpecProvider(UNSEEN_SPEC_CONTENT)
        comp = compose_world(
            reqs,
            env_resolver=resolve_environment,
            spec_provider=provider,
            evidence_placements=(),
            catalog=_catalog(),
            kit=_kits()["office"],
            cache=GeneratedAssetCache(),
        )
        joined = json.dumps(
            [p.asset_id for p in comp.placements]
        ).casefold()
        safe = (
            not any(
                marker in (p.asset_id + p.object_id).casefold()
                for p in comp.placements
                for marker in ("script", "onclick=", "eval(", "javascript:", "data:",
                               "file:", "http", "\\", "../", "cmd.exe", "alert(")
            )
        )
        # the unknown-name requests could never carry a hostile string: any
        # request that survived extraction must be a KNOWN catalog object
        # (quoted-json directive -> possibly the wrench) or nothing.
        names = {o.requested_name for o in reqs.objects}
        hostile_names = [
            n for n in names
            if any(m in n for m in ("script", "onclick", "eval", "javascript",
                                    "http", "file:", "\\", "/", "..", "alert"))
        ]
        ok = not hostile_names and safe and len(provider.calls) == 0
        provider_calls = len(provider.calls)
        if not ok:
            all_clean = False
        summary[label] = {
            "objects": sorted(names),
            "providerCalls": provider_calls,
            "issues": list(comp.issues)[:3],
            "placementAssetIds": sorted({p.asset_id for p in comp.placements})[:10],
            "hostileNames": hostile_names,
        }
    record(
        "5a all hostile prompt shapes yield zero provider calls + zero hostile strings in the world",
        all_clean,
        summary,
    )

    # the quoted-JSON directive may only surface the SAFE known object
    directive = HOSTILE_PROMPTS["quoted-json directive"]
    reqs = extract_world_requirements(directive)
    record(
        "5a quoted-JSON directive: at most the safe KNOWN catalog request, never a structured directive",
        all(o.requested_name in {"wrench", "adjustable wrench", "rope", "glass bottle"} or o.requested_name
            not in {r.requested_name for r in reqs.objects}
            for o in reqs.objects)
        and len(reqs.objects) <= 1,
        {"objects": [o.requested_name for o in reqs.objects]},
    )


def audit_bounds() -> None:
    section("5b. BOUNDS (malformed / unbounded requested names + provider budget)")

    from app.assets.spec_provider import (
        MAX_SPEC_REQUEST_NAME_LENGTH,
        SpecRequestBoundError,
    )
    from app.world.composer import SPEC_PROVIDER_CALL_LIMIT
    from app.world.requirements import ObjectRequest, WorldRequirements

    # 1. >120-char requested name rejected at the typed gates
    long_name = "x" * (MAX_SPEC_REQUEST_NAME_LENGTH + 1)
    spec_gate_ok = False
    try:
        from app.assets.spec_provider import AssetSpecRequest

        AssetSpecRequest(requested_name=long_name)
    except SpecRequestBoundError:
        spec_gate_ok = True
    object_gate_ok = False
    try:
        ObjectRequest(requested_name=long_name)
    except ValueError:
        object_gate_ok = True
    record(
        "5b >120-char requested names rejected at AssetSpecRequest AND ObjectRequest gates",
        spec_gate_ok and object_gate_ok,
        {"maxSpecRequestName": MAX_SPEC_REQUEST_NAME_LENGTH,
         "assetSpecRequestRejected": spec_gate_ok,
         "objectRequestRejected": object_gate_ok},
    )

    # 2. extractor bounds: a long unseen phrase is truncated at
    #    MAX_UNKNOWN_PHRASE_WORDS and stays under the request-name limit
    from app.world.extract import MAX_UNKNOWN_PHRASE_WORDS, extract_world_requirements

    long_prompt = (
        "The killer used a monumental gilded ceremonial bronze ice pick with "
        "jewels near the body in the office."
    )
    reqs = extract_world_requirements(long_prompt)
    phrase_words = 0
    for o in reqs.objects:
        phrase_words = max(phrase_words, len(o.requested_name.split()))
        assert len(o.requested_name) <= 120
    record(
        "5b extractor truncates unseen phrases at the documented word bound (never an unbounded name)",
        phrase_words <= MAX_UNKNOWN_PHRASE_WORDS,
        {"maxWords": MAX_UNKNOWN_PHRASE_WORDS, "longestPhraseWords": phrase_words,
         "objects": [o.requested_name for o in reqs.objects]},
    )

    # 3. provider budget documented constant shared with the composer
    record(
        "5b provider-call budget is the documented MAX=6 per attempt",
        SPEC_PROVIDER_CALL_LIMIT == 6,
        {"SPEC_PROVIDER_CALL_LIMIT": SPEC_PROVIDER_CALL_LIMIT},
    )


def audit_noexec() -> None:
    section("5c. NO PROVIDER EXEC (declarative-only spec path)")

    from app.assets.specs import parse_asset_spec

    # 1. a spec smuggled with URL/path/script/handler fields is REJECTED
    hostile_specs = {
        "url field": (
            '{"canonicalName":"X","category":"decor","subtype":"x",'
            '"dimensions":{"x":0.2,"y":0.2,"z":0.2},"url":"https://evil.example",'
            '"parts":[{"id":"p0","role":"base","primitive":"box","transform":{'
            '"position":{"x":0,"y":0,"z":0},"rotation":{"x":0,"y":0,"z":0},'
            '"scale":{"x":0.2,"y":0.2,"z":0.2}},"material":"plastic"}]}'
        ),
        "script material": (
            '{"canonicalName":"X","category":"decor","subtype":"x",'
            '"dimensions":{"x":0.2,"y":0.2,"z":0.2},'
            '"parts":[{"id":"p0","role":"onclick","primitive":"box","transform":{'
            '"position":{"x":0,"y":0,"z":0},"rotation":{"x":0,"y":0,"z":0},'
            '"scale":{"x":0.2,"y":0.2,"z":0.2}},"material":"javascript:alert(1)"}]}'
        ),
        "path in role": (
            '{"canonicalName":"X","category":"decor","subtype":"x",'
            '"dimensions":{"x":0.2,"y":0.2,"z":0.2},'
            '"parts":[{"id":"p0","role":"../../evil","primitive":"box","transform":{'
            '"position":{"x":0,"y":0,"z":0},"rotation":{"x":0,"y":0,"z":0},'
            '"scale":{"x":0.2,"y":0.2,"z":0.2}},"material":"plastic"}]}'
        ),
    }
    detail = {}
    for label, spec in hostile_specs.items():
        result = parse_asset_spec(spec, non_throwing=True)
        # non_throwing=True returns the parsed AssetSpec or None (rejected);
        # a parsed-but-invalid spec would carry deterministic issues.
        detail[label] = "rejected" if result is None else list(result.issues or ())
    record(
        "5c hostile spec fields (url / handler role / script material / path role) rejected by the strict validator",
        all(v == "rejected" or bool(v) for v in detail.values()),
        detail,
    )

    # 2. provider responses are raw text only; the provider never executes
    #    anything — the response carries ONE of content|pending|error and the
    #    oracle only ever PARSES the content (never evaluates it).
    from app.assets.spec_provider import AssetSpecResponse

    try:
        AssetSpecResponse(content="x", error="y")
        two_outcomes = "accepted"
    except ValueError:
        two_outcomes = "rejected"
    record(
        "5c AssetSpecResponse invariant: at most ONE outcome (never content+error together)",
        two_outcomes == "rejected",
        {"responseWithTwoOutcomes": two_outcomes},
    )


def _catalog():
    from app.assets.catalog import load_catalog_from_repo

    return load_catalog_from_repo()


def _kits():
    from app.environments.manifests import load_all_environments

    return {k.environment_id: k for k in load_all_environments()}


def main() -> int:
    audit_hostile()
    audit_bounds()
    audit_noexec()
    out_path = REPO_ROOT / "e2e" / "artifacts" / "qa-phase145-security-audit.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    fails = [r for r in results if not r["ok"]]
    print(f"\nRESULT: {len(results) - len(fails)}/{len(results)} PASS")
    if fails:
        print("FAILURES:")
        for f in fails:
            print(" -", f["name"])
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())