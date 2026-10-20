import { describe, expect, it } from 'vitest';
import { readSSE, SSEParser, type SSEMessage } from './sse';

function feedAll(chunks: string[]): SSEMessage[] {
  const p = new SSEParser();
  return chunks.flatMap((c) => p.feed(c));
}

describe('SSEParser', () => {
  it('parses a simple event', () => {
    expect(feedAll(['event: token\ndata: {"a":1}\n\n'])).toEqual([{ event: 'token', data: '{"a":1}' }]);
  });

  it('defaults the event name to message', () => {
    expect(feedAll(['data: x\n\n'])[0].event).toBe('message');
  });

  it('handles CRLF and lone CR line endings', () => {
    expect(feedAll(['event: a\r\ndata: 1\r\n\r\nevent: b\rdata: 2\r\r'])).toEqual([
      { event: 'a', data: '1' },
      { event: 'b', data: '2' },
    ]);
  });

  it('handles a CRLF split across chunks without seeing an extra blank line', () => {
    const msgs = feedAll(['data: 1\r', '\ndata: 2\r', '\n\r', '\n']);
    expect(msgs).toEqual([{ event: 'message', data: '1\n2' }]);
  });

  it('reassembles events split at every possible byte boundary', () => {
    const stream = 'event: tool_call\r\ndata: {"id":"x",\r\ndata: "n":2}\r\n\r\n: ping\r\n\r\nevent: done\ndata: {}\n\n';
    for (let size = 1; size <= stream.length; size++) {
      const chunks: string[] = [];
      for (let i = 0; i < stream.length; i += size) chunks.push(stream.slice(i, i + size));
      expect(feedAll(chunks)).toEqual([
        { event: 'tool_call', data: '{"id":"x",\n"n":2}' },
        { event: 'done', data: '{}' },
      ]);
    }
  });

  it('ignores comments and pings, and blank lines without data', () => {
    expect(feedAll([': ping - 2026-10-20\n\n:\n\n\n\nevent: x\n\n'])).toEqual([]);
  });

  it('joins multi-line data with newlines and strips only one leading space', () => {
    expect(feedAll(['data:  two spaces\ndata:no space\ndata\n\n'])).toEqual([
      { event: 'message', data: ' two spaces\nno space\n' },
    ]);
  });

  it('keeps id and retry fields', () => {
    expect(feedAll(['id: 7\nretry: 1500\ndata: a\n\n'])).toEqual([{ event: 'message', data: 'a', id: '7', retry: 1500 }]);
  });

  it('strips a leading BOM', () => {
    expect(feedAll(['\uFEFFdata: a\n\n'])).toEqual([{ event: 'message', data: 'a' }]);
  });

  it('discards an unterminated trailing event', () => {
    const p = new SSEParser();
    expect(p.feed('data: half')).toEqual([]);
    expect(p.flush()).toEqual([]);
  });

  it('allows colons in values', () => {
    expect(feedAll(['data: {"t":"a:b"}\n\n'])[0].data).toBe('{"t":"a:b"}');
  });
});

describe('readSSE', () => {
  it('decodes UTF-8 split across byte chunks', async () => {
    const bytes = new TextEncoder().encode('event: token\ndata: {"text":"héllo → ✓"}\n\n');
    const stream = new ReadableStream<Uint8Array>({
      start(ctrl) {
        for (let i = 0; i < bytes.length; i += 3) ctrl.enqueue(bytes.slice(i, i + 3));
        ctrl.close();
      },
    });
    const got: SSEMessage[] = [];
    await readSSE(stream, (m) => got.push(m));
    expect(got).toEqual([{ event: 'token', data: '{"text":"héllo → ✓"}' }]);
  });
});
