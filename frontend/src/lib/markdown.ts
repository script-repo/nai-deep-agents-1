// A minimal, safe markdown renderer for chat and files. The whole input is HTML-escaped first; the only tags
// in the output are the ones produced here. Links are kept only for http(s), mailto, relative and # targets.
// Covers: headings, paragraphs, bold, italics, strikethrough, inline code, fenced code, lists (nested by
// indent), blockquotes, tables, horizontal rules, links.

export function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

const SAFE_URL = /^(https?:\/\/|mailto:|\/(?!\/)|#|\.{0,2}\/)/i;

/** Inline markup on already-escaped text. */
function inline(s: string): string {
  const slots: string[] = [];
  const keep = (html: string) => `\u0000${slots.push(html) - 1}\u0000`;
  // inline code first, so nothing inside it is touched
  s = s.replace(/(`+)([^`]|[^`][\s\S]*?[^`])\1(?!`)/g, (_, _t, code: string) => keep(`<code>${code.trim()}</code>`));
  s = s.replace(/\[([^\]\n]+)\]\(([^()\s]+)\)/g, (m, text: string, url: string) => {
    if (!SAFE_URL.test(url)) return m;
    const ext = /^https?:/i.test(url) ? ' target="_blank" rel="noopener noreferrer"' : '';
    return keep(`<a href="${url}"${ext}>`) + text + keep('</a>');
  });
  s = s.replace(/(^|[\s(])(https?:\/\/[^\s<]+[^\s<.,;:!?)'"&])/g, (_, pre: string, url: string) =>
    pre + keep(`<a href="${url}" target="_blank" rel="noopener noreferrer">${url}</a>`));
  s = s.replace(/\*\*(?=\S)([\s\S]*?\S)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/__(?=\S)([\s\S]*?\S)__/g, '<strong>$1</strong>');
  s = s.replace(/(^|[^*\w])\*(?=\S)([^*\n]*?\S)\*(?!\*)/g, '$1<em>$2</em>');
  s = s.replace(/(^|[^_\w])_(?=\S)([^_\n]*?\S)_(?![_\w])/g, '$1<em>$2</em>');
  s = s.replace(/~~(?=\S)([\s\S]*?\S)~~/g, '<del>$1</del>');
  return s.replace(/\u0000(\d+)\u0000/g, (_, i: string) => slots[Number(i)]);
}

const RE = {
  fence: /^ {0,3}(`{3,}|~{3,})\s*([\w+#.-]*)/,
  heading: /^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$/,
  hr: /^ {0,3}([-*_])(\s*\1){2,}\s*$/,
  quote: /^ {0,3}&gt;\s?(.*)$/,
  item: /^(\s*)([-*+]|\d{1,9}[.)])\s+(.*)$/,
  tableSep: /^\s*\|?\s*:?-{1,}:?\s*(\|\s*:?-{1,}:?\s*)*\|?\s*$/,
};

function splitRow(row: string): string[] {
  let r = row.trim();
  if (r.startsWith('|')) r = r.slice(1);
  if (r.endsWith('|') && !r.endsWith('\\|')) r = r.slice(0, -1);
  return r.split(/(?<!\\)\|/).map((c) => c.trim().replace(/\\\|/g, '|'));
}

function renderList(lines: string[]): string {
  // lines are list items (and their continuation lines); nesting follows indentation
  type Node = { ordered: boolean; start: number; items: { text: string; children: string[] }[]; indent: number };
  const first = RE.item.exec(lines[0])!;
  const base = first[1].length;
  const list: Node = { ordered: /\d/.test(first[2]), start: parseInt(first[2], 10) || 1, items: [], indent: base };
  for (const line of lines) {
    const m = RE.item.exec(line);
    if (m && m[1].length <= base + 1) {
      list.items.push({ text: m[3], children: [] });
    } else if (list.items.length) {
      const cur = list.items[list.items.length - 1];
      if (m || cur.children.length) cur.children.push(line);
      else cur.text += '\n' + line.trim();
    }
  }
  const tag = list.ordered ? 'ol' : 'ul';
  const start = list.ordered && list.start !== 1 ? ` start="${list.start}"` : '';
  const items = list.items.map((it) => {
    let text = it.text;
    let check = '';
    const task = /^\[([ xX])\]\s+(.*)$/s.exec(text);
    if (task) {
      check = `<input type="checkbox" disabled${task[1] === ' ' ? '' : ' checked'}> `;
      text = task[2];
    }
    const nested = it.children.length ? renderBlocks(dedent(it.children)) : '';
    return `<li>${check}${inline(text).replace(/\n/g, '<br>')}${nested}</li>`;
  });
  return `<${tag}${start}>${items.join('')}</${tag}>`;
}

function dedent(lines: string[]): string[] {
  const min = Math.min(...lines.filter((l) => l.trim()).map((l) => /^\s*/.exec(l)![0].length));
  return lines.map((l) => l.slice(Number.isFinite(min) ? min : 0));
}

function renderBlocks(lines: string[]): string {
  const out: string[] = [];
  let para: string[] = [];
  const flush = () => {
    if (para.length) out.push(`<p>${inline(para.join('\n')).replace(/\n/g, '<br>')}</p>`);
    para = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    let m: RegExpExecArray | null;
    if ((m = RE.fence.exec(line))) {
      flush();
      const marker = m[1];
      const lang = m[2];
      const body: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trimStart().startsWith(marker)) body.push(lines[i++]);
      const cls = lang ? ` class="lang-${lang.replace(/[^\w+#.-]/g, '')}"` : '';
      out.push(`<pre><code${cls}>${body.join('\n')}</code></pre>`);
      continue;
    }
    if (!line.trim()) {
      flush();
      continue;
    }
    if ((m = RE.heading.exec(line))) {
      flush();
      const n = m[1].length;
      out.push(`<h${n}>${inline(m[2])}</h${n}>`);
      continue;
    }
    if (RE.hr.test(line)) {
      flush();
      out.push('<hr>');
      continue;
    }
    if (line.includes('|') && i + 1 < lines.length && RE.tableSep.test(lines[i + 1]) && lines[i + 1].includes('-')) {
      flush();
      const head = splitRow(line);
      const aligns = splitRow(lines[i + 1]).map((c) =>
        c.startsWith(':') && c.endsWith(':') ? 'center' : c.endsWith(':') ? 'right' : '');
      const al = (j: number) => (aligns[j] ? ` style="text-align:${aligns[j]}"` : '');
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) rows.push(splitRow(lines[i++]));
      i--;
      out.push(
        '<div class="table-wrap"><table><thead><tr>' +
          head.map((c, j) => `<th${al(j)}>${inline(c)}</th>`).join('') +
          '</tr></thead><tbody>' +
          rows.map((r) => '<tr>' + head.map((_, j) => `<td${al(j)}>${inline(r[j] ?? '')}</td>`).join('') + '</tr>').join('') +
          '</tbody></table></div>',
      );
      continue;
    }
    if (RE.quote.test(line)) {
      flush();
      const body: string[] = [];
      while (i < lines.length && (m = RE.quote.exec(lines[i]))) {
        body.push(m[1]);
        i++;
      }
      i--;
      out.push(`<blockquote>${renderBlocks(body)}</blockquote>`);
      continue;
    }
    if (RE.item.test(line) && (para.length === 0 || /^\s*([-*+]|1[.)])\s/.test(line))) {
      flush();
      const body: string[] = [];
      const head = RE.item.exec(line)!;
      const indent = head[1].length;
      const ordered = /\d/.test(head[2]);
      // a top-level item of the other kind (bullet vs number) starts a new list
      const otherKind = (l: string) => {
        const m2 = RE.item.exec(l);
        return !!m2 && m2[1].length <= indent + 1 && /\d/.test(m2[2]) !== ordered;
      };
      while (i < lines.length) {
        const l = lines[i];
        if (body.length && otherKind(l)) break;
        if (!l.trim()) {
          // a blank line continues the list only if more list content follows
          const next = lines[i + 1];
          if (next !== undefined && !otherKind(next) && (RE.item.test(next) || /^\s{2,}\S/.test(next))) {
            i++;
            continue;
          }
          break;
        }
        if (body.length && !RE.item.test(l) && !/^\s/.test(l) &&
            (RE.heading.test(l) || RE.fence.test(l) || RE.hr.test(l) || RE.quote.test(l))) break;
        body.push(l);
        i++;
      }
      i--;
      out.push(renderList(body));
      continue;
    }
    para.push(line);
  }
  flush();
  return out.join('\n');
}

export function renderMarkdown(src: string): string {
  if (!src) return '';
  return renderBlocks(escapeHtml(src.replace(/\u0000/g, '').replace(/\r\n?/g, '\n')).split('\n'));
}
