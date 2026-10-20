import { describe, expect, it } from 'vitest';
import { renderMarkdown } from './markdown';

describe('renderMarkdown', () => {
  it('escapes HTML everywhere', () => {
    const html = renderMarkdown('<img src=x onerror=alert(1)> **<b>x</b>** `<i>` \n```\n<script>\n```');
    expect(html).not.toMatch(/<img|<script|<b>|<i>/);
    expect(html).toContain('&lt;img');
  });

  it('drops unsafe link targets', () => {
    expect(renderMarkdown('[x](javascript:alert(1))')).not.toContain('href');
    expect(renderMarkdown('[x](https://a.b/c?d=1&e=2)')).toContain('href="https://a.b/c?d=1&amp;e=2"');
  });

  it('renders headings, emphasis, code and lists', () => {
    const html = renderMarkdown('# Title\n\nSome **bold**, *it* and `code_x`.\n\n- a\n- b\n  - c\n\n1. one\n2. two');
    expect(html).toContain('<h1>Title</h1>');
    expect(html).toContain('<strong>bold</strong>');
    expect(html).toContain('<em>it</em>');
    expect(html).toContain('<code>code_x</code>');
    expect(html).toContain('<ul><li>a</li><li>b<ul><li>c</li></ul></li></ul>');
    expect(html).toContain('<ol><li>one</li><li>two</li></ol>');
  });

  it('leaves snake_case alone', () => {
    expect(renderMarkdown('apply_routing and model_name')).toBe('<p>apply_routing and model_name</p>');
  });

  it('renders tables', () => {
    const html = renderMarkdown('| Category | Model |\n|---|--:|\n| a | `m` |');
    expect(html).toContain('<th>Category</th>');
    expect(html).toContain('<td style="text-align:right"><code>m</code></td>');
  });

  it('renders fenced code with a language class', () => {
    expect(renderMarkdown('```json\n{"a": 1}\n```')).toBe('<pre><code class="lang-json">{&quot;a&quot;: 1}</code></pre>');
  });
});

import { argEntries } from './format';

describe('argEntries', () => {
  it('keeps multi-line strings as readable text', () => {
    const rows = argEntries({ user: 'd.patel', message: 'Hi,\n\nPlease use X.', limit: 5 });
    expect(rows[1]).toEqual(['message', 'Hi,\n\nPlease use X.', true]);
    expect(rows[2]).toEqual(['limit', '5', false]);
  });
});
