"""Shared fixtures: every test gets its own data directory and fresh settings."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from nai_agents.core.settings import reset_settings_cache


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Point APP_DATA_DIR at a temp folder, force the mock provider and reset cached settings."""
    path = tmp_path / "data"
    monkeypatch.setenv("APP_DATA_DIR", str(path))
    monkeypatch.setenv("LLM_PROVIDER", "mock")
    monkeypatch.setenv("APP_ENABLE_EXTERNAL_CLI", "false")
    reset_settings_cache()
    yield path
    reset_settings_cache()
