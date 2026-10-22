# inspectai-studio

Interactive knowledge base for **InspectAI Studio 4**, built from the user manual
(`inspectai_studio_kb.zip`) with `../../tools/build-kb.mjs`.
Works with any agent harness, with or without MCP.

## Option A: files only (any harness)

Point the harness at this folder and make `AGENTS.md` its instructions.
(For Claude Code: `CLAUDE.md` contains `@AGENTS.md`.)

## Option B: MCP server (recommended)

Zero dependencies, needs Node 18+. Use the absolute path to `mcp-server/server.mjs`.

Claude Code (`.mcp.json`):

```json
{ "mcpServers": { "kb": { "command": "node", "args": ["/abs/path/inspectai-studio/mcp-server/server.mjs"] } } }
```

opencode (`opencode.json`):

```json
{ "mcp": { "kb": { "type": "local", "command": ["node", "/abs/path/inspectai-studio/mcp-server/server.mjs"] } } }
```

Hermes (`config.yaml`):

```yaml
mcp_servers:
  kb:
    command: node
    args: ["/abs/path/inspectai-studio/mcp-server/server.mjs"]
```

## Layout

```
AGENTS.md                      agent instructions (hand-maintained)
index.md                       module list (generated)
needs-review.md                known gaps (hand-maintained)
needs-review.generated.md      gaps found by the last build (generated)
interaction/decision-tree.json clarifying interview (hand-maintained)
modules/<module>/README.md     module overview (generated)
modules/<module>/NN-<slug>.md  one section of the manual (generated)
kb.json                        machine-readable index for search (generated)
images/                        screenshots, page_NNN_imgK.png (generated)
pages/                         original page scans, page_NNN.jpg (not in Lattice exports)
mcp-server/server.mjs          MCP server: search + interview
```
