from __future__ import annotations

import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path

from openkb.converter import ConvertResult, _sanitize_stem, convert_document_for_prepare


@dataclass(slots=True)
class PreparedDocument:
    input_index: int
    source_path: Path
    staging_dir: Path
    result: ConvertResult

    @property
    def doc_name_candidate(self) -> str | None:
        return self.result.doc_name


def _prepare_staging_dir(kb_dir: Path, input_index: int, source: Path) -> Path:
    safe = _sanitize_stem(source.stem)
    path = (
        kb_dir
        / ".openkb"
        / "staging"
        / "prepare"
        / f"{input_index:06d}-{safe}-{uuid.uuid4().hex[:8]}"
    )
    path.mkdir(parents=True, exist_ok=False)
    return path


def prepare_document(source: Path, kb_dir: Path, *, input_index: int) -> PreparedDocument:
    """Convert ``source`` into private staging without the KB mutation lock.

    Coordinator-internal: callers must run this under the serial batch owner's
    held ``kb_ingest_lock`` (the ``add`` command acquires it via
    ``@_with_kb_lock``). The reaper reclaims any staging present at the owner's
    first lock acquisition, so once this runs under the held lock the staging
    tree is private to this batch.
    """
    staging_dir = _prepare_staging_dir(kb_dir, input_index, source)
    try:
        result = convert_document_for_prepare(source, kb_dir, staging_dir=staging_dir)
        return PreparedDocument(
            input_index=input_index,
            source_path=source,
            staging_dir=staging_dir,
            result=result,
        )
    except BaseException:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise


def _clear_staging_artifacts(staging_dir: Path) -> None:
    """Drop convertible artifacts (raw/, wiki/) from a prepare staging dir.

    Used before re-converting into the same staging dir at commit time so stale
    images/raw from the prior prepare don't leak into the re-convert.
    """
    for sub in ("raw", "wiki"):
        shutil.rmtree(staging_dir / sub, ignore_errors=True)
