"""Deterministic server-owned identity source (REQUIREMENTS 7.1, 32.1).

Every generation attempt gets a unique ``generationAttemptId``; every attempt
owns exactly one ``caseId`` and (via the admission layer) exactly one
``anonymousQuotaSessionId``. All three counters are per-instance monotonic
counters with injectable prefixes, so the same script always reproduces
identical ids for identical controller construction (Phase4 2).
"""

from __future__ import annotations


class IdSource:
    """Monotonic per-instance id generators (``GA-N`` / ``CASE-N`` / ``QUOTA-N``)."""

    def __init__(
        self,
        prefix: str = "GA",
        case_prefix: str = "CASE",
        quota_prefix: str = "QUOTA",
    ) -> None:
        if not prefix or not isinstance(prefix, str):
            raise ValueError("prefix must be a non-empty string")
        if not case_prefix or not isinstance(case_prefix, str):
            raise ValueError("case_prefix must be a non-empty string")
        if not quota_prefix or not isinstance(quota_prefix, str):
            raise ValueError("quota_prefix must be a non-empty string")
        self._prefix = prefix
        self._case_prefix = case_prefix
        self._quota_prefix = quota_prefix
        self._attempt_counter = 0
        self._case_counter = 0
        self._quota_counter = 0

    def generation_attempt_id(self) -> str:
        """Return the next unique generation attempt id (``<prefix>-<n>``)."""
        self._attempt_counter += 1
        return f"{self._prefix}-{self._attempt_counter}"

    def case_id(self) -> str:
        """Return the next case id (``<casePrefix>-<n>``)."""
        self._case_counter += 1
        return f"{self._case_prefix}-{self._case_counter}"

    def session_id(self) -> str:
        """Return the next anonymous quota session id (``<quotaPrefix>-<n>``).

        Extension needed by ``AdmissionController.create_anonymous_quota_session``
        so the quota identity is also deterministic per controller instance.
        """
        self._quota_counter += 1
        return f"{self._quota_prefix}-{self._quota_counter}"