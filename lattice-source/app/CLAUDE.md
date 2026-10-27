# Working on Lattice

Lattice is a desktop document assistant: a Tauri app (`desktop/`) over a Python engine (`backend/oasis_engine`).
Chat mode runs on a model server (vLLM, Qwen 3.5/3.6 27B at the office); Manuals mode on a small local model
(Qwen 3.5 4B) or the server. Knowledge bases come in the office kb_converter_tool format (page_index.json, one
folder per document with kb.json, modules/, interaction/decision-tree.json).

## Every change is recorded (more than one person, and their Claude sessions, work on this)

1. **CHANGELOG.md**: add a line for every change a user or tester would notice, under the version being
   prepared at the top (`## 0.2.1 - <date>`). Say what was wrong and what it does now, in plain words.
   Start that section if the top one is a version already built.
2. **Version**: `VERSION` holds it; set a new one with `powershell -ExecutionPolicy Bypass -File set-version.ps1 0.2.1`
   (last number for fixes, middle one for features). Never edit the version in the other files by hand.
3. **Build**: `powershell -ExecutionPolicy Bypass -File build-release.ps1` runs the engine tests and UI tests,
   packages the engine and builds the installer. It refuses a version already built and one with no
   CHANGELOG section, keeps the installer as `Lattice-share\builds\Lattice-<version>-setup.exe` and adds the
   version's changes to `Lattice-share\builds\BUILDS.md`.
4. **Tests**: add a test for each fix (`backend/tests`, run `.venv\Scripts\python.exe -m pytest -q` in backend/).
   Tests use scripted models; check answers with a real model too (any OpenAI-compatible server) before a build.
5. **Commits**: one per change, saying what was wrong and what was seen (model, question), as the history does.

## Ground rules

- Fixes must work for any model: no tuning prompts for one model. Check model output in code where it matters.
- Never commit API keys, `.env` files, model files or a company's documents.
