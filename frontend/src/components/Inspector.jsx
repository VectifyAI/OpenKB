import { useEffect, useRef } from "react";
import { useApp } from "../state/AppContext.jsx";
import { useI18n } from "../i18n.jsx";

export default function Inspector() {
  const { inspItems, inspBusy } = useApp();
  const { t } = useI18n();
  const bodyRef = useRef(null);

  useEffect(() => {
    if (bodyRef.current) bodyRef.current.scrollTop = bodyRef.current.scrollHeight;
  }, [inspItems]);

  return (
    <aside className="inspector">
      <div className="insp-head">
        <span className="insp-title">{t("inspectorTitle")}</span>
        <span className={`insp-status ${inspBusy ? "busy" : ""}`}>{inspBusy ? t("inspBusy") : t("inspIdle")}</span>
      </div>
      <div className="insp-body" ref={bodyRef}>
        {inspItems.length === 0 ? (
          <div className="insp-empty">
            {t("inspEmptyHint")}
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
