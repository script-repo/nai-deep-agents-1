<script lang="ts">
  import { onMount } from 'svelte';
  import { app } from './lib/store.svelte';
  import Icon from './components/Icon.svelte';
  import Sidebar from './components/Sidebar.svelte';
  import Chat from './components/Chat.svelte';
  import RightPanel from './components/RightPanel.svelte';
  import SettingsDialog from './components/SettingsDialog.svelte';
  import SignIn from './components/SignIn.svelte';

  onMount(() => {
    const narrowMq = matchMedia('(max-width: 900px)');
    const darkMq = matchMedia('(prefers-color-scheme: dark)');
    app.setNarrow(narrowMq.matches);
    app.systemDark = darkMq.matches;
    const onNarrow = (e: MediaQueryListEvent) => app.setNarrow(e.matches);
    const onDark = (e: MediaQueryListEvent) => (app.systemDark = e.matches);
    narrowMq.addEventListener('change', onNarrow);
    darkMq.addEventListener('change', onDark);
    void app.boot();
    return () => {
      narrowMq.removeEventListener('change', onNarrow);
      darkMq.removeEventListener('change', onDark);
    };
  });

  $effect(() => {
    const root = document.documentElement;
    if (app.theme) root.dataset.theme = app.theme;
    else delete root.dataset.theme;
  });

  const statusText = $derived.by(() => {
    const v = app.view;
    if (app.isRunning) return 'Agent is working…';
    if (v.status === 'interrupted') return 'Waiting for your approval';
    if (v.status === 'error') return 'Run failed';
    return '';
  });

  function onKey(e: KeyboardEvent) {
    if (e.key === 'Escape' && app.narrow && (app.leftOpen || app.rightOpen)) {
      app.leftOpen = false;
      app.rightOpen = false;
    }
  }
</script>

<svelte:window onkeydown={onKey} />

{#if !app.booted}
  <div class="boot" aria-busy="true"><span class="spinner"></span> Loading…</div>
{:else if app.needsAuth}
  <SignIn />
{:else}
  <div class="shell" class:narrow={app.narrow}>
    <header class="top">
      <button
        class="icon-btn only-narrow"
        aria-label="Agents and chats"
        aria-expanded={app.leftOpen}
        aria-controls="sidebar"
        onclick={() => ((app.leftOpen = !app.leftOpen), (app.rightOpen = false))}
      >
        <Icon name="menu" />
      </button>
      <div class="brand">
        <span class="logo" aria-hidden="true">
          <svg viewBox="0 0 32 32" width="22" height="22"
            ><rect width="32" height="32" rx="8" fill="var(--accent)" /><path
              d="M9 22V10l7 8 7-8v12"
              fill="none"
              stroke="var(--accent-text)"
              stroke-width="2.6"
              stroke-linecap="round"
              stroke-linejoin="round"
            /></svg
          >
        </span>
        <span class="brand-name">Deep Agents</span>
        {#if app.agent}
          <span class="crumb" aria-hidden="true">/</span>
          <span class="agent-name" title={app.agent.summary}>{app.agent.name}</span>
        {/if}
      </div>

      <div class="status" role="status" aria-live="polite">
        {#if statusText}
          <span class="pill" class:accent={app.isRunning} class:warn={!app.isRunning && app.view.status === 'interrupted'}
            class:danger={!app.isRunning && app.view.status === 'error'}>
            {#if app.isRunning}<span class="spinner" style="width:10px;height:10px;border-width:1.5px"></span>
            {:else if app.view.status === 'interrupted'}<Icon name="lock" size={12} />
            {:else}<Icon name="alert" size={12} />{/if}
            <span class="status-text">{statusText}</span>
          </span>
        {/if}
      </div>

      <div class="actions">
        {#if app.config?.mock}
          <span class="pill accent mock" title="The server answers with scripted mock models; no LLM is called">Mock mode</span>
        {/if}
        <button
          class="icon-btn"
          aria-label={app.effectiveTheme === 'dark' ? 'Switch to light theme' : 'Switch to dark theme'}
          title="Toggle theme"
          onclick={() => app.toggleTheme()}
        >
          <Icon name={app.effectiveTheme === 'dark' ? 'sun' : 'moon'} />
        </button>
        <button class="icon-btn" aria-label="Model settings" title="Model settings" onclick={() => (app.settingsOpen = true)}>
          <Icon name="settings" />
        </button>
        {#if app.config && app.config.auth !== 'none'}
          <button class="icon-btn" aria-label="Sign out" title="Sign out" onclick={() => app.signOut()}>
            <Icon name="logout" />
          </button>
        {/if}
        <button
          class="icon-btn"
          aria-label={app.rightOpen ? 'Hide plan, files and activity' : 'Show plan, files and activity'}
          title="Plan, files and activity"
          aria-expanded={app.rightOpen}
          aria-controls="panel"
          onclick={() => ((app.rightOpen = !app.rightOpen), app.narrow && (app.leftOpen = false))}
        >
          <Icon name="panel" />
        </button>
      </div>
    </header>

    {#if app.bootError}
      <div class="boot-error" role="alert">
        <Icon name="alert" />
        <span>{app.bootError}</span>
        <button class="btn" onclick={() => app.boot()}>Retry</button>
      </div>
    {/if}

    <div class="body" class:panel-closed={!app.rightOpen}>
      <aside id="sidebar" class="sidebar drawer-left" class:open={app.leftOpen} aria-label="Agents and chats"
        inert={app.narrow && !app.leftOpen}>
        <Sidebar />
      </aside>
      <main class="main">
        <Chat />
      </main>
      {#if app.rightOpen || app.narrow}
        <aside id="panel" class="panel drawer-right" class:open={app.rightOpen} aria-label="Plan, files and activity"
          inert={app.narrow && !app.rightOpen}>
          <RightPanel />
        </aside>
      {/if}
      {#if app.narrow && (app.leftOpen || app.rightOpen)}
        <button class="scrim" aria-label="Close" tabindex="-1"
          onclick={() => ((app.leftOpen = false), (app.rightOpen = false))}></button>
      {/if}
    </div>
  </div>
  {#if app.settingsOpen}
    <SettingsDialog onclose={() => (app.settingsOpen = false)} />
  {/if}
{/if}

<style>
  .boot {
    height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    gap: 10px;
    color: var(--text-2);
  }

  .shell {
    height: 100%;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  .top {
    height: var(--header-h);
    flex: none;
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 0 8px 0 14px;
    border-bottom: 1px solid var(--border);
    background: var(--surface);
  }

  .narrow .top {
    padding-left: 4px;
  }

  .only-narrow {
    display: none;
  }

  .narrow .only-narrow {
    display: inline-flex;
  }

  .brand {
    display: flex;
    align-items: center;
    gap: 8px;
    min-width: 0;
    font-weight: 650;
  }

  .logo {
    display: flex;
  }

  .brand-name {
    white-space: nowrap;
  }

  .crumb {
    color: var(--text-3);
    font-weight: 400;
  }

  .agent-name {
    font-weight: 500;
    color: var(--text-2);
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .status {
    flex: 1;
    display: flex;
    justify-content: center;
    min-width: 0;
  }

  .actions {
    display: flex;
    align-items: center;
    gap: 2px;
  }

  .mock {
    margin-right: 6px;
  }

  @media (max-width: 640px) {
    .brand-name,
    .crumb {
      display: none;
    }
    .status-text {
      display: none;
    }
    .status .pill {
      padding: 0 6px;
    }
    .mock {
      margin-right: 0;
    }
  }

  .boot-error {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 8px 14px;
    background: var(--danger-soft);
    color: var(--danger);
    border-bottom: 1px solid var(--border);
  }

  .boot-error span {
    flex: 1;
  }

  .body {
    flex: 1;
    min-height: 0;
    display: grid;
    grid-template-columns: var(--sidebar-w) minmax(0, 1fr) var(--panel-w);
    position: relative;
  }

  .body.panel-closed {
    grid-template-columns: var(--sidebar-w) minmax(0, 1fr);
  }

  .sidebar {
    border-right: 1px solid var(--border);
    background: var(--surface);
    min-height: 0;
    overflow: hidden;
  }

  .main {
    min-width: 0;
    min-height: 0;
    display: flex;
    flex-direction: column;
  }

  .panel {
    border-left: 1px solid var(--border);
    background: var(--surface);
    min-height: 0;
    overflow: hidden;
  }

  @media (max-width: 1180px) {
    .body {
      --panel-w: 320px;
      --sidebar-w: 256px;
    }
  }

  /* below 900px both side columns become slide-over drawers */
  .narrow .body,
  .narrow .body.panel-closed {
    grid-template-columns: minmax(0, 1fr);
  }

  .narrow .sidebar,
  .narrow .panel {
    position: absolute;
    top: 0;
    bottom: 0;
    z-index: 20;
    width: min(88vw, 360px);
    box-shadow: var(--shadow-lg);
    transition: transform 0.2s ease;
  }

  .narrow .drawer-left {
    left: 0;
    transform: translateX(-102%);
  }

  .narrow .drawer-right {
    right: 0;
    transform: translateX(102%);
  }

  .narrow .drawer-left.open,
  .narrow .drawer-right.open {
    transform: none;
  }

  .scrim {
    position: absolute;
    inset: 0;
    z-index: 10;
    border: 0;
    background: var(--scrim);
  }
</style>
