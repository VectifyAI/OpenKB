import { useState, useRef, useEffect } from "react";
import { Search, Send, StopCircle } from "lucide-react";
import { useSSEStream } from "../hooks/useSSEStream.js";
import { useApp } from "../state/AppContext.jsx";
import { useI18n } from "../i18n.jsx";
import EmptyState from "../components/EmptyState.jsx";
import Markdown from "../components/Markdown.jsx";

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

export default function Query({ kb }) {
  const { inspReset, inspAdd, inspDone, toastMsg } = useApp();
  const { t } = useI18n();
  const { busy, start, stop } = useSSEStream();
  const [q, setQ] = useState("");
  const [msgs, setMsgs] = useState([]);
  const msgIdRef = useRef(0);
  const taRef = useRef(null);
  const scrollRef = useRef(null);

  useEffect(() => {
    function onPrefill(e) { setQ(e.detail); if (taRef.current) taRef.current.focus(); }
    window.addEventListener("openkb:prefill-query", onPrefill);
    return () => window.removeEventListener("openkb:prefill-query", onPrefill);
  }, []);

  useEffect(() => { if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight; }, [msgs]);

  // Stop any in-flight stream when the KB changes.
  useEffect(() => { return () => stop(); }, [kb]);

  function autosize() {
    const ta = taRef.current;
    if (ta) { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 140) + "px"; }
  }

  async function run() {
    const question = q.trim();
    if (!question || busy) return;
    setQ("");
    if (taRef.current) taRef.current.style.height = "auto";
    inspReset(true);
    const pairId = ++msgIdRef.current;
    const pair = { id: pairId, user: question, acc: "" };
    setMsgs((m) => [...m, pair]);
    const onAbort = () => {
      setMsgs((m) => m.map((x) => x.id === pairId ? { ...x, aborted: true } : x));
      inspAdd("tool", t("stopped"), t("userInterrupted"));
    };
    try {
      await start(
        { path: "/api/v1/query", payload: { kb, question, stream: true } },
        (ev, d) => {
          if (ev === "tool_call") inspAdd("tool", t("retrieve") + " · " + (d.name || "tool"), `<code>${esc((d.arguments || "").slice(0, 120))}</code>`);
          else if (ev === "delta") {
            pair.acc += d.text || "";
            setMsgs((m) => m.map((x) => x.id === pairId ? { ...pair } : x));
          } else if (ev === "final") {
            pair.acc = d.answer || pair.acc;
            setMsgs((m) => m.map((x) => x.id === pairId ? { ...pair } : x));
            inspAdd("done", t("completed"), t("reasoningDone"));
          } else if (ev === "error") {
            pair.acc = `<span style="color:var(--red)">${esc(d.message)}</span>`;
            setMsgs((m) => m.map((x) => x.id === pairId ? { ...pair } : x));
            inspAdd("error", t("error"), esc(d.message));
          }
        },
        onAbort
      );
    } finally {
      inspDone();
    }
  }

  return (
    <div className="qa-wrap">
      <div className="qa-stream" ref={scrollRef}>
        {msgs.length === 0 ? (
          <EmptyState
            icon={<Search size={40} strokeWidth={1.5} />}
            title={t("askKb")}
            desc={t("askKbDesc")}
          />
        ) : (
          msgs.map((m, i) => (
            <div className="msg" key={i}>
              <div className="msg-role user"><span className="role-dot" />{t("you")}</div>
              <div className="msg-bubble user">{m.user}</div>
              <div className="msg-role"><span className="role-dot" />OpenKB</div>
              <div className="msg-bubble">{m.acc ? <Markdown>{m.acc}</Markdown> : (m.aborted ? <span className="cell-meta">{t("stopped")}</span> : <span className="spinner-wrap"><span className="spinner" /></span>)}</div>
            </div>
          ))
        )}
      </div>
      <div className="qa-input-bar">
        <textarea
          ref={taRef}
          className="qa-input"
          placeholder={t("typeQuestion")}
          rows={1}
          value={q}
          onChange={(e) => { setQ(e.target.value); autosize(); }}
          // isComposing: 避免拼音输入法确认候选词时误触发提交
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); run(); } }}
        />
        {busy
          ? <button className="btn btn-ghost" onClick={stop}><StopCircle size={15} /> {t("stopGeneration")}</button>
          : <button className="btn btn-primary" onClick={run}><Send size={15} /> {t("ask")}</button>}
      </div>
    </div>
  );
}
