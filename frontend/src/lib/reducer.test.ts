import { describe, expect, it } from 'vitest';
import { applyEvent, beginResume, beginUserTurn, emptyView, stopRun, viewFromSnapshot } from './reducer';
import type { RunEvent } from './types';

const run = (events: RunEvent[], view = emptyView()) => {
  for (const e of events) applyEvent(view, e);
  return view;
};

const interrupt: RunEvent = {
  type: 'interrupt', id: 'i1',
  actions: [{ name: 'apply_routing', args: { category: 'classification' }, description: 'needs approval' }],
  review_configs: [{ action_name: 'apply_routing', allowed_decisions: ['approve', 'edit', 'reject'] }],
};

describe('applyEvent', () => {
  it('streams main tokens into one assistant bubble and ignores sub-agent tokens', () => {
    const v = emptyView();
    beginUserTurn(v, 'hi');
    run([
      { type: 'token', text: 'Hel', agent: 'main' },
      { type: 'token', text: 'sub', agent: 'eval-runner' },
      { type: 'token', text: 'lo', agent: 'main' },
    ], v);
    expect(v.messages.map((m) => [m.role, m.content])).toEqual([['user', 'hi'], ['assistant', 'Hello']]);
    expect(v.messages[1].streaming).toBe(true);
    applyEvent(v, { type: 'done', final: 'Hello', interrupted: false });
    expect(v.messages).toHaveLength(2);
    expect(v.messages[1].streaming).toBe(false);
    expect(v.status).toBe('idle');
  });

  it('starts a new bubble after a main tool call', () => {
    const v = run([
      { type: 'token', text: 'Thinking', agent: 'main' },
      { type: 'tool_call', id: 'c1', name: 'list_models', args: {}, agent: 'main' },
      { type: 'token', text: 'Answer', agent: 'main' },
    ]);
    expect(v.messages.map((m) => m.content)).toEqual(['Thinking', 'Answer']);
  });

  it('adds the final text when no tokens were streamed', () => {
    const v = emptyView();
    beginUserTurn(v, 'q');
    applyEvent(v, { type: 'done', final: 'All done', interrupted: false });
    expect(v.messages.at(-1)).toMatchObject({ role: 'assistant', content: 'All done' });
  });

  it('tracks tool calls and results, deduping repeated calls by id', () => {
    const v = run([
      { type: 'tool_call', id: 'c1', name: 'apply_routing', args: { a: 1 }, agent: 'main' },
      interrupt,
    ]);
    expect(v.activity).toHaveLength(1);
    expect(v.activity[0].status).toBe('pending');
    beginResume(v);
    run([
      { type: 'tool_call', id: 'c1', name: 'apply_routing', args: { a: 2 }, agent: 'main' },
      { type: 'tool_result', id: 'c1', name: 'apply_routing', content: 'ok', agent: 'main', status: 'success' },
    ], v);
    expect(v.activity).toHaveLength(1);
    expect(v.activity[0]).toMatchObject({ args: { a: 2 }, result: 'ok', status: 'success' });
  });

  it('marks failed tool results', () => {
    const v = run([
      { type: 'tool_call', id: 'c1', name: 'x', args: {}, agent: 'main' },
      { type: 'tool_result', id: 'c1', name: 'x', content: 'boom', agent: 'main', status: 'error' },
    ]);
    expect(v.activity[0].status).toBe('error');
  });

  it('records sub-agent start and end', () => {
    const v = run([
      { type: 'subagent', status: 'start', id: 't1', name: 'eval-runner', description: 'extraction' },
      { type: 'tool_call', id: 'c2', name: 'compare_models', args: {}, agent: 'eval-runner' },
      { type: 'subagent', status: 'end', id: 't1', name: 'eval-runner', description: '', result: 'cheapest: x' },
    ]);
    expect(v.activity.map((a) => [a.kind, a.agent, a.status])).toEqual([
      ['subagent', 'main', 'success'], ['tool', 'eval-runner', 'running'],
    ]);
    expect(v.activity[0]).toMatchObject({ description: 'extraction', result: 'cheapest: x' });
  });

  it('replaces todos and merges files', () => {
    const v = run([
      { type: 'todos', todos: [{ content: 'a', status: 'pending' }] },
      { type: 'files', files: { '/a.md': '1' } },
      { type: 'files', files: { '/b.md': '2', '/a.md': '3' } },
      { type: 'todos', todos: [{ content: 'a', status: 'completed' }] },
    ]);
    expect(v.todos).toEqual([{ content: 'a', status: 'completed' }]);
    expect(v.files).toEqual({ '/a.md': '3', '/b.md': '2' });
  });

  it('keeps one pending interrupt per id and clears them on resume', () => {
    const v = run([interrupt, interrupt, { type: 'done', final: '', interrupted: true }]);
    expect(v.interrupts).toHaveLength(1);
    expect(v.status).toBe('interrupted');
    beginResume(v);
    expect(v.interrupts).toHaveLength(0);
    expect(v.status).toBe('running');
  });

  it('records errors', () => {
    const v = emptyView();
    beginUserTurn(v, 'q');
    run([{ type: 'tool_call', id: 'c', name: 'x', args: {}, agent: 'main' }, { type: 'error', message: 'RuntimeError: x' }], v);
    expect(v.status).toBe('error');
    expect(v.error).toBe('RuntimeError: x');
    expect(v.activity[0].status).toBe('error');
  });

  it('stopRun ends streaming and returns to idle', () => {
    const v = emptyView();
    beginUserTurn(v, 'q');
    applyEvent(v, { type: 'token', text: 'par', agent: 'main' });
    stopRun(v);
    expect(v.status).toBe('idle');
    expect(v.messages[1].streaming).toBe(false);
  });
});

describe('viewFromSnapshot', () => {
  it('restores chat, activity, todos, files and interrupts', () => {
    const v = viewFromSnapshot({
      thread: { status: 'interrupted' },
      messages: [
        { role: 'user', content: 'go' },
        { role: 'assistant', content: '', tool_calls: [{ id: 't1', name: 'task', args: { subagent_type: 'eval-runner', description: 'd' } }] },
        { role: 'tool', name: 'task', tool_call_id: 't1', content: 'result' },
        { role: 'assistant', content: 'Here you go', tool_calls: [] },
        { role: 'assistant', content: '', tool_calls: [{ id: 'c9', name: 'apply_routing', args: {} }] },
      ],
      todos: [{ content: 'a', status: 'in_progress' }],
      files: { '/r.md': '# R' },
      interrupts: [{ id: 'i1', actions: [{ name: 'apply_routing', args: {} }], review_configs: [] }],
    });
    expect(v.messages.map((m) => [m.role, m.content])).toEqual([['user', 'go'], ['assistant', 'Here you go']]);
    expect(v.activity.map((a) => [a.key, a.status])).toEqual([
      ['tool:t1', 'success'], ['sub:t1', 'success'], ['tool:c9', 'pending'],
    ]);
    expect(v.activity[1].name).toBe('eval-runner');
    expect(v.todos).toHaveLength(1);
    expect(v.files['/r.md']).toBe('# R');
    expect(v.status).toBe('interrupted');
    expect(v.interrupts[0].id).toBe('i1');
  });
});
