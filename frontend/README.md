# Web UI

The browser front end for NAI Deep Agents: Svelte 5 (runes), Vite 8 and TypeScript, with hand-written CSS and
no UI or CSS framework. It builds into `src/nai_agents/server/static/`, which the Python server serves at `/`.
The production bundle is about 39 KB of gzipped JS and 6 KB of gzipped CSS.

## Develop

```bash
# terminal 1: the API (mock mode needs no key)
uv sync
uv run uvicorn --factory nai_agents.server.app:get_app --port 8000

# terminal 2: the UI with hot reload, proxying /api to 127.0.0.1:8000
cd frontend
npm ci
npm run dev            # http://localhost:5173
```

## Build, check, test

```bash
npm run build   # → ../src/nai_agents/server/static (emptied first; .gitkeep is recreated)
npm run check   # svelte-check (types + a11y), warnings fail
npm test        # vitest: SSE parser, event reducer, markdown renderer
```

The built files are gitignored. After a build, restart the server: it mounts the UI only when `index.html` is
there at startup.

## Structure

```
index.html                  app shell; applies the saved theme before first paint
vite.config.ts              build output, /api dev proxy, vitest config
src/
  main.ts                   mounts App
  app.css                   design tokens (light/dark custom properties), shared controls, markdown styles
  App.svelte                header, three-column layout, slide-over drawers below 900px
  lib/
    types.ts                API and run-event types (docs/api.md)
    api.ts                  fetch client: Authorization header from localStorage, 401 → sign-in, SSE runs
    sse.ts                  SSE parser for fetch streams (chunk boundaries, CR/LF/CRLF, comments, multi-line data)
    reducer.ts              run events and thread snapshots → chat, plan, files, activity, pending interrupts
    store.svelte.ts         app state with runes: agents, threads, open views, runs, theme, layout, URL hash
    markdown.ts             small safe markdown renderer (escapes all HTML first, allows only safe links)
    format.ts               display helpers
  components/
    Sidebar.svelte          agent picker and the agent's chats (new, open, delete, status badges)
    Chat.svelte             messages, streamed answer, example prompts, composer with stop
    InterruptCard.svelte    approve / reject (with reason) / edit args (JSON) per action, then /resume
    RightPanel.svelte       Plan, Files and Activity tabs
    PlanView.svelte, FilesView.svelte, ActivityView.svelte
    SettingsDialog.svelte   model settings (GET/PUT /api/settings) and the model catalog
    SignIn.svelte           token and/or username + password, per /api/config
    Icon.svelte             inline line icons; unknown agent icons fall back to "bot"
```

## How a run works

`POST /api/threads/{id}/messages` (and `/resume`) answer with Server-Sent Events, so the UI reads them with
`fetch()` and a stream reader instead of `EventSource`. Each event goes through `applyEvent` in `reducer.ts`,
which mutates the thread's `$state` view in place:

- `token` events from agent `main` stream into the current assistant bubble. Sub-agent tokens are not shown in
  the chat; their tool calls show in Activity under the sub-agent's name.
- `tool_call` events are deduplicated by id, since a call is announced again after a resume.
- `interrupt` adds an approval card. Answering it posts one decision per action to `/resume`, and the answer
  keeps streaming into the same conversation.
- Reopening a chat rebuilds the view from `GET /api/threads/{id}`. Tool messages and assistant messages that only
  call tools are left out of the chat and shown in Activity, and pending interrupts come back as cards. A
  sub-agent's internal tool calls are not part of the saved thread, so they appear only while the run is live.

The stop button aborts the fetch. The server notices the disconnect and ends the run.

The URL hash (`#/<agent>/<thread>`) keeps the open chat across reloads. The theme follows
`prefers-color-scheme` until you toggle it; the choice is saved in `localStorage` (`nai.theme`), and so is the
credential (`nai.auth`) until you sign out.
