"""Phase 14 — world validation + repair integration tests.

Covers the WORld validation bucket and the deterministic world-repair loop at
the service level:

- an invalid composition (unresolved required object / evidence on a
  non-evidence-capable anchor) routes through the configured world repair
  provider with STRUCTURED, sanitized diagnostics; the repaired composition
  runs the COMPLETE validation pipeline again and publishes; LOCKED fields are
  never passed to the repair provider and never change;
- a locked-constraint violation stays TERMINAL (never repaired, never
  published) — this is the EXISTING pipeline guarantee (repair must not
  change locked fields) extended to the world layer.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.generation.pipeline import normalize_prompt  # noqa: E402
from app.generation.state_machine import GenerationState  # noqa: E402
from app.world.requirements import ObjectRequest, WorldRequirements  # noqa: E402

from fixtures.golden_generation import GOLDEN_LOCKED  # noqa: E402
from phase5_helpers import (  # noqa: E402
    GOLDEN_PROMPT,
    golden_script,
    held_published,
    seed_session,
)


def _service_ctx(url, world_repair_provider):
    """Migrated store + service over ``url`` with a deterministic repair hook."""
    from app.persistence.store import Store

    from app.services.generation import GenerationService
    from conftest import upgrade_db
    from phase5_helpers import _phase5_settings_for

    upgrade_db(url)
    store = Store(url)
    settings = _phase5_settings_for(url)
    service = GenerationService(
        settings=settings,
        store=store,
        world_repair_provider=world_repair_provider,
    )
    return store, service


def _held_record(url, store):
    script = golden_script(store, url)
    return held_published(url, script, seed=11)


class RecordingWorldRepair:
    """Deterministic world-repair script: records diagnostics, returns a
    revised WorldRequirements (or None)."""

    def __init__(self, revised=None):
        self.calls: list[tuple[str, ...]] = []
        self.revised = revised

    def __call__(self, diagnostics):
        self.calls.append(tuple(sorted(diagnostics) if diagnostics else ()))
        return self.revised


# --------------------------------------------------------------------------- #
# unresolved required object -> repair -> full revalidate -> PUBLISHED
# --------------------------------------------------------------------------- #


def test_repair_resolves_unresolved_object_and_publishes_keeping_locks(
    database_url,
):
    from app.persistence.store import Store

    store, service = _service_ctx(
        database_url,
        world_repair_provider=RecordingWorldRepair(revised=WorldRequirements()),
    )
    published, record, _session_id, _clock = _held_record(database_url, store)
    assert record.state is GenerationState.PUBLISHED
    locked_before = record.published.locked

    crafted = WorldRequirements(
        objects=(ObjectRequest(requested_name="quantum woggle"),)
    )
    ok = service._apply_kit_composition(record, "apartment", crafted, None)
    assert ok is True
    assert record.state is GenerationState.PUBLISHED
    # the world-repair provider was consulted with the structured diagnostics
    assert service._world_repair_provider.calls
    diagnostics = service._world_repair_provider.calls[0]
    assert any("world.unresolved-object" in issue for issue in diagnostics)
    # diagnostics are sanitized: no paths, no URLs, no prompt-like internals
    joined = " ".join(diagnostics)
    for marker in ("http://", "https://", "C:", "\\backend\\", "Traceback", ".."):
        assert marker not in joined
    # the repaired (empty) composition placed the golden apartment and the
    # COMPLETE validation pipeline ran again on the composed draft
    assert record.published.draft.scene.environment_id == "apartment"
    assert record.published.report is not None
    assert record.published.report.valid is True or record.last_validation.valid is True
    # locked constraints are untouched (never passed to the provider)
    assert record.published.locked == locked_before
    assert record.published.locked == GOLDEN_LOCKED
    # no bogus asset ever reached the world
    assert all("woggle" not in p.object_id for p in record.published.draft.world_graph.placements)


def test_repair_removes_invalid_evidence_placement_and_publishes(database_url):
    from app.persistence.store import Store

    store, service = _service_ctx(
        # the world repair drops the invalid request entirely
        database_url,
        world_repair_provider=RecordingWorldRepair(revised=WorldRequirements()),
    )
    _published, record, _session_id, _clock = _held_record(database_url, store)
    crafted = WorldRequirements(
        objects=(
            ObjectRequest(
                requested_name="window",
                evidence_id="forensic_knife_match_01",
            ),
        )
    )
    ok = service._apply_kit_composition(record, "apartment", crafted, None)
    assert ok is True
    assert record.state is GenerationState.PUBLISHED
    # the crafted evidence-window never reached the world
    assert all("window" not in p.object_id for p in record.published.draft.world_graph.placements)
    # the golden knife placement survives the repair loop untouched
    knife = next(
        p for p in record.published.draft.world_graph.placements
        if p.object_id == "kitchen_knife"
    )
    assert knife.evidence_id == "forensic_knife_match_01"
    assert knife.interaction == "inspect"
    diagnostics = service._world_repair_provider.calls[0]
    assert any("world.invalid-placement" in i or "world.unreachable-evidence" in i for i in diagnostics)


def test_repair_without_provider_degrades_but_never_crashes(database_url):
    from app.persistence.store import Store

    store, service = _service_ctx(database_url, world_repair_provider=None)
    _published, record, _session_id, _clock = _held_record(database_url, store)
    crafted = WorldRequirements(
        objects=(ObjectRequest(requested_name="quantum woggle"),)
    )
    ok = service._apply_kit_composition(record, "apartment", crafted, None)
    assert ok is False  # documented degradation, never a crash
    assert record.state is GenerationState.PUBLISHED
    assert record.published.draft.scene.environment_id == "apartment"


# --------------------------------------------------------------------------- #
# locked-constraint violation -> TERMINAL, never repaired
# --------------------------------------------------------------------------- #


def test_locked_murderer_violation_stays_terminal_never_repaired(database_url):
    from app.persistence.store import Store
    from app.persistence.timebase import EpochClock

    store, service = _service_ctx(
        database_url,
        world_repair_provider=RecordingWorldRepair(revised=WorldRequirements()),
    )
    seed_session(store, "SESS-1", EpochClock())

    # The prompt locks a WRONG murderer; the (golden) generated draft has
    # thomas_reed -> TERMINAL in the pipeline, long before any world repair.
    wrong_prompt = (
        "Victim: sarah_miller\n"
        "Murderer: walter_white\n"
        "Motive: cover_up_embezzlement\n"
        "Weapon: kitchen_knife\n"
        "Time: 2026-09-11T22:17:00+02:00\n"
        "Witness: emily_reed\n"
    )
    started = service.start_case_generation(
        wrong_prompt,
        anonymous_quota_session_id="SESS-1",
    )
    assert started.status == "FAILED", started
    # the world repair provider was NEVER consulted (nothing to repair)
    assert service._world_repair_provider.calls == []
    # nothing was published for this attempt
    assert store.get_published(started.case_id, 1) is None


def test_world_repair_never_receives_locked_constraints(database_url):
    from app.persistence.store import Store

    store, service = _service_ctx(
        database_url,
        world_repair_provider=RecordingWorldRepair(revised=WorldRequirements()),
    )
    _published, record, _session_id, _clock = _held_record(database_url, store)
    crafted = WorldRequirements(
        objects=(ObjectRequest(requested_name="quantum woggle"),)
    )
    service._apply_kit_composition(record, "apartment", crafted, None)
    diagnostics = service._world_repair_provider.calls[0]
    joined = " ".join(diagnostics)
    # locked field names / values never leak into the repair request
    assert "thomas_reed" not in joined
    assert "murderer" not in joined
    assert "locked" not in joined


def test_golden_default_prompt_never_triggers_world_repair(database_url):
    from app.persistence.store import Store

    store, service = _service_ctx(
        database_url,
        world_repair_provider=RecordingWorldRepair(revised=WorldRequirements()),
    )
    _published, record, _session_id, _clock = _held_record(database_url, store)
    locked, _note = normalize_prompt(GOLDEN_PROMPT, max_chars=4000)
    from app.world.extract import extract_world_requirements

    world_reqs = extract_world_requirements(GOLDEN_PROMPT, locked)
    ok = service._apply_kit_composition(record, "apartment", world_reqs, None)
    assert ok is True
    # the legacy byte-identity path never consults the world repair provider
    assert service._world_repair_provider.calls == []
    assert len(record.published.draft.world_graph.placements) == 9