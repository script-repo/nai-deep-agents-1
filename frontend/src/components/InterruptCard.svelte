<script lang="ts">
  import { untrack } from 'svelte';
  import { allowedDecisions } from '../lib/reducer';
  import { argEntries, pretty } from '../lib/format';
  import type { Decision, Interrupt } from '../lib/types';
  import Icon from './Icon.svelte';

  let { interrupt, disabled = false, onsubmit }: {
    interrupt: Interrupt;
    disabled?: boolean;
    onsubmit: (decisions: Decision[]) => void;
  } = $props();

  type Choice = 'approve' | 'reject' | 'edit' | null;
  interface Row {
    choice: Choice;
    reason: string;
    editText: string;
    error: string | null;
  }

  // the parent keys this card by interrupt id, so the initial actions are the ones it decides on
  const rows = $state<Row[]>(
    untrack(() => interrupt.actions).map((a) => ({ choice: null, reason: '', editText: pretty(a.args ?? {}), error: null })),
  );
  let sent = $state(false);
  const single = $derived(interrupt.actions.length === 1);

  function decisionFor(i: number): Decision | null {
    const r = rows[i];
    const a = interrupt.actions[i];
    if (r.choice === 'approve') return { type: 'approve' };
    if (r.choice === 'reject') return r.reason.trim() ? { type: 'reject', message: r.reason.trim() } : { type: 'reject' };
    if (r.choice === 'edit') {
      try {
        const args = JSON.parse(r.editText);
        if (!args || typeof args !== 'object' || Array.isArray(args)) throw new Error('Arguments must be a JSON object');
        r.error = null;
        return { type: 'edit', edited_action: { name: a.name, args } };
      } catch (err) {
        r.error = err instanceof SyntaxError ? `Invalid JSON: ${err.message}` : (err as Error).message;
        return null;
      }
    }
    return null;
  }

  function submit() {
    const decisions = rows.map((_, i) => decisionFor(i));
    if (decisions.some((d) => d === null)) return;
    sent = true;
    onsubmit(decisions as Decision[]);
  }

  function choose(i: number, choice: Choice) {
    rows[i].choice = rows[i].choice === choice && choice !== 'approve' ? null : choice;
    rows[i].error = null;
    if (single && choice === 'approve') submit();
  }

  const ready = $derived(rows.every((r) => r.choice !== null));

  function summaryOf(desc: string | undefined): string {
    if (!desc) return '';
    // the default HITL description repeats the tool and args we already show
    return desc.split(/\n\s*\n/)[0].trim();
  }
</script>

<section class="card" aria-label="Approval needed">
  <header>
    <span class="badge"><Icon name="lock" size={14} /></span>
    <div>
      <h2>Approval needed</h2>
      <p>
        The agent paused before {single ? 'this action' : `these ${interrupt.actions.length} actions`}.
        {single ? 'Approve, reject or edit it' : 'Decide on each'} to continue.
      </p>
    </div>
  </header>

  {#each interrupt.actions as action, i (i)}
    {@const allowed = allowedDecisions(interrupt, action.name)}
    {@const row = rows[i]}
    <div class="action">
      <div class="action-head">
        <code class="tool">{action.name}</code>
        {#if summaryOf(action.description)}
          <span class="desc">{summaryOf(action.description)}</span>
        {/if}
      </div>
      {#if row.choice === 'edit'}
        <label class="field">
          <span>Arguments (JSON)</span>
          <textarea class="input mono" rows={Math.min(14, row.editText.split('\n').length + 1)}
            bind:value={row.editText} aria-invalid={row.error ? 'true' : undefined} spellcheck="false"></textarea>
        </label>
        {#if row.error}<p class="err" role="alert">{row.error}</p>{/if}
      {:else}
        <dl class="args">
          {#each argEntries(action.args) as [key, value, isText] (key)}
            <div class="arg">
              <dt>{key}</dt>
              <dd><pre class={isText ? 'text' : 'json'}>{value}</pre></dd>
            </div>
          {/each}
        </dl>
      {/if}
      {#if row.choice === 'reject'}
        <label class="field reason">
          <span>Reason (optional, sent to the agent)</span>
          <input class="input" bind:value={row.reason} placeholder="e.g. Keep the current model for now" />
        </label>
      {/if}

      <div class="buttons" role="group" aria-label={`Decision for ${action.name}`}>
        {#if allowed.includes('approve')}
          <button class="btn" class:primary={single || row.choice === 'approve'} aria-pressed={single ? undefined : row.choice === 'approve'}
            disabled={disabled || sent} onclick={() => choose(i, 'approve')}>
            <Icon name="check" size={16} /> Approve
          </button>
        {/if}
        {#if allowed.includes('reject')}
          <button class="btn" class:chosen={row.choice === 'reject'} aria-pressed={row.choice === 'reject'}
            disabled={disabled || sent} onclick={() => choose(i, 'reject')}>
            <Icon name="x" size={16} /> Reject
          </button>
        {/if}
        {#if allowed.includes('edit')}
          <button class="btn" class:chosen={row.choice === 'edit'} aria-pressed={row.choice === 'edit'}
            disabled={disabled || sent} onclick={() => choose(i, 'edit')}>
            <Icon name="wrench" size={15} /> Edit args
          </button>
        {/if}
        {#if single && (row.choice === 'reject' || row.choice === 'edit')}
          <button class="btn primary confirm" disabled={disabled || sent} onclick={submit}>
            {row.choice === 'reject' ? 'Send rejection' : 'Approve with edits'}
          </button>
        {/if}
      </div>
    </div>
  {/each}

  {#if !single}
    <footer>
      <button class="btn primary" disabled={!ready || disabled || sent} onclick={submit}>Submit decisions</button>
    </footer>
  {/if}
</section>

<style>
  .args {
    margin: 0;
    display: grid;
    gap: 8px;
  }
  .arg dt {
    font-size: 12px;
    font-weight: 600;
    color: var(--text-2);
    margin-bottom: 3px;
  }
  .arg dd {
    margin: 0;
  }
  pre.text {
    margin: 0;
    padding: 8px 10px;
    border-radius: var(--radius-sm);
    background: var(--surface-2);
    border: 1px solid var(--border);
    font: inherit;
    font-size: 13px;
    line-height: 1.5;
    white-space: pre-wrap;
    overflow-wrap: anywhere;
    max-height: 320px;
    overflow: auto;
  }
  .card {
    margin-top: 16px;
    border-radius: var(--radius-lg);
    border: 1px solid color-mix(in srgb, var(--warn) 45%, var(--border));
    background: var(--surface);
    box-shadow: var(--shadow);
    overflow: hidden;
  }

  header {
    display: flex;
    gap: 10px;
    padding: 12px 14px;
    background: var(--warn-soft);
    border-bottom: 1px solid color-mix(in srgb, var(--warn) 25%, var(--border));
  }

  .badge {
    flex: none;
    width: 28px;
    height: 28px;
    border-radius: 8px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--warn);
    color: var(--surface);
  }

  h2 {
    margin: 0;
    font-size: 14px;
    font-weight: 650;
  }

  header p {
    margin: 1px 0 0;
    color: var(--text-2);
    font-size: 13px;
  }

  .action {
    padding: 12px 14px;
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 10px;
  }

  .action + .action {
    border-top: 1px solid var(--border);
  }

  .action-head {
    display: flex;
    flex-wrap: wrap;
    align-items: baseline;
    gap: 8px;
  }

  .tool {
    font-weight: 600;
    font-size: 13px;
    padding: 2px 7px;
    border-radius: 6px;
    background: var(--accent-soft);
    color: var(--accent);
  }

  .desc {
    color: var(--text-2);
    font-size: 13px;
  }

  .mono {
    font-family: var(--mono);
    font-size: 12.5px;
  }

  .reason {
    margin-top: -2px;
  }

  .buttons {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
  }

  .btn.chosen {
    border-color: var(--accent);
    background: var(--accent-soft);
    color: var(--accent);
  }

  .confirm {
    margin-left: auto;
  }

  .err {
    margin: -4px 0 0;
    color: var(--danger);
    font-size: 12.5px;
  }

  footer {
    padding: 10px 14px 14px;
    display: flex;
    justify-content: flex-end;
  }
</style>
