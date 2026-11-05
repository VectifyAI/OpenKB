"""`.env` merge used by credential writers.

One implementation of the mode ``0600`` write. :mod:`openkb.api_config` calls
it for the REST config patch. Connector sign-in calls it too, so the CLI does
not import FastAPI.
"""

from __future__ import annotations

from pathlib import Path

from openkb.locks import atomic_write_text


def merge_env_file(env_path: Path, updates: dict[str, str | None]) -> None:
    """Merge ``updates`` into ``env_path`` and leave the file at mode ``0600``.

    A ``None`` value removes the key. A leading ``export `` is stripped so the
    key matches what python-dotenv reads back. Callers hold the KB lock: this
    is a read-modify-write. Credential values are never logged.

    The file is tightened to ``0o600`` before the replace. ``atomic_write_text``
    copies the target's current mode onto its temp file, so the renamed result
    is already ``0600``.
    """
    env_lines: dict[str, str] = {}
    if env_path.exists():
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export ") or line.startswith("export\t"):
                line = line[len("export") :].lstrip()
            if "=" not in line:
                continue
            key, _eq, stored = line.partition("=")
            env_lines[key.strip()] = stored
    for key, value in updates.items():
        if value is None:
            env_lines.pop(key, None)
        else:
            env_lines[key] = value
    env_path.parent.mkdir(parents=True, exist_ok=True)
    env_path.touch(mode=0o600, exist_ok=True)
    env_path.chmod(0o600)
    atomic_write_text(env_path, "".join(f"{k}={v}\n" for k, v in env_lines.items()))
