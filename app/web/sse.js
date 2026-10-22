// 增量按“行”解析，支持 CRLF 跨块、多个事件同块及多行 data。
export class SSEParser {
  constructor(onEvent) {
    this.onEvent = onEvent;
    this.buffer = "";
    this.event = "message";
    this.data = [];
  }
  line(line) {
    if (line === "") {
      if (this.data.length) this.onEvent({event: this.event, data: this.data.join("\n")});
      this.event = "message";
      this.data = [];
      return;
    }
    if (line.startsWith(":")) return;
    const colon = line.indexOf(":");
    const name = colon < 0 ? line : line.slice(0, colon);
    let value = colon < 0 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (name === "event") this.event = value;
    if (name === "data") this.data.push(value);
  }
  feed(text, final = false) {
    this.buffer += text;
    while (true) {
      const index = this.buffer.search(/[\r\n]/);
      if (index < 0) break;
      if (this.buffer[index] === "\r" && index === this.buffer.length - 1 && !final) break;
      const width = this.buffer[index] === "\r" && this.buffer[index + 1] === "\n" ? 2 : 1;
      const line = this.buffer.slice(0, index);
      this.buffer = this.buffer.slice(index + width);
      this.line(line);
    }
  }
  finish() { this.feed("", true); }
}

export async function readSSE(body, onEvent) {
  const reader = body.getReader();
  const decoder = new TextDecoder("utf-8", {fatal: true});
  const parser = new SSEParser(frame => onEvent(frame.event, JSON.parse(frame.data)));
  let ended = false;
  try {
    while (true) {
      const {done, value} = await reader.read();
      if (done) { ended = true; break; }
      parser.feed(decoder.decode(value, {stream: true}));
    }
    parser.feed(decoder.decode());
    parser.finish();
  } finally {
    if (!ended) await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
