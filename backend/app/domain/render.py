"""Phase 19G — closed evidence-rendering model (deterministic, player-safe).

Two responsibilities:

1. ``EvidenceRenderType`` — the CLOSED render-type vocabulary. The render type
   is DECLARATIVE metadata ONLY: it can never map to dynamic code, component
   names, scripts or templates (Phase19G §3). It is ALWAYS derived
   deterministically from the evidence ``kind`` via the frozen
   ``KIND_TO_RENDER_TYPE`` table — the provider/raw payload is never consulted
   for a renderer name, so hostile ``presentation.renderType`` metadata is
   structurally impossible to trust (it is not even read).

2. ``render_payload_of`` — the deterministic player-safe evidence payload that
   ``publication.project_read_content`` emits for a DISCOVERED record. Only
   allowlisted content ever appears; the entry/time projection reads ONLY:

   - the fact's public ``presentation`` (title/description/timestamp/events);
   - the parsed ``propositions[].observed_at`` strings of the published payload
     (a tight, single-key read — never the proposition object itself, never
     any solver/truth material).

   The concrete player-visible time clues come from those allowlisted anchors
   (Phase19G §6 WHEN guarantee): an ACTIVITY_LOG/TIMELINE payload carries
   ``entries: [{time, text}]`` instead of leaving the player with only
   "around the locked time".

   Purity: this module performs ZERO provider/LLM calls and never touches the
   payload's hidden sections (``truth`` / ``solverProof`` / ``universes`` /
   ``report`` / ``seed`` / ``model`` / ``prompt`` / ``locked``).
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Mapping

from app.domain.time_interval import parse_iso8601_to_epoch


class EvidenceRenderType(str, Enum):
    """The frozen, CLOSED evidence render types (Phase19G §3).

    The enum VALUE is what is emitted in the DTO; the set is closed — a payload
    can never carry a renderer name that is not one of these values.
    """

    GENERIC_TEXT = "GENERIC_TEXT"
    ACTIVITY_LOG = "ACTIVITY_LOG"
    FORENSIC_COMPARISON = "FORENSIC_COMPARISON"
    MESSAGE = "MESSAGE"
    DOCUMENT = "DOCUMENT"
    BODY_OBSERVATION = "BODY_OBSERVATION"
    TIMELINE = "TIMELINE"


# Frozen kind -> render-type map (Phase19G §3 "use existing project terminology
# where equivalent concepts exist"). The mapping is the SINGLE derivation rule:
# ``render_type_for_kind`` returns exactly one of the closed enum VALUES for
# ANY kind, falling back to GENERIC_TEXT so unknown valid evidence never breaks
# the UI (Phase19G §8 GENERIC_TEXT fallback).
KIND_TO_RENDER_TYPE: dict[str, EvidenceRenderType] = {
    # activity/access/device-log class (laptop log, security/access log, CCTV)
    "cctv": EvidenceRenderType.ACTIVITY_LOG,
    "cctv_observation": EvidenceRenderType.ACTIVITY_LOG,
    "view_record": EvidenceRenderType.ACTIVITY_LOG,
    # messages / documents / readable records
    "email": EvidenceRenderType.MESSAGE,
    "document": EvidenceRenderType.DOCUMENT,
    "digital": EvidenceRenderType.DOCUMENT,
    "financial": EvidenceRenderType.DOCUMENT,
    # forensic / physical comparison class
    "forensic": EvidenceRenderType.FORENSIC_COMPARISON,
    "physical": EvidenceRenderType.FORENSIC_COMPARISON,
    # witness statement / body-observation class
    "testimonial": EvidenceRenderType.BODY_OBSERVATION,
    "witness_statement": EvidenceRenderType.BODY_OBSERVATION,
    "statement": EvidenceRenderType.BODY_OBSERVATION,
    "suspect_statement": EvidenceRenderType.BODY_OBSERVATION,
    # time-bearing witness observations render as a player-read timeline
    "witness_observation": EvidenceRenderType.TIMELINE,
    # world-object note class stays a generic safe text panel
    "object": EvidenceRenderType.GENERIC_TEXT,
}

DEFAULT_RENDER_TYPE = EvidenceRenderType.GENERIC_TEXT

# Frozen neutral entry text; used ONLY when a time-bearing fact's presentation
# carries neither title nor description (never reveal solver/truth material).
ENTRY_TEXT_FALLBACK = "Recorded activity"


def render_type_for_kind(kind: str) -> str:
    """Deterministic closed render type for an evidence kind (string value).

    Any kind — including unknown/crafted ones — maps to exactly one of the
    closed enum values. The raw kind string can never leak into the DTO as a
    renderer name.
    """
    return KIND_TO_RENDER_TYPE.get(str(kind), DEFAULT_RENDER_TYPE).value


def _safe_str(value: Any) -> str:
    """A plain string or '' — hostile/non-string values stay inert text."""
    return value if isinstance(value, str) else ""


def _presentation_of(fact: Mapping[str, Any]) -> Mapping[str, Any]:
    presentation = fact.get("presentation")
    return presentation if isinstance(presentation, Mapping) else {}


def _event_entries_of(
    presentation: Mapping[str, Any], title: str
) -> list[dict[str, str]]:
    """Chronological entries derived from the ALLOWLISTED ``events`` list.

    ``time`` and ``text`` come from the allowlisted event fields
    (``time`` + ``action``). Events whose time does not parse, or that are not
    mappings, are skipped deterministically.
    """
    events = presentation.get("events")
    if not isinstance(events, list):
        return []
    raw: list[tuple[int, str, str]] = []
    for event in events:
        if not isinstance(event, Mapping):
            continue
        raw_time = _safe_str(event.get("time"))
        if not raw_time:
            continue
        try:
            epoch = parse_iso8601_to_epoch(raw_time)
        except ValueError:
            continue  # unverifiable time -> never invent
        text = _safe_str(event.get("action")) or title or ENTRY_TEXT_FALLBACK
        raw.append((epoch, raw_time, text))
    return _dedupe_sorted_entries(raw)


def _observed_at_anchors_of(fact: Mapping[str, Any]) -> list[tuple[int, str]]:
    """(epoch, iso) anchors parsed from the published propositions'
    ``observed_at`` strings — the ONLY proposition field ever read here.

    The propositions carry the canonical time-bearing facts (e.g. driver
    ``d_ev_when_obs`` CRIME_SCENE_OBSERVATION_AT with observed_at). Only
    parseable ISO-8601-with-offset strings enter; everything else is skipped
    deterministically (same rule as the reveal timeline).
    """
    anchors: list[tuple[int, str]] = []
    for prop in fact.get("propositions") or ():
        if not isinstance(prop, Mapping):
            continue
        raw_at = _safe_str(prop.get("observed_at"))
        if not raw_at:
            continue
        try:
            anchors.append((parse_iso8601_to_epoch(raw_at), raw_at))
        except ValueError:
            continue
    return anchors


def _dedupe_sorted_entries(
    raw: list[tuple[int, str, str]],
) -> list[dict[str, str]]:
    """Deterministic order + dedupe: chronological by epoch, ties by text then
    raw time, exact (time, text) pairs deduplicated (repeat reads are
    byte-identical — Phase19G §10)."""
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for _epoch, raw_time, text in sorted(raw, key=lambda item: (item[0], item[2], item[1])):
        key = (raw_time, text)
        if key in seen:
            continue
        seen.add(key)
        out.append({"time": raw_time, "text": text})
    return out


def render_entries_of(fact: Mapping[str, Any]) -> list[dict[str, str]]:
    """Deterministic player-safe time entries of one evidence fact.

    - when the allowlisted ``events`` list carries concrete event times, the
      entries come from those events (``events.time`` + ``events.action``);
    - otherwise, when the fact is time-bearing (propositions carry parseable
      ``observed_at`` anchors), ONE entry per unique anchor is synthesized from
      the presentation text — the Phase19G §2 deterministic synthesis. This is
      what makes the d_ev_when_* / cctv activity records show e.g.
      ``22:17 — Activity logged at the scene`` instead of only
      "around the locked time";
    - facts with neither events nor time anchors return [] (no ``entries`` key
      is emitted — never an empty list, never a fabricated time).
    """
    presentation = _presentation_of(fact)
    title = _safe_str(presentation.get("title"))
    description = _safe_str(presentation.get("description"))
    event_entries = _event_entries_of(presentation, title)
    if event_entries:
        return event_entries
    anchors = _observed_at_anchors_of(fact)
    if not anchors:
        return []
    fallback_text = title or description or ENTRY_TEXT_FALLBACK
    unique: list[tuple[int, str, str]] = []
    seen_times: set[str] = set()
    for epoch, raw_time in sorted(anchors, key=lambda item: (item[0], item[1])):
        if raw_time in seen_times:
            continue
        seen_times.add(raw_time)
        unique.append((epoch, raw_time, fallback_text))
    return [
        {"time": raw_time, "text": fallback_text}
        for _epoch, raw_time, _text in unique
    ]


def _comparison_of(presentation: Mapping[str, Any]) -> str:
    """The player-visible forensic comparison result string.

    Kind forensic/physical evidence already carries the result in its PUBLIC
    presentation (e.g. "Forensic comparison identifies the locked object as the
    source." / "Blood on the kitchen knife matches the victim."); the most
    informative of the two allowlisted title/description strings is emitted
    (longest; ties resolve to the title — deterministic).
    """
    title = _safe_str(presentation.get("title"))
    description = _safe_str(presentation.get("description"))
    candidates = [text for text in (title, description) if text]
    return max(candidates, key=len) if candidates else ""


def render_payload_of(
    fact: Mapping[str, Any],
    *,
    content: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The Phase19G player-safe evidence payload of ONE evidence fact.

    The returned mapping is the ``content`` of the read-record DTO. It ALWAYS
    carries the closed ``renderType`` and the safe title/description-level
    ``summary``, then the type-specific fields and the kind-allowlisted
    ``content`` keys (the explicit Phase 6 allowlist, applied by the caller).

    Only values from the allowlisted public sections ever appear; the hidden
    payload sections are never read. All values are plain strings/bools/lists
    of strings/ISO-time strings.
    """
    kind = _safe_str(fact.get("kind"))
    render_type = render_type_for_kind(kind)
    presentation = _presentation_of(fact)
    title = _safe_str(presentation.get("title"))
    description = _safe_str(presentation.get("description"))
    summary = description or title  # safe description-level text (never '' if title exists)

    payload: dict[str, Any] = {
        "renderType": render_type,
        "summary": summary,
    }
    if render_type == EvidenceRenderType.FORENSIC_COMPARISON.value:
        # Phase19G §8 FORENSIC_COMPARISON: the actual player-visible result.
        payload["comparison"] = _comparison_of(presentation)
    if render_type in (
        EvidenceRenderType.ACTIVITY_LOG.value,
        EvidenceRenderType.TIMELINE.value,
    ):
        entries = render_entries_of(fact)
        if entries:
            payload["entries"] = entries
    if content:
        payload.update(dict(content))
    return payload


__all__ = [
    "DEFAULT_RENDER_TYPE",
    "ENTRY_TEXT_FALLBACK",
    "EvidenceRenderType",
    "KIND_TO_RENDER_TYPE",
    "render_entries_of",
    "render_payload_of",
    "render_type_for_kind",
]