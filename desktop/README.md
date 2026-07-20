# OpenKB Desktop (Tauri)

A desktop app that wraps OpenKB for non-technical users: no `pip`, no terminal.
It is a thin [Tauri](https://tauri.app) (Rust) shell around two things OpenKB
already produces:

1. **The API sidecar** — `openkb-api` (the FastAPI server from the Knowledge
   Workbench, `openkb.api:main`) frozen into a self-contained binary with
   PyInstaller. It serves both the JSON/SSE API and the built web UI.
2. **The web UI** — the `frontend/` Vite SPA, served by the sidecar at `/`.

```
┌─────────────────────────────────────────────┐
│  Tauri shell (Rust + system WebView)         │
│                                              │
│   spawns ──▶  openkb-api-sidecar (frozen)    │
│               ├─ FastAPI  /api/v1/*          │
│               └─ web UI   /                  │
│   WebView ──▶ http://127.0.0.1:<port>/       │
└─────────────────────────────────────────────┘
```

The user double-clicks the app; the Rust shell starts the sidecar on a
localhost port, waits until it answers, then points the WebView at it. No
browser, no port, no "server" ever surfaces to the user.

## Why this shape

- OpenKB's capability lives in a heavy Python stack (litellm, pageindex,
  markitdown, pymupdf). Rust can't replace that, so the Python runs as a frozen
  sidecar. The Rust shell is glue: window, WebView, process lifecycle, updater.
- Tauri (vs Electron) ships no Chromium — smaller download, less memory — at the
  cost of using each platform's system WebView.

## Layout

```
desktop/
  packaging/            # freeze the Python API into a slim sidecar binary
    build_sidecar.sh    #   PyInstaller recipe (slim: no magika/onnxruntime)
    sidecar_entry.py    #   frozen entry -> openkb.api:main
    pyi_rthook_magika.py#   runtime hook: stub magika (drops onnxruntime)
    prune_litellm_proxy.py  # post-build: delete the unused LiteLLM proxy server
  src-tauri/            # Tauri (Rust) shell — spawns the sidecar, opens the window
```

## Build pipeline

```bash
# 1. Python env with API + PyInstaller
python -m venv .venv && . .venv/bin/activate
pip install -e ".[api]" pyinstaller

# 2. Build the web UI (its output is what the sidecar serves)
cd frontend && npm install && npm run build && cd ..

# 3. Freeze the slim API sidecar  ->  desktop/packaging/dist/openkb-api-sidecar/
PYTHON=.venv/bin/python desktop/packaging/build_sidecar.sh

# 4. Build the Tauri app (bundles the sidecar + opens the WebView)
cd desktop/src-tauri && cargo tauri build
```

## Slimming (measured, x86_64 Linux)

The sidecar reuses the packaging work from the CLI slimming (see PR #186 for the
lazy-markitdown source change that makes it possible):

| stage | compressed |
|---|---|
| full PyInstaller freeze | 147 MB |
| − magika / onnxruntime (stub hook) | 140 MB |
| − LiteLLM proxy server | 133 MB |

The frozen **API sidecar** (adds FastAPI + uvicorn over the CLI baseline)
measures **≈134 MB compressed / 338 MB on disk**, verified booting uvicorn and
serving `GET /api/v1/kbs` → 200.

`pymupdf` (PDF engine) and `pandas`/`numpy` (Excel support) are kept
deliberately — they are load-bearing for OpenKB's document formats.

## Toolchain / status

Full builds need, per platform: Rust + Cargo, Node + npm, a Python 3.10+ env,
and on **Linux `webkit2gtk`** (`libwebkit2gtk-4.1-dev`) for the WebView.

**Status:** the API sidecar freeze (`packaging/`) is implemented and verified.
The `src-tauri/` shell is a reference skeleton — it is *not* compiled in the
CI/dev container used so far because `webkit2gtk` is absent there; build it on a
machine (or CI runner) that has the WebView libraries installed.
