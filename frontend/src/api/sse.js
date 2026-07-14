// SSE streaming over fetch (EventSource cannot set Authorization headers).
// Parses `event:` / `data:` blocks from a ReadableStream and invokes handlers.

import { baseUrl, getToken, notifyUnauthorized } from "./client.js";

// Parse raw text buffer into complete SSE blocks, returning [events, remainder].
function parseBuffer(buf) {
  const blocks = buf.split("\n\n");
  const remainder = blocks.pop();
  const events = [];
  for (const blk of blocks) {
    const evMatch = blk.match(/^event:\s*(.+)$/m);
    const daMatch = blk.match(/^data:\s*(.+)$/m);
    if (evMatch && daMatch) {
      let data;
      try {
        data = JSON.parse(daMatch[1]);
      } catch {
        data = { raw: daMatch[1] };
      }
      events.push({ event: evMatch[1].trim(), data });
    }
  }
  return [events, remainder];
}

async function readStream(res, onEvent) {
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    const [events, remainder] = parseBuffer(buf);
    buf = remainder;
    for (const { event, data } of events) {
      if (onEvent(event, data) === false) return;
    }
  }
}

// Build headers with token. JSON bodies are stringified; FormData passed through.
function buildHeaders(body, extra = {}) {
  const token = getToken();
  const isForm = body instanceof FormData;
  return {
    ...(body && !isForm ? { "Content-Type": "application/json" } : {}),
    Accept: "text/event-stream",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...extra,
  };
}

// Stream a JSON-POST endpoint. Returns an AbortController-like handle via opts.signal.
export async function streamSSE(path, payload, onEvent, opts = {}) {
  const res = await fetch(baseUrl() + path, {
    method: "POST",
    headers: buildHeaders(payload),
    body: JSON.stringify(payload),
    signal: opts.signal,
  });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail || j);
    } catch {
      // keep default
    }
  const err = new Error(msg);
  err.status = res.status;
  if (res.status === 401) notifyUnauthorized();
  throw err;
}
await readStream(res, onEvent);
}

// Stream a multipart upload (FormData) endpoint.
export async function streamUpload(path, form, onEvent, opts = {}) {
  const res = await fetch(baseUrl() + path, {
    method: "POST",
    headers: buildHeaders(form),
    body: form,
    signal: opts.signal,
  });
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail || j);
    } catch {
      // keep default
    }
  const err = new Error(msg);
  err.status = res.status;
  if (res.status === 401) notifyUnauthorized();
  throw err;
}
await readStream(res, onEvent);
}
