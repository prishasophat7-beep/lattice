# Real knowledge bases

**Status: REAL. Production layout, import-ready.** These are complete knowledge bases in the
format Lattice uses in production, not test mocks. Import any of them as they are:
Lattice → **Manage → Import bundle...** → pick the zip. Needs the 26b installer or newer.

Each zip is one knowledge base, built from the KB Manager sample exports with
[`kb-format/tools/convert_kb.py`](../kb-format/tools/convert_kb.py):

```
<knowledge base>/
├── page_index.json       master index: every document, its folder, codes and related documents
├── README.md
└── <document>/           one folder per file
    ├── images/  interaction/  modules/  references/
    ├── AGENTS.md  index.md  kb.json  needs-review.md  README.md  validate_kb.py
```

| Zip | Documents |
|---|---|
| `skylink-support.zip` | 3: R7 router troubleshooting guide, Q3 2026 support review, limited warranty |
| `nimbus-x2.zip` | 3: installation and user guide, hardware design spec, firmware release notes |
| `helix-large.zip` | 9: HX-500 / HX-900 manuals, spare parts, spindle log, supplier agreement, reports, policies |
| `helix-precision.zip` | 6: a smaller Helix set |
| `taskflow.zip` | 7: user manual with screenshots and related documents |
| `inspectai-studio.zip` | 1: user guide, with a hand-written interview (decision tree) |
| `acme-people-it.zip` | 3: employee handbook, IT setup guide, security training |
| `sample-kb.zip` | 3: budget, meeting notes, report |

[`TEST-QUESTIONS.txt`](TEST-QUESTIONS.txt) lists questions for each, with the expected answers.

The same knowledge bases, unzipped, are in [`kb-format/knowledge-bases/`](../kb-format/knowledge-bases/).
Test-only bundles (other tools' layouts) are in [`mock-knowledge-bases/`](../mock-knowledge-bases/).
