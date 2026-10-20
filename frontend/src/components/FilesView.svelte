<script lang="ts">
  import { renderMarkdown } from '../lib/markdown';
  import { prettyMaybeJson } from '../lib/format';
  import Icon from './Icon.svelte';

  let { files }: { files: Record<string, string> } = $props();

  let selected = $state<string | null>(null);
  let raw = $state(false);
  let copied = $state(false);

  const paths = $derived(Object.keys(files).sort());
  // follow the newest file unless the person picked one that still exists
  const current = $derived(selected && selected in files ? selected : (paths[0] ?? null));
  const content = $derived(current ? files[current] : '');
  const kind = $derived(current ? (/\.(md|markdown)$/i.test(current) ? 'md' : /\.json$/i.test(current) ? 'json' : 'text') : 'text');
  const shown = $derived(kind === 'json' ? prettyMaybeJson(content).text : content);

  function size(s: string): string {
    const n = new TextEncoder().encode(s).length;
    return n < 1024 ? `${n} B` : `${(n / 1024).toFixed(1)} KB`;
  }

  async function copy() {
    try {
      await navigator.clipboard.writeText(content);
      copied = true;
      setTimeout(() => (copied = false), 1500);
    } catch {
      /* clipboard unavailable */
    }
  }
</script>

{#if paths.length}
  <div class="files">
    <ul class="list" aria-label="Files">
      {#each paths as p (p)}
        <li>
          <button class="file" aria-current={p === current ? 'true' : undefined} onclick={() => (selected = p)}>
            <Icon name="file" size={15} />
            <span class="path">{p}</span>
            <span class="size">{size(files[p])}</span>
          </button>
        </li>
      {/each}
    </ul>
    {#if current}
      <div class="viewer">
        <div class="viewer-head">
          <span class="viewer-path" title={current}>{current}</span>
          {#if kind === 'md'}
            <button class="btn small" aria-pressed={raw} onclick={() => (raw = !raw)}>{raw ? 'Rendered' : 'Raw'}</button>
          {/if}
          <button class="btn small" onclick={copy} aria-live="polite">{copied ? 'Copied' : 'Copy'}</button>
        </div>
        {#if kind === 'md' && !raw}
          <div class="body md">{@html renderMarkdown(content)}</div>
        {:else}
          <pre class="body code">{shown}</pre>
        {/if}
      </div>
    {/if}
  </div>
{:else}
  <div class="none">
    <Icon name="file" size={22} />
    <p>No files yet. Reports and notes the agent writes show up here.</p>
  </div>
{/if}

<style>
  .files {
    padding: 10px;
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 10px;
  }

  .list {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 2px;
  }

  .file {
    width: 100%;
    min-height: var(--tap);
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 0 10px;
    border: 1px solid transparent;
    border-radius: var(--radius-sm);
    background: none;
    text-align: left;
    color: var(--text-2);
  }

  .file:hover {
    background: var(--surface-2);
  }

  .file[aria-current='true'] {
    background: var(--accent-soft);
    color: var(--accent);
  }

  .path {
    flex: 1;
    min-width: 0;
    font-family: var(--mono);
    font-size: 12.5px;
    color: var(--text);
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .size {
    font-size: 11.5px;
    color: var(--text-3);
  }

  .viewer {
    border: 1px solid var(--border);
    border-radius: var(--radius);
    overflow: hidden;
    background: var(--surface);
  }

  .viewer-head {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 6px 8px 6px 12px;
    border-bottom: 1px solid var(--border);
    background: var(--surface-2);
  }

  .viewer-path {
    flex: 1;
    min-width: 0;
    font-family: var(--mono);
    font-size: 12px;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .btn.small {
    min-height: 30px;
    padding: 0 9px;
    font-size: 12px;
  }

  .body {
    padding: 12px 14px;
    font-size: 13.5px;
  }

  .code {
    margin: 0;
    font-size: 12px;
    line-height: 1.5;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
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
