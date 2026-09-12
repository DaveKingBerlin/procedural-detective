"""Deterministic scripted FakeProvider + CountingProvider (Phase4 D, K).

``FakeProvider`` is fully scriptable, seeded/deterministic and uses NO network.

Script entries (per ``GenerationStage``) are either:

- a raw ``str`` -> returned as ``ProviderResult(content=...)``
- an explicit ``ProviderResult`` -> returned as-is
- a directive string from ``{"ok:<content>", "malformed", "timeout",
  "exception", "pending"}``:

  - ``ok:<content>``  -> ``ProviderResult(content=<content>)``
  - ``malformed``     -> ``ProviderResult(content="<not-json>")``
  - ``timeout``       -> ``ProviderResult(timed_out=True)``
  - ``exception``     -> raises ``ProviderError("scripted failure")``
  - ``pending``       -> ``ProviderResult(pending=True, pending_id=<auto>)``
    WITHOUT invoking the sink

``generate`` pops the next entry for the request's stage and raises
``ProviderError`` when the script is exhausted (so tests detect an unexpected
call). ``resolve(pending_id, result)`` is the ONLY way a "delayed completion"
happens: it synchronously routes ``result`` into the attached ``CompletionSink``
(no threads, no sleeps). Unknown/completed ``pending_id`` -> ``KeyError``.

Seed: ``seed`` may drive any scripted variation generator. Deterministic
variation: the literal token ``{seed}`` inside a raw string (or ``ok:``
content) is replaced with ``str(seed)``, so the SAME script + seed always
reproduces identical outputs.
"""

from __future__ import annotations

from typing import Any, Mapping

from app.generation.provider import (
    GenerateRequest,
    ProviderError,
    ProviderResult,
)

_DIRECTIVE_MALFORMED = "malformed"
_DIRECTIVE_TIMEOUT = "timeout"
_DIRECTIVE_EXCEPTION = "exception"
_DIRECTIVE_PENDING = "pending"
_OK_PREFIX = "ok:"

_SEED_TOKEN = "{seed}"


class FakeProvider:
    """Fully scriptable, seeded, deterministic provider (tests only)."""

    def __init__(
        self,
        script: "dict[Any, list]",  # GenerationStage -> entries
        sink: Any = None,  # CompletionSink | None
        seed: int = 0,
    ) -> None:
        self._script: dict[Any, list] = {
            stage: list(entries) for stage, entries in script.items()
        }
        self._sink = sink
        self.seed = int(seed)
        self.calls: list[GenerateRequest] = []
        self._pending: set[str] = set()
        self._pending_counter = 0

    # -- provider surface -----------------------------------------------------

    def generate(self, request: GenerateRequest) -> ProviderResult:
        self.calls.append(request)
        entries = self._script.get(request.stage)
        if not entries:
            raise ProviderError(
                f"FakeProvider script exhausted for stage {request.stage.value!r}"
            )
        entry = entries.pop(0)
        return self._render_entry(entry)

    # -- deferred-completion surface ------------------------------------------

    def resolve(self, pending_id: str, result: ProviderResult) -> None:
        """Complete a deferred call; routes to the sink synchronously."""
        if pending_id not in self._pending:
            raise KeyError(pending_id)
        self._pending.discard(pending_id)
        if self._sink is not None:
            self._sink.on_completion(pending_id, result)

    def pending_ids(self) -> tuple[str, ...]:
        """Outstanding (unresolved) pending ids, deterministically sorted."""
        return tuple(sorted(self._pending))

    # -- factories ------------------------------------------------------------

    @classmethod
    def from_golden(
        cls,
        stage_payloads: "Mapping[Any, str]",  # GenerationStage -> payload str
        sink: Any = None,
    ) -> "FakeProvider":
        """Build a default success script from fixture payloads."""
        return cls(
            {stage: [payload] for stage, payload in stage_payloads.items()},
            sink=sink,
        )

    # -- internals ------------------------------------------------------------

    def _render_entry(self, entry: Any) -> ProviderResult:
        if isinstance(entry, ProviderResult):
            return entry
        if isinstance(entry, str):
            if entry == _DIRECTIVE_MALFORMED:
                return ProviderResult(content="<not-json>")
            if entry == _DIRECTIVE_TIMEOUT:
                return ProviderResult(timed_out=True)
            if entry == _DIRECTIVE_EXCEPTION:
                raise ProviderError("scripted failure")
            if entry == _DIRECTIVE_PENDING:
                pending_id = f"pending-{self._pending_counter}"
                self._pending_counter += 1
                self._pending.add(pending_id)
                return ProviderResult(pending=True, pending_id=pending_id)
            if entry.startswith(_OK_PREFIX):
                return ProviderResult(
                    content=entry[len(_OK_PREFIX):].replace(_SEED_TOKEN, str(self.seed))
                )
            return ProviderResult(content=entry.replace(_SEED_TOKEN, str(self.seed)))
        raise ProviderError(
            f"FakeProvider script entry must be a str or ProviderResult; got "
            f"{type(entry).__name__}"
        )


class CountingProvider:
    """Wraps any Provider; counts calls and logs requests.

    Used to prove admission rejection consumes zero provider calls: build a
    ``CountingProvider`` around the real fake and assert ``call_count == 0``
    after a rejected admission.
    """

    def __init__(self, inner: Any) -> None:  # inner: Provider
        self.inner = inner
        self.call_count = 0
        self.call_log: list[GenerateRequest] = []

    def generate(self, request: GenerateRequest) -> ProviderResult:
        self.call_count += 1
        self.call_log.append(request)
        return self.inner.generate(request)