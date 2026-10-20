// Turns run events and thread snapshots into what the UI shows. Mutates a plain object in place, so it
// works on a Svelte `$state` proxy (fine-grained updates while tokens stream) and in plain unit tests.

import type { Interrupt, RunEvent, ThreadSnapshot, Todo } from './types';

export interface ChatMessage {
  key: number;
  role: 'user' | 'assistant';
  content: string;
  streaming?: boolean;
}

export type ActivityStatus = 'running' | 'success' | 'error' | 'pending';

export interface ActivityItem {
  key: string;
  kind: 'tool' | 'subagent';
  id: string;
  agent: string;
  name: string;
  args?: Record<string, unknown>;
  description?: string;
  result?: string;
  status: ActivityStatus;
}

export type RunStatus = 'idle' | 'running' | 'interrupted' | 'error';

export interface ThreadView {
  messages: ChatMessage[];
  todos: Todo[];
  files: Record<string, string>;
  activity: ActivityItem[];
  interrupts: Interrupt[];
  status: RunStatus;
  error: string | null;
  /** index in `messages` where the current run started */
  runStart: number;
  nextKey: number;
}

export function emptyView(): ThreadView {
  return {
    messages: [],
    todos: [],
    files: {},
    activity: [],
    interrupts: [],
    status: 'idle',
    error: null,
    runStart: 0,
    nextKey: 1,
  };
}

function push(view: ThreadView, role: ChatMessage['role'], content: string, streaming = false): ChatMessage {
  const msg: ChatMessage = { key: view.nextKey++, role, content };
  if (streaming) msg.streaming = true;
  view.messages.push(msg);
  return msg;
}

function endStreaming(view: ThreadView): void {
  const last = view.messages[view.messages.length - 1];
  if (last?.streaming) {
    last.streaming = false;
    if (!last.content.trim()) view.messages.pop();
  }
}

function findActivity(view: ThreadView, key: string): ActivityItem | undefined {
  // newest first: the match is almost always near the end
  for (let i = view.activity.length - 1; i >= 0; i--) if (view.activity[i].key === key) return view.activity[i];
  return undefined;
}

/** The person sent a message: show it and start a run. */
export function beginUserTurn(view: ThreadView, content: string): void {
  push(view, 'user', content);
  view.runStart = view.messages.length;
  view.status = 'running';
  view.error = null;
  view.interrupts = [];
}

/** The person answered the pending interrupts: the same conversation keeps streaming. */
export function beginResume(view: ThreadView): void {
  view.runStart = view.messages.length;
  view.status = 'running';
  view.error = null;
  view.interrupts = [];
}

/** The person stopped the run (aborted the request). */
export function stopRun(view: ThreadView): void {
  endStreaming(view);
  for (const a of view.activity) if (a.status === 'running') a.status = 'pending';
  if (view.status === 'running') view.status = 'idle';
}

export function applyEvent(view: ThreadView, ev: RunEvent): void {
  switch (ev.type) {
    case 'token': {
      if (ev.agent !== 'main' || !ev.text) return;
      const last = view.messages[view.messages.length - 1];
      if (last?.role === 'assistant' && last.streaming) last.content += ev.text;
      else push(view, 'assistant', ev.text, true);
      return;
    }
    case 'tool_call': {
      if (ev.agent === 'main') endStreaming(view);
      const key = `tool:${ev.id}`;
      const existing = findActivity(view, key);
      if (existing) {
        // the same call is announced again after a resume (possibly with edited args)
        existing.args = ev.args;
        if (existing.status === 'pending') existing.status = 'running';
        return;
      }
      view.activity.push({ key, kind: 'tool', id: ev.id, agent: ev.agent || 'main', name: ev.name,
        args: ev.args, status: 'running' });
      return;
    }
    case 'tool_result': {
      const key = `tool:${ev.id}`;
      const status: ActivityStatus = ev.status === 'error' ? 'error' : 'success';
      const item = findActivity(view, key);
      if (item) {
        item.result = ev.content;
        item.status = status;
      } else {
        view.activity.push({ key, kind: 'tool', id: ev.id, agent: ev.agent || 'main', name: ev.name,
          result: ev.content, status });
      }
      return;
    }
    case 'subagent': {
      const key = `sub:${ev.id}`;
      const item = findActivity(view, key);
      if (ev.status === 'start') {
        if (item) item.status = 'running';
        else view.activity.push({ key, kind: 'subagent', id: ev.id, agent: 'main', name: ev.name,
          description: ev.description, status: 'running' });
      } else if (item) {
        item.status = 'success';
        if (ev.result) item.result = ev.result;
      } else {
        view.activity.push({ key, kind: 'subagent', id: ev.id, agent: 'main', name: ev.name,
          result: ev.result, status: 'success' });
      }
      return;
    }
    case 'todos':
      view.todos = ev.todos ?? [];
      return;
    case 'files':
      view.files = { ...view.files, ...(ev.files ?? {}) };
      return;
    case 'interrupt': {
      endStreaming(view);
      const intr: Interrupt = { id: ev.id ?? null, actions: ev.actions ?? [], review_configs: ev.review_configs ?? [] };
      const i = intr.id ? view.interrupts.findIndex((x) => x.id === intr.id) : -1;
      if (i >= 0) view.interrupts[i] = intr;
      else view.interrupts.push(intr);
      for (const action of intr.actions) markAwaiting(view, action.name);
      view.status = 'interrupted';
      return;
    }
    case 'done': {
      endStreaming(view);
      const final = (ev.final ?? '').trim();
      if (final) {
        const said = view.messages.slice(view.runStart).some((m) => m.role === 'assistant' && m.content.trim());
        if (!said) push(view, 'assistant', final);
      }
      if (ev.interrupted || view.interrupts.length) view.status = 'interrupted';
      else {
        view.status = view.status === 'error' ? 'error' : 'idle';
        for (const a of view.activity) if (a.status === 'running') a.status = 'success';
      }
      return;
    }
    case 'error':
      endStreaming(view);
      view.status = 'error';
      view.error = ev.message || 'The run failed';
      for (const a of view.activity) if (a.status === 'running') a.status = 'error';
      return;
  }
}

/** The tool call an interrupt is about has no result yet: show it as awaiting a decision. */
function markAwaiting(view: ThreadView, toolName: string): void {
  for (let i = view.activity.length - 1; i >= 0; i--) {
    const a = view.activity[i];
    if (a.kind === 'tool' && a.name === toolName && a.result === undefined) {
      a.status = 'pending';
      return;
    }
  }
}

/** Rebuild the view of a reopened thread from GET /api/threads/{id}. */
export function viewFromSnapshot(snap: Pick<ThreadSnapshot, 'messages' | 'todos' | 'files' | 'interrupts'> &
  { thread?: { status?: string } }): ThreadView {
  const view = emptyView();
  for (const m of snap.messages ?? []) {
    if (m.role === 'user') {
      push(view, 'user', m.content ?? '');
    } else if (m.role === 'assistant') {
      if (m.content?.trim()) push(view, 'assistant', m.content);
      for (const tc of ('tool_calls' in m && m.tool_calls) || []) {
        view.activity.push({ key: `tool:${tc.id}`, kind: 'tool', id: tc.id, agent: 'main', name: tc.name,
          args: tc.args, status: 'pending' });
        if (tc.name === 'task') {
          const a = tc.args as { subagent_type?: string; description?: string };
          view.activity.push({ key: `sub:${tc.id}`, kind: 'subagent', id: tc.id, agent: 'main',
            name: a.subagent_type || 'subagent', description: a.description, status: 'pending' });
        }
      }
    } else if (m.role === 'tool' && 'tool_call_id' in m) {
      const item = findActivity(view, `tool:${m.tool_call_id}`);
      if (item) {
        item.result = m.content;
        item.status = 'success';
      }
      const sub = findActivity(view, `sub:${m.tool_call_id}`);
      if (sub) {
        sub.result = m.content;
        sub.status = 'success';
      }
    }
  }
  view.todos = snap.todos ?? [];
  view.files = { ...(snap.files ?? {}) };
  view.interrupts = (snap.interrupts ?? []).map((i) => ({ id: i.id ?? null, actions: i.actions ?? [],
    review_configs: i.review_configs ?? [] }));
  view.runStart = view.messages.length;
  if (view.interrupts.length) view.status = 'interrupted';
  else if (snap.thread?.status === 'error') view.status = 'error';
  return view;
}

export function allowedDecisions(intr: Interrupt, actionName: string): string[] {
  return intr.review_configs.find((r) => r.action_name === actionName)?.allowed_decisions
    ?? ['approve', 'reject'];
}
