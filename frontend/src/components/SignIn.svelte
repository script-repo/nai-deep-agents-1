<script lang="ts">
  import { basic, bearer, HttpError } from '../lib/api';
  import { app } from '../lib/store.svelte';

  const mode = $derived(app.config?.auth ?? 'token');
  const hasToken = $derived(mode.includes('token'));
  const hasBasic = $derived(mode.includes('basic'));

  let method = $state<'token' | 'basic'>('token');
  let token = $state('');
  let user = $state('');
  let password = $state('');
  let error = $state<string | null>(null);
  let busy = $state(false);

  const active = $derived(hasToken && hasBasic ? method : hasBasic ? 'basic' : 'token');

  async function submit(e: SubmitEvent) {
    e.preventDefault();
    error = null;
    busy = true;
    try {
      await app.signIn(active === 'basic' ? basic(user.trim(), password) : bearer(token));
    } catch (err) {
      error = err instanceof HttpError && err.status === 401
        ? active === 'basic' ? 'Wrong username or password.' : 'That token was not accepted.'
        : err instanceof Error ? err.message : String(err);
    } finally {
      busy = false;
    }
  }
</script>

<main class="wrap">
  <form class="card" onsubmit={submit} aria-labelledby="signin-h">
    <div class="logo" aria-hidden="true">
      <svg viewBox="0 0 32 32" width="36" height="36"><rect width="32" height="32" rx="8" fill="var(--accent)" /><path
          d="M9 22V10l7 8 7-8v12" fill="none" stroke="var(--accent-text)" stroke-width="2.6" stroke-linecap="round"
          stroke-linejoin="round" /></svg>
    </div>
    <h1 id="signin-h">Sign in to Deep Agents</h1>
    <p class="sub">This server needs a credential.</p>

    {#if hasToken && hasBasic}
      <div class="seg" role="radiogroup" aria-label="Sign-in method">
        <label class:on={method === 'token'}><input type="radio" bind:group={method} value="token" /> Token</label>
        <label class:on={method === 'basic'}><input type="radio" bind:group={method} value="basic" /> User account</label>
      </div>
    {/if}

    {#if active === 'token'}
      <label class="field">
        <span>Access token</span>
        <input class="input" type="password" bind:value={token} required autocomplete="current-password" />
      </label>
    {:else}
      <label class="field">
        <span>Username</span>
        <input class="input" bind:value={user} required autocomplete="username" autocapitalize="none" />
      </label>
      <label class="field">
        <span>Password</span>
        <input class="input" type="password" bind:value={password} required autocomplete="current-password" />
      </label>
    {/if}

    {#if error}<p class="err" role="alert">{error}</p>{/if}
    <button class="btn primary" type="submit" disabled={busy}>
      {#if busy}<span class="spinner"></span>{/if} Sign in
    </button>
    <p class="hint">Your credential is kept in this browser until you sign out.</p>
  </form>
</main>

<style>
  .wrap {
    min-height: 100%;
    display: flex;
    align-items: center;
    justify-content: center;
    padding: 24px 16px;
  }

  .card {
    width: min(380px, 100%);
    display: grid;
    gap: 14px;
    padding: 28px 24px 22px;
    border: 1px solid var(--border);
    border-radius: var(--radius-lg);
    background: var(--surface);
    box-shadow: var(--shadow);
  }

  .logo {
    display: flex;
    justify-content: center;
  }

  h1 {
    margin: 0;
    text-align: center;
    font-size: 19px;
    font-weight: 650;
  }

  .sub {
    margin: -8px 0 4px;
    text-align: center;
    color: var(--text-2);
  }

  .seg {
    display: grid;
    grid-template-columns: 1fr 1fr;
    padding: 3px;
    gap: 3px;
    border-radius: var(--radius-sm);
    background: var(--surface-2);
  }

  .seg label {
    min-height: 36px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 5px;
    font-weight: 550;
    font-size: 13px;
    color: var(--text-2);
    cursor: pointer;
  }

  .seg label.on {
    background: var(--surface);
    color: var(--text);
    box-shadow: var(--shadow);
  }

  .seg label:focus-within {
    outline: 2px solid var(--accent);
  }

  .seg input {
    position: absolute;
    opacity: 0;
    pointer-events: none;
  }

  .err {
    margin: 0;
    color: var(--danger);
    font-size: 13px;
  }

  .hint {
    margin: 0;
    text-align: center;
  }
</style>
