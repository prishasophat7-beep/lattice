# Mock knowledge bases

**Status: MOCK. For testing the importer only.** These are not real knowledge bases. They copy
how other tools write a multi-document knowledge base, to check that Lattice imports them
(26c installer or newer). For real, import-ready knowledge bases see
[`real-knowledge-bases/`](../real-knowledge-bases/).

| Zip | What it mimics |
|---|---|
| `multi-doc-test-internal-layout.zip` | The internal "multi-doc-test" bundle: the parser's own `page_index.json` at the top of the zip (`file_id`, `bundle_zip`, no `folder`), folders named like `b-acd-e1sem-ai-frontier70-usermanual-1`, `kb.json` without the `generator` / `document` fields. Before 26c: "The export has no document with text". |
| `multi-doc-test-mixed.zip` | A "multi doc test" top folder; one document folder from `convert_kb.py`, one with an older one-manual `kb.json` whose sections have no `file`; lower-case `agents.md`. |
