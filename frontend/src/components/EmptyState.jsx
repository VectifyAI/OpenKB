export default function EmptyState({ title, desc, icon }) {
  return (
    <div className="empty-state">
      {icon || null}
      {title && <h3>{title}</h3>}
      {desc && <p>{desc}</p>}
    </div>
  );
}
