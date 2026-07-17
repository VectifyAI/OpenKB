// Unified SSE lifecycle hook: a single busy state + abortable streams.
// start() forwards every SSE event to onEvent and surfaces fetch/network
// errors as a synthetic {error} event so callers only handle one shape.
// stop() aborts the in-flight request via AbortController; onAbort fires
// so the caller can flip a pending bubble to a settled state.

import { useCallback, useEffect, useRef, useState } from "react";
import { streamSSE, streamUpload } from "../api/sse.js";
import { useI18n } from "../i18n.jsx";

export function useSSEStream() {
  const [busy, setBusy] = useState(false);
  const ctrlRef = useRef(null);
  const { t } = useI18n();

  // Abort any in-flight stream when the component using this hook unmounts.
  // Without this, leaving a Chat/Query view mid-stream keeps the fetch alive
  // and fires setMsgs/inspector callbacks after unmount, writing deltas for
  // the old KB/session captured in the start() closure.
  useEffect(() => {
    return () => {
      if (ctrlRef.current) {
        ctrlRef.current.abort();
        ctrlRef.current = null;
      }
    };
  }, []);

  const stop = useCallback(() => {
    if (ctrlRef.current) {
      ctrlRef.current.abort();
      ctrlRef.current = null;
    }
  }, []);

  const start = useCallback(async (cfg, onEvent, onAbort) => {
    // If a prior stream is somehow still in-flight, abort it before starting
    // a new one instead of silently dropping the call.
    if (ctrlRef.current) {
      ctrlRef.current.abort();
      ctrlRef.current = null;
    }
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
        onEvent("error", { message: err?.message || t("requestFailed") });
      }
    } finally {
      // A replaced stream may finish after its successor has started. Only
      // clear state when this controller still owns the active stream.
      if (ctrlRef.current === ctrl) {
        ctrlRef.current = null;
        setBusy(false);
      }
    }
  }, [t]);

  return { busy, start, stop };
}
