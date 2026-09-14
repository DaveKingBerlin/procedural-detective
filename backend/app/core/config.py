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

import os
from pathlib import Path
from typing import Annotated, Literal

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
    generation_provider: Literal["fake", "live"] = Field(
        default="fake", description="GENERATION_PROVIDER (fake|live)."
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