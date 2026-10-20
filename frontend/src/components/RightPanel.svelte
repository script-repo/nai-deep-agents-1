<script lang="ts">
  import { app, type PanelTab } from '../lib/store.svelte';
  import Icon from './Icon.svelte';
  import PlanView from './PlanView.svelte';
  import FilesView from './FilesView.svelte';
  import ActivityView from './ActivityView.svelte';

  const view = $derived(app.view);
  const done = $derived(view.todos.filter((t) => t.status === 'completed').length);
  const tabs = $derived<{ id: PanelTab; label: string; count: string }[]>([
    { id: 'plan', label: 'Plan', count: view.todos.length ? `${done}/${view.todos.length}` : '' },
    { id: 'files', label: 'Files', count: Object.keys(view.files).length ? String(Object.keys(view.files).length) : '' },
    { id: 'activity', label: 'Activity', count: view.activity.length ? String(view.activity.length) : '' },
  ]);

  function onTabKey(e: KeyboardEvent) {
    const order: PanelTab[] = ['plan', 'files', 'activity'];
    const i = order.indexOf(app.tab);
    let next = i;
    if (e.key === 'ArrowRight') next = (i + 1) % order.length;
    else if (e.key === 'ArrowLeft') next = (i + order.length - 1) % order.length;
    else if (e.key === 'Home') next = 0;
    else if (e.key === 'End') next = order.length - 1;
    else return;
    e.preventDefault();
    app.tab = order[next];
    document.getElementById(`tab-${order[next]}`)?.focus();
  }
</script>

<div class="panel">
  <div class="tabs-row">
    <div class="tabs" role="tablist" aria-label="Run details" tabindex="-1" onkeydown={onTabKey}>
      {#each tabs as t (t.id)}
        <button
          id="tab-{t.id}"
          role="tab"
          class="tab"
          aria-selected={app.tab === t.id}
          aria-controls="tabpanel-{t.id}"
          tabindex={app.tab === t.id ? 0 : -1}
          onclick={() => (app.tab = t.id)}
        >
          {t.label}
          {#if t.count}<span class="count">{t.count}</span>{/if}
        </button>
      {/each}
    </div>
    <button class="icon-btn" aria-label="Close panel" title="Close panel" onclick={() => (app.rightOpen = false)}>
      <Icon name="x" size={17} />
    </button>
  </div>
  <div class="content" id="tabpanel-{app.tab}" role="tabpanel" aria-labelledby="tab-{app.tab}" tabindex="-1">
    {#if app.tab === 'plan'}
      <PlanView todos={view.todos} />
    {:else if app.tab === 'files'}
      <FilesView files={view.files} />
    {:else}
      <ActivityView items={view.activity} />
    {/if}
  </div>
</div>

<style>
  .panel {
    height: 100%;
    display: flex;
    flex-direction: column;
  }

  .tabs-row {
    flex: none;
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 6px 6px 0 10px;
    border-bottom: 1px solid var(--border);
  }

  .tabs {
    flex: 1;
    display: flex;
    gap: 2px;
  }

  .tab {
    position: relative;
    display: inline-flex;
    align-items: center;
    gap: 6px;
    min-height: var(--tap);
    padding: 0 10px;
    border: 0;
    background: none;
    color: var(--text-2);
    font-weight: 550;
  }

  .tab:hover {
    color: var(--text);
  }

  .tab[aria-selected='true'] {
    color: var(--text);
  }

  .tab[aria-selected='true']::after {
    content: '';
    position: absolute;
    left: 8px;
    right: 8px;
    bottom: -1px;
    height: 2px;
    border-radius: 2px;
    background: var(--accent);
  }

  .count {
    font-size: 11px;
    font-weight: 600;
    padding: 0 6px;
    border-radius: 999px;
    background: var(--surface-2);
    color: var(--text-2);
    font-variant-numeric: tabular-nums;
  }

  .content {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    overscroll-behavior: contain;
  }

  .content:focus-visible {
    outline-offset: -2px;
  }
</style>
