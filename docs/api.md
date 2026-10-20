# HTTP API

Everything lives under `/api`. Interactive docs: `/api/docs` (OpenAPI at `/api/openapi.json`).

## Auth

If `APP_AUTH_TOKEN` and/or `APP_BASIC_AUTH` are set, every endpoint except `/api/health` and `/api/config`
requires `Authorization: Bearer <token>` or `Authorization: Basic <base64 user:pass>`. A missing or wrong
credential answers `401`. `GET /api/config` → `{version, auth: "none"|"token"|"basic"|"token+basic",
allow_model_settings, allow_api_keys, mock}` tells the UI whether to show a sign-in form.

## Endpoints

| Method | Path | Body | Answer |
|---|---|---|---|
| GET | `/api/health` | | `{status, version}` |
| GET | `/api/config` | | see above |
| GET | `/api/me` | | `{user}` |
| GET | `/api/agents` | | `[{id, number, name, summary, icon, example_prompts[], tools[], subagents[{name, description}], interrupt_on[]}]` |
| GET | `/api/agents/{id}` | | the same plus `system_prompt` |
| GET | `/api/models` | | catalog: `[{name, provider, model, input_per_mtok, output_per_mtok, context_window, tags}]` |
| GET | `/api/settings` | | `{model: {provider, model, base_url, temperature, has_api_key}, providers[], editable, api_keys_allowed}` |
| PUT | `/api/settings` | `{provider, model, base_url?, api_key?, temperature}` | `{model}`; `403` when locked |
| GET | `/api/threads?agent_id=` | | `[{id, agent_id, owner, title, created_at, updated_at, status}]` newest first; status is `idle`, `running`, `interrupted` or `error` |
| POST | `/api/threads` | `{agent_id, title?}` | the thread (`201`) |
| GET | `/api/threads/{id}` | | `{thread, messages[], todos[], files{path: content}, interrupts[]}` |
| DELETE | `/api/threads/{id}` | | `204` |
| POST | `/api/threads/{id}/messages` | `{content}` | **SSE stream** (below); `409` if the thread is already running |
| POST | `/api/threads/{id}/resume` | `{decisions: [{type: approve\|reject\|edit\|respond, message?, edited_action?: {name, args}}], interrupt_id?}` | **SSE stream** |

`messages[]` items: `{role: "user", content}`, `{role: "assistant", content, tool_calls: [{id, name, args}]}`,
`{role: "tool", name, tool_call_id, content}`.

## The run stream (Server-Sent Events over POST)

The stream is `text/event-stream`; each event has `event: <type>` and `data: <json>` where the JSON also
carries `type`. Read it with `fetch()` and a stream reader (EventSource can't POST). Types:

| type | fields | meaning |
|---|---|---|
| `token` | `text, agent` | streamed model text. `agent` is `"main"` or a sub-agent name; only `main` belongs in the chat bubble |
| `tool_call` | `id, name, args, agent` | the model called a tool (`write_todos`, `write_file`, `task` are built-ins) |
| `tool_result` | `id, name, content, agent, status` | the tool's answer (truncated to 6000 chars) |
| `todos` | `todos: [{content, status: pending\|in_progress\|completed}]` | the plan, replaced wholesale |
| `files` | `files: {path: content}, agent` | files created or changed in the agent's virtual filesystem (merge into what you have) |
| `subagent` | `status: start\|end, id, name, description, result?` | a sub-agent was delegated a task / returned |
| `interrupt` | `id, actions: [{name, args, description}], review_configs: [{action_name, allowed_decisions}]` | the run paused for a human decision: answer with `/resume`, one decision per action, in order |
| `done` | `final, interrupted` | the run ended (or paused, if `interrupted`) |
| `error` | `message` | the run failed |

A `: ping` comment arrives every 15 s to keep proxies from closing the stream.
