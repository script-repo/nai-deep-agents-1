<script lang="ts">
  import { onMount } from 'svelte';
  import { api } from '../lib/api';
  import { app } from '../lib/store.svelte';
  import type { CatalogModel, SettingsInfo } from '../lib/types';
  import Icon from './Icon.svelte';

  let { onclose }: { onclose: () => void } = $props();

  let dialog = $state<HTMLDialogElement>();
  let info = $state<SettingsInfo | null>(null);
  let catalog = $state<CatalogModel[]>([]);
  let loadError = $state<string | null>(null);
  let saving = $state(false);
  let saveError = $state<string | null>(null);
  let saved = $state(false);

  let provider = $state('');
  let model = $state('');
  let baseUrl = $state('');
  let temperature = $state(0);
  let apiKey = $state('');

  const providerLabel: Record<string, string> = {
    mock: 'Mock (offline, scripted)',
    openai_compatible: 'OpenAI-compatible (vLLM, Nutanix AI, …)',
    openai: 'OpenAI',
    anthropic: 'Anthropic',
    ollama: 'Ollama',
  };

  onMount(() => {
    dialog?.showModal();
    void load();
  });

  async function load() {
    loadError = null;
    try {
      const [s, m] = await Promise.all([api.settings(), api.models().catch(() => [] as CatalogModel[])]);
      info = s;
      catalog = m;
      provider = s.model.provider;
      model = s.model.model;
      baseUrl = s.model.base_url ?? '';
      temperature = s.model.temperature;
    } catch (err) {
      loadError = err instanceof Error ? err.message : String(err);
    }
  }

  const readOnly = $derived(!info?.editable);

  async function save(e: SubmitEvent) {
    e.preventDefault();
    if (!info || readOnly) return;
    saving = true;
    saveError = null;
    saved = false;
    try {
      const res = await api.saveSettings({
        provider,
        model: model.trim(),
        base_url: baseUrl.trim() || null,
        temperature: Number(temperature),
        ...(info.api_keys_allowed && apiKey ? { api_key: apiKey } : {}),
      });
      info.model = res.model;
      apiKey = '';
      saved = true;
      app.config = await api.config();
    } catch (err) {
      saveError = err instanceof Error ? err.message : String(err);
    } finally {
      saving = false;
    }
  }

  function useModel(m: CatalogModel) {
    if (readOnly) return;
    provider = m.provider;
    model = m.model;
  }

  function price(n: number | null): string {
    return n === null || n === undefined ? '—' : `$${n}`;
  }

  function ctx(n: number | null): string {
    if (!n) return '—';
    return n >= 1000 ? `${Math.round(n / 1000)}k` : String(n);
  }
</script>

<dialog bind:this={dialog} onclose={onclose} aria-labelledby="settings-h" onclick={(e) => e.target === dialog && dialog?.close()}>
  <div class="inner">
    <header>
      <h2 id="settings-h">Model settings</h2>
      <button class="icon-btn" aria-label="Close" onclick={() => dialog?.close()}><Icon name="x" /></button>
    </header>

    {#if loadError}
      <p class="err" role="alert">{loadError}</p>
    {:else if !info}
      <p class="muted"><span class="spinner"></span> Loading…</p>
    {:else}
      {#if readOnly}
        <p class="note"><Icon name="lock" size={14} /> The administrator has locked the model settings.</p>
      {/if}
      <form onsubmit={save}>
        <fieldset disabled={readOnly || saving}>
          <legend class="sr-only">Model used by all agents</legend>
          <label class="field">
            <span>Provider</span>
            <select class="input" bind:value={provider}>
              {#each info.providers as p (p)}
                <option value={p}>{providerLabel[p] ?? p}</option>
              {/each}
            </select>
          </label>
          <label class="field">
            <span>Model</span>
            <input class="input" bind:value={model} required autocomplete="off" spellcheck="false" />
          </label>
          <label class="field">
            <span>Base URL <em>(optional)</em></span>
            <input class="input" bind:value={baseUrl} type="url" placeholder="https://…/v1" autocomplete="off" />
          </label>
          <label class="field">
            <span>Temperature <output>{Number(temperature).toFixed(1)}</output></span>
            <input type="range" min="0" max="2" step="0.1" bind:value={temperature} />
          </label>
          {#if info.api_keys_allowed}
            <label class="field">
              <span>API key</span>
              <input class="input" bind:value={apiKey} type="password" autocomplete="off"
                placeholder={info.model.has_api_key ? 'A key is set; leave blank to keep it' : 'Not set'} />
            </label>
          {:else if provider !== 'mock'}
            <p class="hint">API keys are set on the server (LLM_API_KEY).{info.model.has_api_key ? ' A key is set.' : ''}</p>
          {/if}
        </fieldset>
        {#if saveError}<p class="err" role="alert">{saveError}</p>{/if}
        <div class="row">
          <span class="saved" role="status">{saved ? 'Saved. New runs use this model.' : ''}</span>
          {#if !readOnly}
            <button class="btn primary" type="submit" disabled={saving || !model.trim()}>
              {#if saving}<span class="spinner"></span>{/if} Save
            </button>
          {/if}
        </div>
      </form>

      {#if catalog.length}
        <h3>Model catalog</h3>
        <div class="table-wrap">
          <table>
            <caption class="sr-only">Known models with prices in USD per million tokens</caption>
            <thead>
              <tr><th scope="col">Name</th><th scope="col">Provider</th><th scope="col" class="num">In $/M</th>
                <th scope="col" class="num">Out $/M</th><th scope="col" class="num">Context</th><th scope="col">Tags</th></tr>
            </thead>
            <tbody>
              {#each catalog as m (m.name)}
                <tr class:current={m.provider === info.model.provider && m.model === info.model.model}>
                  <td>
                    {#if readOnly}<code>{m.name}</code>{:else}
                      <button class="link" onclick={() => useModel(m)} title="Use this model">{m.name}</button>
                    {/if}
                  </td>
                  <td>{m.provider}</td>
                  <td class="num">{price(m.input_per_mtok)}</td>
                  <td class="num">{price(m.output_per_mtok)}</td>
                  <td class="num">{ctx(m.context_window)}</td>
                  <td class="tags">{m.tags.join(', ')}</td>
                </tr>
              {/each}
            </tbody>
          </table>
        </div>
      {/if}
    {/if}
  </div>
</dialog>

<style>
  dialog {
    width: min(640px, calc(100vw - 24px));
    max-height: calc(100dvh - 48px);
    padding: 0;
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    background: var(--surface);
    color: var(--text);
    box-shadow: var(--shadow-lg);
  }

  dialog::backdrop {
    background: var(--scrim);
  }

  .inner {
    padding: 6px 18px 18px;
  }

  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin: 0 -8px 6px 0;
  }

  h2 {
    margin: 0;
    font-size: 16px;
    font-weight: 650;
  }

  h3 {
    margin: 20px 0 8px;
    font-size: 13px;
    font-weight: 650;
  }

  fieldset {
    border: 0;
    margin: 0;
    padding: 0;
    display: grid;
    gap: 12px;
  }

  em {
    font-style: normal;
    font-weight: 400;
    color: var(--text-3);
  }

  output {
    float: right;
    font-weight: 500;
    font-variant-numeric: tabular-nums;
  }

  input[type='range'] {
    width: 100%;
    accent-color: var(--accent);
    min-height: 28px;
  }

  .row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    margin-top: 14px;
  }

  .saved {
    color: var(--ok);
    font-size: 13px;
  }

  .note {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 8px 10px;
    border-radius: var(--radius-sm);
    background: var(--surface-2);
    color: var(--text-2);
    font-size: 13px;
  }

  .err {
    color: var(--danger);
    font-size: 13px;
  }

  .muted {
    display: flex;
    align-items: center;
    gap: 8px;
    color: var(--text-2);
  }

  .table-wrap {
    overflow-x: auto;
    border: 1px solid var(--border);
    border-radius: var(--radius-sm);
  }

  table {
    width: 100%;
    border-collapse: collapse;
    font-size: 12.5px;
  }

  th,
  td {
    padding: 6px 10px;
    text-align: left;
    border-bottom: 1px solid var(--border);
    white-space: nowrap;
  }

  tbody tr:last-child td {
    border-bottom: 0;
  }

  th {
    background: var(--surface-2);
    font-weight: 600;
    color: var(--text-2);
  }

  .num {
    text-align: right;
    font-variant-numeric: tabular-nums;
  }

  .tags {
    color: var(--text-3);
  }

  tr.current td {
    background: var(--accent-soft);
  }

  .link {
    padding: 0;
    border: 0;
    background: none;
    color: var(--accent);
    font-family: var(--mono);
    font-size: 12.5px;
    text-decoration: underline;
    text-underline-offset: 2px;
    min-height: 28px;
  }
</style>
