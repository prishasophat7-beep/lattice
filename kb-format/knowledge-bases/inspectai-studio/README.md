# InspectAI Studio

InspectAI Studio (test export)

1 documents. Each folder is a self-contained interactive knowledge base with the same layout;
`page_index.json` is the master index of all of them (the parser's outline of every document, each
heading pointing at the section file that holds it).

| Document | Folder | Modules | Sections | Images | Decision tree | Status |
| --- | --- | --- | --- | --- | --- | --- |
| [InspectAI Studio 4 User Guide](inspectai-studio-4-user-guide/index.md) | `inspectai-studio-4-user-guide/` | 6 | 25 | 0 | 4 nodes → 12 results | Reviewed |
| **Total** | | 6 | 25 | 0 | | |

## Finding things

1. Pick the document: the table above, or `page_index.json` (each document's summary, codes and
   related documents).
2. In its folder, `AGENTS.md` says how to answer, `index.md` lists the modules, `kb.json` has the
   sections, and `interaction/decision-tree.json` the clarifying questions.

## Per-document folder

| File / folder | Purpose |
| --- | --- |
| `AGENTS.md` | Agent behaviour: interview first, then step-by-step walkthrough |
| `README.md` | Setup and use for that document |
| `index.md` | Modules with page ranges and one-line summaries |
| `kb.json` | Modules, sections (text, pictures, pages, summaries), picture descriptions |
| `needs-review.md` | Auto-detected gaps (figure-only sections, TBD values, missing descriptions) plus hand-written notes |
| `validate_kb.py` | Validation: structure, decision tree, interview simulation |
| `interaction/decision-tree.json` | Clarifying-question tree; options lead to results |
| `references/decision-tree-guide.md` | How to refine the auto-generated draft tree |
| `modules/<module>/NN-<slug>.md` | Section files with front matter (id, module, title, pages) |
| `images/` | Pictures referenced by the sections |

## Regenerating

```
python convert_kb.py inspectai_studio_kb.zip <this-folder>
```

Regenerating rewrites `modules/`, `images/`, `kb.json`, `index.md`, `page_index.json` and this README;
it keeps `AGENTS.md`, each document's `README.md`, the decision trees, the decision-tree guide and
the hand-written part of each `needs-review.md`.
