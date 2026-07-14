import { useState, useRef, useEffect, useCallback } from "react";
import { MessageSquare, Send, Plus, Trash2, StopCircle } from "lucide-react";
import { useSSEStream } from "../hooks/useSSEStream.js";
import { api } from "../api/client.js";
import { useApp } from "../state/AppContext.jsx";
import { useI18n } from "../i18n.jsx";
import EmptyState from "../components/EmptyState.jsx";
import Markdown from "../components/Markdown.jsx";

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

export default function Chat({ kb }) {
  const { inspReset, inspAdd, inspDone, toastMsg } = useApp();
  const { t } = useI18n();
  const { busy, start, stop } = useSSEStream();
  const [sessions, setSessions] = useState([]);
  const [sessionId, setSessionId] = useState(null);
  const [msgs, setMsgs] = useState([]);
  const msgIdRef = useRef(0);
  const [input, setInput] = useState("");
  const taRef = useRef(null);
  const scrollRef = useRef(null);

  const loadSessions = useCallback(() => {
    api.chatSessions(kb).then((r) => setSessions(r.sessions || [])).catch(() => setSessions([]));
  }, [kb]);

  useEffect(loadSessions, [loadSessions]);

  // Stop any in-flight stream when the KB changes so deltas for the old
  // KB do not write into the new KB's message list.
  useEffect(() => { return () => stop(); }, [kb]);

  useEffect(() => { if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight; }, [msgs]);

  function newSession() {
    stop();
    setSessionId(null);
    setMsgs([]);
  }

  async function loadHistory(sid) {
    stop();
    setSessionId(sid);
    setMsgs([]);
    try {
      const r = await api.chatSessionLoad(kb, sid);
      const hist = [];
      const n = Math.max(r.user_turns.length, r.assistant_texts.length);
      for (let i = 0; i < n; i++) {
        if (i < r.user_turns.length) hist.push({ role: "user", text: r.user_turns[i] });
        if (i < r.assistant_texts.length) hist.push({ role: "assistant", text: r.assistant_texts[i] });
      }
      setMsgs(hist);
    } catch (e) {
      toastMsg(e.message, "err");
    }
  }

  async function delSession(sid, e) {
    e.stopPropagation();
    if (!window.confirm(t("deleteSession"))) return;
    try {
      await api.chatSessionDelete(kb, sid);
      if (sid === sessionId) newSession();
      loadSessions();
      toastMsg(t("deleted"), "ok");
    } catch (e2) {
      toastMsg(e2.message, "err");
    }
  }

  function autosize() {
    const ta = taRef.current;
    if (ta) { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 140) + "px"; }
  }

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    if (taRef.current) taRef.current.style.height = "auto";
    inspReset(true);
    const aiId = ++msgIdRef.current;
    const userMsg = { id: ++msgIdRef.current, role: "user", text };
    const aiMsg = { id: aiId, role: "assistant", text: "", pending: true };
    setMsgs((m) => [...m, userMsg, aiMsg]);
    let acc = "";
    const onAbort = () => {
      setMsgs((m) => m.map((x) => x.id === aiId ? { ...x, text: acc, pending: false, aborted: true } : x));
      inspAdd("tool", t("stopped"), t("userInterrupted"));
    };
    try {
      await start(
        { path: "/api/v1/chat", payload: { kb, message: text, session_id: sessionId, stream: true } },
        (ev, d) => {
          if (ev === "start" && d.session_id) setSessionId(d.session_id);
          else if (ev === "tool_call") inspAdd("tool", t("retrieve") + " · " + (d.name || "tool"), `<code>${esc((d.arguments || "").slice(0, 120))}</code>`);
          else if (ev === "delta") {
            acc += d.text || "";
            setMsgs((m) => m.map((x) => x.id === aiId ? { ...x, text: acc, pending: true } : x));
          } else if (ev === "final") {
            acc = d.answer || acc;
            setMsgs((m) => m.map((x) => x.id === aiId ? { ...x, text: acc, pending: false } : x));
            inspAdd("done", t("completed"), t("turnCount").replace("{n}", d.turn_count || ""));
          } else if (ev === "error") {
            setMsgs((m) => m.map((x) => x.id === aiId ? { ...x, text: `<span style="color:var(--red)">${esc(d.message)}</span>`, pending: false } : x));
            inspAdd("error", t("error"), esc(d.message));
          }
        },
        onAbort
      );
      loadSessions();
    } finally {
      inspDone();
    }
  }

  return (
    <div className="qa-wrap">
      <div className="chat-sessions">
        <button className={`session-item ${!sessionId ? "active" : ""}`} onClick={newSession}>
          <Plus size={13} /> {t("newSession")}
        </button>
        {sessions.map((s) => (
          <button key={s.id} className={`session-item ${s.id === sessionId ? "active" : ""}`} onClick={() => loadHistory(s.id)}>
            <span className="session-title">{s.title || s.id}</span>
            <Trash2 size={13} className="session-del" onClick={(e) => delSession(s.id, e)} />
          </button>
        ))}
      </div>
      <div className="qa-stream" ref={scrollRef}>
        {msgs.length === 0 ? (
          <EmptyState
            icon={<MessageSquare size={40} strokeWidth={1.5} />}
            title={t("multiTurn")}
            desc={t("chatPersist")}
          />
        ) : (
          msgs.map((m, i) => (
            <div className="msg" key={i}>
              <div className={`msg-role ${m.role === "user" ? "user" : ""}`}><span className="role-dot" />{m.role === "user" ? t("you") : "OpenKB"}</div>
              <div className={`msg-bubble ${m.role === "user" ? "user" : ""}`}>
                {m.role === "user" ? m.text : (m.text ? <Markdown>{m.text}</Markdown> : (m.aborted ? <span className="cell-meta">{t("stopped")}</span> : <span className="spinner-wrap"><span className="spinner" /></span>))}
              </div>
            </div>
          ))
        )}
      </div>
      <div className="qa-input-bar">
        <button className="btn btn-ghost btn-sm" onClick={newSession}><Plus size={14} /> {t("newSession")}</button>
        <textarea
          ref={taRef}
          className="qa-input"
          placeholder={t("typeToContinue")}
          rows={1}
          value={input}
          onChange={(e) => { setInput(e.target.value); autosize(); }}
          // isComposing: 避免拼音输入法确认候选词时误触发提交
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }}
        />
        {busy
          ? <button className="btn btn-ghost" onClick={stop}><StopCircle size={15} /> {t("stopGeneration")}</button>
          : <button className="btn btn-primary" onClick={send}><Send size={15} /> {t("send")}</button>}
      </div>
    </div>
  );
}
