"""Procedural Detective — Phase 22 BYO-Ollama bridge CLIENT (pd-ollama-bridge).

Connects your local Ollama (localhost only by default) to a Procedural
Detective server over an authenticated outbound WSS session and serves
STRUCTURED_INFERENCE jobs. The bridge exposes exactly ONE capability to the
server: model inference. It can never be told to fetch URLs, read files or run
commands.
"""

# The SINGLE authoritative bridge version (Phase 49 §4). ``pyproject.toml``
# consumes this attribute dynamically (``dynamic = ["version"]``) and the CLI
# ``--version`` reads this same constant, so the source, the CLI, the package
# metadata, the build tooling and the release manifest can never drift apart.
__version__ = "1.0.0"

__all__ = ["__version__"]