"""Phase35 — case-quality reference corpus regression tests (hermetic).

Validates the committed golden/negative corpus under
``tests/fixtures/case_quality``:

- manifest byte-length + SHA-256 fingerprints match the committed files;
- all three Demo golden exports PASS the deterministic case-quality validator
  (``case_quality.validate_case_quality``);
- the real Ollama negative export triggers EXACTLY the four confirmed codes
  (public role truth leak, victim in suspect candidates, witness in suspect
  candidates, witness statement missing);
- ZERO provider calls anywhere in this module.

The goldens/negative are SavegameV1 ``.pdcase`` documents (post-reveal replay
exports). This test reconstructs the PARSED canonical inputs the validator
consumes (PublicCase / CaseTruth / evidence facts / CandidateUniverses) from
the documented savegame sections — the same canonical reconstruction path the
replay runtime uses; it never re-parses raw provider JSON.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.domain.eligibility import derive_universes  # noqa: E402
from app.domain.public import (  # noqa: E402
    PublicCase,
    PublicLocation,
    PublicMotive,
    PublicObject,
    PublicPerson,
    PublicScene,
    PublicTravelRule,
)
from app.domain.truth import CaseTruth, Crime, CrimeTime  # noqa: E402
from app.generation.case_quality import (  # noqa: E402
    PUBLIC_ROLE_TRUTH_LEAK,
    VICTIM_IN_SUSPECT_CANDIDATES,
    WITNESS_IN_SUSPECT_CANDIDATES,
    WITNESS_STATEMENT_MISSING,
    validate_case_quality,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
CORPUS_ROOT = REPO_ROOT / "tests" / "fixtures" / "case_quality"

MANIFEST = CORPUS_ROOT / "manifest.json"

GOLDEN_01 = (
    CORPUS_ROOT
    / "golden"
    / "demo"
    / "procedural-detective-case-CASE-iA2tcy7PkB1P.ok.pdcase"
)
GOLDEN_02 = (
    CORPUS_ROOT
    / "golden"
    / "demo"
    / "procedural-detective-case-CASE-Yw0tGvxleJab.ok.pdcase"
)
GOLDEN_03 = (
    CORPUS_ROOT
    / "golden"
    / "demo"
    / "procedural-detective-case-CASE-htbCd0X0mDkt.ok.pdcase"
)
NEGATIVE_01 = (
    CORPUS_ROOT
    / "negative"
    / "generated"
    / "procedural-detective-case-CASE-LZig0W97AIyW.nok.pdcase"
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _evidence_view(rec: dict) -> SimpleNamespace:
    """A lightweight PARSED canonical evidence view from a savegame record.

    The savegame read-record carries the kind + presentation (title/description
    + the Phase 23 interview-source tags speakerName/statement/witnessId/
    questionType). ``witness_attributed_to`` consumes kind + speakerName (and
    optional proposition person-ids); the savegame does not persist raw
    propositions, so they are empty here (attribution relies on speakerName
    normalization — the same read path the replay runtime uses).
    """
    content = rec.get("content") or {}
    presentation = {
        "title": rec.get("title"),
        "description": rec.get("description"),
    }
    for key in ("speakerName", "statement", "witnessId", "questionType"):
        if content.get(key) is not None:
            presentation[key] = content[key]
    return SimpleNamespace(
        id=rec["evidenceId"],
        kind=rec["kind"],
        presentation=presentation,
        propositions=(),
        discoverable=True,
    )


def canonical_inputs(document: dict):
    """Reconstruct the canonical (public, truth, evidence, universes) of a
    SavegameV1 replay document — the documented canonical reconstruction path.
    """
    case = document["case"]
    public_case = case["publicCase"]
    replay = case["replayTruth"]
    scene = case["scene"]["location"]

    persons = [
        PublicPerson(
            person_id=p["personId"],
            name=p["name"],
            role=p["role"],
            public_affordances=frozenset(p["affordances"]),
        )
        for p in public_case["persons"]
    ]
    motives = [
        PublicMotive(
            motive_id=m["motiveId"],
            label=m["label"],
            public_affordances=frozenset(m["affordances"]),
        )
        for m in public_case["motives"]
    ]
    objects = [
        PublicObject(
            object_id=o["objectId"],
            asset_id=o["assetId"],
            public_affordances=frozenset(o["affordances"]),
            subtype=o.get("subtype"),
        )
        for o in public_case["objects"]
    ]
    locations = [
        PublicLocation(location_id=l["locationId"], name=l["name"])
        for l in public_case["locations"]
    ]
    travel_rules = [
        PublicTravelRule(
            from_location_id=t["fromLocationId"],
            to_location_id=t["toLocationId"],
            travel_time_seconds=t["travelTimeSeconds"],
        )
        for t in public_case["travelRules"]
    ]
    public = PublicCase(
        case_id=public_case["caseId"],
        case_version=public_case["caseVersion"],
        persons=persons,
        motives=motives,
        objects=objects,
        locations=locations,
        travel_rules=travel_rules,
        scene=PublicScene(location_id=scene["locationId"], name=scene["name"]),
    )
    victim = next(
        p["personId"] for p in public_case["persons"] if p["role"] == "victim"
    )
    truth = CaseTruth(
        case_id=public_case["caseId"],
        case_version=1,
        title=case["metadata"]["title"],
        crime=Crime(
            type="murder",
            victim_id=victim,
            murderer_id=replay["murdererId"],
            motive_id=replay["motiveId"],
            weapon_id=replay["weaponId"],
            location_id=scene["locationId"],
            crime_time=CrimeTime(
                canonical=replay["crimeTime"],
                accusation_tolerance_seconds=replay["accusationToleranceSeconds"],
            ),
        ),
    )
    evidence = tuple(_evidence_view(rec) for rec in case["evidence"])
    return public, truth, evidence, derive_universes(public)


# --------------------------------------------------------------------------- #
# manifest fingerprints
# --------------------------------------------------------------------------- #


def test_manifest_fingerprints_match_committed_files():
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert manifest["formatVersion"] == 1
    cases = {entry["id"]: entry for entry in manifest["cases"]}
    assert set(cases) == {
        "demo-golden-01",
        "demo-golden-02",
        "demo-golden-03",
        "ollama-negative-01",
    }
    files = {
        "demo-golden-01": GOLDEN_01,
        "demo-golden-02": GOLDEN_02,
        "demo-golden-03": GOLDEN_03,
        "ollama-negative-01": NEGATIVE_01,
    }
    for entry in manifest["cases"]:
        path = files[entry["id"]]
        assert path.is_file(), path
        assert path.stat().st_size == entry["bytes"], (
            f"{entry['id']}: byte length drifted from the manifest"
        )
        assert _sha256(path) == entry["sha256"], (
            f"{entry['id']}: SHA-256 drifted from the manifest"
        )


# --------------------------------------------------------------------------- #
# golden corpus — must PASS the quality validator
# --------------------------------------------------------------------------- #


def test_golden_01_passes_quality_validator():
    document = json.loads(GOLDEN_01.read_text(encoding="utf-8"))
    codes = validate_case_quality(*canonical_inputs(document))
    assert codes == (), codes


def test_golden_02_passes_quality_validator():
    document = json.loads(GOLDEN_02.read_text(encoding="utf-8"))
    codes = validate_case_quality(*canonical_inputs(document))
    assert codes == (), codes


def test_golden_03_passes_quality_validator():
    document = json.loads(GOLDEN_03.read_text(encoding="utf-8"))
    codes = validate_case_quality(*canonical_inputs(document))
    assert codes == (), codes


def test_all_goldens_share_the_canonical_witness_proof():
    """Each golden witness carries a matching witness_statement (speakerName +
    statement + witnessId) — the canonical completeness pattern §8 expects."""
    for path in (GOLDEN_01, GOLDEN_02, GOLDEN_03):
        document = json.loads(path.read_text(encoding="utf-8"))
        case = document["case"]
        witness_ids = {
            p["personId"]
            for p in case["publicCase"]["persons"]
            if str(p["role"]) == "witness"
        }
        assert witness_ids, f"{path.name}: at least one witness expected"
        statement_witness_ids = {
            rec.get("content", {}).get("witnessId")
            for rec in case["evidence"]
            if rec.get("kind") == "witness_statement"
            and rec.get("content", {}).get("witnessId")
        }
        assert witness_ids <= statement_witness_ids, path.name


# --------------------------------------------------------------------------- #
# negative corpus — exact confirmed codes
# --------------------------------------------------------------------------- #


def test_negative_triggers_confirmed_quality_codes():
    document = json.loads(NEGATIVE_01.read_text(encoding="utf-8"))
    codes = validate_case_quality(*canonical_inputs(document))
    assert set(codes) == {
        PUBLIC_ROLE_TRUTH_LEAK,
        VICTIM_IN_SUSPECT_CANDIDATES,
        WITNESS_IN_SUSPECT_CANDIDATES,
        WITNESS_STATEMENT_MISSING,
    }, codes


def test_negative_witness_lacks_statement_evidence():
    """The negative witness (nina_weber, REMOTE_STATEMENT) has ZERO
    witness_statement evidence — the witness-completeness primary defect."""
    document = json.loads(NEGATIVE_01.read_text(encoding="utf-8"))
    case = document["case"]
    assert any(
        str(p["role"]) == "witness" for p in case["publicCase"]["persons"]
    ), "negative case should carry a public witness"
    statement_kinds = [rec["kind"] for rec in case["evidence"]]
    assert "witness_statement" not in statement_kinds


# --------------------------------------------------------------------------- #
# provider isolation — zero provider calls
# --------------------------------------------------------------------------- #


def test_corpus_tests_touch_zero_providers(monkeypatch):
    """No transport/HTTP/LLM call may ever be made while validating the corpus.

    The corpus path is PURE domain + generation-quality code; any accidental
    provider import/call would be a hermeticity regression.
    """
    import socket

    def _deny(*args, **kwargs):
        raise AssertionError("network access blocked during corpus tests")

    monkeypatch.setattr(socket, "socket", _deny)
    for path in (GOLDEN_01, GOLDEN_02, GOLDEN_03, NEGATIVE_01):
        document = json.loads(path.read_text(encoding="utf-8"))
        codes = validate_case_quality(*canonical_inputs(document))
        assert isinstance(codes, tuple)
