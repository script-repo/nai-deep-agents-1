"""The web app: one process, one port. REST + SSE under /api, the built Svelte UI at /."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from nai_agents import __version__
from nai_agents.core.checkpoint import open_checkpointer
from nai_agents.core.models import ModelConfig, load_catalog
from nai_agents.core.registry import AgentSpec, all_agents
from nai_agents.core.runner import resume_command, stream_events, thread_snapshot
from nai_agents.core.settings import Settings, get_settings
from nai_agents.server.auth import auth_mode, identify
from nai_agents.server.threads import PostgresThreadStore, SqliteThreadStore, ThreadMeta

log = logging.getLogger("nai_agents")
STATIC_DIR = Path(__file__).parent / "static"
PROVIDERS = ["mock", "openai_compatible", "openai", "anthropic", "ollama"]


class Runtime:
    """Process-wide state: checkpointer, thread store, current model config, compiled graphs."""

    def __init__(self, settings: Settings, checkpointer: Any) -> None:
        self.settings = settings
        self.checkpointer = checkpointer
        if settings.app_checkpointer == "postgres" and settings.app_database_url:
            self.threads: SqliteThreadStore = PostgresThreadStore(settings.app_database_url)
        else:
            self.threads = SqliteThreadStore(settings.state_dir / "threads.sqlite")
        self.model_cfg = ModelConfig.from_settings(settings)
        self.specs: dict[str, AgentSpec] = {s.id: s for s in all_agents()}
        self._graphs: dict[str, Any] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def graph(self, agent_id: str) -> Any:
        if agent_id not in self._graphs:
            self._graphs[agent_id] = self.specs[agent_id].build(self.model_cfg, self.checkpointer)
        return self._graphs[agent_id]

    def set_model(self, cfg: ModelConfig) -> None:
        self.model_cfg = cfg
        self._graphs.clear()

    def lock(self, thread_id: str) -> asyncio.Lock:
        return self._locks.setdefault(thread_id, asyncio.Lock())


# ---- request bodies -------------------------------------------------------------------------------


class NewThread(BaseModel):
    agent_id: str
    title: str | None = None


class UserMessage(BaseModel):
    content: str = Field(min_length=1, max_length=100_000)


class Decision(BaseModel):
    type: Literal["approve", "reject", "edit", "respond"]
    message: str | None = None
    edited_action: dict[str, Any] | None = None


class ResumeBody(BaseModel):
    decisions: list[Decision]
    interrupt_id: str | None = None


class ModelSettingsBody(BaseModel):
    provider: Literal["mock", "openai_compatible", "openai", "anthropic", "ollama"]
    model: str = Field(min_length=1)
    base_url: str | None = None
    api_key: str | None = None
    temperature: float = Field(default=0.0, ge=0, le=2)


# ---- app ------------------------------------------------------------------------------------------


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with open_checkpointer(settings) as saver:
            app.state.rt = Runtime(settings, saver)
            yield

    app = FastAPI(
        title="NAI Deep Agents",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )
    if settings.app_cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.app_cors_origins.split(","),
            allow_methods=["*"],
            allow_headers=["*"],
        )

    def rt(request: Request) -> Runtime:
        return request.app.state.rt

    def user(request: Request) -> str:
        return identify(request, settings)

    def owned(thread_id: str, request: Request, who: str) -> ThreadMeta:
        meta = rt(request).threads.get(thread_id)
        if meta is None or meta.owner != who:
            raise HTTPException(404, "Thread not found")
        return meta

    # -- public ----------------------------------------------------------------------------------
    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "version": __version__}

    @app.get("/api/config")
    async def config(request: Request) -> dict[str, Any]:
        return {
            "version": __version__,
            "auth": auth_mode(settings),
            "allow_model_settings": settings.app_allow_ui_model_settings,
            "allow_api_keys": settings.app_allow_ui_api_keys,
            "mock": rt(request).model_cfg.provider == "mock",
        }

    # -- authenticated ---------------------------------------------------------------------------
    @app.get("/api/me")
    async def me(who: str = Depends(user)) -> dict[str, str]:
        return {"user": who}

    @app.get("/api/agents")
    async def agents(request: Request, who: str = Depends(user)) -> list[dict[str, Any]]:
        return [s.public() for s in rt(request).specs.values()]

    @app.get("/api/agents/{agent_id}")
    async def agent(agent_id: str, request: Request, who: str = Depends(user)) -> dict[str, Any]:
        spec = rt(request).specs.get(agent_id)
        if not spec:
            raise HTTPException(404, "Unknown agent")
        return spec.public() | {"system_prompt": spec.system_prompt}

    @app.get("/api/models")
    async def models(who: str = Depends(user)) -> list[dict[str, Any]]:
        return [m.public() for m in load_catalog(settings=settings)]

    @app.get("/api/settings")
    async def get_model_settings(request: Request, who: str = Depends(user)) -> dict[str, Any]:
        return {
            "model": rt(request).model_cfg.public(),
            "providers": PROVIDERS,
            "editable": settings.app_allow_ui_model_settings,
            "api_keys_allowed": settings.app_allow_ui_api_keys,
        }

    @app.put("/api/settings")
    async def put_model_settings(
        body: ModelSettingsBody, request: Request, who: str = Depends(user)
    ) -> dict[str, Any]:
        if not settings.app_allow_ui_model_settings:
            raise HTTPException(403, "Model settings are locked by the administrator")
        runtime = rt(request)
        api_key = runtime.model_cfg.api_key
        if body.api_key:
            if not settings.app_allow_ui_api_keys:
                raise HTTPException(
                    403, "Entering API keys in the UI is disabled; set LLM_API_KEY on the server"
                )
            api_key = body.api_key
        cfg = ModelConfig(
            provider=body.provider,
            model=body.model,
            base_url=body.base_url or None,
            api_key=api_key,
            temperature=body.temperature,
            max_tokens=runtime.model_cfg.max_tokens,
            timeout_s=runtime.model_cfg.timeout_s,
        )
        try:
            from nai_agents.core.models import build_chat_model

            build_chat_model(cfg)
        except Exception as exc:
            raise HTTPException(400, f"Invalid model settings: {exc}") from exc
        runtime.set_model(cfg)
        log.info("model settings changed by %s: %s", who, cfg.public())
        return {"model": cfg.public()}

    @app.get("/api/threads")
    async def list_threads(
        request: Request, agent_id: str | None = None, who: str = Depends(user)
    ) -> list[dict[str, Any]]:
        return [t.public() for t in rt(request).threads.list(who, agent_id)]

    @app.post("/api/threads", status_code=201)
    async def create_thread(body: NewThread, request: Request, who: str = Depends(user)) -> dict[str, Any]:
        if body.agent_id not in rt(request).specs:
            raise HTTPException(404, "Unknown agent")
        return rt(request).threads.create(body.agent_id, who, body.title or "New chat").public()

    @app.get("/api/threads/{thread_id}")
    async def get_thread(thread_id: str, request: Request, who: str = Depends(user)) -> dict[str, Any]:
        meta = owned(thread_id, request, who)
        snap = await thread_snapshot(rt(request).graph(meta.agent_id), thread_id)
        return {"thread": meta.public(), **snap}

    @app.delete("/api/threads/{thread_id}", status_code=204)
    async def delete_thread(thread_id: str, request: Request, who: str = Depends(user)) -> None:
        owned(thread_id, request, who)
        runtime = rt(request)
        with contextlib.suppress(AttributeError, NotImplementedError):
            await runtime.checkpointer.adelete_thread(thread_id)
        runtime.threads.delete(thread_id)

    async def _sse(request: Request, meta: ThreadMeta, graph_input: Any) -> EventSourceResponse:
        runtime = rt(request)
        lock = runtime.lock(meta.id)
        if lock.locked():
            raise HTTPException(409, "This thread is already running")

        async def gen() -> AsyncIterator[dict[str, str]]:
            async with lock:
                runtime.threads.update(meta.id, status="running")
                status = "idle"
                try:
                    async for event in stream_events(runtime.graph(meta.agent_id), graph_input, meta.id):
                        if event["type"] == "interrupt":
                            status = "interrupted"
                        elif event["type"] == "error":
                            status = "error"
                        yield {"event": event["type"], "data": json.dumps(event, default=str)}
                        if await request.is_disconnected():
                            break
                finally:
                    runtime.threads.update(meta.id, status=status)

        return EventSourceResponse(gen(), ping=15, headers={"X-Accel-Buffering": "no"})

    @app.post("/api/threads/{thread_id}/messages")
    async def send_message(
        thread_id: str, body: UserMessage, request: Request, who: str = Depends(user)
    ) -> EventSourceResponse:
        meta = owned(thread_id, request, who)
        if meta.title == "New chat":
            rt(request).threads.update(thread_id, title=body.content.strip().splitlines()[0][:80])
        return await _sse(request, meta, {"messages": [{"role": "user", "content": body.content}]})

    @app.post("/api/threads/{thread_id}/resume")
    async def resume(
        thread_id: str, body: ResumeBody, request: Request, who: str = Depends(user)
    ) -> EventSourceResponse:
        meta = owned(thread_id, request, who)
        decisions = [d.model_dump(exclude_none=True) for d in body.decisions]
        return await _sse(request, meta, resume_command(decisions, body.interrupt_id))

    # -- UI --------------------------------------------------------------------------------------
    @app.exception_handler(404)
    async def spa_fallback(request: Request, exc: Exception) -> Any:
        if request.url.path.startswith("/api/") or request.method != "GET":
            detail = getattr(exc, "detail", "Not found")
            return JSONResponse({"detail": detail}, status_code=404)
        index = STATIC_DIR / "index.html"
        if index.exists():
            return HTMLResponse(index.read_text(encoding="utf-8"))
        return HTMLResponse(NO_UI_HTML, status_code=200)

    if (STATIC_DIR / "index.html").exists():
        app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="ui")
    else:

        @app.get("/", response_class=HTMLResponse)
        async def no_ui() -> str:
            return NO_UI_HTML

    return app


NO_UI_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>NAI Deep Agents</title></head>
<body style="font-family:system-ui;max-width:40rem;margin:4rem auto;line-height:1.5">
<h1>NAI Deep Agents API is running</h1>
<p>The web UI has not been built into this install. Build it with
<code>cd frontend &amp;&amp; npm ci &amp;&amp; npm run build</code>, or use the Docker image, which includes it.</p>
<p>API docs: <a href="/api/docs">/api/docs</a></p></body></html>"""


def get_app() -> FastAPI:
    """Uvicorn factory: `uvicorn --factory nai_agents.server.app:get_app`."""
    return create_app()
