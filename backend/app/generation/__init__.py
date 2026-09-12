"""Phase 4 — generation primitives.

This package holds the provider abstraction (``provider.py``), the scripted
deterministic fake provider (``fake_provider.py``), the narrow live HTTP
adapter (``live_provider.py``), the typed stage-output models (``schemas.py``),
the strict provider-output parser (``parser.py``), generated-content safety
(``safety.py``), and the locked-constraints/prompt-normalization layer
(``constraints.py`` + ``prompt.py``).

The determinisitic Phase 3 deduction domain (``app.domain``) remains the
authority: generated content is data only and must pass strict schema, safety
and solver validation before it can affect a published case.
"""