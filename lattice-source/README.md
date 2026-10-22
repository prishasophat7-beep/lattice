# Lattice

Offline desktop assistant that answers from company documents, with citations.
Tauri 2 (Rust) shell, React 19 + TypeScript UI, Python engine (FastAPI + PydanticAI), llama.cpp model server.

| Folder | What it is |
|---|---|
| `app/desktop/` | The desktop app: React UI (`src/`), Tauri shell (`src-tauri/`), UI stability tests (`ui-tests/`) |
| `app/backend/` | The engine (`oasis_engine/`), its tests (`tests/`), the RAG evaluation (`eval/rag_eval/`) |
| `app/docs/` | Deployment guide, knowledge-base bundle format, project plan, diagrams |
| `app/sample-data/` | Small bundles used by tests |
| `sample_kb_*.zip`, `sample_knowledge_base.zip` | Sample knowledge bases (web-platform export format), used by the eval and UI tests |

## Branches

- `main`: the released document assistant (Manuals and Chat modes).
- `code-mode`: experimental coding-agent mode (jcode). Checked out separately in `..\lattice-code-mode\`
  so the released app is never disturbed.

## Set up after cloning

Not in git (recreated or copied):

- `app/backend/.venv`: `py -3.12 -m venv app\backend\.venv` then `app\backend\.venv\Scripts\pip install -e app\backend[dev]`
- `app/desktop/node_modules`: `npm install` in `app\desktop`
- `app/runtime/llama/`: llama.cpp `llama-server.exe` with its DLLs and license files (see `app/prepare-runtime.ps1`)
- `app/backend/eval/rag_eval/data/`: public eval datasets, `python app\backend\eval\rag_eval\fetch_datasets.py`
- Models (`.gguf`): imported in the app (Manage), not stored in the repository

## Build and test

```
powershell -ExecutionPolicy Bypass -File app\build-release.ps1
```

Runs the engine tests and the UI stability tests, packages the engine, and builds the installer
(`app\desktop\src-tauri\target\release\bundle\nsis\`).
