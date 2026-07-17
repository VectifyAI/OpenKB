import { useI18n } from "../i18n.jsx";

export default function Spinner({ size = 18 }) {
  const { t } = useI18n();
  return <span className="spinner" style={{ width: size, height: size }} aria-label={t("loading")} />;
}
