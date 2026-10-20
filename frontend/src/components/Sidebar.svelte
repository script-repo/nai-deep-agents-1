<script lang="ts">
  import { app } from '../lib/store.svelte';
  import { relTime } from '../lib/format';
  import type { Thread } from '../lib/types';
  import Icon from './Icon.svelte';

  let confirming = $state<string | null>(null);
  let deleteError = $state<string | null>(null);

  const badge: Record<string, { label: string; cls: string } | undefined> = {
    running: { label: 'Running', cls: 'accent' },
    interrupted: { label: 'Needs approval', cls: 'warn' },
    error: { label: 'Error', cls: 'danger' },
  };

  async function remove(t: Thread) {
    deleteError = null;
    try {
      await app.deleteThread(t.id);
    } catch (err) {
      deleteError = err instanceof Error ? err.message : String(err);
    } finally {
      confirming = null;
    }
  }

  function agentKey(e: KeyboardEvent, index: number) {
    if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return;
    e.preventDefault();
    const items = (e.currentTarget as HTMLElement).closest('ul')?.querySelectorAll<HTMLButtonElement>('button.agent');
    if (!items) return;
    const next = (index + (e.key === 'ArrowDown' ? 1 : -1) + items.length) % items.length;
    items[next].focus();
  }
</script>

<div class="sidebar">
  <section class="agents" aria-labelledby="agents-h">
    <h2 id="agents-h" class="section-h">Agents</h2>
    <ul class="agent-list">
      {#each app.agents as a, i (a.id)}
        <li>
          <button
            class="agent"
            class:active={a.id === app.agentId}
            aria-current={a.id === app.agentId ? 'true' : undefined}
            onclick={() => app.selectAgent(a.id)}
            onkeydown={(e) => agentKey(e, i)}
            title={a.summary}
          >
            <span class="agent-icon"><Icon name={a.icon} size={17} /></span>
            <span class="agent-text">
              <span class="agent-title"><span class="num">{a.number}</span>{a.name}</span>
              <span class="agent-summary">{a.summary}</span>
            </span>
          </button>
        </li>
      {/each}
    </ul>
  </section>

  <section class="threads" aria-labelledby="threads-h">
    <div class="threads-head">
      <h2 id="threads-h" class="section-h">Chats</h2>
      <button class="btn new" onclick={() => app.newChat()} disabled={!app.agentId}>
        <Icon name="plus" size={16} /> New chat
      </button>
    </div>
    {#if deleteError}<p class="err" role="alert">{deleteError}</p>{/if}
    <ul class="thread-list">
      {#each app.threads as t (t.id)}
        {@const status = app.threadStatus(t)}
        <li class="thread" class:active={t.id === app.threadId}>
          {#if confirming === t.id}
            <div class="confirm" role="group" aria-label="Delete this chat?">
              <span>Delete this chat?</span>
              <button class="btn danger small" onclick={() => remove(t)}>Delete</button>
              <button class="btn small" onclick={() => (confirming = null)}>Cancel</button>
            </div>
          {:else}
            <button class="thread-open" aria-current={t.id === app.threadId ? 'true' : undefined}
              onclick={() => app.openThread(t.id)}>
              <span class="thread-title">{t.title || 'New chat'}</span>
              <span class="thread-meta">
                {#if badge[status]}
                  <span class="pill {badge[status]!.cls}">
                    {#if status === 'running'}<span class="spinner dot"></span>{/if}
                    {badge[status]!.label}
                  </span>
                {/if}
                <span class="time">{relTime(t.updated_at)}</span>
              </span>
            </button>
            <button class="icon-btn del" aria-label={`Delete chat: ${t.title}`} title="Delete chat"
              onclick={() => (confirming = t.id)}>
              <Icon name="trash" size={16} />
            </button>
          {/if}
        </li>
      {:else}
        <li class="empty">{app.threadsLoading ? 'Loading…' : 'No chats yet with this agent.'}</li>
      {/each}
    </ul>
  </section>
</div>

<style>
  .sidebar {
    height: 100%;
    display: flex;
    flex-direction: column;
    overflow-y: auto;
    overscroll-behavior: contain;
  }

  .section-h {
    margin: 0;
    font-size: 11.5px;
    font-weight: 650;
    text-transform: uppercase;
    letter-spacing: 0.06em;
    color: var(--text-3);
  }

  .agents {
    padding: 14px 10px 8px;
    border-bottom: 1px solid var(--border);
  }

  .agents .section-h {
    padding: 0 6px 8px;
  }

  ul {
    list-style: none;
    margin: 0;
    padding: 0;
  }

  .agent-list {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 2px;
  }

  .agent {
    width: 100%;
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 7px 8px;
    min-height: var(--tap);
    border: 1px solid transparent;
    border-radius: var(--radius-sm);
    background: none;
    text-align: left;
  }

  .agent:hover {
    background: var(--surface-2);
  }

  .agent.active {
    background: var(--accent-soft);
    border-color: color-mix(in srgb, var(--accent) 25%, transparent);
  }

  .agent-icon {
    flex: none;
    width: 30px;
    height: 30px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 8px;
    background: var(--surface-2);
    color: var(--text-2);
  }

  .agent.active .agent-icon {
    background: var(--accent);
    color: var(--accent-text);
  }

  .agent-text {
    min-width: 0;
    display: grid;
    grid-template-columns: minmax(0, 1fr);
  }

  .agent-title {
    font-weight: 600;
    font-size: 13.5px;
    line-height: 1.3;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .num {
    display: inline-block;
    min-width: 1.4em;
    color: var(--text-3);
    font-variant-numeric: tabular-nums;
    font-weight: 500;
  }

  .agent-summary {
    font-size: 12px;
    color: var(--text-2);
    line-height: 1.35;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .threads {
    padding: 12px 10px 16px;
    flex: 1;
  }

  .threads-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 0 0 8px 6px;
  }

  .new {
    min-height: 34px;
    padding: 0 10px;
    font-size: 13px;
  }

  @media (pointer: coarse) {
    .new {
      min-height: var(--tap);
    }
  }

  .thread-list {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 2px;
  }

  .thread {
    display: flex;
    align-items: center;
    border-radius: var(--radius-sm);
  }

  .thread:hover,
  .thread:focus-within {
    background: var(--surface-2);
  }

  .thread.active {
    background: var(--surface-3);
  }

  .thread-open {
    flex: 1;
    min-width: 0;
    min-height: var(--tap);
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 2px;
    padding: 7px 8px;
    border: 0;
    background: none;
    text-align: left;
  }

  .thread-title {
    font-size: 13.5px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .thread-meta {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 11.5px;
    color: var(--text-3);
  }

  .thread-meta .pill {
    height: 18px;
    font-size: 10.5px;
    padding: 0 6px;
  }

  .spinner.dot {
    width: 8px;
    height: 8px;
    border-width: 1.5px;
  }

  .del {
    opacity: 0;
  }

  .thread:hover .del,
  .thread:focus-within .del,
  .thread.active .del {
    opacity: 1;
  }

  @media (hover: none) {
    .del {
      opacity: 1;
    }
  }

  .confirm {
    flex: 1;
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 4px 6px;
    font-size: 13px;
  }

  .confirm span {
    flex: 1;
  }

  .btn.small {
    min-height: 32px;
    padding: 0 10px;
    font-size: 12.5px;
  }

  .empty {
    padding: 8px 6px;
    color: var(--text-3);
    font-size: 13px;
  }

  .err {
    margin: 0 6px 8px;
    color: var(--danger);
    font-size: 12.5px;
  }
</style>
