# Installing Lattice on a line machine

Lattice runs entirely on the machine: no internet connection is needed or used.

## What goes on the USB drive

| File | Size | What it is |
|---|---|---|
| `Lattice_<version>_x64-setup.exe` | ~40 MB | The app: interface, engine and the model server (llama.cpp) |
| `Qwen3.5-4B-Q4_K_M.gguf` | 2.7 GB | The language model (Qwen3.5 4B, Apache-2.0). Shipped separately because the installer format cannot hold files over 2 GB |
| `kb-<version>.zip` | varies | The knowledge-base bundle from the web portal team |
| `embeddinggemma-300M-Q8_0.gguf` | 334 MB | Optional: the embedding model for search by meaning (see below) |

## Machine requirements

- Windows 10 or 11, 64-bit, with the WebView2 runtime (built into Windows 11)
- **8 GB RAM or more**, with Lattice the main program running. The model uses about 3.3 GB while loaded.

### Choosing a model per machine

| Machine | Model file | Size | Notes |
|---|---|---|---|
| 8 GB RAM, CPU only (default) | `Qwen3.5-4B-Q4_K_M.gguf` | 2.7 GB | Best quality that fits |
| Same, if answers are too slow | `granite-4.2-3b-Q4_K_M.gguf` (IBM) | 2.2 GB | About 30% faster; to be compared on real manuals |
| Less than 8 GB, or busy machines | `Qwen3.5-2B-Q4_K_M.gguf` | 1.3 GB | Noticeably weaker |

All three are Apache-2.0. Swap models from **Manage, then Replace model file**.
- About 4 GB of free disk space, plus the knowledge base
- No GPU needed. Answers take roughly 10-60 seconds on a typical office CPU.

## Steps

1. Run `Lattice_<version>_x64-setup.exe`. It installs for all users of the machine (administrator rights needed).
2. Start **Lattice** from the Start menu. The first start takes up to a minute while Windows scans the new files.
3. The app shows **No language model installed**: choose **Import a model file** and pick the `.gguf` file
   from the USB drive. Copying takes a minute or two; the model then loads (up to a minute).
4. Open **Manage** and choose **Choose bundle file...**, then pick the `kb-<version>.zip` from the USB drive.
   The bundle is checked file by file before it becomes active.
5. Ask a test question whose answer you know, and check that the answer shows its source.
6. Optional: **Manage → Search by meaning → Import embedding model...** and pick
   `embeddinggemma-300M-Q8_0.gguf`. The documents are indexed in the background (seconds for a few
   documents, longer for hundreds); keyword search works meanwhile. The status shows *Ready* when done.

## Search by meaning (optional, works offline)

By default a question is matched to the manuals by its words. That misses questions worded differently
from the manual ("money for my desk at home" for "home office stipend") and questions in another language.
With an embedding model installed, Lattice also compares questions and sections by meaning, on the machine
itself, with no network. Exact codes and part numbers (E-217, DT-450) are still found by their words first.

| Model | Size | Memory | Languages | License |
|---|---|---|---|---|
| `embeddinggemma-300M-Q8_0.gguf` (recommended) | 334 MB | about 600 MB | 100+ | Gemma Terms of Use: commercial use allowed under Google's use policy; check with legal before rollout |
| `nomic-embed-text-v1.5.Q8_0.gguf` | 146 MB | about 220 MB | English only | Apache-2.0 |

On the three sample bundles (89 test questions), the right section was among the top 3 for 20 of 22
reworded questions (words only: 10) and all 6 questions in Korean, Spanish or Hindi (words only: 3), with
no loss on direct questions. Each question takes about 0.35 s longer on a laptop CPU; the answer itself
still takes most of the time. Budget the extra memory: with the 4B model, about 4 GB in total.

A language model file is refused as the embedding model and the other way round. **Turn off** removes the
embedding model. On office machines, an embedding server set in Manage → Assistant is used instead.

## Updating

- **New knowledge base:** Manage, then Choose bundle file. The previous version stays installed; switch back
  under *Installed versions* if needed.
- **New model:** Manage, then Replace model file. The old model file is removed.
- **New app version:** run the new installer; knowledge base and model are kept.

## Office machines: Chat mode and a network model

The same installer serves offices. In **Manage → Assistant**:

1. *Where this machine is used* → **Office**. This adds Chat mode next to Manuals mode. Chat mode looks things
   up in the manuals and cites them, and can also answer general questions; those answers are marked
   "General answer, not from the company manuals". Choose the starting mode and whether to keep it to
   work topics.
2. *Language model* → **Model server on the network**: the endpoint URL (e.g. `http://llm.company.lan:8000/v1`),
   the model name, and the API key if the server needs one. **Test connection** lists the server's models.
   Tick *Reasoning model* for models that think before answering. **Save settings.** While a network model
   is used, the built-in model server is not started (it would only use memory); after switching to a
   network model, restart Lattice once so the built-in one stops.

   *Thinking models (Qwen 3.x, e.g. Qwen 3.6 27B):* leave *Reasoning model* unticked for fast answers.
   Lattice then asks the server not to think first (`chat_template_kwargs: {"enable_thinking": false}`,
   which vLLM, SGLang and llama.cpp accept; a server that rejects the field is asked again without it).
   Tick it only if the model should think before every answer: slower, with larger answer limits.

Requirements for the model server (vLLM, or any OpenAI-compatible server):

- **Tool calling** switched on, for Chat mode: vLLM `--enable-auto-tool-choice --tool-call-parser <parser>`,
  with the parser for the model family (e.g. `hermes` for Qwen, `llama3_json` for Llama 3.x).
- **JSON-schema output** for Manuals mode (vLLM supports it by default); otherwise set *Structured replies*
  to *Prompt only*.
- A model that is good at tool use; the larger the better for general answers.

The settings are stored in `config.json` (see below), including the API key, readable by that Windows user.

## Where things are stored

Per Windows user, in `%LOCALAPPDATA%\com.oasis.lineassistant\` (the app's earlier name, kept so that
data from earlier versions carries over):

| Path | Contents |
|---|---|
| `versions\` | installed knowledge-base versions |
| `active.json` | which version is active |
| `models\` | the language model file |
| `embed-models\`, `embeddings\` | the embedding model file, and the saved index of the documents (rebuilt only for changed sections) |
| `config.json` | Manage → Assistant settings: profile, Chat mode, model connection (and its API key) |
| `history.jsonl`, `feedback.jsonl` | questions asked and answer votes, on this machine only |
| `backend.log`, `llm.log`, `embed.log` | engine, model-server and embedding-server logs, for troubleshooting |

Because data is stored per Windows user, line machines should use one shared Windows account for Lattice,
or the model and knowledge base must be imported once per account.

## Security notes

- The engine and the model servers listen only on `127.0.0.1` (not reachable from the network), each with a
  random key created at every start, so other programs on the machine cannot use them.
- If the app crashes, Windows stops the engine and model server automatically (Job Object).
- Knowledge-base bundles are checked against their SHA-256 list; see `kb-bundle-spec.md`.

## Licenses of bundled components

- llama.cpp: MIT (license files in the install folder under `runtime\llama\`)
- Qwen3.5-4B (default model): Apache-2.0
- EmbeddingGemma-300M (optional embedding model): Gemma Terms of Use, not an open-source license; to be
  cleared with legal. nomic-embed-text-v1.5 (Apache-2.0) is the alternative
- Python engine dependencies (FastAPI, PydanticAI, Uvicorn, rank-bm25, ...): open-source; a full license
  audit of the packaged engine is still to be done before release
