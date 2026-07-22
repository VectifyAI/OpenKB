"""Prune the unused LiteLLM Proxy Server from a frozen build.

`--collect-all litellm` pulls litellm's entire *Proxy Server* (~44 MB: a
FastAPI gateway with an admin UI, swagger assets and OpenAPI snapshots), but
OpenKB only uses litellm as a client. `import litellm` loads a handful of small
proxy submodules; none of the static/data assets below are reached by any
client code path (verified: not in sys.modules after `import litellm` plus a
completion call). Deleting them from the frozen tree is safe; the sidecar smoke
test (`GET /api/v1/kbs` returns 200) confirms the client path stays intact.

Usage: python prune_litellm_proxy.py <path-to-frozen-app-dir>
The frozen app dir is PyInstaller's onedir output (contains `_internal/`).
"""

from __future__ import annotations

import sys
from pathlib import Path

# Static/data dead weight — never imported by a litellm *client*.
_PRUNE_DIRS = ("_experimental", "swagger")
_PRUNE_GLOBS = ("*.jpg", "*.json", "*.yaml", "*.txt", "README.md")


def _dir_size_mb(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file()) // (1024 * 1024)


def prune(app_dir: Path) -> None:
    proxy = app_dir / "_internal" / "litellm" / "proxy"
    if not proxy.is_dir():
        print(f"prune_litellm_proxy: no proxy dir at {proxy}; nothing to do")
        return
    before = _dir_size_mb(proxy)
    import shutil

    for name in _PRUNE_DIRS:
        target = proxy / name
        if target.exists():
            shutil.rmtree(target)
    for pattern in _PRUNE_GLOBS:
        for f in proxy.glob(pattern):
            f.unlink()
    after = _dir_size_mb(proxy)
    print(f"prune_litellm_proxy: {before}M -> {after}M")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: python prune_litellm_proxy.py <frozen-app-dir>")
    prune(Path(sys.argv[1]))
