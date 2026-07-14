import { useState, useRef, useEffect, useCallback } from "react";
import { MessageSquare, Send, Plus, Trash2, StopCircle } from "lucide-react";
import { useSSEStream } from "../hooks/useSSEStream.js";
import { api } from "../api/client.js";
import { useApp } from "../state/AppContext.jsx";
import EmptyState from "../components/EmptyState.jsx";
import Markdown from "../components/Markdown.jsx";

function esc(s) {
  return String(s == null ? "" : s).replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

export default function Chat({ kb }) {
  const { inspReset, inspAdd, inspDone, toastMsg } = useApp();
  const { busy, start, stop } = useSSEStream();
  const [sessions, setSessions] = useState([]);
  const [sessionId, setSessionId] = useState(null);
  const [msgs, setMsgs] = useState([]);
  const [input, setInput] = useState("");
  const taRef = useRef(null);
  const scrollRef = useRef(null);

  const loadSessions = useCallback(() => {
    api.chatSessions(kb).then((r) => setSessions(r.sessions || [])).catch(() => setSessions([]));
  }, [kb]);

  useEffect(loadSessions, [loadSessions]);

  useEffect(() => { if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight; }, [msgs]);

  function newSession() {
    setSessionId(null);
    setMsgs([]);
  }

  async function loadHistory(sid) {
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
    if (!window.confirm("删除此会话？")) return;
    try {
      await api.chatSessionDelete(kb, sid);
      if (sid === sessionId) newSession();
      loadSessions();
      toastMsg("已删除", "ok");
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
    const userMsg = { role: "user", text };
    const aiMsg = { role: "assistant", text: "", pending: true };
    setMsgs((m) => [...m, userMsg, aiMsg]);
    const aiIdx = msgs.length + 1;
    let acc = "";
    const onAbort = () => {
      setMsgs((m) => { const n = [...m]; n[aiIdx] = { role: "assistant", text: acc, pending: false, aborted: true }; return n; });
      inspAdd("tool", "已停止", "用户中断生成");
    };
    try {
      await start(
        { path: "/api/v1/chat", payload: { kb, message: text, session_id: sessionId, stream: true } },
        (ev, d) => {
          if (ev === "start" && d.session_id) setSessionId(d.session_id);
          else if (ev === "tool_call") inspAdd("tool", "检索 · " + (d.name || "tool"), `<code>${esc((d.arguments || "").slice(0, 120))}</code>`);
          else if (ev === "delta") {
            acc += d.text || "";
            setMsgs((m) => { const n = [...m]; n[aiIdx] = { role: "assistant", text: acc, pending: true }; return n; });
          } else if (ev === "final") {
            acc = d.answer || acc;
            setMsgs((m) => { const n = [...m]; n[aiIdx] = { role: "assistant", text: acc, pending: false }; return n; });
            inspAdd("done", "完成", `第 ${d.turn_count || ""} 轮`);
          } else if (ev === "error") {
            setMsgs((m) => { const n = [...m]; n[aiIdx] = { role: "assistant", text: `<span style="color:var(--red)">${esc(d.message)}</span>`, pending: false }; return n; });
            inspAdd("error", "错误", esc(d.message));
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
          <Plus size={13} /> 本次新会话
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
            title="多轮对话"
            desc="会话自动持久化，可跨次恢复。"
          />
        ) : (
          msgs.map((m, i) => (
            <div className="msg" key={i}>
              <div className={`msg-role ${m.role === "user" ? "user" : ""}`}><span className="role-dot" />{m.role === "user" ? "你" : "OpenKB"}</div>
              <div className={`msg-bubble ${m.role === "user" ? "user" : ""}`}>
                {m.role === "user" ? m.text : (m.text ? <Markdown>{m.text}</Markdown> : (m.aborted ? <span className="cell-meta">已停止生成</span> : <span className="spinner-wrap"><span className="spinner" /></span>))}
              </div>
            </div>
          ))
        )}
      </div>
      <div className="qa-input-bar">
        <button className="btn btn-ghost btn-sm" onClick={newSession}><Plus size={14} /> 新会话</button>
        <textarea
          ref={taRef}
          className="qa-input"
          placeholder="继续对话…"
          rows={1}
          value={input}
          onChange={(e) => { setInput(e.target.value); autosize(); }}
          // isComposing: 避免拼音输入法确认候选词时误触发提交
          onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) { e.preventDefault(); send(); } }}
        />
        {busy
          ? <button className="btn btn-ghost" onClick={stop}><StopCircle size={15} /> 停止生成</button>
          : <button className="btn btn-primary" onClick={send}><Send size={15} /> 发送</button>}
      </div>
    </div>
  );
}
