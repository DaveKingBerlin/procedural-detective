"""Procedural Detective — Phase 22 BYO-Ollama bridge CLIENT (pd-ollama-bridge).

Connects your local Ollama (localhost only by default) to a Procedural
Detective server over an authenticated outbound WSS session and serves
STRUCTURED_INFERENCE jobs. The bridge exposes exactly ONE capability to the
server: model inference. It can never be told to fetch URLs, read files or run
commands.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]