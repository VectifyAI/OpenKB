#!/usr/bin/env bash
#
# Build the OpenKB API sidecar: a self-contained, slimmed PyInstaller binary
# that the Tauri desktop shell spawns and talks to over localhost HTTP.
#
# Slimming (see ../README.md for measured sizes):
#   - exclude magika + onnxruntime; a runtime hook stubs magika so markitdown
#     still converts Office/HTML by file extension.
#   - post-build prune of the unused LiteLLM Proxy Server.
#
# Prerequisites: a Python env with `pip install -e ".[api]"` and `pyinstaller`.
# Set PYTHON to that env's interpreter (default: python3).
#
# Usage:  PYTHON=/path/to/venv/bin/python desktop/packaging/build_sidecar.sh
# Output: desktop/packaging/dist/openkb-api-sidecar/
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"
cd "$HERE"

rm -rf build dist openkb-api-sidecar.spec

# `openkb` is collected whole so PyInstaller bundles (a) its data files —
# crucially the built web UI at openkb/web that the API serves at `/`, plus
# prompt/template assets — and (b) submodules reached only via lazy/dynamic
# imports (e.g. the deferred `from openkb.converter import ...`). The web UI
# must be built (npm run build → openkb/web) BEFORE this script runs.
COLLECT=(openkb litellm markitdown pageindex tiktoken agents openai tiktoken_ext uvicorn fastapi)
EXCLUDE=(magika onnxruntime)

ARGS=(--onedir --name openkb-api-sidecar --noconfirm --clean --log-level=WARN)
ARGS+=(--runtime-hook "$HERE/pyi_rthook_magika.py")
for pkg in "${COLLECT[@]}"; do ARGS+=(--collect-all "$pkg"); done
for pkg in "${EXCLUDE[@]}"; do ARGS+=(--exclude-module "$pkg"); done

echo ">>> freezing openkb-api sidecar (slim: no magika/onnxruntime)"
"$PYTHON" -m PyInstaller "${ARGS[@]}" sidecar_entry.py

echo ">>> pruning unused litellm proxy server"
"$PYTHON" prune_litellm_proxy.py "dist/openkb-api-sidecar"

echo ">>> done: dist/openkb-api-sidecar/openkb-api-sidecar"
