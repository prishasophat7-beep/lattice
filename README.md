# Lattice

Offline document assistant (Tauri desktop app + Python engine) that answers from a company's
knowledge bases, in Manuals mode (small offline models) and Chat mode (large models such as
Qwen 3.5 27B on an office server).

| Folder | What it is |
|---|---|
| [`real-knowledge-bases/`](real-knowledge-bases/) | **Real knowledge bases**, import-ready zips in the production layout, with test questions |
| [`kb-format/`](kb-format/) | The knowledge-base format, the converter `tools/convert_kb.py`, and the same knowledge bases unzipped |
| [`mock-knowledge-bases/`](mock-knowledge-bases/) | Test-only bundles that mimic other tools' layouts |
| [`lattice-source/`](lattice-source/) | The app: `app/backend` (engine), `app/desktop` (UI), build scripts |
| [`FIXES-TODO.md`](FIXES-TODO.md) | Issues found in testing and what was fixed |
| `README.txt` | Notes from the 0.1.0 build (20 Oct 2026) |

Latest work is on the `kb-sem-ai-structure` branch.
