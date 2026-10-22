# kb-format: sem-ai-kb knowledge bases from Lattice exports

Tools for building knowledge bases in the **sem-ai-kb** layout ([STRUCTURE.md](STRUCTURE.md)) from
Lattice knowledge-base exports, and an MCP server that serves them to any agent harness.
How it differs from the current Lattice bundle: [COMPARISON.md](COMPARISON.md).

```
kb-format/
├── STRUCTURE.md                 the layout specification
├── COMPARISON.md                current bundle vs. this layout
├── tools/build-kb.mjs           converter: Lattice export .zip -> KB folder
├── tools/smoke-test.mjs         drives a KB's MCP server over JSON-RPC
├── mcp-server/server.mjs        the MCP server (copied into each KB by the build)
└── knowledge-bases/
    └── inspectai-studio/        InspectAI Studio 4, with a hand-written interview
```

Everything needs only Node 18+, no packages.

## Build a knowledge base

```bash
node tools/build-kb.mjs ../sample-knowledge-bases/inspectai_studio_kb.zip knowledge-bases/inspectai-studio --name "InspectAI Studio 4"
```

Rebuilding overwrites `index.md`, `kb.json`, `modules/`, `images/` and
`needs-review.generated.md`. It never touches `AGENTS.md`, `README.md`, `CLAUDE.md`,
`needs-review.md` or `interaction/decision-tree.json`; the first build writes starter versions
of these, which you then edit by hand.

## Test it

```bash
node tools/smoke-test.mjs knowledge-bases/inspectai-studio
```

## Use it from an agent

See the knowledge base's own [README](knowledge-bases/inspectai-studio/README.md): either point the
harness at the folder with `AGENTS.md` as its instructions, or register
`mcp-server/server.mjs` as an MCP server.

## Extensions to the spec

- `kb.json` sections may carry `summary` (from the Lattice export), and modules `source` (the
  file they came from) and `summary`.
- `pages` is `null` when the export has no page numbers; citations then name the module and section.
- `decision-tree.json` may set `resolve_keys` (default `["task", "framework"]`): the variables
  joined with `:` to look up `resolve`. InspectAI uses `["task", "variant"]`.
- An option with `"next": "open"` (or no `next`) is the "something else" choice: `kb_answer`
  returns `status: "open"` and the agent asks the user to describe what they need.
- `kb_get_section` also returns `cite_as`.
