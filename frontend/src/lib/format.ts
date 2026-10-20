// Small display helpers.

export function pretty(value: unknown): string {
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
}

/** Pretty-print a string if it holds JSON; otherwise return it as is. */
/** Tool arguments as [key, display text, isPlainText] rows: strings keep real line breaks. */
export function argEntries(args: unknown): [string, string, boolean][] {
  if (!args || typeof args !== 'object' || Array.isArray(args)) return [['value', pretty(args ?? {}), false]];
  const rows: [string, string, boolean][] = [];
  for (const [key, value] of Object.entries(args as Record<string, unknown>)) {
    if (typeof value === 'string') rows.push([key, value, true]);
    else rows.push([key, pretty(value), false]);
  }
  return rows.length ? rows : [['(no arguments)', '', true]];
}

export function prettyMaybeJson(text: string): { text: string; json: boolean } {
  const t = text.trim();
  if ((t.startsWith('{') && t.endsWith('}')) || (t.startsWith('[') && t.endsWith(']'))) {
    try {
      return { text: JSON.stringify(JSON.parse(t), null, 2), json: true };
    } catch {
      /* not JSON */
    }
  }
  return { text, json: false };
}

export function relTime(iso: string, now = Date.now()): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '';
  const s = Math.round((now - t) / 1000);
  if (s < 45) return 'just now';
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.round(h / 24);
  if (d < 7) return `${d} d ago`;
  return new Date(t).toLocaleDateString();
}

export function argsSummary(args: Record<string, unknown> | undefined, max = 80): string {
  if (!args) return '';
  const parts = Object.entries(args).map(([k, v]) => {
    const s = typeof v === 'string' ? v : JSON.stringify(v);
    return `${k}: ${s}`;
  });
  const out = parts.join(', ');
  return out.length > max ? out.slice(0, max - 1) + '…' : out;
}
