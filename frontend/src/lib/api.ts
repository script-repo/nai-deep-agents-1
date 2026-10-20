// The HTTP client: every request carries the stored Authorization header; a 401 anywhere signs out.

import { readSSE } from './sse';
import type {
  Agent, AppConfig, CatalogModel, Decision, ModelSettings, RunEvent, SettingsInfo, Thread, ThreadSnapshot,
} from './types';

const AUTH_KEY = 'nai.auth';

export class HttpError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

let unauthorized: () => void = () => {};

export function onUnauthorized(fn: () => void): void {
  unauthorized = fn;
}

export function getAuth(): string | null {
  try {
    return localStorage.getItem(AUTH_KEY);
  } catch {
    return null;
  }
}

export function setAuth(value: string | null): void {
  try {
    if (value) localStorage.setItem(AUTH_KEY, value);
    else localStorage.removeItem(AUTH_KEY);
  } catch {
    /* private mode: the session still works until reload */
  }
}

export function bearer(token: string): string {
  return `Bearer ${token.trim()}`;
}

export function basic(user: string, password: string): string {
  const bytes = new TextEncoder().encode(`${user}:${password}`);
  let bin = '';
  for (const b of bytes) bin += String.fromCharCode(b);
  return `Basic ${btoa(bin)}`;
}

async function errorText(res: Response): Promise<string> {
  try {
    const body = await res.json();
    if (typeof body?.detail === 'string') return body.detail;
    if (Array.isArray(body?.detail)) return body.detail.map((d: { msg?: string }) => d.msg).join('; ');
  } catch {
    /* not JSON */
  }
  return `${res.status} ${res.statusText}`.trim();
}

async function send(method: string, path: string, body?: unknown, signal?: AbortSignal,
  accept = 'application/json', authHeader?: string): Promise<Response> {
  const headers: Record<string, string> = { Accept: accept };
  const auth = authHeader ?? getAuth();
  if (auth) headers.Authorization = auth;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  const res = await fetch(path, {
    method, headers, signal, body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (res.status === 401) {
    if (!authHeader) unauthorized();
    throw new HttpError(401, 'Sign in required');
  }
  if (!res.ok) throw new HttpError(res.status, await errorText(res));
  return res;
}

async function json<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await send(method, path, body);
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

const enc = encodeURIComponent;

export const api = {
  config: () => json<AppConfig>('GET', '/api/config'),
  /** check a credential without touching the stored one */
  verify: (auth: string) => send('GET', '/api/me', undefined, undefined, 'application/json', auth),
  agents: () => json<Agent[]>('GET', '/api/agents'),
  models: () => json<CatalogModel[]>('GET', '/api/models'),
  settings: () => json<SettingsInfo>('GET', '/api/settings'),
  saveSettings: (body: { provider: string; model: string; base_url?: string | null; api_key?: string; temperature: number }) =>
    json<{ model: ModelSettings }>('PUT', '/api/settings', body),
  threads: (agentId: string) => json<Thread[]>('GET', `/api/threads?agent_id=${enc(agentId)}`),
  createThread: (agentId: string) => json<Thread>('POST', '/api/threads', { agent_id: agentId }),
  thread: (id: string) => json<ThreadSnapshot>('GET', `/api/threads/${enc(id)}`),
  deleteThread: (id: string) => json<void>('DELETE', `/api/threads/${enc(id)}`),
  sendMessage: (id: string, content: string, onEvent: (e: RunEvent) => void, signal?: AbortSignal) =>
    run(`/api/threads/${enc(id)}/messages`, { content }, onEvent, signal),
  resume: (id: string, decisions: Decision[], interruptId: string | null, onEvent: (e: RunEvent) => void,
    signal?: AbortSignal) =>
    run(`/api/threads/${enc(id)}/resume`, interruptId ? { decisions, interrupt_id: interruptId } : { decisions },
      onEvent, signal),
};

/** POST a run and feed its event stream to `onEvent` until the stream ends. */
async function run(path: string, body: unknown, onEvent: (e: RunEvent) => void, signal?: AbortSignal): Promise<void> {
  const res = await send('POST', path, body, signal, 'text/event-stream');
  if (!res.body) throw new Error('The server sent no stream');
  await readSSE(res.body, (msg) => {
    let data: Record<string, unknown>;
    try {
      data = JSON.parse(msg.data);
    } catch {
      return; // not one of ours
    }
    if (typeof data.type !== 'string') data.type = msg.event;
    onEvent(data as unknown as RunEvent);
  });
}
