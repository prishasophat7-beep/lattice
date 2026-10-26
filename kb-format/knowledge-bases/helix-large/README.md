# Helix Large

Helix Large (test export)

9 documents. Each folder is a self-contained interactive knowledge base with the same layout;
`page_index.json` is the master index of all of them (the parser's outline of every document, each
heading pointing at the section file that holds it).

| Document | Folder | Modules | Sections | Images | Decision tree | Status |
| --- | --- | --- | --- | --- | --- | --- |
| [HX-500 manual](hx-500-manual/index.md) | `hx-500-manual/` | 9 | 23 | 0 | 3 nodes → 9 results | Draft |
| [HX-900 service manual](hx-900-service-manual/index.md) | `hx-900-service-manual/` | 41 | 281 | 0 | 7 nodes → 41 results | Draft |
| [HX-900 spare parts](hx-900-spare-parts/index.md) | `hx-900-spare-parts/` | 1 | 1 | 0 | 1 node → 1 result | Draft |
| [HX-900 spindle log](hx-900-spindle-log/index.md) | `hx-900-spindle-log/` | 1 | 1 | 0 | 1 node → 1 result | Draft |
| [It security standard](it-security-standard/index.md) | `it-security-standard/` | 8 | 8 | 0 | 1 node → 8 results | Draft |
| [Plant performance h1 2026](plant-performance-h1-2026/index.md) | `plant-performance-h1-2026/` | 6 | 6 | 0 | 1 node → 6 results | Draft |
| [Plant safety standard](plant-safety-standard/index.md) | `plant-safety-standard/` | 9 | 13 | 0 | 3 nodes → 9 results | Draft |
| [Supplier agreement arvind metals](supplier-agreement-arvind-metals/index.md) | `supplier-agreement-arvind-metals/` | 3 | 14 | 0 | 1 node → 3 results | Draft |
| [Travel expense policy](travel-expense-policy/index.md) | `travel-expense-policy/` | 10 | 10 | 0 | 3 nodes → 10 results | Draft |
| **Total** | | 88 | 357 | 0 | | |

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
python convert_kb.py Helix_Large_knowledge_base.zip <this-folder>
```

Regenerating rewrites `modules/`, `images/`, `kb.json`, `index.md`, `page_index.json` and this README;
it keeps `AGENTS.md`, each document's `README.md`, the decision trees, the decision-tree guide and
the hand-written part of each `needs-review.md`.
