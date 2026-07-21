"""Knowledge-base admin operations: physically delete a KB + registry cleanup.

Kept out of config.py (which sits near the per-file line gate) because these
mutate the filesystem (``shutil.rmtree`` under the KB ingest lock) and the
global registry — side effects the otherwise-pure config-loading module avoids.

Imports the config MODULE (not names) so ``config.GLOBAL_CONFIG_PATH`` is read
at call time — a test that monkeypatches it (isolated global.yaml) must be
honored, which a by-value ``from openkb.config import GLOBAL_CONFIG_PATH`` would
silently break.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from openkb import config
from openkb.locks import kb_ingest_lock


def unregister_kb(kb_path: Path) -> None:
    """Remove ``kb_path`` from the global registry (its ``known_kbs`` entry, any
    ``kb_aliases`` pointing at it, ``default_kb`` if it referenced it). Held
    under the global-config lock; a no-op for a never-registered path."""
    resolved = str(kb_path.resolve())
    with config._with_global_config_lock():
        gc = config._load_global_config_unlocked()
        changed = False
        known = gc.get("known_kbs")
        if isinstance(known, list):
            pruned = [k for k in known if k != resolved]
            if len(pruned) != len(known):
                gc["known_kbs"] = pruned
                changed = True
        aliases = gc.get("kb_aliases")
        if isinstance(aliases, dict):
            pruned_aliases = {a: p for a, p in aliases.items() if p != resolved}
            if len(pruned_aliases) != len(aliases):
                gc["kb_aliases"] = pruned_aliases
                changed = True
        if gc.get("default_kb") == resolved:
            del gc["default_kb"]
            changed = True
        if changed:
            config._atomic_yaml_dump(config.GLOBAL_CONFIG_PATH, gc)


def delete_kb(kb_dir: Path) -> None:
    """Physically delete a KB directory and unregister it (irreversible; the
    CALLER confirms). An existing ``kb_dir`` MUST be a KB (``.openkb`` + ``wiki``)
    or :class:`ValueError` is raised and nothing removed; a ghost entry
    (directory already gone) is tolerated — no ``rmtree``, just unregistered.
    """
    kb_dir = kb_dir.resolve()
    if kb_dir.exists():
        if not config._is_kb_dir(kb_dir):
            raise ValueError(f"Refusing to delete: not a knowledge base directory: {kb_dir}")
        # Serialize against in-flight ingest/recompile on this KB (like `openkb
        # remove`). POSIX: rmtree unlinks the held lock file, but the open fd
        # stays valid, so the funlock on context-exit is safe.
        with kb_ingest_lock(kb_dir / ".openkb"):
            shutil.rmtree(kb_dir)
    unregister_kb(kb_dir)
