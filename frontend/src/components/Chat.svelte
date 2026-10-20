<script lang="ts">
  import { tick } from 'svelte';
  import { app } from '../lib/store.svelte';
  import { renderMarkdown } from '../lib/markdown';
  import Icon from './Icon.svelte';
  import InterruptCard from './InterruptCard.svelte';

  let text = $state('');
  let scroller = $state<HTMLElement>();
  let input = $state<HTMLTextAreaElement>();
  let stick = true;

  const view = $derived(app.view);
  const agent = $derived(app.agent);
  const empty = $derived(view.messages.length === 0 && !app.loadingThread);
  const lastLen = $derived(view.messages.at(-1)?.content.length ?? 0);

  // keep the newest content in view while the person hasn't scrolled up
  $effect(() => {
    void view.messages.length;
    void lastLen;
    void view.interrupts.length;
    void view.error;
    if (stick) void tick().then(() => scroller?.scrollTo({ top: scroller.scrollHeight }));
  });

  // a different thread: jump to its end
  $effect(() => {
    void app.threadId;
    stick = true;
    void tick().then(() => scroller?.scrollTo({ top: scroller.scrollHeight }));
  });

  function onScroll() {
    if (!scroller) return;
    stick = scroller.scrollHeight - scroller.scrollTop - scroller.clientHeight < 80;
  }

  function autosize() {
    if (!input) return;
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 220) + 'px';
  }

  async function submit(value = text) {
    if (!value.trim() || app.isRunning || view.status === 'interrupted') return;
    text = '';
    stick = true;
    await tick();
    autosize();
    await app.send(value);
  }

  function onKey(e: KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      void submit();
    }
  }

  const blocked = $derived(view.status === 'interrupted');
</script>

<div class="chat">
  <div class="scroll" bind:this={scroller} onscroll={onScroll}>
    <div class="col">
      {#if app.loadingThread}
        <div class="loading"><span class="spinner"></span> Opening chat…</div>
      {:else if empty && agent}
        <div class="empty">
          <div class="empty-icon"><Icon name={agent.icon} size={26} /></div>
          <h1>{agent.name}</h1>
          <p class="summary">{agent.summary}</p>
          {#if agent.example_prompts.length}
            <p class="try">Try one of these</p>
            <div class="chips">
              {#each agent.example_prompts as p (p)}
                <button class="chip" onclick={() => submit(p)} disabled={app.isRunning}>{p}</button>
              {/each}
            </div>
          {/if}
          {#if agent.interrupt_on.length}
            <p class="hint approval-hint">
              <Icon name="lock" size={13} /> Asks for your approval before: {agent.interrupt_on.join(', ')}
            </p>
          {/if}
        </div>
      {/if}

      <ol class="messages" aria-label="Conversation">
        {#each view.messages as m (m.key)}
          <li class="msg {m.role}">
            {#if m.role === 'user'}
              <div class="bubble user-bubble">{m.content}</div>
            {:else}
              <div class="avatar" aria-hidden="true"><Icon name={agent?.icon ?? 'bot'} size={15} /></div>
              <div class="bubble assistant-bubble md" class:streaming={m.streaming}>
                <span class="sr-only">{agent?.name ?? 'Agent'}:</span>
                {@html renderMarkdown(m.content)}
              </div>
            {/if}
          </li>
        {/each}
      </ol>

      {#if app.isRunning && view.messages.at(-1)?.role !== 'assistant'}
        <div class="working" aria-hidden="true">
          <span class="dots"><i></i><i></i><i></i></span>
          {#if view.activity.length}
            {@const last = view.activity[view.activity.length - 1]}
            <span class="working-text">
              {last.kind === 'subagent' ? `Delegated to ${last.name}` : `${last.agent !== 'main' ? `${last.agent} · ` : ''}${last.name}`}
            </span>
          {/if}
        </div>
      {/if}

      {#each view.interrupts as intr, i (intr.id ?? i)}
        <InterruptCard interrupt={intr} disabled={app.isRunning}
          onsubmit={(decisions) => app.resolveInterrupt(decisions, intr.id)} />
      {/each}

      {#if view.error}
        <div class="error" role="alert">
          <Icon name="alert" size={16} />
          <span>{view.error}</span>
        </div>
      {/if}
    </div>
  </div>

  <form class="composer" onsubmit={(e) => (e.preventDefault(), submit())}>
    <div class="col">
      <div class="box" class:disabled={blocked}>
        <label for="prompt" class="sr-only">Message {agent?.name ?? 'the agent'}</label>
        <textarea
          id="prompt"
          bind:this={input}
          bind:value={text}
          oninput={autosize}
          onkeydown={onKey}
          rows="1"
          placeholder={blocked ? 'Answer the approval request above to continue' : `Message ${agent?.name ?? 'the agent'}…`}
          disabled={blocked || !agent}
          enterkeyhint="send"
        ></textarea>
        {#if app.isRunning}
          <button type="button" class="send stop" aria-label="Stop the run" title="Stop" onclick={() => app.stop()}>
            <Icon name="stop" size={16} />
          </button>
        {:else}
          <button type="submit" class="send" aria-label="Send" title="Send (Enter)" disabled={!text.trim() || blocked}>
            <Icon name="send" size={17} />
          </button>
        {/if}
      </div>
      <p class="kbd-hint">Enter to send · Shift+Enter for a new line</p>
    </div>
  </form>
</div>

<style>
  .chat {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
  }

  .scroll {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    overscroll-behavior: contain;
  }

  .col {
    width: 100%;
    max-width: 780px;
    margin: 0 auto;
    padding: 0 20px;
  }

  @media (max-width: 640px) {
    .col {
      padding: 0 12px;
    }
  }

  .scroll .col {
    padding-top: 20px;
    padding-bottom: 20px;
  }

  .loading {
    display: flex;
    align-items: center;
    gap: 10px;
    color: var(--text-2);
    padding: 40px 0;
    justify-content: center;
  }

  .empty {
    text-align: center;
    padding: 6vh 0 12px;
  }

  .empty-icon {
    width: 52px;
    height: 52px;
    margin: 0 auto 14px;
    display: flex;
    align-items: center;
    justify-content: center;
    border-radius: 14px;
    background: var(--accent-soft);
    color: var(--accent);
  }

  .empty h1 {
    margin: 0 0 6px;
    font-size: 22px;
    font-weight: 650;
    letter-spacing: -0.01em;
  }

  .summary {
    margin: 0 auto;
    max-width: 520px;
    color: var(--text-2);
  }

  .try {
    margin: 26px 0 10px;
    font-size: 12px;
    font-weight: 600;
    color: var(--text-3);
    text-transform: uppercase;
    letter-spacing: 0.06em;
  }

  .chips {
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 8px;
    max-width: 560px;
    margin: 0 auto;
  }

  .chip {
    min-height: var(--tap);
    padding: 9px 14px;
    border-radius: var(--radius);
    border: 1px solid var(--border);
    background: var(--surface);
    text-align: left;
    color: var(--text);
    box-shadow: var(--shadow);
    transition: border-color 0.12s, background 0.12s;
  }

  .chip:hover:not(:disabled) {
    border-color: var(--accent);
    background: var(--accent-soft);
  }

  .approval-hint {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    margin-top: 18px;
  }

  .messages {
    list-style: none;
    margin: 0;
    padding: 0;
    display: grid;
    grid-template-columns: minmax(0, 1fr);
    gap: 14px;
  }

  .msg {
    display: flex;
    gap: 10px;
    min-width: 0;
  }

  .msg.user {
    justify-content: flex-end;
  }

  .bubble {
    min-width: 0;
    max-width: 100%;
  }

  .user-bubble {
    max-width: 85%;
    padding: 9px 14px;
    border-radius: 16px 16px 4px 16px;
    background: var(--accent);
    color: var(--accent-text);
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }

  .avatar {
    flex: none;
    width: 28px;
    height: 28px;
    margin-top: 2px;
    border-radius: 8px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--surface-2);
    color: var(--text-2);
    border: 1px solid var(--border);
  }

  .assistant-bubble {
    flex: 1;
    padding: 9px 14px;
    border-radius: 4px 16px 16px 16px;
    background: var(--surface);
    border: 1px solid var(--border);
  }

  .streaming :global(> :last-child)::after {
    content: '';
    display: inline-block;
    width: 7px;
    height: 1em;
    margin-left: 2px;
    vertical-align: -0.15em;
    background: var(--accent);
    border-radius: 1px;
    animation: blink 1s steps(2) infinite;
  }

  @keyframes blink {
    50% {
      opacity: 0;
    }
  }

  .working {
    display: flex;
    align-items: center;
    gap: 10px;
    margin-top: 14px;
    padding-left: 38px;
    color: var(--text-2);
    font-size: 13px;
  }

  .working-text {
    font-family: var(--mono);
    font-size: 12px;
  }

  .dots {
    display: inline-flex;
    gap: 4px;
  }

  .dots i {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--accent);
    animation: bounce 1.2s infinite ease-in-out;
  }

  .dots i:nth-child(2) {
    animation-delay: 0.15s;
  }

  .dots i:nth-child(3) {
    animation-delay: 0.3s;
  }

  @keyframes bounce {
    0%,
    80%,
    100% {
      opacity: 0.3;
      transform: translateY(0);
    }
    40% {
      opacity: 1;
      transform: translateY(-3px);
    }
  }

  .error {
    display: flex;
    align-items: flex-start;
    gap: 8px;
    margin-top: 14px;
    padding: 10px 12px;
    border-radius: var(--radius);
    background: var(--danger-soft);
    color: var(--danger);
    overflow-wrap: anywhere;
  }

  .error :global(svg) {
    margin-top: 2px;
    flex: none;
  }

  .composer {
    flex: none;
    padding: 0 0 max(10px, env(safe-area-inset-bottom));
    background: linear-gradient(to bottom, transparent, var(--bg) 14px);
  }

  .box {
    display: flex;
    align-items: flex-end;
    gap: 6px;
    padding: 6px 6px 6px 14px;
    border-radius: var(--radius-lg);
    border: 1px solid var(--border-strong);
    background: var(--surface);
    box-shadow: var(--shadow);
  }

  .box:focus-within {
    border-color: var(--accent);
    box-shadow: 0 0 0 3px color-mix(in srgb, var(--accent) 18%, transparent), var(--shadow);
  }

  .box.disabled {
    background: var(--surface-2);
  }

  textarea {
    flex: 1;
    min-height: var(--tap);
    max-height: 220px;
    padding: 9px 0;
    border: 0;
    outline: none;
    resize: none;
    background: transparent;
    line-height: 1.45;
  }

  textarea:focus-visible {
    outline: none;
  }

  .send {
    flex: none;
    width: var(--tap);
    height: var(--tap);
    border: 0;
    border-radius: 11px;
    display: flex;
    align-items: center;
    justify-content: center;
    background: var(--accent);
    color: var(--accent-text);
  }

  .send:hover:not(:disabled) {
    background: var(--accent-hover);
  }

  .send:disabled {
    background: var(--surface-3);
    color: var(--text-3);
  }

  .send.stop {
    background: var(--text);
    color: var(--surface);
  }

  .kbd-hint {
    margin: 5px 0 0;
    text-align: center;
    font-size: 11.5px;
    color: var(--text-3);
  }

  @media (pointer: coarse) {
    .kbd-hint {
      display: none;
    }
  }
</style>
