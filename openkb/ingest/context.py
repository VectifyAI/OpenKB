"""Context object shared across ingest pipeline stages."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from openkb.config import load_config
from openkb.converter import _sanitize_stem


@dataclass(frozen=True)
class IngestContext:
    kb_dir: Path
    config: dict
    staging_dir: Path

    @classmethod
    def for_target(cls, kb_dir: Path, target_name: str) -> "IngestContext":
        safe = _sanitize_stem(Path(target_name).stem)
        staging_dir = kb_dir / ".openkb" / "staging" / f"bundle-{safe}-{uuid.uuid4().hex[:8]}"
        staging_dir.mkdir(parents=True, exist_ok=False)
        return cls(
            kb_dir=kb_dir,
            config=load_config(kb_dir / ".openkb" / "config.yaml"),
            staging_dir=staging_dir,
        )
