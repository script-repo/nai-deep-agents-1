"""Provider-agnostic chat model factory and the candidate-model catalog.

The main model for every agent comes from `LLM_*` settings. Agents that compare models (router,
reviewer, watchdog, scout) also read a catalog (`examples/models.yaml` by default) listing candidate
models with their prices. Any OpenAI-compatible endpoint works: Nutanix Enterprise AI, vLLM,
LM Studio, llama.cpp server, Ollama's `/v1` API, LiteLLM, OpenRouter and so on.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from langchain_core.language_models import BaseChatModel

from nai_agents.core.mock import ScriptedChatModel, SimulatedAnswerModel
from nai_agents.core.settings import Settings, get_settings


@dataclass
class ModelConfig:
    provider: str
    model: str
    base_url: str | None = None
    api_key: str | None = None
    temperature: float = 0.0
    max_tokens: int | None = None
    timeout_s: float = 120.0

    @classmethod
    def from_settings(cls, settings: Settings) -> ModelConfig:
        return cls(
            provider=settings.llm_provider,
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key.get_secret_value() if settings.llm_api_key else None,
            temperature=settings.llm_temperature,
            max_tokens=settings.llm_max_tokens,
            timeout_s=settings.llm_timeout_s,
        )

    def public(self) -> dict[str, Any]:
        """Safe to show in the UI: never includes the key."""
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "temperature": self.temperature,
            "has_api_key": bool(self.api_key),
        }


def build_chat_model(cfg: ModelConfig, mock_scripts: dict[str, Any] | None = None) -> BaseChatModel:
    """Create a LangChain chat model for `cfg`. Imports provider packages lazily."""
    provider = cfg.provider
    common: dict[str, Any] = {"temperature": cfg.temperature}
    if cfg.max_tokens:
        common["max_tokens"] = cfg.max_tokens

    if provider == "mock":
        return ScriptedChatModel(scripts=mock_scripts or {}, model_name=cfg.model)

    if provider in {"openai_compatible", "openai"}:
        from langchain_openai import ChatOpenAI

        base_url = cfg.base_url if provider == "openai_compatible" else (cfg.base_url or None)
        if provider == "openai_compatible" and not base_url:
            raise ValueError(
                "LLM_BASE_URL is required for the openai_compatible provider "
                "(e.g. https://nai.example.com/api/v1 or http://localhost:11434/v1)"
            )
        # Local servers usually accept any key; ChatOpenAI insists on one being set.
        api_key = cfg.api_key or ("not-needed" if provider == "openai_compatible" else None)
        return ChatOpenAI(
            model=cfg.model,
            base_url=base_url,
            api_key=api_key,
            timeout=cfg.timeout_s,
            use_responses_api=False,
            stream_usage=True,
            **common,
        )

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        kwargs: dict[str, Any] = {"model": cfg.model, "timeout": cfg.timeout_s, **common}
        if cfg.api_key:
            kwargs["api_key"] = cfg.api_key
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        kwargs.setdefault("max_tokens", 8192)
        return ChatAnthropic(**kwargs)

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        kwargs = {"model": cfg.model, "temperature": cfg.temperature}
        if cfg.base_url:
            kwargs["base_url"] = cfg.base_url
        return ChatOllama(**kwargs)

    raise ValueError(f"Unknown LLM provider: {provider!r}")


# ---- candidate catalog --------------------------------------------------------------------------


@dataclass
class CatalogModel:
    """A model you could route to. Prices are USD per million tokens."""

    name: str
    provider: str
    model: str
    base_url: str | None = None
    api_key_env: str | None = None
    input_per_mtok: float = 0.0
    output_per_mtok: float = 0.0
    context_window: int | None = None
    tags: list[str] = field(default_factory=list)
    # Only used by the mock provider:
    sim_skill: float = 0.8
    sim_latency_ms: float = 400.0
    sim_version: str = "1"

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return input_tokens / 1e6 * self.input_per_mtok + output_tokens / 1e6 * self.output_per_mtok

    def public(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "provider": self.provider,
            "model": self.model,
            "input_per_mtok": self.input_per_mtok,
            "output_per_mtok": self.output_per_mtok,
            "context_window": self.context_window,
            "tags": self.tags,
        }


def load_catalog(path: Path | None = None, settings: Settings | None = None) -> list[CatalogModel]:
    settings = settings or get_settings()
    path = path or settings.catalog_path
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    models = [CatalogModel(**entry) for entry in data.get("models", [])]
    if settings.llm_provider != "mock":
        # With a real provider configured, mock entries make no sense; keep only real ones,
        # unless the catalog has nothing else (then the demo stays usable).
        real = [m for m in models if m.provider != "mock"]
        models = real or models
    return models


def find_model(name: str, catalog: list[CatalogModel] | None = None) -> CatalogModel:
    catalog = catalog if catalog is not None else load_catalog()
    for m in catalog:
        if m.name == name or m.model == name:
            return m
    raise KeyError(f"Model {name!r} is not in the catalog. Known: {', '.join(m.name for m in catalog)}")


def build_candidate(
    entry: CatalogModel, answers: dict[str, str] | None = None, difficulty: dict[str, float] | None = None
) -> BaseChatModel:
    """A chat model for a catalog entry, used by the eval harness."""
    if entry.provider == "mock":
        return SimulatedAnswerModel(
            model_name=entry.name,
            skill=entry.sim_skill,
            version=entry.sim_version,
            answers=answers or {},
            difficulty=difficulty or {},
            latency_ms=entry.sim_latency_ms,
        )
    settings = get_settings()
    api_key = os.environ.get(entry.api_key_env) if entry.api_key_env else None
    if api_key is None and entry.provider == settings.llm_provider and settings.llm_api_key:
        api_key = settings.llm_api_key.get_secret_value()
    base_url = entry.base_url or (settings.llm_base_url if entry.provider == settings.llm_provider else None)
    return build_chat_model(
        ModelConfig(
            provider=entry.provider,
            model=entry.model,
            base_url=base_url,
            api_key=api_key,
            timeout_s=settings.llm_timeout_s,
        )
    )
