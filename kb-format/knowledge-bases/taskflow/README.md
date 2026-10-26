# Sample Knowledge Base

A combined KB with HR policies and file type validation samples

7 documents. Each folder is a self-contained interactive knowledge base with the same layout;
`page_index.json` is the master index of all of them (the parser's outline of every document, each
heading pointing at the section file that holds it).

| Document | Folder | Modules | Sections | Images | Decision tree | Status |
| --- | --- | --- | --- | --- | --- | --- |
| [User manual](user-manual/index.md) | `user-manual/` | 3 | 9 | 3 | 1 node → 3 results | Draft |
| [Leave policy](leave-policy/index.md) | `leave-policy/` | 11 | 11 | 1 | 3 nodes → 11 results | Draft |
| [Performance metrics](performance-metrics/index.md) | `performance-metrics/` | 1 | 1 | 0 | 1 node → 1 result | Draft |
| [Quarterly report](quarterly-report/index.md) | `quarterly-report/` | 1 | 1 | 0 | 1 node → 1 result | Draft |
| [Manifest](manifest/index.md) | `manifest/` | 1 | 1 | 0 | 1 node → 1 result | Draft |
| [Config](config/index.md) | `config/` | 12 | 12 | 0 | 3 nodes → 12 results | Draft |
| [Requirements](requirements/index.md) | `requirements/` | 5 | 5 | 0 | 1 node → 5 results | Draft |
| **Total** | | 34 | 40 | 4 | | |

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
python convert_kb.py sample_kb_taskflow_images.zip <this-folder>
```

Regenerating rewrites `modules/`, `images/`, `kb.json`, `index.md`, `page_index.json` and this README;
it keeps `AGENTS.md`, each document's `README.md`, the decision trees, the decision-tree guide and
the hand-written part of each `needs-review.md`.
