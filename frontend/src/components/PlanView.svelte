<script lang="ts">
  import type { Todo } from '../lib/types';
  import Icon from './Icon.svelte';

  let { todos }: { todos: Todo[] } = $props();

  const label: Record<string, string> = { pending: 'Pending', in_progress: 'In progress', completed: 'Done' };
  const icon: Record<string, string> = { pending: 'circle', in_progress: 'circle-dot', completed: 'check-circle' };
  const done = $derived(todos.filter((t) => t.status === 'completed').length);
</script>

{#if todos.length}
  <div class="plan">
    <div class="progress" role="progressbar" aria-label="Plan progress" aria-valuemin={0} aria-valuemax={todos.length}
      aria-valuenow={done}>
      <div class="bar" style="width:{(done / todos.length) * 100}%"></div>
    </div>
    <p class="meta">{done} of {todos.length} done</p>
    <ol>
      {#each todos as t, i (i)}
        <li class={t.status}>
          <span class="icon" title={label[t.status] ?? t.status}><Icon name={icon[t.status] ?? 'circle'} size={17} /></span>
          <span class="text">{t.content}</span>
          <span class="sr-only">({label[t.status] ?? t.status})</span>
        </li>
      {/each}
    </ol>
  </div>
{:else}
  <div class="none">
    <Icon name="list" size={22} />
    <p>No plan yet. The agent writes its to-do list here as it works.</p>
  </div>
{/if}

<style>
  .plan {
    padding: 14px;
  }

  .progress {
    height: 6px;
    border-radius: 999px;
    background: var(--surface-2);
    overflow: hidden;
  }

  .bar {
    height: 100%;
    border-radius: 999px;
    background: var(--ok);
    transition: width 0.3s ease;
  }

  .meta {
    margin: 6px 0 10px;
    font-size: 12px;
    color: var(--text-3);
  }

  ol {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 2px;
  }

  li {
    display: flex;
    align-items: flex-start;
    gap: 10px;
    padding: 7px 8px;
    border-radius: var(--radius-sm);
  }

  .icon {
    flex: none;
    display: flex;
    margin-top: 1px;
    color: var(--text-3);
  }

  .in_progress {
    background: var(--accent-soft);
  }

  .in_progress .icon {
    color: var(--accent);
  }

  .in_progress .text {
    font-weight: 550;
  }

  .completed .icon {
    color: var(--ok);
  }

  .completed .text {
    color: var(--text-2);
    text-decoration: line-through;
    text-decoration-color: var(--text-3);
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
