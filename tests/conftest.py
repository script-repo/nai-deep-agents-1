"""Shared fixtures: every test runs offline in mock mode with its own data directory."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from nai_agents.core.settings import reset_settings_cache


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "var"))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_MODEL", "mock-large")
    monkeypatch.setenv("APP_CHECKPOINTER", "memory")
    monkeypatch.setenv("APP_ENABLE_EXTERNAL_CLI", "false")
    for var in ("APP_AUTH_TOKEN", "APP_BASIC_AUTH", "LLM_API_KEY", "LLM_BASE_URL", "MODEL_CATALOG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)  # so a developer's .env is never picked up
    reset_settings_cache()
    yield tmp_path
    reset_settings_cache()


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point APP_DATA_DIR at a known temp folder (for tests that inspect files written there)."""
    path = tmp_path / "data"
    monkeypatch.setenv("APP_DATA_DIR", str(path))
    reset_settings_cache()
    yield path
    reset_settings_cache()
