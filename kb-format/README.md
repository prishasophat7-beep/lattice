# kb-format: sem-ai-kb knowledge bases from Lattice exports

The parser in KB Manager returns one bundle per file (Markdown, a flat list of headings with pages
and summaries, pictures). **`tools/convert_kb.py`** post-processes that export on the KB web
platform into one interactive knowledge base per document under a master index (below), which
Lattice imports directly (**Manage → Import bundle...**, the folder zipped).
`tools/build-kb.mjs` is the earlier one-folder converter ([STRUCTURE.md](STRUCTURE.md),
[COMPARISON.md](COMPARISON.md)); Lattice still imports its output.

## convert_kb.py (use this one)

```bash
python3 tools/convert_kb.py <kb-manager-export.zip> <out-folder> [--name "KB name"] [--image-names page|figure]
```

Python 3.9+, standard library only.

```
<out-folder>/
├── README.md                 master index: documents with module/section/image counts, tree status
├── page_index.json           master page index: the parser's, without download links; each document
│                             names its folder, codes and related documents; each heading its section file
└── <document>/               one per document, all with the same layout:
    ├── AGENTS.md  README.md  index.md  kb.json  needs-review.md  validate_kb.py
    ├── interaction/decision-tree.json        draft interview ("_draft": true)
    ├── references/decision-tree-guide.md     how to refine it
    ├── modules/<module>/NN-<slug>.md          section files (front matter: id, module, title, pages)
    └── images/                                pictures (page_NNN_imgK.png, or figure_NNN.png)
```

Every top-level chapter is a module. Rebuilding keeps each document's `AGENTS.md`, `README.md`,
decision tree, guide and the hand-written part of `needs-review.md`. Run `python validate_kb.py`
in a document folder after editing it.

In Lattice, each document folder is one document. A question that matches sections in several
documents is asked "Which document is your question about?", then that document's own questions
(which part, which method). Chat mode gets each document's description, codes and related
documents to pick the right one, then its outline, then the sections.

## build-kb.mjs (earlier, one folder)

```
kb-format/
├── STRUCTURE.md                 the layout specification
├── COMPARISON.md                parser bundle vs. this layout
├── tools/build-kb.mjs           post-processor: KB Manager export .zip -> knowledge-base folder
├── tools/smoke-test.mjs         optional: drives mcp-server/ against a knowledge base
├── mcp-server/server.mjs        optional: serves a knowledge base to other agent tools (not used by Lattice)
└── knowledge-bases/             the 8 sample knowledge bases, built from sample-knowledge-bases/
    ├── inspectai-studio/        hand-written interview, AGENTS.md and needs-review.md
    └── ...                      generated interview (document -> module -> way of doing a step)
```

Only Node 18+ is needed, no packages.

## A knowledge base

```
AGENTS.md                      how the assistant behaves, with an Interview tone section (hand-maintained)
README.md                      how to use it (hand-maintained)
index.md                       module list (generated)
needs-review.md                known gaps (hand-maintained; an auto-detected block is rewritten by builds)
interaction/decision-tree.json clarifying interview (hand-maintained after the first build)
modules/<module>/README.md     module overview (generated)
modules/<module>/NN-<slug>.md  one section, with YAML front matter (generated)
kb.json                        modules and sections with plain text, pages, images, summaries (generated)
images/page_NNN_imgK.png       pictures; each one's description sits next to it in its section (generated)
pages/                         page screenshots (not produced yet)
```

## Build one

```bash
node tools/build-kb.mjs <export.zip> knowledge-bases/<name> [--name "Product Name"]
```

Rebuilding overwrites `index.md`, `kb.json`, `modules/`, `images/` and the auto-detected block of
`needs-review.md`. `AGENTS.md`, `README.md`, the rest of `needs-review.md` and
`interaction/decision-tree.json` are written only by the first build; edit them by hand afterwards.

## Import it into Lattice

Zip the folder's contents (or the folder itself) and import the zip in Lattice. Lattice:

- loads each source file as one document, its modules and sections as headings, with the pages,
  summaries and pictures from `kb.json`;
- asks back from `interaction/decision-tree.json` on a first question, only when the tree's
  question separates what the search found (see `backend/oasis_engine/interview.py`);
- gives the chat model `AGENTS.md` and `needs-review.md`, and the Manuals-mode answer step the
  known gaps.

## Extensions to the spec

- `kb.json` sections carry `summary` (from the parser) and modules `source` (the file they came
  from) and, for a whole-document module, `summary`.
- `pages` is `null` when the parser gives no page numbers; citations then name the module and section.
- `decision-tree.json` may set `resolve_keys` (default `["task", "framework"]`); InspectAI uses
  `["task", "variant"]`. An option with `"next": "open"` is "something else".
