"""Runtime configuration, read from environment variables and an optional `.env` file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

Provider = Literal["mock", "openai_compatible", "openai", "anthropic", "ollama"]

REPO_ROOT = Path(__file__).resolve().parents[3]


def _default_examples_dir() -> Path:
    # Running from a checkout: <repo>/examples. Installed elsewhere: ./examples.
    candidate = REPO_ROOT / "examples"
    return candidate if candidate.is_dir() else Path.cwd() / "examples"


class Settings(BaseSettings):
    """All knobs. Every field maps to an upper-case environment variable (e.g. `LLM_PROVIDER`)."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- model -------------------------------------------------------------------------------
    llm_provider: Provider = "mock"
    llm_model: str = "mock-large"
    llm_base_url: str | None = None
    llm_api_key: SecretStr | None = None
    llm_temperature: float = 0.0
    llm_max_tokens: int | None = None
    llm_timeout_s: float = 120.0
    # Catalog of candidate models for routing, reviews and evals (YAML).
    model_catalog: Path | None = None

    # --- app ---------------------------------------------------------------------------------
    app_host: str = "127.0.0.1"
    app_port: int = 8000
    app_data_dir: Path = Path("./var")
    app_examples_dir: Path = Field(default_factory=_default_examples_dir)
    app_checkpointer: Literal["memory", "sqlite", "postgres"] = "sqlite"
    app_database_url: str | None = None  # postgres://... when app_checkpointer=postgres
    app_auth_token: SecretStr | None = None
    app_basic_auth: SecretStr | None = None  # "alice:secret,bob:secret2"
    app_allow_ui_model_settings: bool = True
    app_allow_ui_api_keys: bool = False
    app_cors_origins: str = ""
    app_log_level: str = "info"
    # Let the fleet manager run external coding CLIs (claude, codex, cursor-agent).
    app_enable_external_cli: bool = False

    @property
    def state_dir(self) -> Path:
        path = self.app_data_dir.resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def examples_dir(self) -> Path:
        return self.app_examples_dir.resolve()

    @property
    def catalog_path(self) -> Path:
        return (self.model_catalog or self.examples_dir / "models.yaml").resolve()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
