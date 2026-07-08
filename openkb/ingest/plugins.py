"""Entry point loading for external ingest plugins."""

from __future__ import annotations

from importlib.metadata import entry_points
from typing import Any

IMPORTER_ENTRY_POINT_GROUP = "openkb.ingest.importers"
NORMALIZER_ENTRY_POINT_GROUP = "openkb.ingest.normalizers"
ENRICHER_ENTRY_POINT_GROUP = "openkb.ingest.enrichers"


def load_ingest_entry_points(
    group: str,
    enabled_names: tuple[str, ...],
    *,
    exclude: set[str] | None = None,
) -> list[Any]:
    """Load configured external ingest components from Python entry points."""
    enabled = set(enabled_names)
    if not enabled:
        return []
    excluded = exclude or set()
    components: list[Any] = []
    for entry_point in _entry_points_for(group):
        if entry_point.name not in enabled or entry_point.name in excluded:
            continue
        try:
            loaded = entry_point.load()
            components.append(_component_instance(loaded, group, entry_point.name))
        except Exception as exc:
            raise ValueError(
                f"Could not load ingest plugin {entry_point.name!r} from {group}: {exc}"
            ) from exc
    return components


def _entry_points_for(group: str) -> list[Any]:
    points = entry_points()
    if hasattr(points, "select"):
        return list(points.select(group=group))
    return list(points.get(group, []))


def _component_instance(loaded: Any, group: str, name: str) -> Any:
    if isinstance(loaded, type):
        instance = loaded()
        if _looks_like_component(instance):
            return instance
    if _looks_like_component(loaded):
        return loaded
    if callable(loaded):
        instance = loaded()
        if _looks_like_component(instance):
            return instance
    raise TypeError(f"entry point {name!r} in {group} did not produce an ingest component")


def _looks_like_component(value: Any) -> bool:
    return hasattr(value, "name")
