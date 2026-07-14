import { useEffect, useRef } from "react";
import { useApp } from "../state/AppContext.jsx";

export default function Inspector() {
  const { inspItems, inspBusy } = useApp();
  const bodyRef = useRef(null);

  useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }, [inspItems]);

  return (
    <aside className="inspector">
      <div className="insp-head">
        <span className="insp-title">检索与推理</span>
        <span className={`insp-status ${inspBusy ? "busy" : ""}`}>{inspBusy ? "推理中…" : "空闲"}</span>
      </div>
      <div className="insp-body" ref={bodyRef}>
        {inspItems.length === 0 ? (
          <div className="insp-empty">
            发起查询或对话后，<br />无向量检索与推理过程将在此实时呈现。
          </div>
        ) : (
          inspItems.map((it, i) => (
            <div key={i} className={`timeline-item ${it.kind}`}>
              <span className="tl-tag">{it.tag}</span>
              <div className="tl-body" dangerouslySetInnerHTML={{ __html: it.body }} />
            </div>
          ))
        )}
      </div>
    </aside>
  );
}
