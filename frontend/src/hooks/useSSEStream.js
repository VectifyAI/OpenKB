// Unified SSE lifecycle hook: a single busy state + abortable streams.
// start() forwards every SSE event to onEvent and surfaces fetch/network
// errors as a synthetic {error} event so callers only handle one shape.
// stop() aborts the in-flight request via AbortController; onAbort fires
// so the caller can flip a pending bubble to a settled state.

import { useCallback, useRef, useState } from "react";
import { streamSSE, streamUpload } from "../api/sse.js";

export function useSSEStream() {
  const [busy, setBusy] = useState(false);
  const ctrlRef = useRef(null);

  const stop = useCallback(() => {
    if (ctrlRef.current) {
      ctrlRef.current.abort();
    }
  }, []);

  const start = useCallback(async (cfg, onEvent, onAbort) => {
    if (ctrlRef.current) return; // already streaming
    const ctrl = new AbortController();
    ctrlRef.current = ctrl;
    setBusy(true);
    try {
      if (cfg.form) {
        await streamUpload(cfg.path, cfg.form, onEvent, { signal: ctrl.signal });
      } else {
        await streamSSE(cfg.path, cfg.payload, onEvent, { signal: ctrl.signal });
      }
    } catch (err) {
      if (ctrl.signal.aborted) {
        // User-initiated stop; do not surface as an error.
        if (onAbort) onAbort();
      } else {
        onEvent("error", { message: err?.message || "请求失败" });
      }
    } finally {
      ctrlRef.current = null;
      setBusy(false);
    }
  }, []);

  return { busy, start, stop };
}
