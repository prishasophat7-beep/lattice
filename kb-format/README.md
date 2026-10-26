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

## Samples

`knowledge-bases/` holds the 8 sample knowledge bases converted with `convert_kb.py` (the layout
above). InspectAI Studio keeps its hand-written interview and AGENTS.md. Zip a knowledge base's
folder (for example `knowledge-bases/skylink-support`) and import the zip in Lattice.

Lattice also imports a multi-document knowledge base made by other tools in the same layout:
`page_index.json` may be the parser's own index (without `folder` entries), a document folder's
`kb.json` may be the one-manual sem-ai-kb kind (`name`, `modules`, `sections`), sections may leave
out `file` (found from the section files' front matter), and `agents.md` may be lower case.

## build-kb.mjs (earlier, one folder)

The earlier converter writes one folder for the whole knowledge base ([STRUCTURE.md](STRUCTURE.md),
[COMPARISON.md](COMPARISON.md)). Lattice still imports that layout. Its samples are in the git
history (commit 3ee6323 and earlier).
