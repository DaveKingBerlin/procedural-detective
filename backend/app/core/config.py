"""Application configuration (REQUIREMENTS section 45).

A single pydantic-settings object is the one source of runtime configuration.
Exactly one canonical environment-variable name per setting (no duplicate
aliases). Values are resolved with this precedence:

1. values passed to ``Settings(...)`` at construction (tests, callers);
2. real environment variables (canonical names from section 45);
3. an optional dotenv file: the path given by ``ENV_FILE`` when set, otherwise
   ``<repo-root>/.env`` derived from the package location (only when present);
4. defaults below.

No ``.env`` file is ever required.
"""

from __future__ import annotations

import ipaddress
import os
import re
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import (
    BaseSettings,
    DotEnvSettingsSource,
    NoDecode,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)
from sqlalchemy.engine.url import make_url
from sqlalchemy.exc import ArgumentError

SERVICE_NAME = "procedural-detective"
SERVICE_VERSION = "0.1.0"

# backend/app/core/config.py -> 3 levels up is the backend dir, 4 is the repo root.
BACKEND_DIR = Path(__file__).resolve().parents[2]
REPO_ROOT = Path(__file__).resolve().parents[3]


def _default_database_url() -> str:
    """Absolute SQLite URL anchored at the repository root (DEF-018).

    The default must never depend on the process CWD: an operator who migrates
    from the repo root and serves from backend/ must address one and the same
    database file. Only the URL string is defined here — no file is created.
    """
    return f"sqlite:///{(REPO_ROOT / 'procedural_detective.db').as_posix()}"


# ---------------------------------------------------------------------------
# Phase 16 — local Ollama provider configuration surface (safe defaults).
# ---------------------------------------------------------------------------

# Documented default base URL used when GENERATION_PROVIDER == "ollama" and
# OLLAMA_BASE_URL is unset (H. local development). Docker Desktop uses
# http://host.docker.internal:11434; a private/LAN host may be configured as
# http://<private-host>:11434 (validated private-only below). No
# developer-specific IP is hardcoded anywhere.
DEFAULT_OLLAMA_BASE_URL = "http://127.0.0.1:11434"

# OLLAMA_MODEL: 1..80 chars from the safe token set only.
_OLLAMA_MODEL_RE = re.compile(r"[A-Za-z0-9._:\-]+")

# OLLAMA_BASE_URL host allowlist: exactly these hostnames (plus literal
# loopback/private IPs) may be configured. .local / .lan hostnames are never
# accepted unless they are exactly one of the allowlisted names.
_OLLAMA_ALLOWED_HOSTNAMES = frozenset({"localhost", "host.docker.internal"})

# The RFC1918 private IPv4 ranges (10/8, 172.16/12, 192.168/16). Explicit
# range checks keep the allowlist documented and deterministic (IPv4
# link-local 169.254/16 and TEST-NET ranges are NOT included).
_OLLAMA_PRIVATE_V4_NETWORKS: tuple[ipaddress.IPv4Network, ...] = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)


def is_allowed_ollama_host(host: str) -> bool:
    """True when ``host`` is an allowed Ollama endpoint host.

    Allowed: literal loopback (127.0.0.0/8, ::1), the private/LAN IPv4 ranges
    (10/8, 172.16/12, 192.168/16), IPv6 ULA (fc00::/7) and the exact hostnames
    ``localhost`` / ``host.docker.internal``. IPv6 link-local (fe80::/10) and
    every public host is rejected — a local Ollama server may never be
    configured to a public internet endpoint by default (a clearly-flagged
    opt-in for a public host is intentionally NOT implemented).
    """
    lowered = host.lower()
    if lowered in _OLLAMA_ALLOWED_HOSTNAMES:
        return True
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        # Any other hostname (including .local / .lan spellings) is rejected.
        return False
    if address.version == 4:
        return address.is_loopback or any(
            address in network for network in _OLLAMA_PRIVATE_V4_NETWORKS
        )
    if address.is_link_local:
        return False
    return address == ipaddress.ip_address("::1") or address.is_private


class Settings(BaseSettings):
    """Single configuration source (REQUIREMENTS section 45)."""

    model_config = SettingsConfigDict(
        # The dotenv source is provided explicitly in settings_customise_sources.
        env_file=None,
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: str = Field(
        default_factory=_default_database_url,
        description="SQLAlchemy database URL (DATABASE_URL).",
    )
    api_host: str = Field(default="127.0.0.1", description="ASGI bind host (API_HOST).")
    api_port: int = Field(default=8000, description="ASGI bind port (API_PORT).")
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:5173"],
        description="Comma-separated CORS allowlist (CORS_ALLOWED_ORIGINS).",
    )
    env_file: str | None = Field(
        default=None,
        description="Optional explicit path to a .env file (ENV_FILE).",
    )

    # -- Phase 4 generation lifecycle (REQUIREMENTS 32.5-32.11, 45) -----------

    generation_deadline_seconds: int = Field(
        default=60, gt=0, description="CASE_GENERATION_DEADLINE_SECONDS."
    )
    max_llm_calls_per_generation: int = Field(
        default=8, gt=0, description="MAX_LLM_CALLS_PER_GENERATION."
    )
    max_repair_passes: int = Field(
        default=2, ge=0, description="MAX_REPAIR_PASSES."
    )
    max_full_regenerations: int = Field(
        default=1, ge=0, description="MAX_FULL_REGENERATIONS."
    )
    max_concurrent_generations: int = Field(
        default=1, gt=0, description="MAX_CONCURRENT_GENERATIONS."
    )
    max_concurrent_generations_global: int = Field(
        default=3, gt=0, description="MAX_CONCURRENT_GENERATIONS_GLOBAL."
    )
    max_generations_per_session_per_window: int = Field(
        default=3, gt=0, description="MAX_GENERATIONS_PER_SESSION_PER_WINDOW."
    )
    max_generations_global_per_window: int = Field(
        default=20, gt=0, description="MAX_GENERATIONS_GLOBAL_PER_WINDOW."
    )
    max_prompt_chars: int = Field(
        default=4000, gt=0, description="MAX_PROMPT_CHARS."
    )
    max_request_body_size: int = Field(
        default=65536, gt=0, description="MAX_REQUEST_BODY_SIZE."
    )
    anonymous_quota_session_ttl_seconds: int = Field(
        default=86400, gt=0, description="ANONYMOUS_QUOTA_SESSION_TTL_SECONDS."
    )
    # -- Phase 5 auth token TTLs (REQUIREMENTS 40.1 / 45 canonical names) ----

    creator_token_ttl_seconds: int = Field(
        default=86400, gt=0, description="CREATOR_TOKEN_TTL_SECONDS."
    )
    playthrough_token_ttl_seconds: int = Field(
        default=14400, gt=0, description="PLAYTHROUGH_TOKEN_TTL_SECONDS."
    )
    generation_provider: Literal["fake", "live", "ollama"] = Field(
        default="fake", description="GENERATION_PROVIDER (fake|live|ollama)."
    )
    llm_api_key: str | None = Field(
        default=None,
        description="LLM_API_KEY (optional; absence must not affect fake mode).",
    )
    llm_model: str | None = Field(
        default=None, description="LLM_MODEL (optional)."
    )
    live_provider_url: str | None = Field(
        default=None,
        description="LIVE_PROVIDER_URL (must start with https:// when set).",
    )
    fake_provider_script: Path | None = Field(
        default=None,
        description=(
            "FAKE_PROVIDER_SCRIPT: optional JSON file path "
            "(generation-stage -> list of directives/strings)."
        ),
    )
    # -- Phase 16 local Ollama provider (configurable local generation) -------
    # OPERATOR-ONLY configuration: OLLAMA_BASE_URL can never come from a prompt
    # or generated data and is never emitted to player DTOs/logs. Documented
    # configurations (H): local ``http://127.0.0.1:11434`` (also the default
    # when unset), Docker Desktop ``http://host.docker.internal:11434``, LAN
    # ``http://<private-host>:11434`` (private-only hosts are validated below).
    ollama_base_url: str | None = Field(
        default=None,
        description=(
            "OLLAMA_BASE_URL: operator-only base URL of the local Ollama "
            "server. None (default) uses the documented "
            "http://127.0.0.1:11434 when GENERATION_PROVIDER=ollama. Only "
            "loopback, private/LAN and host.docker.internal are accepted."
        ),
    )
    ollama_model: str = Field(
        default="llama3.2:3b",
        description=(
            "OLLAMA_MODEL: Ollama model name/tag (e.g. llama3.2:3b). 1..80 "
            "chars from [A-Za-z0-9._:-] only."
        ),
    )
    ollama_timeout_seconds: float = Field(
        default=60.0,
        ge=5,
        le=300,
        description="OLLAMA_TIMEOUT_SECONDS (bounded 5..300).",
    )
    ollama_temperature: float = Field(
        default=0.2,
        ge=0.0,
        le=2.0,
        description="OLLAMA_TEMPERATURE (bounded 0.0..2.0).",
    )
    ollama_num_ctx: int = Field(
        default=4096,
        ge=512,
        le=32768,
        description=(
            "OLLAMA_NUM_CTX: optional bounded context/token setting "
            "(512..32768)."
        ),
    )
    # -- Phase 8 production static serving (REQUIREMENTS 46 / Phase8 J/I) ----
    # When STATIC_DIR is set the app ALSO serves the built frontend at "/" with
    # SPA fallback. Unset (local dev) keeps the API-only surface unchanged so
    # every existing dev-mode test and workflow is untouched.
    static_dir: Path | None = Field(
        default=None,
        description=(
            "STATIC_DIR: absolute path of the production frontend build "
            "(index.html + assets). When set, the backend serves the SPA at "
            "/ with an index.html fallback for /scene /accuse /reveal; "
            "assets under /assets are served with immutable caching."
        ),
    )
    # -- Phase 10 Asset Oracle (Phase10 / Phase_POST_MVP_ROADMAP) ----------
    # Optional explicit path to the Asset Oracle manifest. None (default)
    # derives <repo-root>/assets/catalog/catalog.json from the package
    # location (never from the process CWD).
    asset_catalog_path: Path | None = Field(
        default=None,
        description=(
            "ASSET_CATALOG_PATH: optional absolute path of the Asset Oracle "
            "catalog manifest (assets/catalog/catalog.json). None derives the "
            "repo-root path from the package location."
        ),
    )

    @field_validator("static_dir", mode="before")
    @classmethod
    def _sanitize_static_dir(cls, value: object) -> object:
        """STATIC_DIR: tolerate empty strings; reject NUL bytes."""
        if value is None:
            return None
        if isinstance(value, str):
            if not value.strip():
                return None
            if "\x00" in value:
                raise ValueError("STATIC_DIR must not contain NUL characters")
        return value

    @field_validator("live_provider_url")
    @classmethod
    def _validate_live_provider_url(cls, value: str | None) -> str | None:
        """LIVE_PROVIDER_URL must be a non-empty https:// URL when set."""
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError("LIVE_PROVIDER_URL must be a non-empty string when set")
        text = value.strip()
        if "\x00" in text:
            raise ValueError("LIVE_PROVIDER_URL must not contain NUL characters")
        if not text.startswith("https://"):
            raise ValueError("LIVE_PROVIDER_URL must start with https:// when set")
        return text

    @field_validator("ollama_base_url")
    @classmethod
    def _validate_ollama_base_url(cls, value: str | None) -> str | None:
        """OLLAMA_BASE_URL: operator-only local/LAN endpoint (validated).

        Allowed schemes: http/https. The host must be loopback, private/LAN or
        ``host.docker.internal`` (``is_allowed_ollama_host``). Rejected:
        javascript:/file:/data: schemes, embedded credentials (user:pass@),
        malformed URLs (no netloc), query strings, fragments and any path
        other than ``/`` (the adapter appends the /api endpoints). Empty
        strings are tolerated and treated as None (default).
        """
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("OLLAMA_BASE_URL must be a string when set")
        text = value.strip()
        if not text:
            return None
        if "\x00" in text:
            raise ValueError("OLLAMA_BASE_URL must not contain NUL characters")
        try:
            parsed = urlparse(text)
        except (TypeError, ValueError):
            raise ValueError("OLLAMA_BASE_URL is not a valid URL") from None
        if parsed.scheme not in ("http", "https"):
            raise ValueError(
                "OLLAMA_BASE_URL must use the http or https scheme "
                "(operator-configured local Ollama only)"
            )
        if not parsed.netloc:
            raise ValueError("OLLAMA_BASE_URL must include a host")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("OLLAMA_BASE_URL must not embed credentials")
        if parsed.query:
            raise ValueError("OLLAMA_BASE_URL must not contain a query string")
        if parsed.fragment:
            raise ValueError("OLLAMA_BASE_URL must not contain a fragment")
        path = parsed.path or ""
        if path.strip("/"):
            raise ValueError(
                "OLLAMA_BASE_URL must be a base URL without a path "
                "(the /api/chat endpoints are appended by the adapter)"
            )
        host = parsed.hostname
        if host is None:
            raise ValueError("OLLAMA_BASE_URL must include a host")
        if not is_allowed_ollama_host(host):
            raise ValueError(
                "OLLAMA_BASE_URL host must be loopback, private/LAN, or "
                "host.docker.internal (public internet hosts are not allowed)"
            )
        return text

    @field_validator("ollama_model")
    @classmethod
    def _validate_ollama_model(cls, value: str) -> str:
        """OLLAMA_MODEL: 1..80 chars from [A-Za-z0-9._:-] only (no injection
        surface — the model name is operator configuration, never prompt data)."""
        if not isinstance(value, str) or not value:
            raise ValueError("OLLAMA_MODEL must be a non-empty string")
        if len(value) > 80:
            raise ValueError("OLLAMA_MODEL must be at most 80 characters")
        if not _OLLAMA_MODEL_RE.fullmatch(value):
            raise ValueError(
                "OLLAMA_MODEL may contain only [A-Za-z0-9._:-] characters"
            )
        return value

    @field_validator("fake_provider_script", mode="before")
    @classmethod
    def _sanitize_fake_provider_script(cls, value: object) -> object:
        """FAKE_PROVIDER_SCRIPT: tolerate empty strings; reject NUL bytes."""
        if value is None:
            return None
        if isinstance(value, str):
            if not value.strip():
                return None
            if "\x00" in value:
                raise ValueError("FAKE_PROVIDER_SCRIPT must not contain NUL characters")
        return value

    @field_validator("asset_catalog_path", mode="before")
    @classmethod
    def _sanitize_asset_catalog_path(cls, value: object) -> object:
        """ASSET_CATALOG_PATH: tolerate empty strings; reject NUL bytes."""
        if value is None:
            return None
        if isinstance(value, str):
            if not value.strip():
                return None
            if "\x00" in value:
                raise ValueError(
                    "ASSET_CATALOG_PATH must not contain NUL characters"
                )
        return value

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _parse_cors_list(cls, value: object) -> object:
        """Accept a comma-separated string (env/dotenv) or an already-split list."""
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("cors_allowed_origins")
    @classmethod
    def _reject_cors_wildcard_or_null(cls, value: list[str]) -> list[str]:
        """DEF-015: '*' (or the literal 'null') with allow_credentials=True is a
        reflect-any-origin credentialed CORS posture and is rejected up front."""
        for origin in value:
            if origin in ("*", "null"):
                raise ValueError(
                    "CORS_ALLOWED_ORIGINS must not use %r (credentials are enabled)" % origin
                )
        return value

    @field_validator("database_url")
    @classmethod
    def _validate_database_url(cls, value: str) -> str:
        """DEF-020/022: reject in-memory SQLite and malformed URLs with a clean,
        sanitized configuration error instead of a raw SQLAlchemy traceback or a
        silently never-ready database."""
        if value is None or not str(value).strip():
            raise ValueError("DATABASE_URL must not be empty")
        text = str(value)
        if "\x00" in text:
            raise ValueError("DATABASE_URL must not contain NUL characters")
        try:
            parsed = make_url(text)
        except (ArgumentError, ValueError, TypeError):
            raise ValueError(
                "DATABASE_URL is not a valid SQLAlchemy URL: "
                "could not parse the connection string"
            ) from None
        if (parsed.get_backend_name() or "").lower().startswith("sqlite"):
            database = parsed.database
            if database in (":memory:", "file::memory:") or (
                isinstance(database, str) and database.startswith("file::memory:")
            ):
                raise ValueError(
                    "DATABASE_URL must use a persistent SQLite file, not an "
                    "in-memory database (:memory:): migrations and the runtime "
                    "need a shared file (hackathon/local runs must use a file path)"
                )
        return value

    @field_validator("api_port")
    @classmethod
    def _validate_api_port(cls, value: int) -> int:
        """DEF-023: the bind port must be a real TCP port number (1-65535)."""
        if not 1 <= value <= 65535:
            raise ValueError("API_PORT must be an integer between 1 and 65535")
        return value

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Real environment variables win over dotenv values; construction
        # arguments (init_settings) win over everything.
        return (
            init_settings,
            env_settings,
            cls._dotenv_source(settings_cls),
            file_secret_settings,
        )

    @classmethod
    def _dotenv_source(cls, settings_cls: type[BaseSettings]) -> PydanticBaseSettingsSource:
        explicit = os.environ.get("ENV_FILE")
        if explicit:
            paths = [explicit]
        else:
            default_dotenv = REPO_ROOT / ".env"
            paths = [str(default_dotenv)] if default_dotenv.exists() else []
        return DotEnvSettingsSource(
            settings_cls,
            env_file=paths,
            env_file_encoding="utf-8",
            case_sensitive=False,
        )