# Current Lattice bundle vs. sem-ai-kb structure

Compared on the InspectAI Studio sample (`sample-knowledge-bases/inspectai_studio_kb.zip`), and
checked by converting all 8 sample bundles with `tools/build-kb.mjs`. The new layout is described
in [STRUCTURE.md](STRUCTURE.md); the converted InspectAI knowledge base is in
[knowledge-bases/inspectai-studio](knowledge-bases/inspectai-studio).

## At a glance

| | Current Lattice bundle | sem-ai-kb |
|---|---|---|
| Container | One `.zip`: `page_index.json` + one nested `.bundle.zip` per document | A plain folder (readable without tools, diffable in git) |
| Content | One Markdown file per **document** (InspectAI: one 4.7 KB file) | One Markdown file per **section** with YAML front matter (InspectAI: 25 files in 6 modules) |
| Hierarchy | `page_index.json` lists sections flat (InspectAI: all 34 nodes have `level: 1`); the real hierarchy is only in the Markdown headings | Explicit: module folder → numbered section files; `index.md` and each module `README.md` list them |
| Search index | Built by Lattice at import (`search.py`, optional embeddings) | `kb.json`: every section's plain text, title, pages and images, ready for BM25 |
| Clarifying questions | Worked out at run time by heuristics (`vague.py`, `focus.py`) or left to the model (`ask_user`) | Written down in `interaction/decision-tree.json`: questions, options, variables and results |
| Agent behaviour | Lattice's built-in system prompt (`chat.py` `CHAT_INSTRUCTIONS`) | `AGENTS.md` shipped with each knowledge base |
| Known gaps | Not recorded | `needs-review.md`: hand-written notes plus a block of auto-detected gaps rewritten by each build |
| Pictures | `images/figure_NNN.png` in the nested zip; descriptions in a separate "Image Descriptions" chapter at the end | `images/page_NNN_imgK.png`; description written next to the picture in its section |
| Page numbers | `start_page` / `end_page` per node (null in InspectAI, TaskFlow, Helix) | `pages: [start, end]` in front matter and `kb.json` (null when the export has none) |
| Page scans | Not included (some exports include the original PDF) | `pages/page_NNN.jpg` expected; Lattice exports have none, so empty |
| Section summaries | One-line `summary` per node | Not in the spec; the converter keeps them as an extra `summary` field in `kb.json` |
| Serving | Only through the Lattice app | Lattice imports it (source format `semkb`); any other agent can read the files with `AGENTS.md` as instructions |

## What the new structure fixes from the 22 Oct test findings

| Finding (see FIXES-TODO.md) | In sem-ai-kb |
|---|---|
| #1 picture descriptions cut to their first sentence | The full description sits in the section next to the picture (`Image description: ...`). The TaskFlow dashboard section now contains "Overdue 3". |
| #4 second half of a question searched without its subject | Partly. Sections are smaller (Skylink "4 Common Issues" becomes 4.1, 4.2 and 4.3), so a search for "E-217" ranks *4.3 Firmware Update Failure* first. The search itself still works one query at a time. |
| #8 "How do I annotate text?" does not ask Method 1 / Method 2 | The decision tree asks it explicitly: Build a model → Read text → Manual or Assisted. |
| "Which manual?" (HX-500 vs HX-900) | In a knowledge base with several manuals, chapter modules carry the manual's name (`HX-500 manual: Maintenance`), so search results and citations say which manual. |
| Vague first questions ("How do I train?") | Answered by the interview (`kb_start` / `kb_answer`) rather than by heuristics, and the same on every model. |
| Values carried over between project types (epochs 40 vs 60) | `AGENTS.md` and `needs-review.md` state which values differ and which are undocumented (batch size, OCR export). |

## What gets lost or needs care

- **Page numbers** are missing from most sample exports, so the guide's citation format
  *(Module, p. N)* cannot be used for them. The converter writes `pages: null`, labels them
  "pages not recorded", and the agent cites *(Module › Section)* instead. Re-exporting with page
  numbers fixes this.
- **Page scans** (`pages/`) are not in any Lattice export.
- **The decision tree has to be written by hand.** The converter writes a one-question starter
  tree (pick a module); a real interview (goal → task → variant) needs someone who knows the
  product. The InspectAI tree has 4 questions and 12 results.
- **Bold UI labels**: the guide asks for `**Train**`-style bold labels from the OCR. The sample
  exports have no such markup (InspectAI has none), so the converter keeps the text as it is.
- **Images named `page_000`**: when the export has no page numbers, pictures are named
  `page_000_imgK.png` and listed in `needs-review.md`.
- **Lattice cannot read this layout yet.** Importing it needs a new loader in
  `backend/oasis_engine/loaders.py` (read `kb.json` and the section files) and probably using
  `decision-tree.json` in place of the `vague.py` heuristics.

## How the converter maps a Lattice export

1. Reads `page_index.json` and each document's Markdown from its nested `.bundle.zip`
   (or the loose `.md` in older exports), plus its pictures.
2. Takes off a heading that is only the file name ("# InspectAI_Studio_4_User_Guide.pdf"), and a
   single title heading above everything else ("# TaskFlow Pro 4.2 - User Manual").
3. Decides the modules:
   - **chapters become modules** when most chapters have at least two sub-sections (a user
     manual: "2 Image Classification" with 2.1-2.6). Each sub-section is one section file; deeper
     headings ("2.2.1 Option A") stay inside it.
   - otherwise **the document is one module** (a short guide, a report, a policy), and each
     chapter, or each sub-section of a chapter that has them, is one section file.
   - an unnumbered first chapter ahead of numbered ones becomes **Overview**.
4. Matches each section to its `page_index.json` record **in document order** for pages and
   summary. Matching by title alone mixed up "2.2 Importing Images" and "3.2 Importing Images".
5. Writes the section files, module READMEs, `index.md`, `kb.json`, pictures and
   the auto-detected block of `needs-review.md`; creates `AGENTS.md` (with an Interview tone
   section), `README.md` and `decision-tree.json` only if they don't exist yet.

## All 8 sample bundles, converted

| Bundle | Modules | Sections | With page numbers | Pictures |
|---|---|---|---|---|
| InspectAI Studio | 6 | 25 | 0 | 0 |
| Skylink support | 3 | 21 | 12 | 5 |
| Nimbus X2 product | 3 | 30 | 20 | 8 |
| Acme People & IT | 3 | 23 | 8 | 6 |
| TaskFlow (images) | 9 | 44 | 0 | 4 |
| Sample knowledge base | 3 | 16 | 5 | 3 |
| Helix Precision | 16 | 78 | 0 | 0 |
| Helix Large | 59 | 363 | 0 | 0 |

Only InspectAI is committed with a hand-written interview; the others convert cleanly but still
have the one-question starter tree.

## Checks run

- Lattice's engine tests: the original 386 plus 12 for this format (`tests/test_semkb.py`): import of a
  zipped folder, loading, pictures with their whole description, AGENTS.md and needs-review.md for
  the model, the decision tree's questions back, and the Manuals and Chat flows through the API.
- All 8 sample knowledge bases import into Lattice; asking back behaves as with the parser's bundles,
  or better ("How do I annotate text?" now asks Method 1 or Method 2).
