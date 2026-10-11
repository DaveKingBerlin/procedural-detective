"""Trusted case-resolution model — the CLOSED resolution vocabulary.

PHASE 41 (Phase41.md §3): every case truth resolves to exactly one of the two
closed resolution types:

    HOMICIDE   the victim was killed by another person (``murderer_id``
               required, never the victim);
    SUICIDE    the death was self-inflicted (``murderer_id`` absent/null, the
               public victim is the implicit subject of the self-inflicted
               outcome).

A death is NEVER encoded as ``murderer_id == victim_id``.

Legacy rule (Phase41.md §4): any truth/crime WITHOUT a ``resolution_type`` and
WITH a non-null ``murderer_id`` infers HOMICIDE — existing cases keep working
byte-for-byte.

MODULE BOUNDARIES: this is a PURE trusted-value module. It never imports
``app.domain.truth`` (so the derivation helpers can be used by any layer) and
it never imports Pydantic (it stays a plain frozen dataclass/enum pair that
can never be picked up as a public DTO schema). It is not a solver input.
"""

from __future__ import annotations

from enum import Enum
from typing import Any


class ResolutionType(str, Enum):
    """The CLOSED trusted resolution vocabulary (Phase41.md §3).

    ``ACCIDENT`` is deliberately NOT added in Phase 41 — it may be a future
    extension (any provider/request token outside this closed set is rejected).
    """

    HOMICIDE = "HOMICIDE"
    SUICIDE = "SUICIDE"


def resolution_of(crime: Any) -> ResolutionType:
    """The trusted resolution of a crime-like object (``resolution_type`` or
    the documented legacy inference).

    Legacy rule: a crime with NO ``resolution_type`` and a non-null
    ``murderer_id`` is HOMICIDE (Phase41.md §4). Because the trusted
    ``Crime`` dataclass enforces that a missing murderer + no resolution can
    never exist, the fallback here is purely defensive: non-null murderer OR
    no identifiable suicide signal -> HOMICIDE.
    """
    raw = getattr(crime, "resolution_type", None)
    if raw is not None and not isinstance(raw, str):
        raw = getattr(raw, "value", None)
    if raw in ("HOMICIDE", "SUICIDE"):
        return ResolutionType(raw)
    if raw is None:
        # Legacy inference: absent resolution + a non-null murderer id is a
        # homicide; SUICIDE truths always carry the explicit resolution.
        murderer = getattr(crime, "murderer_id", None)
        if murderer is None or murderer == "":
            raise ValueError(
                "crime is neither HOMICIDE nor SUICIDE: a null murderer "
                "requires an explicit SUICIDE resolution_type"
            )
        return ResolutionType.HOMICIDE
    raise ValueError(f"unsupported resolution_type: {raw!r}")


__all__ = ["ResolutionType", "resolution_of"]