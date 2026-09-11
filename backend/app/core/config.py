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
from typing import Annotated

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