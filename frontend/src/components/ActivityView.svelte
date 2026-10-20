<script lang="ts">
  import type { ActivityItem } from '../lib/reducer';
  import { argsSummary, pretty, prettyMaybeJson } from '../lib/format';
  import Icon from './Icon.svelte';

  let { items }: { items: ActivityItem[] } = $props();

  // consecutive items of the same agent form one group
  const groups = $derived.by(() => {
    const out: { agent: string; items: ActivityItem[] }[] = [];
    for (const it of items) {
      const last = out[out.length - 1];
      if (last && last.agent === it.agent) last.items.push(it);
      else out.push({ agent: it.agent, items: [it] });
    }
    return out;
  });

  const statusLabel: Record<string, string> = {
    running: 'Running', success: 'Done', error: 'Failed', pending: 'Waiting',
  };
</script>

{#if items.length}
  <div class="activity">
    {#each groups as g, gi (gi)}
      <section class="group" class:sub={g.agent !== 'main'}>
        <h3 class="group-h">
          <Icon name={g.agent === 'main' ? 'bot' : 'users'} size={13} />
          {g.agent === 'main' ? 'Main agent' : g.agent}
        </h3>
        <ol>
          {#each g.items as it (it.key)}
            <li class="item {it.status}">
              <details>
                <summary>
                  <span class="st" title={statusLabel[it.status]}>
                    {#if it.status === 'running'}<span class="spinner"></span>
                    {:else if it.status === 'success'}<Icon name="check" size={14} />
                    {:else if it.status === 'error'}<Icon name="alert" size={14} />
                    {:else}<Icon name="pause" size={14} />{/if}
                    <span class="sr-only">{statusLabel[it.status]}</span>
                  </span>
                  {#if it.kind === 'subagent'}
                    <span class="name">→ {it.name}</span>
                    <span class="args">{it.description ?? ''}</span>
                  {:else}
                    <span class="name">{it.name}</span>
                    <span class="args">{argsSummary(it.args, 60)}</span>
                  {/if}
                  <span class="chev" aria-hidden="true"><Icon name="chevron" size={14} /></span>
                </summary>
                <div class="detail">
                  {#if it.kind === 'subagent'}
                    <p class="label">Sub-agent {it.status === 'running' ? 'started' : it.status === 'success' ? 'returned' : statusLabel[it.status].toLowerCase()}</p>
                    {#if it.description}<p class="text">{it.description}</p>{/if}
                  {:else if it.args !== undefined}
                    <p class="label">Arguments</p>
                    <pre class="json">{pretty(it.args)}</pre>
                  {/if}
                  {#if it.result !== undefined}
                    <p class="label">{it.status === 'error' ? 'Error' : 'Result'}</p>
                    <pre class="json">{prettyMaybeJson(it.result).text}</pre>
                  {:else if it.status === 'pending'}
                    <p class="text muted">No result yet.</p>
                  {/if}
                </div>
              </details>
            </li>
          {/each}
        </ol>
      </section>
    {/each}
  </div>
{:else}
  <div class="none">
    <Icon name="activity" size={22} />
    <p>No activity yet. Tool calls and sub-agents appear here as the agent works.</p>
  </div>
{/if}

<style>
  .activity {
    padding: 10px;
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 10px;
  }

  .group,
  li,
  details {
    min-width: 0;
  }

  .group-h {
    display: flex;
    align-items: center;
    gap: 6px;
    margin: 0 0 4px;
    padding: 0 6px;
    font-size: 11.5px;
    font-weight: 650;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-3);
  }

  .group.sub {
    margin-left: 12px;
    padding-left: 8px;
    border-left: 2px solid var(--accent-soft);
  }

  .group.sub .group-h {
    color: var(--accent);
    text-transform: none;
    letter-spacing: 0;
    font-size: 12px;
  }

  ol {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 2px;
  }

  summary {
    display: flex;
    align-items: center;
    gap: 8px;
    min-height: 36px;
    padding: 4px 6px;
    border-radius: var(--radius-sm);
    cursor: pointer;
    list-style: none;
  }

  summary::-webkit-details-marker {
    display: none;
  }

  summary:hover {
    background: var(--surface-2);
  }

  @media (pointer: coarse) {
    summary {
      min-height: var(--tap);
    }
  }

  .st {
    flex: none;
    width: 20px;
    height: 20px;
    border-radius: 6px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--surface-2);
    color: var(--text-3);
  }

  .success .st {
    background: var(--ok-soft);
    color: var(--ok);
  }

  .error .st {
    background: var(--danger-soft);
    color: var(--danger);
  }

  .pending .st {
    background: var(--warn-soft);
    color: var(--warn);
  }

  .running .st {
    color: var(--accent);
  }

  .st .spinner {
    width: 11px;
    height: 11px;
    border-width: 1.5px;
  }

  .name {
    flex: none;
    font-family: var(--mono);
    font-size: 12.5px;
    font-weight: 600;
  }

  .args {
    flex: 1;
    min-width: 0;
    font-size: 12px;
    color: var(--text-3);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .chev {
    flex: none;
    display: flex;
    color: var(--text-3);
    transition: transform 0.12s;
  }

  details[open] .chev {
    transform: rotate(90deg);
  }

  .detail {
    padding: 4px 6px 10px 34px;
    display: grid;
    gap: 6px;
  }

  .label {
    margin: 4px 0 0;
    font-size: 11px;
    font-weight: 650;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--text-3);
  }

  .text {
    margin: 0;
    font-size: 13px;
  }

  .muted {
    color: var(--text-3);
  }

  .none {
    padding: 36px 24px;
    text-align: center;
    color: var(--text-3);
  }

  .none p {
    margin: 8px 0 0;
    font-size: 13px;
  }
</style>
