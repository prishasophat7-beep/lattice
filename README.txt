Lattice - build 0.1.0 (20 Oct 2026)
===================================

Contents
  Lattice_0.1.0_x64-setup.exe   the Windows installer (built 20 Oct 2026, 17:41)
  lattice-source\               source code (see "Building from source" below)
  sample-knowledge-bases\       test knowledge bases (Helix Precision, Helix Large, InspectAI
                                Studio, and the sample bundles)
  final-test-report.md          test results up to the 19 Oct build

Model files (Qwen3.5-0.8B, Qwen3.5-4B, embeddinggemma-300M) are in the separate "models" folder next to
this one. They are needed only on machines that run the model offline; machines that use a model
server on the network do not need them.

Tests of this build: 386 engine tests and 41 UI tests pass (they run as part of the build).

Changes since the 19 Oct build
  Working through a document as a conversation
  - The answer bar shows where the conversation is ("Working in: manual > chapter > section"),
    with Back / Next to step through the manual in its order, and x to leave it.
  - Follow-ups ("How do I test it?") are answered from the part of the manual the conversation
    is in, not from a section with the same title elsewhere.
  - A step the manual describes in several ways (Type 1 / Type 2, Method 1 / Method 2) is asked
    back; naming one ("with method 2") answers from it.
  - "Same step in:" buttons under an answer ask about the same step in the manual's other parts.
  - "Walk me through <task>": one step at a time ("Step 1 of 5 ..."), Next for each next step,
    and it says when the task is done.
  - An answer whose steps come from different parts of the manual says so.
  - A question back ("which one do you mean?") has a "Something else..." button to type what
    you meant.
  - When nothing is found, the closest sections are offered as buttons.

  Reliability
  - A failed or stopped Regenerate / Edit keeps the earlier answer; Try again after an error
    replaces it instead of adding a turn.
  - If the assistant's engine stops, Lattice starts it again and says so meanwhile.
  - Progress is shown one step at a time, the same in Chat and Manuals mode.
  - Questions stay fast as the history grows.
  - Model service problems are said plainly (busy, refused key, model no longer offered).
  - Old knowledge-base versions can be removed in Manage; attached files of conversations
    never asked in are cleaned up after a day.
  - Chat mode on a model server: a picked option or a follow-up in the current part is read
    from exactly that section.

1. Install
   Run Lattice_0.1.0_x64-setup.exe (needs admin rights).
   If Windows says "Windows protected your PC": click More info -> Run anyway
   (the test build is not code-signed).
   Already have an older Lattice? Close it first; the new version installs over it
   and keeps your model, documents and settings.

2. Language model (Manage -> Import model file...)
   - Laptops with 8 GB of memory: Qwen3.5-0.8B-Q4_K_M.gguf (about 0.5 GB).
     Answers in 1 to 3 minutes; answers from the documents only.
   - 16 GB or more: Qwen3.5-4B-Q4_K_M.gguf (about 2.7 GB). Clearly better answers
     (comparisons, follow-ups, other languages), but on an 8 GB laptop it took
     4 to 11 minutes per answer.
   - Office machines: connect the network model server in Manage instead.

3. Start Lattice from the Start menu, then in Manage:
   - Import model file...  -> choose the .gguf file
   - Import bundle...      -> choose a knowledge bundle (.zip)

4. Optional: Search by meaning (finds reworded and other-language questions)
   Manage -> Search by meaning -> Import embedding model... ->
   embeddinggemma-300M-Q8_0.gguf (about 330 MB). Not part of the installer.
   Note: EmbeddingGemma is under Google's Gemma licence; check it is acceptable
   for your use.

5. Optional: Manage -> Assistant -> Office profile turns on Chat mode
   (searches and cites the documents; general knowledge is marked as such).

Everything runs on your PC; nothing is sent anywhere unless you connect a model
server in Manage. Use Helpful / Wrong under answers; feedback can be exported from Manage.

Building from source
  Needs Windows, Python 3.11+, Node.js 20+, Rust (stable) and PowerShell.
  1. Engine: cd lattice-source\backend
       python -m venv .venv
       .venv\Scripts\pip install -e .[dev] pyinstaller
  2. UI: cd lattice-source\desktop ; npm install
  3. llama.cpp is not included: download the Windows x64 CPU release of llama.cpp
     from github.com/ggml-org/llama.cpp/releases (tested with build b11100) and
     unzip it into lattice-source\runtime\llama\ (llama-server.exe and its DLLs),
     together with its LICENSE file saved as LICENSE-llama.cpp.
  4. From lattice-source: powershell -ExecutionPolicy Bypass -File .\build-release.ps1
     (runs the tests, packages the engine, builds the installer).
