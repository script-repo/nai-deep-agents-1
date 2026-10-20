# NAI Deep Agents

Ten working AI agents for the problems AI adoption creates (cost, governance, identity, safety, and keeping
up with new models), built on **[LangChain Deep Agents](https://github.com/langchain-ai/deepagents)** with a
fast streaming web UI. Provider-agnostic: point it at **any OpenAI-compatible endpoint** (Nutanix Enterprise
AI, vLLM, LM Studio, Ollama, LiteLLM...), Anthropic, OpenAI or Ollama, or run the whole thing **offline in
mock mode** with no API key.

[![CI](https://github.com/script-repo/nai-deep-agents-1/actions/workflows/ci.yml/badge.svg)](https://github.com/script-repo/nai-deep-agents-1/actions/workflows/ci.yml)

```text
uv sync && uv run nai-agents serve      # → http://127.0.0.1:8000 (mock mode, no key needed)
```

---

## Contents

- [The ten agents](#the-ten-agents)
- [Quick start (5 minutes)](#quick-start-5-minutes)
- [Architecture](#architecture)
- [Why this web stack](#why-this-web-stack)
- [Models and providers](#models-and-providers)
- [Deployment](#deployment)
  - [Linux (Ubuntu/Debian, RHEL/Fedora)](#linux)
  - [Windows 10/11](#windows-1011)
  - [macOS (Apple Silicon and Intel)](#macos)
  - [Docker and Docker Compose](#docker-and-docker-compose)
  - [Kubernetes (Kustomize or Helm)](#kubernetes)
  - [Nutanix Enterprise AI and NKP](#nutanix-enterprise-ai-and-nkp)
  - [Shared and multi-user deployments](#shared-and-multi-user-deployments)
- [Configuration reference](#configuration-reference)
- [Using the agents](#using-the-agents)
- [Command line](#command-line)
- [HTTP API](#http-api)
- [Security notes](#security-notes)
- [Troubleshooting](#troubleshooting)
- [Upgrading](#upgrading)
- [Development](#development)
- [Project layout](#project-layout)

---

## The ten agents

| # | Agent | What it does | Human approval before |
|---|---|---|---|
| 1 | [**Model cost router**](docs/agents/01-cost-router.md) | Sends each task to the cheapest model that passes your quality tests; keeps an eval set, tracks price and latency, estimates savings | changing a production route |
| 2 | [**AI spend & ROI governor**](docs/agents/02-spend-governor.md) | Ingests usage/billing exports (CSV/JSON), attributes spend by team, flags waste and anomalies, writes an ROI report | setting a budget alert/cap |
| 3 | [**Shadow-AI discovery**](docs/agents/03-shadow-ai.md) | Scans proxy, DNS and SaaS logs for unsanctioned AI tools, ranks risk, drafts guidance toward approved tools | sending guidance, blocking a domain |
| 4 | [**Agent identity & permissions broker**](docs/agents/04-identity-broker.md) | Reviews agents' permission requests against least privilege, issues scoped short-lived credentials | issuing any high-risk scope |
| 5 | [**Rogue-agent containment monitor**](docs/agents/05-rogue-monitor.md) | Watches agent traces for sandbox escape, deception, exfiltration, covert channels; writes incident reports | kill switch, quarantine |
| 6 | [**Agent context & memory curator**](docs/agents/06-context-curator.md) | Audits AGENTS.md / CLAUDE.md / memory files for stale, contradictory or bloated context; proposes a pruned version as a diff | writing the changes |
| 7 | [**Coding-agent fleet manager**](docs/agents/07-fleet-manager.md) | Splits a coding goal into tasks, dispatches to Claude Code / Codex / Cursor CLIs (or a built-in coder), tracks limits and cost | running an external coding CLI |
| 8 | [**Multi-model code review**](docs/agents/08-code-review.md) | Reviews a diff with 2+ models, runs the tests, reconciles findings into one review with severities | running tests |
| 9 | [**Eval watchdog**](docs/agents/09-eval-watchdog.md) | Re-runs your eval suite when a model changes (new version or silent update), detects regressions | replacing a baseline |
| 10 | [**New-model release scout**](docs/agents/10-release-scout.md) | Watches provider/Hugging Face release feeds, benchmarks new models on your workloads, recommends switch or hold | marking releases as seen |

Every agent is a real `create_deep_agent` graph with: a system prompt, real Python tools, a **planning todo
list**, a **virtual filesystem** for its reports, at least one **sub-agent**, **human-in-the-loop interrupts**
on risky tools, and sample data under [`examples/`](examples/) so it runs end to end on day one.

## Quick start (5 minutes)

You need [uv](https://docs.astral.sh/uv/) (it installs the right Python for you). Node is only needed if
you build the UI yourself.

```bash
git clone https://github.com/script-repo/nai-deep-agents-1.git
cd nai-deep-agents-1
uv sync                                   # creates .venv with pinned dependencies
# UI: build it once (Node 22+)...
(cd frontend && npm ci && npm run build)
# ...or download the prebuilt UI from a release instead of building:
# mkdir -p src/nai_agents/server/static && curl -fsSL \
#   https://github.com/script-repo/nai-deep-agents-1/releases/latest/download/nai-agents-ui.tar.gz \
#   | tar -xz -C src/nai_agents/server/static
uv run nai-agents serve
```

Open <http://127.0.0.1:8000>, pick **Model cost router**, click an example prompt, and watch the plan, files
and sub-agent activity stream in. When the agent wants to change a route, an approval card appears.

That ran on the **mock model**. To use a real one, copy `.env.example` to `.env` and set, for example:

```bash
LLM_PROVIDER=openai_compatible
LLM_BASE_URL=https://nai.example.com/api/v1     # Nutanix Enterprise AI, vLLM, LM Studio, Ollama /v1 ...
LLM_MODEL=llama-3-3-70b-instruct                # the endpoint / model name
LLM_API_KEY=...                                 # never commit this
```

Or try it in a terminal without the UI:

```bash
uv run nai-agents list
uv run nai-agents run shadow-ai "Who in finance is uploading files to unapproved AI tools?"
```

## Architecture

```mermaid
flowchart TB
    subgraph Browser
      UI["Svelte 5 SPA<br/>agent picker · chat · plan · files · activity · approvals"]
    end
    subgraph Process["One Python process (uvicorn) · one port"]
      API["FastAPI<br/>REST + SSE (/api)"]
      STATIC["Static files<br/>(built UI)"]
      RUN["Runner<br/>LangGraph stream → UI events"]
      REG["Agent registry (10 specs)"]
      subgraph DA["create_deep_agent graph (per agent)"]
        M["Chat model<br/>(provider-agnostic)"]
        TODO["write_todos<br/>(planning)"]
        FS["Virtual filesystem<br/>(reports)"]
        SUB["Sub-agents<br/>(task tool)"]
        HITL["Human-in-the-loop<br/>interrupt_on"]
        TOOLS["Agent tools<br/>(Python functions)"]
      end
      CORE["Core: settings · model factory · mock model · eval harness · safe data access"]
    end
    CK[("Checkpointer<br/>SQLite or Postgres")]
    DATA[("Data dir<br/>threads · simulated stores · outputs")]
    LLM["LLM endpoints<br/>NAI / vLLM / Ollama / OpenAI / Anthropic"]

    UI -- "fetch + SSE over POST" --> API
    UI -. "GET /" .-> STATIC
    API --> RUN --> DA
    API --> REG
    DA --> CK
    TOOLS --> DATA
    TOOLS --> CORE
    M --> LLM
```

How a turn flows:

1. The UI posts a message to `/api/threads/{id}/messages` and reads the response as a Server-Sent Events
   stream.
2. The runner calls `graph.astream(..., stream_mode=["messages", "updates"], subgraphs=True)` and turns
   LangGraph's output into a small event vocabulary: `token`, `tool_call`, `tool_result`, `todos`, `files`,
   `subagent`, `interrupt`, `done`, `error` (see [docs/api.md](docs/api.md)).
3. When a tool listed in the agent's `interrupt_on` is called, the graph pauses and the UI shows an approval
   card. The decision goes to `/api/threads/{id}/resume`, which continues the same thread.
4. All state (messages, todos, files, pending interrupts) lives in the LangGraph checkpointer, so threads
   survive restarts and can be reopened from history.

Versions are pinned in `pyproject.toml`/`uv.lock` and `frontend/package-lock.json`. At the time of writing:
`deepagents 0.7.28`, `langchain 1.4.6`, `langchain-core 1.6.12`, `langgraph 1.2.16`, `langchain-openai 1.7.2`,
`langchain-anthropic 1.7.6`, `langchain-ollama 1.1.0`, FastAPI 0.151, Svelte 5.57, Vite 8.3.

> **Note on deepagents 0.7.x:** `create_deep_agent` no longer adds the planning middleware by default. This
> project adds `TodoListMiddleware` explicitly (in `core/registry.py`), so every agent still plans with
> `write_todos` and the UI's Plan tab stays live.

## Why this web stack

The brief: the most responsive, lowest-overhead, easiest-to-deploy UI. The choice is a **FastAPI backend that
streams agent events over SSE, plus a Svelte 5 + Vite single-page app compiled to static files and served by
the same Python process**.

| Concern | Choice and why |
|---|---|
| Deployment | **One process, one port, one container.** No Node at runtime, no separate frontend server, no CORS. Systemd, NSSM, launchd and Kubernetes all run exactly one thing. |
| Streaming | **SSE over HTTP POST**, read with `fetch()` + a stream reader. Tokens and plan updates arrive as they happen; it passes through nginx, Traefik and ingress controllers with buffering off; no WebSocket upgrade or sticky-session headaches. |
| Responsiveness | Svelte compiles to small, direct DOM updates (no virtual DOM). The built UI has no runtime framework weight beyond Svelte itself and no CSS framework, so first load is fast on a phone. |
| Python fit | Agents, LangGraph checkpointers and tools are Python; serving the API from the same process means no RPC layer and one dependency set. |
| Alternatives considered | Streamlit/Gradio (fast to start, but heavy reruns, weak control over streaming UIs and approvals), Next.js/React (adds a Node server or a second deploy unit), LangGraph Platform/Agent Chat UI (great, but brings its own server and licensing model; this repo stays self-contained). |

## Models and providers

All models are LangChain chat models created by `core/models.py` from `LLM_*` settings:

| `LLM_PROVIDER` | Use for | Required |
|---|---|---|
| `mock` (default) | Offline demo, tests, CI. Deterministic scripted runs of each agent. | nothing |
| `openai_compatible` | **Nutanix Enterprise AI**, vLLM, TGI, LM Studio, llama.cpp server, Ollama `/v1`, LiteLLM, OpenRouter... | `LLM_BASE_URL`, `LLM_MODEL`, usually `LLM_API_KEY` |
| `openai` | OpenAI | `LLM_API_KEY`, `LLM_MODEL` |
| `anthropic` | Anthropic | `LLM_API_KEY`, `LLM_MODEL` |
| `ollama` | Ollama native API | `LLM_MODEL`, optional `LLM_BASE_URL` (default `http://localhost:11434`) |

Agents that compare models (router, reviewer, watchdog, scout) also read a **model catalog**
(`examples/models.yaml`, or your file via `MODEL_CATALOG`) listing candidates and their prices. Each entry
can point at its own endpoint and read its key from an environment variable named in `api_key_env`.

**Mock mode** has two halves: a `ScriptedChatModel` plays back a per-agent script of tool calls so the full
graph (planning, sub-agents, files, interrupts, streaming) runs deterministically, and a
`SimulatedAnswerModel` stands in for candidate models in evals with per-model quality. Everything mock mode
produces is labeled as simulated.

**Tool calling is required.** Deep agents rely on the model calling tools. With open models, use ones with
solid function calling (Llama 3.1+ 70B, Qwen 2.5/3, Mistral Large, gpt-oss and similar) and, on vLLM, start
the server with `--enable-auto-tool-choice --tool-call-parser <parser>`.

## Deployment

The app is a single Python package (`nai-agents`) plus static UI files. Every deployment below runs
`nai-agents serve`, keeps state in `APP_DATA_DIR`, and reads configuration from environment variables or a
`.env` file in the working directory.

### Linux

Tested layout: code in `/opt/nai-deep-agents`, state in `/var/lib/nai-agents`, config in
`/etc/nai-agents/nai-agents.env`, a dedicated `nai` system user, systemd, nginx with Let's Encrypt TLS.

**1. Prerequisites**

Ubuntu 22.04/24.04, Debian 12/13:

```bash
sudo apt update
sudo apt install -y git curl nginx
# Node is only needed to build the UI (or download the prebuilt UI tarball instead)
curl -fsSL https://deb.nodesource.com/setup_22.x | sudo -E bash - && sudo apt install -y nodejs
```

RHEL 9/10, Rocky/Alma, Fedora 40+:

```bash
sudo dnf install -y git curl nginx
sudo dnf install -y nodejs        # Fedora ships Node 22; on RHEL 9: sudo dnf module install -y nodejs:22
```

**2. User, code, uv and dependencies**

```bash
sudo useradd --system --create-home --home-dir /var/lib/nai-agents --shell /usr/sbin/nologin nai
sudo git clone https://github.com/script-repo/nai-deep-agents-1.git /opt/nai-deep-agents
sudo chown -R nai:nai /opt/nai-deep-agents

# uv (system-wide binary in /usr/local/bin)
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh

cd /opt/nai-deep-agents
sudo -u nai uv sync --frozen --no-dev                 # add --extra postgres for Postgres
sudo -u nai bash -c 'cd frontend && npm ci && npm run build && rm -rf node_modules'
```

**3. Configuration**

```bash
sudo mkdir -p /etc/nai-agents
sudo cp .env.example /etc/nai-agents/nai-agents.env
sudo chown root:nai /etc/nai-agents/nai-agents.env && sudo chmod 640 /etc/nai-agents/nai-agents.env
sudoedit /etc/nai-agents/nai-agents.env
```

Set at least: `LLM_PROVIDER`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_API_KEY`, `APP_DATA_DIR=/var/lib/nai-agents`,
`APP_AUTH_TOKEN=$(openssl rand -hex 32)` (or `APP_BASIC_AUTH`), and keep `APP_HOST=127.0.0.1` (nginx is the
only public listener).

**4. systemd service**

```bash
sudo cp deploy/systemd/nai-agents.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nai-agents
systemctl status nai-agents
curl -s http://127.0.0.1:8000/api/health        # {"status":"ok",...}
journalctl -u nai-agents -f                     # logs
```

The unit runs as `nai`, with `ProtectSystem=strict` and write access only to `/var/lib/nai-agents`.

**5. nginx reverse proxy with TLS**

```bash
# Ubuntu/Debian
sudo cp deploy/nginx/nai-agents.conf /etc/nginx/sites-available/
sudo ln -s /etc/nginx/sites-available/nai-agents.conf /etc/nginx/sites-enabled/
# RHEL/Fedora
sudo cp deploy/nginx/nai-agents.conf /etc/nginx/conf.d/

sudo sed -i 's/agents.example.com/agents.yourcompany.com/g' /etc/nginx/{sites-available,conf.d}/nai-agents.conf 2>/dev/null
```

Get a certificate (the config references Let's Encrypt paths; comment out the 443 server block until the
certificate exists, or use your company CA files instead):

```bash
# Ubuntu/Debian
sudo apt install -y certbot python3-certbot-nginx
# Fedora
sudo dnf install -y certbot python3-certbot-nginx
# RHEL/Rocky/Alma: enable EPEL first
sudo dnf install -y epel-release && sudo dnf install -y certbot python3-certbot-nginx

sudo certbot certonly --nginx -d agents.yourcompany.com
sudo nginx -t && sudo systemctl reload nginx
```

RHEL/Fedora extras:

```bash
sudo setsebool -P httpd_can_network_connect 1          # let nginx proxy to 127.0.0.1:8000 under SELinux
sudo firewall-cmd --permanent --add-service=http --add-service=https && sudo firewall-cmd --reload
sudo systemctl enable --now nginx
```

Ubuntu with ufw: `sudo ufw allow 'Nginx Full'`.

The nginx config disables proxy buffering and sets one-hour read timeouts on `/api/` so SSE streams aren't
cut or delayed.

### Windows 10/11

**1. Install tools (PowerShell)**

```powershell
winget install --id Git.Git -e
winget install --id OpenJS.NodeJS.LTS -e        # only to build the UI
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
# open a new terminal so PATH updates apply
```

**2. Get the code and dependencies**

```powershell
git clone https://github.com/script-repo/nai-deep-agents-1.git C:\nai-deep-agents
cd C:\nai-deep-agents
uv sync --frozen --no-dev
cd frontend; npm ci; npm run build; Remove-Item -Recurse -Force node_modules; cd ..
Copy-Item .env.example .env
notepad .env                                      # set LLM_* and APP_* values
uv run nai-agents serve                           # test: http://127.0.0.1:8000, Ctrl+C to stop
```

**3a. Run as a Windows service with NSSM (recommended)**

```powershell
winget install --id NSSM.NSSM -e
# elevated PowerShell:
powershell -ExecutionPolicy Bypass -File deploy\windows\install-service.ps1 -InstallDir C:\nai-deep-agents
Get-Service NaiDeepAgents
```

The script registers `NaiDeepAgents` (auto start, log rotation) with its working directory set to the
install folder so `.env` is read, and state stored in `C:\ProgramData\nai-agents`. Manage it with
`nssm restart NaiDeepAgents`, `nssm edit NaiDeepAgents`, or `nssm remove NaiDeepAgents confirm`.

**3b. Or as a Scheduled Task (no extra software)**

```powershell
powershell -ExecutionPolicy Bypass -File deploy\windows\install-service.ps1 -InstallDir C:\nai-deep-agents -ScheduledTask
```

This starts at boot as SYSTEM and restarts on failure. Remove with
`Unregister-ScheduledTask -TaskName NaiDeepAgents`.

**Exposing it on the network:** keep the app on `127.0.0.1` and put IIS (with URL Rewrite + ARR, response
buffering off) or Caddy in front for TLS; or set `APP_HOST=0.0.0.0`, set `APP_AUTH_TOKEN`, and open the
port: `New-NetFirewallRule -DisplayName "NAI Agents" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow`.

**WSL2 alternative:** `wsl --install -d Ubuntu`, then follow the [Linux](#linux) steps inside WSL (systemd is
enabled by default on current WSL; check `/etc/wsl.conf` has `[boot] systemd=true`). Windows reaches the app
at `http://localhost:8000` through WSL's localhost forwarding.

### macOS

Works the same on Apple Silicon and Intel (all dependencies ship universal or per-arch wheels; Homebrew lives
in `/opt/homebrew` on Apple Silicon and `/usr/local` on Intel).

```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"   # if needed
brew install uv git node
git clone https://github.com/script-repo/nai-deep-agents-1.git ~/nai-deep-agents
cd ~/nai-deep-agents
uv sync --frozen --no-dev
(cd frontend && npm ci && npm run build && rm -rf node_modules)
cp .env.example .env && open -e .env
uv run nai-agents serve
```

Run it at login with launchd:

```bash
sed "s#/Users/YOU#$HOME#g" deploy/macos/com.nai.deepagents.plist > ~/Library/LaunchAgents/com.nai.deepagents.plist
mkdir -p "$HOME/Library/Application Support/nai-agents"
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.nai.deepagents.plist
launchctl print gui/$(id -u)/com.nai.deepagents | head
tail -f ~/Library/Logs/nai-agents.log
# stop / remove
launchctl bootout gui/$(id -u)/com.nai.deepagents
```

Local models on a Mac: run Ollama (`brew install ollama && ollama serve`, `ollama pull qwen2.5:14b`) or
LM Studio's server, then `LLM_PROVIDER=openai_compatible`, `LLM_BASE_URL=http://localhost:11434/v1` (Ollama)
or `http://localhost:1234/v1` (LM Studio).

### Docker and Docker Compose

The multi-stage [`Dockerfile`](Dockerfile) builds the UI with Node, then installs the app with uv into a slim
Python 3.13 image that runs as a non-root user (uid 10001) with a health check. No Node at runtime.

```bash
docker build -t nai-deep-agents .
docker run --rm -p 8000:8000 -v nai-data:/data nai-deep-agents                 # mock mode
docker run -d --name nai-agents -p 8000:8000 -v nai-data:/data --env-file .env nai-deep-agents
```

Prebuilt multi-arch images are published on releases: `ghcr.io/script-repo/nai-deep-agents:<version>`.

**Compose, single host:** `cp .env.example .env`, edit, then `docker compose up -d`.

**Compose, shared (Postgres + automatic TLS with Caddy):**

```bash
cp .env.example .env
# in .env: APP_AUTH_TOKEN or APP_BASIC_AUTH, POSTGRES_PASSWORD=..., DOMAIN=agents.yourcompany.com, LLM_*
docker compose -f deploy/compose/docker-compose.postgres.yml --env-file .env up -d
```

### Kubernetes

Two options with the same result: plain manifests with Kustomize ([`deploy/k8s`](deploy/k8s)) or a Helm
chart ([`deploy/helm/nai-deep-agents`](deploy/helm/nai-deep-agents)). Both run as non-root with a read-only
root filesystem, probe `/api/health`, store state on a PVC, and expose an Ingress with SSE-friendly settings.

**1. Secret (never commit it):**

```bash
kubectl create namespace nai-agents
kubectl -n nai-agents create secret generic nai-agents-secrets \
  --from-literal=LLM_API_KEY='<endpoint api key>' \
  --from-literal=APP_AUTH_TOKEN="$(openssl rand -hex 32)"
# for multiple users: --from-literal=APP_BASIC_AUTH='alice:...,bob:...'
# for Postgres:       --from-literal=APP_DATABASE_URL='postgresql://nai:...@postgres:5432/nai_agents'
```

**2a. Kustomize:** edit `deploy/k8s/configmap.yaml` (provider, base URL, model) and the host in
`ingress.yaml`, then `kubectl apply -k deploy/k8s`.

**2b. Helm:**

```bash
helm upgrade --install nai-agents deploy/helm/nai-deep-agents -n nai-agents \
  --set llm.baseUrl=https://nai.example.com/api/v1 \
  --set llm.model=llama-3-3-70b-instruct \
  --set ingress.host=agents.yourcompany.com \
  --set ingress.className=nginx
```

Useful values: `replicaCount` (more than 1 requires `app.checkpointer=postgres`; the chart refuses otherwise),
`persistence.storageClass`, `modelCatalog.configMap` (mount your own `models.yaml`), `app.extraEnv`,
`image.repository/tag` for a private registry, `resources`, `nodeSelector`/`tolerations`.

Using your own registry: `docker build -t registry.example.com/ai/nai-deep-agents:0.1.0 . && docker push ...`
then `--set image.repository=registry.example.com/ai/nai-deep-agents --set image.tag=0.1.0`.

### Nutanix Enterprise AI and NKP

[Nutanix Enterprise AI](https://www.nutanix.com/products/nutanix-enterprise-ai) (NAI) serves models behind
OpenAI-compatible endpoints, so the agents talk to it with `LLM_PROVIDER=openai_compatible`:

1. In the NAI console, create (or pick) an **endpoint** for a tool-calling model (e.g. Llama 3.3 70B
   Instruct) and create an **API key** for it.
2. Configure:

   ```bash
   LLM_PROVIDER=openai_compatible
   LLM_BASE_URL=https://<nai-host>/api/v1       # newer NAI releases also document /enterpriseai/v1
   LLM_MODEL=<endpoint-name>                     # the model field is the endpoint name
   LLM_API_KEY=<endpoint api key>
   ```

3. Test it: `curl -s $LLM_BASE_URL/chat/completions -H "Authorization: Bearer $LLM_API_KEY"
   -H 'Content-Type: application/json' -d '{"model":"<endpoint-name>","messages":[{"role":"user","content":"hi"}]}'`.
4. If NAI uses a private CA, mount the CA bundle and set `SSL_CERT_FILE=/path/ca.pem` (httpx and the OpenAI
   SDK honor it). In Kubernetes, mount it from a ConfigMap and add `SSL_CERT_FILE` via `app.extraEnv`.
5. For the router, reviewer, watchdog and scout, list several NAI endpoints (small and large models) in a
   catalog with your amortized cost per million tokens, and set `MODEL_CATALOG`.

**On NKP (Nutanix Kubernetes Platform):** deploy with the Helm chart. NKP's default ingress is Traefik, which
streams SSE without extra settings: set `--set ingress.className=kommander-traefik` (check
`kubectl get ingressclass`), use the cluster's default Nutanix CSI storage class for the PVC, and keep the
API key in a Kubernetes Secret (or sync it from Vault with External Secrets). When NAI runs on the same NKP
cluster, point `LLM_BASE_URL` at its in-cluster service or ingress to keep traffic inside the cluster.

### Shared and multi-user deployments

- **Authentication.** Set `APP_AUTH_TOKEN` (one shared bearer token) and/or `APP_BASIC_AUTH` (named users,
  `user:password` or `user:sha256:<hex>`; generate a hash with
  `python -c "import hashlib,getpass;print(hashlib.sha256(getpass.getpass().encode()).hexdigest())"`).
  The UI shows a sign-in screen and sends the credential on every request. For SSO, put an authenticating
  proxy in front (oauth2-proxy, Authelia, Cloudflare Access) and keep a token on the app as defense in depth.
- **Per-user threads.** Each basic-auth user only sees and opens their own threads; everyone using the
  shared token is one user (`token`).
- **Persistence.** SQLite (default) is right for one instance: state lives in `APP_DATA_DIR`
  (`checkpoints.sqlite`, `threads.sqlite`, and each agent's simulated stores). For several replicas or
  managed backups, use Postgres: `uv sync --extra postgres` (the Docker image includes it),
  `APP_CHECKPOINTER=postgres`, `APP_DATABASE_URL=postgresql://...`. Thread metadata then also lives in
  Postgres. Agent tool stores (routing tables, credentials, containment state) are files in the data dir: put
  it on shared storage (RWX) if you run several replicas, or run one replica.
- **Lock down model settings.** `APP_ALLOW_UI_MODEL_SETTINGS=false` makes the provider/model read-only in
  the UI; `APP_ALLOW_UI_API_KEYS=false` (default) means keys only come from the server environment.
- **Resource sizing.** The app itself is light (~150-300 MB RAM); the model endpoint does the heavy lifting.
  One instance comfortably serves dozens of concurrent users; each running thread holds one SSE connection.

## Configuration reference

All settings are environment variables (or lines in `.env`). Defaults in parentheses.

| Variable | Default | Meaning |
|---|---|---|
| `LLM_PROVIDER` | `mock` | `mock`, `openai_compatible`, `openai`, `anthropic`, `ollama` |
| `LLM_MODEL` | `mock-large` | Model or endpoint name |
| `LLM_BASE_URL` | – | Endpoint URL (required for `openai_compatible`; optional override for others) |
| `LLM_API_KEY` | – | API key (secret) |
| `LLM_TEMPERATURE` | `0` | Sampling temperature |
| `LLM_MAX_TOKENS` | – | Max output tokens (Anthropic defaults to 8192) |
| `LLM_TIMEOUT_S` | `120` | Per-request timeout |
| `MODEL_CATALOG` | `examples/models.yaml` | Candidate models and prices for routing/review/evals/scouting |
| `APP_HOST` | `127.0.0.1` | Bind address (`0.0.0.0` in containers) |
| `APP_PORT` | `8000` | Port |
| `APP_DATA_DIR` | `./var` | State: checkpoints, thread list, simulated stores, outputs |
| `APP_EXAMPLES_DIR` | `<repo>/examples` | Sample data and default input location |
| `APP_CHECKPOINTER` | `sqlite` | `memory` (tests), `sqlite`, `postgres` |
| `APP_DATABASE_URL` | – | Postgres URL when `APP_CHECKPOINTER=postgres` |
| `APP_AUTH_TOKEN` | – | Bearer token required for the API/UI |
| `APP_BASIC_AUTH` | – | `user:pass,user2:sha256:<hex>` named users |
| `APP_ALLOW_UI_MODEL_SETTINGS` | `true` | Allow changing provider/model from the UI |
| `APP_ALLOW_UI_API_KEYS` | `false` | Allow entering an API key in the UI (kept in memory only) |
| `APP_CORS_ORIGINS` | – | Comma-separated origins (only for a separately hosted UI) |
| `APP_LOG_LEVEL` | `info` | uvicorn log level |
| `APP_ENABLE_EXTERNAL_CLI` | `false` | Let the fleet manager run installed coding CLIs (`claude`, `codex`, `cursor-agent`) |

Tools only read files under `APP_EXAMPLES_DIR` and `APP_DATA_DIR`; to analyze your own exports, logs or
repos, copy them there (or mount them) and give the agent the relative path.

## Using the agents

Each agent's page in [`docs/agents/`](docs/agents/) covers its purpose, a flow diagram, every tool, the
approval points, input formats, example prompts and how to connect real data. Good first prompts:

| Agent | Try |
|---|---|
| Model cost router | "Find the cheapest model for each task category in our general eval suite and estimate the savings." |
| AI spend & ROI governor | "Analyze our AI spend from the sample exports and write an ROI report." |
| Shadow-AI discovery | "Scan the sample proxy, DNS and SaaS logs for shadow AI and write a report." |
| Identity & permissions broker | "Process the pending access requests from our agents." |
| Rogue-agent monitor | "Scan the latest agent traces for rogue behavior and contain any threats." |
| Context & memory curator | "Audit the sample repo's agent context files and propose a pruned version." |
| Coding-agent fleet manager | "Plan and dispatch the sample coding goal across available coding agents." |
| Multi-model code review | "Review the sample diff with two models and run the tests." |
| Eval watchdog | "Check for model changes and re-run the evals on anything that changed." |
| New-model release scout | "Check the release feeds for new models and tell me if we should switch." |

In the UI:

- **Left:** agents and the thread history for the selected agent (status badges show running, waiting for
  approval, or failed threads).
- **Center:** the conversation, streamed token by token. Approval cards appear inline: **Approve**,
  **Reject** (with an optional reason the agent will see), or **Edit** the arguments when the tool allows it.
- **Right:** **Plan** (the agent's live todo list), **Files** (reports it wrote, rendered), **Activity**
  (every tool call and result, and sub-agent start/finish).
- **Settings:** provider, model, base URL, temperature, and the model catalog. Dark/light theme toggle.

## Command line

```text
nai-agents serve [--host H] [--port P] [--forwarded-allow-ips IPS]   web UI + API
nai-agents list                                                    the ten agents
nai-agents run <agent-id> ["prompt"] [--yes|--no] [--thread ID]     run in the terminal; approvals prompt y/N
nai-agents eval [--suite evals/general.yaml] [--category C] [--models a,b]   eval catalog models
```

`run` streams the answer to stdout and tool calls to stderr; `--yes` approves every interrupt (CI demos),
`--no` rejects them.

## HTTP API

REST + SSE under `/api`, documented in [docs/api.md](docs/api.md) with the event vocabulary; interactive
OpenAPI docs at `/api/docs`.

```bash
TOKEN=...   # if APP_AUTH_TOKEN is set
T=$(curl -s -X POST localhost:8000/api/threads -H "Authorization: Bearer $TOKEN" \
     -H 'Content-Type: application/json' -d '{"agent_id":"rogue-monitor"}' | jq -r .id)
curl -N -X POST localhost:8000/api/threads/$T/messages -H "Authorization: Bearer $TOKEN" \
     -H 'Content-Type: application/json' -d '{"content":"Scan the traces and contain threats"}'
# ... event: interrupt ... then:
curl -N -X POST localhost:8000/api/threads/$T/resume -H "Authorization: Bearer $TOKEN" \
     -H 'Content-Type: application/json' -d '{"decisions":[{"type":"approve"}]}'
```

## Security notes

- **No secrets in the repo.** Keys come from the environment or a `.env` file (gitignored). Sample data uses
  obviously fake values (`AKIAFAKEEXAMPLE00000`, `sk-FAKE-...`).
- **Risky actions pause for a human.** Each agent's `interrupt_on` gates tools that change something outside
  the conversation (routes, budgets, messages, blocks, credentials, kill switches, disk writes, external
  CLIs, test execution). Rejections are passed to the agent, which must not retry.
- **Tools are confined.** File-reading tools resolve paths only inside `APP_EXAMPLES_DIR` and `APP_DATA_DIR`
  (path traversal and absolute paths outside are refused). The deep agents' own filesystem is virtual (stored
  in graph state); nothing writes to the host except through the explicit tools above. External coding CLIs
  are off unless `APP_ENABLE_EXTERNAL_CLI=true`, and then still need approval per run.
- **Simulated control planes.** Credentials, containment, outbox and blocklist are simulated stores in the
  data directory, so the agents are safe to demo. Wire them to real systems deliberately (each agent doc
  says where).
- **Network exposure.** Bind to `127.0.0.1` behind a TLS proxy, or set `APP_AUTH_TOKEN`/`APP_BASIC_AUTH`
  before binding to `0.0.0.0`. Tokens are compared in constant time. The UI stores the credential in
  `localStorage`; use HTTPS.
- **Prompt injection.** Logs, diffs, feeds and context files are untrusted input to the model. The approval
  gates and path confinement are the safety net; review approval cards (they show the exact arguments).
- **Model keys in the UI** are disabled by default and never persisted when enabled.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Browser shows "API is running, UI not built" | Build the UI (`cd frontend && npm ci && npm run build`) or download the release UI tarball into `src/nai_agents/server/static/`. |
| Tokens arrive in one burst at the end | A proxy is buffering SSE. nginx: `proxy_buffering off` on `/api/` (see `deploy/nginx`); ingress-nginx: the `proxy-buffering: "off"` annotation; Cloudflare: disable response buffering for the route. |
| Stream cuts off after 60 s | Raise proxy read timeouts (`proxy_read_timeout 3600s`, ingress `proxy-read-timeout`). |
| `LLM_BASE_URL is required` | `openai_compatible` needs the endpoint URL ending in `/v1` (or NAI's `/api/v1`). |
| 401 / 404 from the model endpoint | Check the key, and that `LLM_MODEL` matches the served model or NAI endpoint name exactly. Test with the curl in the NAI section. |
| Agent answers but never calls tools | The model or server lacks tool calling. Use a tool-capable model; on vLLM add `--enable-auto-tool-choice --tool-call-parser ...`. |
| `SSL: CERTIFICATE_VERIFY_FAILED` | Private CA: set `SSL_CERT_FILE` to your CA bundle. |
| `409 This thread is already running` | A run is still streaming for that thread (another tab?). Wait or stop it. |
| `database is locked` with SQLite | Only one process should use a data dir. For several workers/replicas, use Postgres. |
| Windows: `uv` not found after install | Open a new terminal (PATH refresh) or run `$env:Path += ";$env:USERPROFILE\.local\bin"`. |
| SELinux denies nginx → 127.0.0.1:8000 | `sudo setsebool -P httpd_can_network_connect 1`. |
| Port already in use | `nai-agents serve --port 8080` or set `APP_PORT`. |

Logs: `journalctl -u nai-agents` (Linux), `C:\ProgramData\nai-agents\service*.log` (NSSM),
`~/Library/Logs/nai-agents*.log` (macOS), `docker logs nai-agents`, `kubectl logs deploy/...`.

## Upgrading

```bash
cd /opt/nai-deep-agents            # or your install folder
git fetch --tags && git checkout vX.Y.Z      # or: git pull on main
uv sync --frozen --no-dev          # installs exactly the pinned versions in uv.lock
(cd frontend && npm ci && npm run build && rm -rf node_modules)
sudo systemctl restart nai-agents  # / nssm restart NaiDeepAgents / launchctl kickstart -k gui/$(id -u)/com.nai.deepagents
```

Docker/Compose: `docker compose pull && docker compose up -d` (or rebuild). Helm:
`helm upgrade nai-agents deploy/helm/nai-deep-agents -n nai-agents --reuse-values --set image.tag=X.Y.Z`.

Before upgrading, back up `APP_DATA_DIR` (SQLite) or the Postgres database. Checkpointer schemas are migrated
automatically by LangGraph's `setup()` on start. Dependency versions are pinned; to move to newer
deepagents/LangChain releases, bump them in `pyproject.toml`, run `uv lock`, and run the test suite: mock
mode exercises every agent's full graph, so API changes show up immediately.

## Development

```bash
uv sync                                  # with dev tools (pytest, ruff)
uv run pytest -q                         # all agents end to end in mock mode, API, core
uv run ruff check . && uv run ruff format --check .
cd frontend && npm ci && npm run dev     # Vite dev server on :5173, proxies /api to :8000
npm run check && npm test && npm run build
```

Adding an agent: create `src/nai_agents/agents/<name>/` with `tools.py` (typed functions with docstrings
returning JSON), `agent.py` (system prompt, sub-agents, `interrupt_on`, example prompts, and a `mock_script`
so it runs offline), export `SPEC`, add the module to `AGENT_MODULES` in `core/registry.py`, add sample data
under `examples/<name>/`, tests under `tests/agents/`, and a page under `docs/agents/`.

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs ruff, the test suite on Linux
(Python 3.11-3.13), Windows and macOS, the frontend type check/tests/build (and checks the Python app serves
it), Helm lint plus Kustomize render, and a Docker build with a smoke test. Tagging `v*` publishes the image
to GHCR and a GitHub release with the wheel and the prebuilt UI.

## Project layout

```text
src/nai_agents/
  core/            settings, model factory, mock models, eval harness, data access, registry, runner, checkpointers
  agents/          one package per agent: tools.py, agent.py (SPEC)
  server/          FastAPI app, auth, thread store, static/ (built UI)
  cli.py           nai-agents command
frontend/          Svelte 5 + Vite UI (builds into src/nai_agents/server/static)
examples/          sample data for every agent, model catalog, eval suites
tests/             pytest: core, API, every agent end to end in mock mode
docs/              api.md, agents/*.md
deploy/            systemd, nginx, macOS launchd, Windows service script, compose, k8s (kustomize), helm
```

## License

Apache-2.0. See [LICENSE](LICENSE).
