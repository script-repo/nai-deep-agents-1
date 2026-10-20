// A small Server-Sent Events parser for streams read with fetch() (EventSource can't POST).
// Follows the WHATWG event-stream rules: LF, CR or CRLF line ends (also split across chunks),
// `:` comment lines (pings), multi-line `data:`, one optional space after the colon, a leading BOM.

export interface SSEMessage {
  event: string;
  data: string;
  id?: string;
  retry?: number;
}

export class SSEParser {
  private buf = '';
  private sawCR = false;
  private started = false;
  private event = '';
  private data: string[] = [];
  private hasData = false;
  private id: string | undefined;
  private retry: number | undefined;

  /** Feed decoded text; returns the messages completed by it. */
  feed(chunk: string): SSEMessage[] {
    const out: SSEMessage[] = [];
    if (!this.started && chunk.length) {
      this.started = true;
      if (chunk.charCodeAt(0) === 0xfeff) chunk = chunk.slice(1);
    }
    let start = 0;
    for (let i = 0; i < chunk.length; i++) {
      const c = chunk.charCodeAt(i);
      if (c === 10 /* \n */) {
        if (this.sawCR) {
          // second half of a CRLF whose CR ended the previous chunk or line
          this.sawCR = false;
          start = i + 1;
          continue;
        }
        this.line(this.buf + chunk.slice(start, i), out);
        this.buf = '';
        start = i + 1;
      } else if (c === 13 /* \r */) {
        this.line(this.buf + chunk.slice(start, i), out);
        this.buf = '';
        start = i + 1;
        this.sawCR = true;
        continue;
      }
      this.sawCR = false;
    }
    this.buf += chunk.slice(start);
    return out;
  }

  /** End of stream: a trailing message without its blank line is discarded, per spec. */
  flush(): SSEMessage[] {
    this.buf = '';
    this.reset();
    return [];
  }

  private reset(): void {
    this.event = '';
    this.data = [];
    this.hasData = false;
  }

  private line(line: string, out: SSEMessage[]): void {
    if (line === '') {
      if (this.hasData) {
        const msg: SSEMessage = { event: this.event || 'message', data: this.data.join('\n') };
        if (this.id !== undefined) msg.id = this.id;
        if (this.retry !== undefined) msg.retry = this.retry;
        out.push(msg);
      }
      this.reset();
      return;
    }
    if (line.charCodeAt(0) === 58 /* : */) return; // comment / ping
    const colon = line.indexOf(':');
    let field = line;
    let value = '';
    if (colon !== -1) {
      field = line.slice(0, colon);
      value = line.slice(colon + 1);
      if (value.charCodeAt(0) === 32) value = value.slice(1);
    }
    switch (field) {
      case 'event':
        this.event = value;
        break;
      case 'data':
        this.data.push(value);
        this.hasData = true;
        break;
      case 'id':
        if (!value.includes('\0')) this.id = value;
        break;
      case 'retry':
        if (/^\d+$/.test(value)) this.retry = Number(value);
        break;
    }
  }
}

/** Read an SSE response body to the end, calling `onMessage` for each message. */
export async function readSSE(
  body: ReadableStream<Uint8Array>,
  onMessage: (msg: SSEMessage) => void,
): Promise<void> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  const parser = new SSEParser();
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      for (const msg of parser.feed(decoder.decode(value, { stream: true }))) onMessage(msg);
    }
    for (const msg of parser.feed(decoder.decode())) onMessage(msg);
    parser.flush();
  } finally {
    reader.releaseLock();
  }
}
