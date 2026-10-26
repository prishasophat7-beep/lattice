# Acme People & IT Knowledge Base

Employee handbook, IT onboarding guide and security awareness training for Acme Corp employees.

3 documents. Each folder is a self-contained interactive knowledge base with the same layout;
`page_index.json` is the master index of all of them (the parser's outline of every document, each
heading pointing at the section file that holds it).

| Document | Folder | Modules | Sections | Images | Decision tree | Status |
| --- | --- | --- | --- | --- | --- | --- |
| [Employee handbook 2026](employee-handbook-2026/index.md) | `employee-handbook-2026/` | 6 | 8 | 2 | 1 node → 6 results | Draft |
| [It setup guide](it-setup-guide/index.md) | `it-setup-guide/` | 6 | 6 | 2 | 1 node → 6 results | Draft |
| [Security awareness training module2](security-awareness-training-module2/index.md) | `security-awareness-training-module2/` | 6 | 6 | 2 | 1 node → 6 results | Draft |
| **Total** | | 18 | 20 | 6 | | |

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
python convert_kb.py sample_kb_acme_people_it.zip <this-folder>
```

Regenerating rewrites `modules/`, `images/`, `kb.json`, `index.md`, `page_index.json` and this README;
it keeps `AGENTS.md`, each document's `README.md`, the decision trees, the decision-tree guide and
the hand-written part of each `needs-review.md`.
