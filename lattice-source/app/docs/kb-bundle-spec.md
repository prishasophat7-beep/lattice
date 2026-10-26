# Knowledge-base bundle format (v1)

The web portal delivers the knowledge base to the offline desktop app as **one `.zip` file**, the *bundle*.
The desktop app verifies it, installs it next to earlier versions, and switches to it only if it loads
completely. This document is the contract between the two teams.

A self-contained script that builds a valid bundle is at the end of this document
([Building a bundle](#building-a-bundle)). The desktop team's own implementation is
`oasis_engine.bundle.build_bundle()` in `app/backend`.

The desktop app also imports the web platform's **own export** as it is (see
[The web platform export](#the-web-platform-export)); the bundle format below is for other producers.

## The web platform export

A `.zip` with `page_index.json` at the root, as in the sample export `sample_kb_acme_people_it.zip`:

```
page_index.json                           catalog: kb_name, generated_at, documents[]
files/<name>.bundle.zip                   <name>.md (the document as Markdown) + images/<figure>.png
```

Each `documents[]` entry names its bundle: `"bundle_zip": "files/<name>.bundle.zip"`. Earlier exports
(e.g. `sample_knowledge_base.zip`) instead had `"files": {"markdown": ..., "bundle": ...,
"structured_content": ...}` with `files/<name>/<name>.md`, `.bundle.zip` and `.structured.json`; they are
still imported (a separate `.md` is preferred, otherwise the one inside the bundle).

What the desktop app does on import:

- **Version name** from `kb_name` and `generated_at`: `sample-knowledge-base-2026.09.25-0821`. Importing
  the same export again switches back to it, also when it was re-exported in the other layout; a
  *different* export with the same name and time is refused.
- **Sections** come from the Markdown headings. Each `documents[].sections[]` entry gives the section with
  the same title its `summary` and page range (`start_page`/`end_page`, shown in citations as
  "Results > Image Quality Metrics (pp. 3-4)"). "Sheet: Summary" in the Markdown matches "Summary" in the
  catalog. `type: "table"` entries add their summary to the section the table is in.
- **`doc_summary`** is the document's description (the model reads it; the Manuals list shows it).
- **Pictures:** `images/*` from each document's `.bundle.zip` are kept per document; a picture referenced as
  `![...](images/<file>)` in a section is shown with answers from that section and in its source view.
- **Titles** are the file names (`filename`). When two documents have the same file name (in different
  folders), both are shown by their `relative_path` instead.
- **A document without text** (no `.md` in its bundle, or an empty one, e.g. a scan the platform could not read) is
  left out; the rest is imported, and the import message and the Manage panel name what was left out.
  A corrupt `.bundle.zip` loses that document (in the earlier layout, with a separate `.md`, only its pictures).
- Picture names may contain spaces (written `%20` or in `<...>` in the Markdown); pictures in sub-folders of
  `images/` are not used.
- Only the index, the Markdown and the images are stored; `*.structured.json` is not used.

## sem-ai-kb knowledge bases

A knowledge-base folder built by `kb-format/tools/build-kb.mjs` (layout: `kb-format/STRUCTURE.md`) can
be imported as it is: zip the folder (`kb.json` at the top of the zip, or inside one folder) and use
**Import bundle...**. Lattice recognises its `kb.json` by its `modules` and `sections` lists (Lattice's
own manifest of that name has `format_version`), keeps only the knowledge base's files (`kb.json`,
`*.md`, `interaction/decision-tree.json`, `images/`, `pages/`), and installs it as source format
`semkb`. The version name is the knowledge base's name plus a hash of its files, so the same folder
imported again is the same version and any change makes a new one.

- Each `source` file named in `kb.json` becomes one document; its modules are its top headings and
  their sections the next level (`backend/oasis_engine/kbfolder.py`).
- Pages, summaries and pictures come from `kb.json` and the section files; a picture's description
  (`Image description:`) is read in full where the picture is.
- `interaction/decision-tree.json` drives the question back on a first question
  (`backend/oasis_engine/interview.py`); `vague.py` still applies when the tree has nothing to ask.
- `AGENTS.md` (without its "How to use the KB" and "Maintenance" sections) and `needs-review.md` are
  given to the chat model; the known gaps also go with every Manuals-mode answer.

## Layout

```
kb-<kb_version>.zip
├── kb.json                 manifest (required)
├── content/                the knowledge base itself (required)
│   └── ...                 PageIndex store  OR  Markdown files, see below
└── originals/              source PDFs: open cited pages, show pages with figures (optional)
    └── <name>.pdf
```

Rules for every path inside the zip:

- forward slashes only, relative, no `..`, no drive letters, no absolute paths
- every file except `kb.json` must be listed in `kb.json` → `files`, and every listed file must exist
- at most 20,000 files and 4 GB uncompressed

The desktop app rejects the whole bundle if any rule is broken.

## `kb.json`

```json
{
  "format_version": 1,
  "kb_version": "2026.09.22-1",
  "source_format": "pageindex",
  "created_at": "2026-09-22T11:01:55Z",
  "pageindex_version": "0.2.10",
  "files": {
    "content/manifest.json": "<sha256 hex>",
    "content/docs/pi-91f2.../tree.json": "<sha256 hex>"
  },
  "documents": [
    {"id": "pi-91f2...", "title": "Station 4 torque tool manual",
     "description": "Setup, calibration and error codes for the DT-450.",
     "tags": ["line-3", "station-4"], "language": "en"}
  ]
}
```

| Field | Required | Meaning |
|---|---|---|
| `format_version` | yes | Always `1` for this spec. |
| `kb_version` | yes | Unique version name: letters, digits, `.`, `_`, `-`; max 64 characters. Importing the same bundle again just switches back to it; a *different* bundle with an already-installed version name is refused, so give every new build a new version. |
| `source_format` | yes | `"pageindex"` or `"markdown"`. |
| `created_at` | no | ISO 8601 timestamp. |
| `pageindex_version` | recommended for `pageindex` | The PageIndex package version that built the store. The desktop app is tested against `0.2.10`. |
| `files` | yes | Every file in the bundle except `kb.json` → its SHA-256 (lowercase hex). |
| `documents` | recommended | Catalog of the documents. `id` is the PageIndex doc id, or the Markdown file's path under `content/` (e.g. `stations/torque.md`). The app uses it: `title` is the name operators see and cite; `description` tells the model what the manual covers (it is shown when choosing and answering, so one precise sentence helps); `tags` become the **station filter** on the desktop (e.g. `["Station 3"]`; a document with several tags appears under each). An `id` that matches no document is reported to the administrator in the app's Manage panel. |

## `content/` for `source_format: "pageindex"`

The PageIndex local store folder, **as PageIndex writes it** (`PageIndexClient(storage_path=...)`):

```
content/manifest.json
content/docs/<doc_id>/doc.json      {"id", "name", "description", ...}
content/docs/<doc_id>/tree.json     sections: title, node_id, start_index, end_index, summary, nodes
content/docs/<doc_id>/pages.json    [{"page_index": 1, "markdown": "<page text>"}, ...]
```

Please make sure that:

- **every section has a `summary`**. The offline model chooses sections by reading titles and summaries.
  Sections without one get the first ~300 characters of their text instead, which works less well: in
  testing, the one section without a summary was the one the model failed to find.
- **every `doc.json` has a `description`** (one or two sentences: what the document is and what it is
  about, e.g. "Maintenance manual for the DT-450 torque driver at station 4, line 3"). The model uses it
  to understand references like "the tool" or "the Company" inside the text.
- `start_index`/`end_index` stay within the document's pages (the app rejects the document otherwise).
- documents are **text-based PDFs**. Scanned pages have no text in `pages.json` and cannot be answered
  from; run OCR before indexing.

## `content/` for `source_format: "markdown"`

One `.md` file per document, in any folder structure:

```
content/line-3/torque-tool.md
content/line-3/torque-tool.summaries.json     optional
```

- Structure comes **only from ATX headings** (`#`, `##`, `###` ...). Nest them properly: a document that
  uses only `#`, or no headings at all, becomes a flat or empty tree and answers poorly.
  Setext headings (`===` / `---` underlines) are not recognised.
- Text before the first heading becomes a section called "Preface".
- Headings inside fenced code blocks are ignored.
- Optional `<name>.summaries.json` next to a file gives summaries by heading path:

```json
{
  "Station 4 Torque Tool Manual > Troubleshooting > Error E17": "What error E17 means and how to clear it."
}
```

- Best practice: one `#` title heading per file, followed by a one-sentence introduction (used as the
  description when the catalog has none), then `##` / `###` sections.
- The desktop renders Markdown for reading: tables (GitHub style), lists, emphasis and code blocks.
  Raw HTML is not rendered, links are shown as plain text (the machine is offline) and images are not
  shown, so put anything essential in text or tables.

Citations for Markdown documents show the manual title and heading path (e.g. *Station 4 torque manual ›
Troubleshooting › Error E17*); PageIndex documents cite page numbers.

## Updates and rollback

- Each bundle is a **complete** knowledge base; the app does not merge bundles. (Incremental updates can
  be added in a later format version if bundles get large.)
- Installing a new version keeps the previous ones; an administrator can switch back from the app.

## Checklist before shipping a bundle

- [ ] `kb_version` is new (never shipped before) and sorts after the previous one, e.g. `2026.10.01-1`
- [ ] every file under `content/` (and `originals/`) is listed in `kb.json` → `files` with its SHA-256
- [ ] PageIndex: every section has a `summary`; every `doc.json` has a `description`
- [ ] Markdown: headings are nested (`#` → `##` → `###`), no document is one flat block
- [ ] the zip opens, and paths inside it use forward slashes (`content/docs/...`, not `content\docs\...`)

Send the desktop team a first bundle early: they can run it through the app's import check and report
any problem before the process is automated.

## Open questions for the web team

- **Signing.** Checksums catch corruption and accidental edits but not deliberate tampering by someone who
  also rewrites `kb.json`. Proposal: an Ed25519 signature file (`kb.json.sig`) made with a key held by the
  web portal, verified with a public key built into the desktop app. Can the portal hold a signing key?
- **Delivery.** How will bundles reach the line machines (USB drive, shared folder, IT deployment tool)?
- **Size and frequency.** Roughly how many documents and pages, and how often will they change? This
  decides whether incremental updates are worth adding.
- **Originals.** May the source PDFs be included in `originals/`? With them, line staff can open the cited
  page, and answers show the cited pages that hold a picture (a diagram, photo or drawing embedded as an
  image) under "Figures from the manual". Name each file as `doc.json` → `name` says.
- **Language.** Which languages will documents and questions be in?
- **Images and tables** inside documents: answers are found from text only; pictures are shown only
  when the cited page holds one (see Originals). Drawings made of vector lines rather than an embedded
  image are not detected. Are there procedures where the answer is only in a diagram or photo?

## Building a bundle

A standalone Python 3.9+ script (standard library only). Point it at a PageIndex store folder (the
`storage_path` you gave `PageIndexClient`) or a folder of Markdown files:

```python
"""Build a knowledge-base bundle (format v1) for the Lattice desktop app.

    python build_bundle.py <content_dir> <pageindex|markdown> <kb_version> <out.zip> [originals_dir]
"""
import hashlib, json, sys, zipfile
from datetime import datetime, timezone
from pathlib import Path


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def build(content_dir, source_format, kb_version, out_zip, originals_dir=None):
    files = {}
    for prefix, root in (("content", content_dir), ("originals", originals_dir)):
        if root:
            for p in sorted(Path(root).rglob("*")):
                if p.is_file() and not p.name.startswith("."):
                    files[f"{prefix}/{p.relative_to(root).as_posix()}"] = p
    manifest = {
        "format_version": 1,
        "kb_version": kb_version,
        "source_format": source_format,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "files": {name: sha256(p) for name, p in files.items()},
        "documents": [],  # optional: [{"id", "title", "description", "tags", "language"}]
    }
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("kb.json", json.dumps(manifest, indent=1))
        for name, p in files.items():
            zf.write(p, name)


if __name__ == "__main__":
    build(*sys.argv[1:6])
```

Example: `python build_bundle.py ./pageindex_store pageindex 2026.10.01-1 kb-2026.10.01-1.zip`
