// App state (Svelte 5 runes): config, agents, threads, the open thread's view and its runs.

import { api, getAuth, HttpError, onUnauthorized, setAuth } from './api';
import {
  applyEvent, beginResume, beginUserTurn, emptyView, stopRun, viewFromSnapshot, type ThreadView,
} from './reducer';
import type { Agent, AppConfig, Decision, RunEvent, Thread, ThreadStatus } from './types';

const AGENT_KEY = 'nai.agent';
const THEME_KEY = 'nai.theme';

function stored(key: string): string | null {
  try {
    return localStorage.getItem(key);
  } catch {
    return null;
  }
}

function store(key: string, value: string | null): void {
  try {
    if (value === null) localStorage.removeItem(key);
    else localStorage.setItem(key, value);
  } catch {
    /* ignore */
  }
}

function message(err: unknown): string {
  if (err instanceof HttpError) return err.message;
  if (err instanceof Error) return err.message;
  return String(err);
}

export type Theme = 'light' | 'dark';
export type PanelTab = 'plan' | 'files' | 'activity';

class AppState {
  config = $state<AppConfig | null>(null);
  agents = $state<Agent[]>([]);
  agentId = $state<string | null>(null);
  threads = $state<Thread[]>([]);
  threadsLoading = $state(false);
  threadId = $state<string | null>(null);
  /** views of threads opened this session, by thread id (a run keeps writing into its own) */
  views = $state<Record<string, ThreadView>>({});
  draft = $state<ThreadView>(emptyView());
  loadingThread = $state(false);
  running = $state<Record<string, boolean>>({});
  needsAuth = $state(false);
  bootError = $state<string | null>(null);
  booted = $state(false);

  // layout
  narrow = $state(false);
  leftOpen = $state(false);
  rightOpen = $state(true);
  tab = $state<PanelTab>('plan');
  settingsOpen = $state(false);

  theme = $state<Theme | null>(null);
  systemDark = $state(false);

  private controllers = new Map<string, AbortController>();

  agent = $derived(this.agents.find((a) => a.id === this.agentId) ?? null);
  view = $derived(this.threadId ? (this.views[this.threadId] ?? this.draft) : this.draft);
  isRunning = $derived(!!this.threadId && !!this.running[this.threadId]);
  effectiveTheme = $derived<Theme>(this.theme ?? (this.systemDark ? 'dark' : 'light'));

  constructor() {
    const t = stored(THEME_KEY);
    this.theme = t === 'light' || t === 'dark' ? t : null;
    onUnauthorized(() => {
      setAuth(null);
      this.needsAuth = true;
    });
  }

  // ---- boot / auth --------------------------------------------------------------------------------

  async boot(): Promise<void> {
    this.bootError = null;
    try {
      this.config = await api.config();
    } catch (err) {
      this.bootError = `Can't reach the server: ${message(err)}`;
      this.booted = true;
      return;
    }
    if (this.config.auth !== 'none' && !getAuth()) {
      this.needsAuth = true;
      this.booted = true;
      return;
    }
    await this.load();
    this.booted = true;
  }

  private async load(): Promise<void> {
    try {
      this.agents = await api.agents();
    } catch (err) {
      if (!(err instanceof HttpError && err.status === 401)) this.bootError = message(err);
      return;
    }
    this.needsAuth = false;
    const route = this.parseHash();
    const want = route.agent ?? stored(AGENT_KEY);
    const first = this.agents.find((a) => a.id === want) ?? this.agents[0];
    if (first) await this.selectAgent(first.id, route.thread);
  }

  async signIn(authHeader: string): Promise<void> {
    await api.verify(authHeader); // throws HttpError(401) on a wrong credential
    setAuth(authHeader);
    this.needsAuth = false;
    this.config = await api.config();
    await this.load();
  }

  signOut(): void {
    for (const c of this.controllers.values()) c.abort();
    setAuth(null);
    this.agents = [];
    this.threads = [];
    this.views = {};
    this.threadId = null;
    this.draft = emptyView();
    this.needsAuth = true;
  }

  // ---- agents & threads ---------------------------------------------------------------------------

  async selectAgent(id: string, threadId: string | null = null): Promise<void> {
    if (this.agentId !== id) {
      this.agentId = id;
      store(AGENT_KEY, id);
      this.threads = [];
    }
    this.threadId = null;
    this.draft = emptyView();
    await this.refreshThreads();
    if (threadId && this.threads.some((t) => t.id === threadId)) await this.openThread(threadId);
    else this.writeHash();
  }

  async refreshThreads(): Promise<void> {
    const agentId = this.agentId;
    if (!agentId) return;
    this.threadsLoading = true;
    try {
      const list = await api.threads(agentId);
      if (this.agentId === agentId) this.threads = list;
    } catch {
      /* the list keeps what it had; a 401 is handled globally */
    } finally {
      this.threadsLoading = false;
    }
  }

  threadStatus(t: Thread): ThreadStatus {
    if (this.running[t.id]) return 'running';
    const v = this.views[t.id];
    if (v) return v.status === 'error' ? 'error' : v.interrupts.length ? 'interrupted' : 'idle';
    return t.status;
  }

  newChat(): void {
    this.threadId = null;
    this.draft = emptyView();
    this.writeHash();
    if (this.narrow) this.leftOpen = false;
  }

  async openThread(id: string): Promise<void> {
    this.threadId = id;
    this.writeHash();
    if (this.narrow) this.leftOpen = false;
    if (this.running[id]) return; // its live view is already here
    this.loadingThread = !this.views[id];
    try {
      const snap = await api.thread(id);
      if (!this.running[id]) this.views[id] = viewFromSnapshot(snap);
    } catch (err) {
      const v = this.views[id] ?? emptyView();
      v.status = 'error';
      v.error = `Couldn't open this chat: ${message(err)}`;
      this.views[id] = v;
    } finally {
      this.loadingThread = false;
    }
  }

  async deleteThread(id: string): Promise<void> {
    this.controllers.get(id)?.abort();
    try {
      await api.deleteThread(id);
    } catch (err) {
      if (!(err instanceof HttpError && err.status === 404)) throw err;
    }
    this.threads = this.threads.filter((t) => t.id !== id);
    delete this.views[id];
    if (this.threadId === id) this.newChat();
  }

  // ---- runs ---------------------------------------------------------------------------------------

  async send(text: string): Promise<void> {
    const content = text.trim();
    if (!content || !this.agentId || this.isRunning) return;
    let id = this.threadId;
    if (!id) {
      const view = this.draft;
      beginUserTurn(view, content);
      try {
        const thread = await api.createThread(this.agentId);
        id = thread.id;
        this.threads = [thread, ...this.threads];
        this.views[id] = view;
        this.threadId = id;
        this.draft = emptyView();
        this.writeHash();
      } catch (err) {
        view.status = 'error';
        view.error = `Couldn't start a chat: ${message(err)}`;
        return;
      }
    } else {
      beginUserTurn(this.views[id], content);
    }
    const tid = id;
    await this.stream(tid, (onEvent, signal) => api.sendMessage(tid, content, onEvent, signal));
  }

  async resolveInterrupt(decisions: Decision[], interruptId: string | null): Promise<void> {
    const id = this.threadId;
    if (!id || this.running[id]) return;
    beginResume(this.views[id]);
    await this.stream(id, (onEvent, signal) => api.resume(id, decisions, interruptId, onEvent, signal));
  }

  stop(): void {
    if (this.threadId) this.controllers.get(this.threadId)?.abort();
  }

  private async stream(id: string,
    start: (onEvent: (e: RunEvent) => void, signal: AbortSignal) => Promise<void>): Promise<void> {
    const ctrl = new AbortController();
    this.controllers.set(id, ctrl);
    this.running[id] = true;
    const view = () => this.views[id];
    let ended = false;
    try {
      await start((ev) => {
        if (ev.type === 'done' || ev.type === 'error') ended = true;
        const v = view();
        if (v) applyEvent(v, ev);
      }, ctrl.signal);
      const v = view();
      if (v && !ended) {
        // the stream closed without a `done`: the connection dropped
        stopRun(v);
        v.status = 'error';
        v.error = 'The connection closed before the run finished. Reopen the chat to see where it got to.';
      }
    } catch (err) {
      const v = view();
      if (v) {
        if (ctrl.signal.aborted) {
          stopRun(v);
        } else {
          stopRun(v);
          v.status = 'error';
          v.error = err instanceof HttpError && err.status === 409
            ? 'This chat is already running somewhere else. Wait for it to finish, then reopen it.'
            : message(err);
        }
      }
    } finally {
      this.controllers.delete(id);
      delete this.running[id];
      void this.refreshThreads();
    }
  }

  // ---- theme & layout -----------------------------------------------------------------------------

  toggleTheme(): void {
    this.theme = this.effectiveTheme === 'dark' ? 'light' : 'dark';
    store(THEME_KEY, this.theme);
  }

  setNarrow(narrow: boolean): void {
    if (narrow === this.narrow) return;
    this.narrow = narrow;
    this.leftOpen = false;
    this.rightOpen = !narrow;
  }

  // ---- url ----------------------------------------------------------------------------------------

  private parseHash(): { agent: string | null; thread: string | null } {
    const [, agent, thread] = location.hash.replace(/^#/, '').split('/');
    return { agent: agent ? decodeURIComponent(agent) : null, thread: thread ? decodeURIComponent(thread) : null };
  }

  private writeHash(): void {
    if (!this.agentId) return;
    const hash = `#/${encodeURIComponent(this.agentId)}${this.threadId ? `/${encodeURIComponent(this.threadId)}` : ''}`;
    if (location.hash !== hash) history.replaceState(null, '', hash);
  }
}

export const app = new AppState();
