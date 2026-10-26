"""sem-ai-kb knowledge bases: a folder (or a zip of it) built from a manual by kb-format/tools/build-kb.mjs.

    kb.json                         {"name", "description", "modules": [...], "sections": [...]}
    index.md, README.md             for people; not read here
    AGENTS.md                       how the assistant should behave (given to the chat model)
    needs-review.md                 known gaps in the manual (given to the model before it answers)
    interaction/decision-tree.json  the clarifying interview (see interview.py)
    modules/<module>/NN-<slug>.md   one section of the manual, with YAML front matter
    images/page_NNN_imgK.png        the manual's pictures; a section's "Image description:" line says
                                    what one shows

Each source file the modules came from ("source" in kb.json) becomes one Doc: its modules are its
top-level sections and their sections the next level, with the manual's own headings, so the rest of
the engine (search, focus, "Same step in", walkthroughs) works on it as on any Markdown document.
Page numbers and summaries come from kb.json; pictures keep their whole description.

A knowledge base of several documents, as kb-format/tools/convert_kb.py writes it, has one such
folder per document under a master index:

    README.md, page_index.json      the documents (the parser's outline of each, with "folder")
    <document>/kb.json              {"generator": "convert_kb.py", "document": {...}, "modules", "sections", "images"}
    <document>/AGENTS.md, needs-review.md, interaction/decision-tree.json, modules/, images/

Each document folder becomes one Doc. Their decision trees are joined under "Which document is your
question about?" (asked only when a question matches several documents), with ids prefixed by the
folder ("r7-guide::start", "r7-guide/common-issues/01-no-internet").
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from .loaders import (_IMAGE_REF, KnowledgeBaseError, _figure, _notes, _plain_description, parse_markdown,
                      repair_mojibake)
from .models import Doc, clean

KB_INDEX = "kb.json"
AGENTS = "AGENTS.md"
NEEDS_REVIEW = "needs-review.md"
DECISION_TREE = "interaction/decision-tree.json"
# what an import keeps: everything else in the folder (scripts, an MCP server, notes) stays out
KEPT = re.compile(r"(kb\.json|index\.md|README\.md|AGENTS\.md|needs-review\.md|interaction/decision-tree\.json"
                  r"|modules/[^/]+/[^/]+\.md|images/[^/]+\.(?:png|jpe?g|gif|webp)|pages/[^/]+\.(?:png|jpe?g))",
                  re.IGNORECASE)
MASTER_INDEX = "page_index.json"
# the same, for a knowledge base of several document folders under page_index.json
KEPT_SET = re.compile(r"(README\.md|page_index\.json|[^/]+/(?:kb\.json|index\.md|README\.md|AGENTS\.md|needs-review\.md"
                      r"|interaction/decision-tree\.json|modules/[^/]+/[^/]+\.md|images/[^/]+\.(?:png|jpe?g|gif|webp)))",
                      re.IGNORECASE)
GENERATOR = "convert_kb.py"
# a picture's description is read in full where the picture is, up to this length
DESCRIPTION_CHARS = 1500

_FRONT = re.compile(r"\A﻿?---\r?\n(.*?)\r?\n---\r?\n", re.DOTALL)
_PAGES_LINE = re.compile(r"^_Manual (?:pp?\.\s*[\d–-]+|pages not recorded)_\s*$", re.MULTILINE)
# a picture as build-kb.mjs writes it: the image, its path line, and its description line if it has one
_PICTURE_BLOCK = re.compile(r"!\[([^\]]*)\]\(\s*(?:\.\./\.\./)?images/([^)\s]+)\s*\)[ \t]*"
                            r"(?:\n\s*Image path: [^\n]*)?(?:\n\s*Image description: ([^\n]*))?")
_PATH_LINE = re.compile(r"^Image path: .*$\n?", re.MULTILINE)
_DESCRIPTION_LINE = re.compile(r"^Image description: (.+)$\n?", re.MULTILINE)
_NUMBER = re.compile(r"^\s*\d+(?:\.\d+)*\.?\s+")


def is_semkb_index(data: object) -> bool:
    """A sem-ai-kb kb.json (modules and sections), not Lattice's own bundle manifest of that name."""
    return (isinstance(data, dict) and "format_version" not in data and isinstance(data.get("modules"), list)
            and isinstance(data.get("sections"), list))


def is_document_index(data: object) -> bool:
    """One document's kb.json in a knowledge base of document folders (convert_kb.py)."""
    return (isinstance(data, dict) and data.get("generator") == GENERATOR and isinstance(data.get("document"), dict)
            and isinstance(data.get("modules"), list) and isinstance(data.get("sections"), list))


def is_master_index(data: object) -> bool:
    """page_index.json of a knowledge base of document folders: its documents name their folder."""
    return (isinstance(data, dict) and isinstance(data.get("documents"), list)
            and any(isinstance(d, dict) and isinstance(d.get("folder"), str) and d["folder"] for d in data["documents"]))


def is_folder_index(data: object) -> bool:
    """A document folder's kb.json: convert_kb.py's, or a one-manual sem-ai-kb kb.json (modules and
    sections) as other tools write it for each document of a multi-document knowledge base."""
    return is_document_index(data) or is_semkb_index(data)


# folders inside a document folder, never documents themselves
_NOT_DOCUMENTS = {"images", "modules", "interaction", "references", "pages"}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", re.sub(r"\.[A-Za-z0-9]{2,5}$", "", text).lower()).strip("-")


def read_master(root: Path) -> dict | None:
    """A knowledge base of document folders ("multi doc test/<document>/kb.json", README.md and
    page_index.json at the top): {"kb_name", "documents": [...], "folders": [names]}; None for a
    one-folder knowledge base (kb.json at the top) or something else. page_index.json may name each
    document's folder (convert_kb.py) or be the parser's own index; folders are found either way."""
    root = Path(root)
    if (root / KB_INDEX).is_file() or not root.is_dir():
        return None
    folders = sorted(p.name for p in root.iterdir()
                     if p.is_dir() and p.name not in _NOT_DOCUMENTS and not p.name.startswith(".") and (p / KB_INDEX).is_file())
    if not folders:
        return None
    try:
        data = json.loads((root / MASTER_INDEX).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        data = {}
    data = data if isinstance(data, dict) else {}
    entries = [d for d in data.get("documents") or [] if isinstance(d, dict)]
    return {**data, "documents": entries, "folders": folders}


def _entry_for(folder: str, entries: list[dict]) -> dict:
    """The page_index.json entry of a document folder: by its "folder", else by its file name."""
    for e in entries:
        if str(e.get("folder") or "").strip("/") == folder:
            return e
    for e in entries:
        if _slug(str(e.get("filename") or e.get("title") or "")) == folder:
            return e
    return {}


def document_folders(root: Path, master: dict) -> list[tuple[Path, dict]]:
    """(folder, its kb.json) of each document: in page_index.json's order where it names them, the
    rest by name."""
    entries = master.get("documents") or []
    order = []
    for e in entries:
        named = str(e.get("folder") or "").strip("/") or _slug(str(e.get("filename") or ""))
        if named in master["folders"] and named not in order:
            order.append(named)
    order += [f for f in master["folders"] if f not in order]
    out = []
    for folder in order:
        path = Path(root) / folder
        try:
            data = json.loads((path / KB_INDEX).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as e:
            raise KnowledgeBaseError(f"{folder}/{KB_INDEX} is not valid JSON: {e}") from e
        if not is_folder_index(data):
            raise KnowledgeBaseError(f"{folder}/{KB_INDEX} has no modules and sections")
        out.append((path, data))
    return out


def _section_files(folder: Path) -> dict[str, str]:
    """Section id (front matter "id:") -> file, for a kb.json whose sections do not name their file."""
    out = {}
    for md in sorted((folder / "modules").glob("*/*.md")):
        try:
            head = md.read_text(encoding="utf-8-sig")[:600]
        except OSError:
            continue
        m = _FRONT.match(head)
        sid = re.search(r"(?m)^id:\s*(.+?)\s*$", m.group(1)) if m else None
        if sid:
            out[sid.group(1).strip().strip('"')] = md.relative_to(folder).as_posix()
    return out


def _as_semkb(folder: Path, data: dict, entry: dict | None = None) -> dict:
    """A document folder's kb.json in the shape of a one-manual kb.json, with its ids prefixed by its
    folder (ids are only unique within a document)."""
    did, entry = folder.name, entry or {}
    doc = data.get("document") if isinstance(data.get("document"), dict) else {}
    mods = [m for m in data["modules"] if isinstance(m, dict) and isinstance(m.get("id"), str)]
    src = str(doc.get("filename") or entry.get("filename") or doc.get("title") or data.get("name")
              or next((m.get("source") for m in mods if m.get("source")), None) or did)
    files = None
    sections = []
    for s in data["sections"]:
        if not isinstance(s, dict) or not isinstance(s.get("id"), str) or not isinstance(s.get("module"), str):
            continue
        file = s.get("file")
        if not isinstance(file, str):
            files = files if files is not None else _section_files(folder)
            file = files.get(s["id"])
            if not file:
                continue
        sections.append({**s, "id": f"{did}/{s['id']}", "module": f"{did}/{s['module']}", "file": file})
    modules = [{**m, "id": f"{did}/{m['id']}", "source": src, "title": m.get("heading") or m.get("title") or m["id"]}
               for m in mods]
    summary = doc.get("summary") or entry.get("doc_summary") or data.get("description") or ""
    return {"name": src, "modules": modules, "sections": sections, "summary": summary}


def read_index(root: Path) -> dict:
    try:
        data = json.loads((Path(root) / KB_INDEX).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise KnowledgeBaseError(f"{KB_INDEX} is missing or not valid JSON: {e}") from e
    if not is_semkb_index(data):
        raise KnowledgeBaseError(f"{KB_INDEX} has no modules and sections")
    for s in data["sections"]:
        if not isinstance(s, dict) or not all(isinstance(s.get(k), str) for k in ("id", "module", "file")):
            raise KnowledgeBaseError(f"{KB_INDEX}: a section without id, module or file: {str(s)[:80]}")
    return data


def source_of(module: dict, index: dict) -> str:
    return str(module.get("source") or index.get("name") or "manual")


def doc_id_for(source: str) -> str:
    return "kb-" + (re.sub(r"[^a-z0-9]+", "-", source.lower()).strip("-")[:60] or "manual")


def _bare(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", _NUMBER.sub("", title).lower()).strip()


def _demote(text: str, by: int = 1) -> str:
    """A section's own sub-headings ("## 2.2.1 Option A") `by` levels lower, outside code."""
    out, fence = [], False
    for line in text.splitlines():
        if re.match(r"^\s*(```|~~~)", line):
            fence = not fence
        out.append("#" * by + line if not fence and re.match(r"^#{1,5}\s", line) else line)
    return "\n".join(out)


def section_body(root: Path, entry: dict, described: dict[str, str], depth: int = 1) -> str:
    """The section file's text without its front matter, title and page line; pictures as
    ![caption](images/<file>) with their description noted in `described`."""
    path = Path(root) / entry["file"]
    try:
        text = repair_mojibake(path.read_text(encoding="utf-8-sig"))
    except OSError as e:
        raise KnowledgeBaseError(f"{entry['file']}: {e}") from e
    text = _FRONT.sub("", text, count=1).lstrip()
    lines = text.splitlines()
    if lines and lines[0].startswith("# "):  # the section's title, given by its heading in the document
        lines = lines[1:]
    text = _PAGES_LINE.sub("", "\n".join(lines))

    def picture(m: re.Match) -> str:
        name = m.group(2).strip()
        if m.group(3):
            described[name] = m.group(3).strip()
        return f"![{m.group(1)}](images/{name})"

    text = _PICTURE_BLOCK.sub(picture, text)
    text = _DESCRIPTION_LINE.sub("", _PATH_LINE.sub("", text))
    return _demote(re.sub(r"\n{3,}", "\n\n", text).strip(), depth)


@dataclass
class _Expected:
    depth: int
    title: str
    kb_ids: list[str]
    pages: list | None
    summary: str | None


def load_semkb(root: Path) -> list[Doc]:
    root = Path(root)
    master = read_master(root) if not (root / KB_INDEX).is_file() else None
    if master is not None:  # several document folders (convert_kb.py)
        docs = []
        folders = document_folders(root, master)
        titles = {f.name: str((d.get("document") or {}).get("title") or _as_semkb(f, d, _entry_for(f.name, master["documents"]))["name"])
                  for f, d in folders}
        for folder, data in folders:
            entry = _entry_for(folder.name, master["documents"])
            index = _as_semkb(folder, data, entry)
            if not index["modules"] or not index["sections"]:
                raise KnowledgeBaseError(f"{folder.name}/{KB_INDEX} lists no modules or sections with text")
            doc = _load_source(folder, index, index["name"], index["modules"], several=False)
            doc.description = clean(str(index["summary"])) or doc.description
            doc.meta["folder"] = folder.name
            # what tells this document from the others, for finding the right one (see convert_kb.py)
            if entry.get("codes"):
                doc.meta["codes"] = ", ".join(map(str, entry["codes"][:12]))
            related = [f"{titles.get(r.get('document'), r.get('document'))} ({r.get('relation', 'related')}"
                       + (f": {', '.join(r['codes'])}" if r.get("codes") else "") + ")"
                       for r in entry.get("related") or [] if isinstance(r, dict) and r.get("document")]
            if related:
                doc.meta["related"] = "; ".join(related)
            docs.append(doc)
        return docs
    index = read_index(root)
    modules = [m for m in index["modules"] if isinstance(m, dict) and isinstance(m.get("id"), str)]
    if not modules:
        raise KnowledgeBaseError(f"{KB_INDEX} lists no modules")
    sources = list(dict.fromkeys(source_of(m, index) for m in modules))
    return [_load_source(root, index, source, [m for m in modules if source_of(m, index) == source],
                         several=len(sources) > 1) for source in sources]


def _load_source(root: Path, index: dict, source: str, mods: list[dict], *, several: bool) -> Doc:
    """One source file's modules (from `root`'s module files) as a Doc."""
    by_module: dict[str, list[dict]] = {}
    for s in index["sections"]:
        by_module.setdefault(s["module"], []).append(s)
    described: dict[str, str] = {}
    lines: list[str] = []
    expected: list[_Expected] = []
    # several modules: under one title heading, as in the parser's own Markdown ("# HX-500_manual.pdf",
    # then its chapters). The search reads codes in a document's top headings as the document's
    # name ("HX-500"); with each module a top heading, "Q2" in "Part 3: Delivery record Q2 2026"
    # stopped counting as a search word and the delivery record was not found (4B test, 23 Oct)
    top = 0
    if len(mods) > 1:
        lines += [f"# {clean(source)}", ""]
        top = 1
    for m in mods:
        title = str(m.get("title") or m["id"])
        if several and ": " in title and title.split(": ", 1)[0].lower() in source.lower().replace("_", " "):
            title = title.split(": ", 1)[1]  # "HX-500 manual: Maintenance" in the HX-500 manual is "Maintenance"
        secs = list(by_module.get(m["id"], []))
        intro = None
        # a module's first section with the module's own heading ("2 Image Classification") is its intro
        if secs and (_bare(secs[0]["title"]) == _bare(title) or secs[0]["id"].endswith(f"/01-{m['id']}")):
            intro = secs.pop(0)
        heading = clean(intro["title"]) if intro and _bare(intro["title"]) == _bare(title) else clean(title)
        lines += [f"{'#' * (top + 1)} {heading}", ""]
        if intro:
            lines += [section_body(root, intro, described, top + 1), ""]
        expected.append(_Expected(top, heading, [f"module:{m['id']}"] + ([intro["id"]] if intro else []),
                                  m.get("pages") or (intro or {}).get("pages"),
                                  m.get("summary") or (intro or {}).get("summary")))
        for s in secs:
            lines += [f"{'#' * (top + 2)} {clean(s['title'])}", "", section_body(root, s, described, top + 1), ""]
            expected.append(_Expected(top + 1, clean(s["title"]), [s["id"]], s.get("pages"), s.get("summary")))
    doc = parse_markdown("\n".join(lines), doc_id_for(source), source)
    _apply(doc, expected)
    _attach_pictures(doc, root, described)
    doc.assets_dir = root
    doc.title = source
    suffix = Path(source).suffix.lstrip(".").lower()
    if suffix:
        doc.meta["type"] = suffix
    # the document's description: its module's summary, else the first section summary the
    # export wrote (the overview comes first in a manual)
    given = next((str(m["summary"]) for m in mods if m.get("summary")), "")
    first = next((s.summary for s in doc.sections.values() if s.summary and not s.summary_is_fallback), "")
    doc.description = clean(given) or first or doc.description
    return doc


def _apply(doc: Doc, expected: list[_Expected]) -> None:
    """kb.json's ids, pages and summaries onto the parsed sections, matched in document order."""
    want = iter(expected)
    current = next(want, None)
    for sec in doc.sections.values():
        if current is None:
            break
        if sec.depth == current.depth and sec.title == current.title:
            sec.kb_ids = list(current.kb_ids)
            pages = current.pages
            if isinstance(pages, list) and len(pages) == 2 and all(isinstance(p, int) for p in pages):
                sec.start_page, sec.end_page = pages
            if current.summary:
                sec.summary, sec.summary_is_fallback = clean(str(current.summary)), False
            current = next(want, None)


def _attach_pictures(doc: Doc, root: Path, described: dict[str, str]) -> None:
    """Pictures are shown with answers; the model reads each one's whole description where it is."""
    for sec in doc.sections.values():
        sec.images = []
        for caption, ref in _IMAGE_REF.findall(sec.own_text):
            name = ref.strip()
            if (root / "images" / name).is_file() and name not in sec.images:
                sec.images.append(name)
                sec.image_notes[name] = _notes(caption.replace("screenshot ", "").strip(),
                                               _plain_description(described.get(name, "")))

        def figure(m: re.Match) -> str:
            what = _plain_description(described.get(m.group(2).strip(), ""))
            if len(what) > DESCRIPTION_CHARS:
                what = what[:DESCRIPTION_CHARS].rsplit(" ", 1)[0] + " ..."
            caption = re.sub(r"^screenshot\s*(p\.\s*\d+)?\s*", "", m.group(1)).strip()
            return _figure(caption, what)

        sec.own_text = _IMAGE_REF.sub(figure, sec.own_text)


# ── what the knowledge base says beyond its sections ──

@dataclass
class Extras:
    name: str = ""
    guide: str = ""      # AGENTS.md, as instructions for the chat model
    notes: str = ""      # needs-review.md: the known gaps, one per line
    tree: dict | None = None
    module_titles: dict[str, str] = field(default_factory=dict)
    section_module: dict[str, str] = field(default_factory=dict)
    section_titles: dict[str, str] = field(default_factory=dict)


# AGENTS.md sections about the folder itself, not about how to answer: left out of the model's instructions
_NOT_FOR_MODEL = re.compile(r"^(how to use the kb|maintenance)\b", re.IGNORECASE)


def guide_for_model(text: str) -> str:
    parts = re.split(r"(?m)^(?=## )", text)
    kept = [p for p in parts if not (p.startswith("## ") and _NOT_FOR_MODEL.match(p[3:].strip()))]
    return re.sub(r"\n{3,}", "\n\n", "".join(kept)).strip()


# the block build-kb.mjs rewrites with what it detects (pictures without a description, missing page
# numbers): notes for whoever maintains the knowledge base, not facts about the manual's content.
# Given to a small model as "known gaps", they made it decline questions the documents do answer.
_AUTO_BLOCK = re.compile(r"<!-- auto-detected gaps.*?-->.*?<!-- end of auto-detected gaps -->", re.DOTALL)


def notes_for_model(text: str) -> str:
    """needs-review.md's hand-written entries (lines starting with "- "), without the auto-detected block."""
    text = _AUTO_BLOCK.sub("", text)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    items, current = [], None
    for line in text.splitlines():
        if line.startswith("- "):
            if current:
                items.append(current)
            current = line[2:].strip()
        elif current is not None and line.startswith("  ") and line.strip():
            current += " " + line.strip()
        elif not line.strip() and current:
            items.append(current)
            current = None
    if current:
        items.append(current)
    items = [i.replace("**", "") for i in items if i and i.lower() != "none found"]
    return "\n".join(f"- {i}" for i in items)


def _read_tree(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None  # a broken tree: no interview, the usual questions back still work
    if isinstance(data, dict) and isinstance(data.get("nodes"), dict) and data.get("start") in data["nodes"]:
        return data
    return None


def load_extras(root: Path) -> Extras:
    root = Path(root)
    master = read_master(root) if not (root / KB_INDEX).is_file() else None
    if master is not None:
        return _set_extras(root, master)
    index = read_index(root)
    read = lambda rel: (root / rel).read_text(encoding="utf-8-sig") if (root / rel).is_file() else ""  # noqa: E731
    return Extras(
        name=str(index.get("name") or ""),
        guide=guide_for_model(read(AGENTS)),
        notes=notes_for_model(read(NEEDS_REVIEW)),
        tree=_read_tree(root / DECISION_TREE),
        module_titles={m["id"]: str(m.get("title") or m["id"]) for m in index["modules"] if isinstance(m, dict) and "id" in m},
        section_module={s["id"]: s["module"] for s in index["sections"]},
        section_titles={s["id"]: str(s.get("title") or "") for s in index["sections"]},
    )


# a document's AGENTS.md sections that only fit that document: left out when several documents share one chat
_ONE_DOCUMENT = re.compile(r"^(how to find things|other documents)\b", re.IGNORECASE)


def shared_guide(text: str, name: str) -> str:
    """A document's AGENTS.md as instructions for a knowledge base of several documents: its
    behaviour rules (interview, walkthrough, pictures, grounding), without the title and opening
    paragraph that name the one document, and without the parts about finding things in its folder."""
    text = guide_for_model(text)
    parts = re.split(r"(?m)^(?=## )", text)
    kept = [p for p in parts if p.startswith("## ") and not _ONE_DOCUMENT.match(p[3:].strip())]
    if not kept:
        return ""
    head = f"You are an interactive guide for the documents of **{name}**." if name else ""
    return (head + "\n\n" + re.sub(r"\n{3,}", "\n\n", "".join(kept))).strip()


def _prefix_next(nxt: str, did: str) -> str:
    if not nxt or nxt == "open" or nxt.startswith("file:"):
        return "open"
    if nxt == "resolve":
        return f"resolve:{did}"
    if nxt.startswith("result:"):
        return f"result:{did}::{nxt[7:]}"
    return f"{did}::{nxt}"


def merged_tree(trees: list[tuple[str, str, dict | None, list[str]]]) -> dict | None:
    """One tree from each document's: (folder, title, its tree or None, its module ids). Node and
    result ids get the folder as prefix, module ids too ("folder/module"). With several documents,
    the first question is which document; a document without a tree leads to all its modules."""
    nodes: dict = {}
    results: dict = {}
    resolves: dict = {}
    starts: list[tuple[str, str]] = []
    for did, title, tree, mods in trees:
        if tree is None:
            results[f"{did}::all"] = {"modules": [f"{did}/{m}" for m in mods], "summary": title}
            starts.append((title, f"result:{did}::all"))
            continue
        for nid, node in (tree.get("nodes") or {}).items():
            nodes[f"{did}::{nid}"] = {**node, "options": [
                {**o, "next": _prefix_next(str(o.get("next") or ""), did)} for o in node.get("options") or []]}
        for rid, r in (tree.get("results") or {}).items():
            hint = str(r.get("start_hint") or "")
            out = {**r, "modules": [f"{did}/{m}" for m in r.get("modules") or []]}
            if hint and "/" in hint and " " not in hint:  # a section id (convert_kb.py), not "2.2 Training, Option A"
                out["start_section"] = f"{did}/{hint}"
            results[f"{did}::{rid}"] = out
        if tree.get("resolve"):
            resolves[did] = {"keys": tree.get("resolve_keys") or ["task", "framework"],
                             "table": {k: f"{did}::{v}" for k, v in tree["resolve"].items()}}
        starts.append((title, f"{did}::{tree['start']}"))
    if not nodes and len(starts) < 2:
        return None
    if len(starts) == 1:
        start = starts[0][1]
        if start.startswith("result:"):
            return None
    else:
        nodes["kb::document"] = {"ask": "Which document is your question about?",
                                 "options": [{"label": t, "next": n} for t, n in starts] + [{"label": "Something else", "next": "open"}]}
        start = "kb::document"
    return {"start": start, "nodes": nodes, "results": results, "resolves": resolves}


def _read_any_case(folder: Path, rel: str) -> str:
    """A file of the folder by name in any case ("agents.md" for AGENTS.md: the same file on Windows)."""
    path = folder / rel
    if not path.is_file() and path.parent.is_dir():
        path = next((p for p in path.parent.iterdir() if p.name.lower() == path.name.lower()), path)
    try:
        return path.read_text(encoding="utf-8-sig") if path.is_file() else ""
    except OSError:
        return ""


def _set_extras(root: Path, master: dict) -> Extras:
    name = str(master.get("kb_name") or "")
    guide, notes, trees = "", [], []
    module_titles, section_module, section_titles = {}, {}, {}
    folders = document_folders(root, master)
    for folder, data in folders:
        did = folder.name
        doc_meta = data.get("document") if isinstance(data.get("document"), dict) else {}
        title = str(doc_meta.get("title") or _as_semkb(folder, data, _entry_for(did, master["documents"]))["name"])
        read = lambda rel, folder=folder: _read_any_case(folder, rel)  # noqa: E731
        if not guide:
            text = read(AGENTS)
            guide = shared_guide(text, name) if len(folders) > 1 else guide_for_model(text)
        for line in notes_for_model(read(NEEDS_REVIEW)).splitlines():
            notes.append(f"- ({title}) {line[2:]}" if len(folders) > 1 else line)
        trees.append((did, title, _read_tree(folder / DECISION_TREE), [m["id"] for m in data["modules"] if isinstance(m, dict) and "id" in m]))
        for m in data["modules"]:
            if isinstance(m, dict) and "id" in m:
                module_titles[f"{did}/{m['id']}"] = str(m.get("title") or m["id"])
        for s in data["sections"]:
            if isinstance(s, dict) and "id" in s and "module" in s:
                section_module[f"{did}/{s['id']}"] = f"{did}/{s['module']}"
                section_titles[f"{did}/{s['id']}"] = str(s.get("title") or "")
    return Extras(name=name, guide=guide, notes="\n".join(notes), tree=merged_tree(trees),
                  module_titles=module_titles, section_module=section_module, section_titles=section_titles)
