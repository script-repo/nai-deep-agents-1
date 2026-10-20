"""Shared fixtures: every test runs offline in mock mode with its own data directory."""

from __future__ import annotations

import pytest

from nai_agents.core.settings import reset_settings_cache


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATA_DIR", str(tmp_path / "var"))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("LLM_MODEL", "mock-large")
    monkeypatch.setenv("APP_CHECKPOINTER", "memory")
    for var in ("APP_AUTH_TOKEN", "APP_BASIC_AUTH", "LLM_API_KEY", "LLM_BASE_URL", "MODEL_CATALOG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)  # so a developer's .env is never picked up
    reset_settings_cache()
    yield tmp_path
    reset_settings_cache()
