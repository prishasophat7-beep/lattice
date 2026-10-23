"""Loaders for the two knowledge-base formats the web team may ship.

PageIndex store (read directly, no PageIndex library needed):
    <root>/docs/<doc_id>/doc.json    {"id", "name", "description", ...}
    <root>/docs/<doc_id>/tree.json   [{"title", "node_id", "start_index", "end_index", "summary"?, "nodes"?}]
    <root>/docs/<doc_id>/pages.json  [{"page_index": 1, "markdown": "..."}]

Markdown:
    <root>/<name>.md                 ATX headings (#, ##, ###) define the tree
    <root>/<name>.summaries.json     optional {"Heading > Sub heading": "summary"}
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import unquote

from .models import Doc, Section, clean, fallback_summary


class KnowledgeBaseError(ValueError):
    """The knowledge base is missing files or has an unreadable structure."""


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as e:
        raise KnowledgeBaseError(f"Missing file: {path.name} in {path.parent}") from e
    except ValueError as e:
        raise KnowledgeBaseError(f"{path} is not valid JSON: {e}") from e


# ── PageIndex ──

def _text_after_title(page_text: str, title: str) -> str:
    """Opening text of a section that may start mid-page."""
    page, head = clean(page_text), clean(title)[:40]
    idx = page.lower().find(head.lower()) if head else -1
    return page[idx:] if idx >= 0 else page


def load_pageindex_doc(doc_dir: Path) -> Doc:
    meta = _read_json(doc_dir / "doc.json")
    tree = _read_json(doc_dir / "tree.json")
    pages = {int(p["page_index"]): p.get("markdown") or "" for p in _read_json(doc_dir / "pages.json")}
    if not isinstance(tree, list) or not tree:
        raise KnowledgeBaseError(f"{doc_dir.name}: tree.json has no sections")
    sections: dict[str, Section] = {}

    def add(nodes, depth: int, parent: str | None):
        for n in nodes:
            try:
                node_id, start, end = str(n["node_id"]), int(n["start_index"]), int(n["end_index"])
            except (KeyError, TypeError, ValueError) as e:
                raise KnowledgeBaseError(f"{doc_dir.name}: malformed section {n!r:.120}") from e
            if not (1 <= start <= end <= max(pages, default=0)):
                raise KnowledgeBaseError(f"{doc_dir.name}: section {node_id} points at pages "
                                         f"{start}-{end} outside the document")
            summary = clean(n.get("summary") or n.get("prefix_summary") or "")
            sec = Section(node_id=node_id, title=clean(n.get("title") or "Untitled"), summary=summary,
                          depth=depth, parent=parent, start_page=start, end_page=end,
                          children=[str(c["node_id"]) for c in n.get("nodes") or []])
            if not summary:
                sec.summary = fallback_summary(_text_after_title(pages.get(start, ""), sec.title))
                sec.summary_is_fallback = True
            sections[node_id] = sec
            add(n.get("nodes") or [], depth + 1, node_id)

    add(tree, 0, None)
    return Doc(doc_id=str(meta.get("id") or doc_dir.name), name=meta.get("name") or doc_dir.name,
               description=clean(meta.get("description") or ""), kind="pages",
               sections=sections, pages=pages)


def load_pageindex(root: Path) -> list[Doc]:
    docs_dir = Path(root) / "docs"
    if not docs_dir.is_dir():
        raise KnowledgeBaseError(f"No docs/ folder in PageIndex store {root}")
    docs = [load_pageindex_doc(d) for d in sorted(docs_dir.iterdir()) if d.is_dir()]
    if not docs:
        raise KnowledgeBaseError(f"PageIndex store {root} contains no documents")
    return docs


# ── Markdown ──

_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t#]*$")
_FENCE = re.compile(r"^[ \t]*(```|~~~)")


def parse_markdown(text: str, doc_id: str, name: str, summaries: dict[str, str] | None = None) -> Doc:
    """Build a section tree from ATX headings; fenced code blocks are ignored."""
    summaries = summaries or {}
    heads: list[tuple[int, str, int]] = []  # (level, title, line index)
    in_fence = False
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        m = None if in_fence else _HEADING.match(line)
        if m:
            heads.append((len(m.group(1)), clean(m.group(2)), i))

    sections: dict[str, Section] = {}
    counter = 0

    def new_id() -> str:
        nonlocal counter
        counter += 1
        return f"{counter - 1:04d}"

    preface_end = heads[0][2] if heads else len(lines)
    preface = "\n".join(lines[:preface_end]).strip()
    if preface:
        nid = new_id()
        sections[nid] = Section(node_id=nid, title="Preface", summary="", depth=0,
                                own_text=preface, line=1)

    stack: list[tuple[int, str]] = []  # (heading level, node_id) of open ancestors
    for k, (level, title, line_idx) in enumerate(heads):
        body_end = heads[k + 1][2] if k + 1 < len(heads) else len(lines)
        while stack and stack[-1][0] >= level:
            stack.pop()
        parent = stack[-1][1] if stack else None
        nid = new_id()
        sections[nid] = Section(node_id=nid, title=title, summary="", depth=len(stack), parent=parent,
                                own_text="\n".join(lines[line_idx + 1:body_end]).strip(), line=line_idx + 1)
        if parent is not None:
            sections[parent].children.append(nid)
        stack.append((level, nid))

    if not sections:
        raise KnowledgeBaseError(f"{name} is empty")
    doc = Doc(doc_id=doc_id, name=name, description="", kind="markdown", sections=sections)
    for nid, s in sections.items():
        given = summaries.get(" > ".join(doc.path(nid)))
        if given:
            s.summary = clean(given)
        else:
            text = s.own_text or " ".join(sub.own_text for sub in doc.subtree(nid))
            s.summary = fallback_summary(text) or s.title
            s.summary_is_fallback = True
    roots = [s for s in sections.values() if s.parent is None and s.title != "Preface"]
    if len(roots) == 1:  # a title heading: its summary, or the introduction under it
        root = roots[0]
        doc.description = root.summary if not root.summary_is_fallback else fallback_summary(root.own_text)
    return doc


def markdown_doc_id(rel_path: str) -> str:
    """Doc id for a Markdown file given its path under content/ (as kb.json's catalog lists it)."""
    rel = rel_path.replace("\\", "/").removeprefix("content/").removesuffix(".md")
    return "md-" + re.sub(r"[^a-zA-Z0-9]+", "-", rel).strip("-").lower()


def load_markdown(root: Path) -> list[Doc]:
    files = sorted(Path(root).rglob("*.md"))
    if not files:
        raise KnowledgeBaseError(f"No .md files in {root}")
    docs = []
    for f in files:
        sidecar = f.with_suffix(".summaries.json")
        summaries = _read_json(sidecar) if sidecar.exists() else {}
        doc_id = markdown_doc_id(f.relative_to(root).as_posix())
        docs.append(parse_markdown(f.read_text(encoding="utf-8"), doc_id, f.name, summaries))
    return docs


# ── web-platform exports (page_index.json + one Markdown file and its images per document) ──

EXPORT_INDEX = "page_index.json"
# a picture's file name inside images/: no folders, no leading dot, nothing Windows forbids
IMAGE_NAME = re.compile(r"[A-Za-z0-9_][^/\\:*?\"<>|\x00-\x1f]*\.(?:png|jpe?g|gif|webp)", re.IGNORECASE)
# ![caption](images/name.png), also with <...>, a "title", or %20 in the name
_IMAGE_REF = re.compile(r'!\[([^\]]*)\]\(\s*<?(?:\./)?images/([^)>\n"]+?)>?(?:\s+"[^"]*")?\s*\)')
# the chapter of picture descriptions some exports end a document with ("## Image Descriptions")
_DESCRIPTIONS = re.compile(r"(?:image|figure|picture)\s+descriptions?:?", re.IGNORECASE)
_FRONT_MATTER = re.compile(r"\A﻿?---\r?\n.*?\r?\n---\r?\n", re.DOTALL)
SKIPPED = "skipped.json"  # ids of export documents left out at import (they had no Markdown)


def export_doc_id(entry: dict) -> str:
    """Doc id for a document of a web-platform export (its file id, made path-safe)."""
    raw = str(entry.get("file_id") or entry.get("relative_path") or entry.get("filename") or "")
    return "doc-" + (re.sub(r"[^a-zA-Z0-9]+", "-", raw).strip("-").lower() or "unnamed")


def _plain_title(title: str) -> str:
    """"Sheet: Summary" and "Summary" name the same section."""
    return re.sub(r"^(sheet|tab|page)\s*:\s*", "", clean(title), flags=re.IGNORECASE).casefold()


# the platform's summary of a heading with nothing under it, or of a picture: worse than none
# ("Section 5 is titled Creating a MultiX solution but contains no descriptive content in the excerpt.",
# "![Figure](images/figure_252.png)" - Frontier manual, 19 Oct 2026)
_EMPTY_SUMMARY = re.compile(r"\b(?:contains?|has|have|with) no (?:\w+ ){0,2}(?:content|text|excerpt|information|details?)\b"
                            r"|\bno (?:\w+ )?content (?:is )?(?:provided|given|available)\b|\bnothing to summari[sz]e\b"
                            r"|\bno (?:provided |descriptive )?(?:content|excerpt)\b", re.IGNORECASE)
_SECTION_NUMBER = re.compile(r"^Section \d+ (?=[a-z])")


def useful_summary(text: str, whole: bool = False) -> str:
    """A catalog summary without its sentences about missing content and picture links; "" if none is
    left, or with whole=True if any had to go (a summary written from empty headings is wrong all
    through, not only where it says so)."""
    text = repair_mojibake(clean(re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text or "")))
    sentences = [t for t in re.split(r"(?<=[.!?])\s+", text) if t.strip("-. ")]
    kept = [t for t in sentences if not _EMPTY_SUMMARY.search(t)]
    if whole and len(kept) < len(sentences):
        return ""
    text = " ".join(kept).strip(" -")
    return _SECTION_NUMBER.sub("This section ", text) if len(text) >= 3 else ""


def apply_export_catalog(doc: Doc, entry: dict) -> None:
    """Summaries, page ranges and table summaries from the export's catalog onto the sections
    parsed from the document's Markdown. Catalog entries are matched to headings by title, in
    document order; tables (catalog-only nodes) add their summary to the section they are in."""
    unmatched = [s for s in doc.sections.values() if s.title != "Preface"]
    last: dict[int, Section] = {}  # catalog level -> the section matched most recently at that level
    for node in entry.get("sections") or []:
        level = int(node.get("level") or 1)
        summary = useful_summary(node.get("summary") or "")
        if node.get("type") == "table":
            parent = next((last[lv] for lv in sorted(last, reverse=True) if lv < level), None)
            if parent is not None and summary:
                parent.summary = f"{parent.summary} Table: {summary}".strip()
            continue
        key = _plain_title(node.get("title") or "")
        match = next((s for s in unmatched if _plain_title(s.title) == key), None)
        if match is None:
            continue
        unmatched.remove(match)
        last = {lv: sec for lv, sec in last.items() if lv < level}
        last[level] = match
        if summary:
            match.summary, match.summary_is_fallback = whole_sentences(summary), False
        if node.get("start_page"):
            match.start_page = int(node["start_page"])
            match.end_page = int(node.get("end_page") or node["start_page"])
    doc.description = catalog_summary(entry.get("doc_summary")) or doc.description
    doc.description = table_description(doc) or doc.description


def whole_sentences(text: str) -> str:
    """A summary the export cut off mid-word ("... designed for both profes"): up to its last whole
    sentence, or marked as cut ("...") when it has none. A heading-like one ("Hardware
    Requirements:") is whole as it is."""
    text = text.rstrip()
    if not text or text[-1] in ".!?:;)\"'”" or len(text) < 60:
        return text
    end = max(text.rfind(p) for p in (". ", "! ", "? "))
    return text[:end + 1] if end >= 40 else text.rsplit(" ", 1)[0] + " ..."


def catalog_summary(text: str | None) -> str:
    """The export's summary of a document, if it is one: "" when any of it talks about missing
    content (a summary written from empty headings is wrong all through) or it is a run of headings
    ("--- Introduction Functionalities"), not a sentence."""
    given = useful_summary(text or "", whole=True)
    return given if given and (given.rstrip()[-1] in ".!?" or len(given) >= 40) else ""


_TABLE_LINE = re.compile(r"^\s*\|(.*)\|\s*$")
_PARTIAL_NOTE = re.compile(r"showing (?:the )?first [\d,]+ of ([\d,]+) rows\.?\s*(.*)", re.IGNORECASE)


def table_description(doc: Doc) -> str | None:
    """A readable description for a document whose description is its first table read as one line
    ("Part number Description Price (INR) In stock ... HX9 4002 chiller gasket 3,140 54 ..."):
    "HX-900 Spare Parts Catalogue: a table of 500 rows (Part number, Description, ...)", with the
    file's own note when it has one. None when the description reads as prose."""
    heading = ""
    for s in doc.sections.values():
        lines = s.own_text.splitlines()
        rows = [m.group(1) for line in lines if (m := _TABLE_LINE.match(line))]
        if len(rows) < 3:
            if s.parent is not None and not s.own_text.strip() and s.title != "Preface":
                heading = clean(s.title)  # "HX-900 Spare Parts Catalogue", the heading above the table
            continue
        columns = [clean(c) for c in rows[0].split("|") if clean(c)]
        flat = re.sub(r"[^\w]+", " ", doc.description).strip().casefold()
        if not columns or not flat.startswith(re.sub(r"[^\w]+", " ", " ".join(columns[:2])).strip().casefold()):
            return None
        count = sum(1 for r in rows[1:] if not re.fullmatch(r"[\s|:-]*", r))
        note = next((m for line in lines if (m := _PARTIAL_NOTE.search(line.strip("*_ ")))), None)
        if note:
            count = int(note.group(1).replace(",", ""))
        lead = f"{heading}: a table" if heading else "A table"
        text = f"{lead} of {count:,} rows ({', '.join(columns)})."
        if note and note.group(2).strip():
            text += " " + clean(note.group(2)).rstrip("*_ ")
        return text
    return None


def name_of(entry: dict) -> str:
    return str(entry.get("filename") or export_doc_id(entry))


def export_title(entry: dict, names: list[str]) -> str:
    """A document's name: its file name, or its path when another document has the same file name."""
    name = name_of(entry)
    if names.count(name) > 1 and entry.get("relative_path"):
        return str(entry["relative_path"])
    return name


def _figure(caption: str, description: str | None) -> str:
    """What the model reads where a picture is: its caption and, when the export has one, its description."""
    caption = clean(caption)
    if caption.lower() in ("", "figure", "image"):
        caption = ""
    # one line without brackets: the source panel places the picture at this "[Figure: ...]" line
    text = ". ".join(t for t in (caption.rstrip("."), description) if t).replace("[", "(").replace("]", ")")
    return f"[Figure: {text}]" if text else "[Figure]"


# the headings of the platform's picture descriptions: "GLOBAL SCENE: ... KEY OBJECTS: ... TEXT & LABELS: ...",
# often as a Markdown list ("- **KEY OBJECTS & ATTRIBUTES**:")
# - any all-caps label of up to five words, whatever the describing model called its parts
_LABEL_NAMES = r"[A-Z][A-Z0-9]+(?:[ /&-]+[A-Z][A-Z0-9]*){0,4}"
_DESCRIPTION_LABEL = re.compile(rf"(?:^|(?<=\s))(?:[-*]\s+)?\**\b(?:{_LABEL_NAMES})\**\s*:\s*\**", re.MULTILINE)
_DESCRIPTION_PART = re.compile(rf"\**\b({_LABEL_NAMES})\**\s*:\s*\**")
# read in full where the picture is up to this length; a longer one (a screenshot described element by
# element, 2,000 characters each) by its scene and the words on it: a 478-picture manual's steps were
# lost among descriptions of toolbars (Frontier manual, 19 Oct 2026)
BRIEF_DESCRIPTION_CHARS = 400


def _plain_description(text: str) -> str:
    """A picture description as one plain paragraph, without the platform's section labels."""
    text = _DESCRIPTION_LABEL.sub("", text)
    text = re.sub(r"(?m)^\s*[-*]\s+", "", text).replace("**", "")
    return clean(text)


def _brief_description(text: str) -> str:
    """What the model reads where a picture is: the description, or for a long one its scene and the
    words shown in it ("New Solution", "Create")."""
    plain = _plain_description(text)
    if len(plain) <= BRIEF_DESCRIPTION_CHARS:
        return plain
    # the first labelled part is the overall view; without labels, the opening sentences
    pieces = _DESCRIPTION_PART.split(text)
    first = next((body for body in pieces[2::2] if body.strip()), "")
    scene = _plain_description(first)[:300] or plain[:200]
    words = list(dict.fromkeys(w.strip(" ,;:") for w in re.findall(r'["\u201c]([^"\u201d\n]{1,60})["\u201d]', text)))
    words = [w for w in words if w]
    brief = scene.rstrip(".") + "."
    if words:
        brief += " Shows: " + ", ".join(words)
    if len(brief) > BRIEF_DESCRIPTION_CHARS:
        brief = brief[:BRIEF_DESCRIPTION_CHARS].rsplit(" ", 1)[0].rstrip(",") + " ..."
    return brief


def _notes(caption: str, description: str) -> dict[str, str]:
    """A picture's caption (not a bare "Figure") and description, as shown under it."""
    caption = clean(caption)
    return {k: v for k, v in {"caption": "" if caption.lower() in ("figure", "image") else caption,
                              "description": description}.items() if v}


def _image_descriptions(doc: Doc, shown: set[str]) -> tuple[dict[str, str], set[str]]:
    """The web platform ends a document with an "Image Descriptions" chapter: one subsection per
    picture, titled with its file name. A description of a picture the text shows is taken out, to be
    read where the picture is, so that search and citations land on the section that shows it; the
    chapter goes once empty. Returns file name -> description, and the node ids of descriptions left
    in place (pictures the text never shows)."""
    described: dict[str, str] = {}
    unplaced: set[str] = set()
    for nid, s in list(doc.sections.items()):
        kids = [doc.sections[c] for c in s.children]
        if not _DESCRIPTIONS.fullmatch(s.title) or not kids or not all(IMAGE_NAME.fullmatch(k.title) for k in kids):
            continue
        for k in kids:
            if k.title in shown and not k.children:
                described[k.title] = k.own_text  # as written: the full text under the picture, its gist in the text
                s.children.remove(k.node_id)
                del doc.sections[k.node_id]
            else:
                unplaced.add(k.node_id)
        if not s.children and not s.own_text.strip():
            if s.parent is not None:
                doc.sections[s.parent].children.remove(nid)
            del doc.sections[nid]
    return described, unplaced


# UTF-8 read as Latin-1 or Windows-1252 somewhere along the way: "\u00e2\x80\x99" or "\u00e2\u20ac\u2122" for
# a right quote, "\u00c2 " for a no-break space. The Frontier manual's export was full of it, and the model copied it
_MOJIBAKE = re.compile("[\u00c2-\u00f4][\u0080-\u00bf\u0152\u0153\u0160\u0161\u0178\u017d\u017e\u0192"
                       "\u02c6\u02dc\u2013\u2014\u2018-\u201a\u201c-\u201e\u2020-\u2022\u2026\u2030"
                       "\u2039\u203a\u20ac\u2122]{1,3}")


def repair_mojibake(text: str) -> str:
    """The text with doubly-encoded characters put back; text without them is returned unchanged."""
    if not _MOJIBAKE.search(text):
        return text

    def fix(m: re.Match) -> str:
        raw = m.group(0)
        try:
            data = bytes(ord(c) if ord(c) < 256 else c.encode("cp1252")[0] for c in raw)
            return data.decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError, ValueError):
            return raw

    text = _MOJIBAKE.sub(fix, text)
    return re.sub("Â(?=[  ])", "", text)  # a no-break space whose second byte became a space


def load_export(root: Path) -> list[Doc]:
    """A web-platform export as unpacked by bundle.import_export():
    page_index.json, docs/<doc id>/doc.md, docs/<doc id>/images/..."""
    index = _read_json(Path(root) / EXPORT_INDEX)
    skipped_file = Path(root) / SKIPPED
    skipped = set(_read_json(skipped_file)) if skipped_file.is_file() else set()
    entries = [e for e in index.get("documents") or [] if export_doc_id(e) not in skipped]
    # documents are shown by file name; two files of the same name (in different folders) by path
    names = [str(e.get("filename") or "") for e in entries]
    docs = []
    for entry in entries:
        doc_id = export_doc_id(entry)
        folder = Path(root) / "docs" / doc_id
        md = folder / "doc.md"
        if not md.is_file():
            raise KnowledgeBaseError(f"{entry.get('filename')}: its Markdown file is missing")
        text = repair_mojibake(_FRONT_MATTER.sub("", md.read_text(encoding="utf-8-sig"), count=1))
        doc = parse_markdown(text, doc_id, export_title(entry, names))
        shown = {unquote(ref).strip() for sec in doc.sections.values() for _, ref in _IMAGE_REF.findall(sec.own_text)}
        described, unplaced = _image_descriptions(doc, shown)
        for sec in doc.sections.values():  # pictures: shown with answers; the model reads their descriptions
            sec.images = []
            for caption, ref in _IMAGE_REF.findall(sec.own_text):
                image = unquote(ref).strip()
                if IMAGE_NAME.fullmatch(image) and (folder / "images" / image).is_file() and image not in sec.images:
                    sec.images.append(image)
                    sec.image_notes[image] = _notes(caption, _plain_description(described.get(image, "")))
            if sec.node_id in unplaced and (folder / "images" / sec.title).is_file():
                sec.images.append(sec.title)  # described but never placed in the text: shown with its description
                sec.image_notes[sec.title] = _notes("", _plain_description(sec.own_text))
            sec.own_text = _IMAGE_REF.sub(lambda m: _figure(m.group(1), _brief_description(
                described.get(unquote(m.group(2)).strip(), ""))),
                                          sec.own_text)
        doc.assets_dir = folder
        rel = str(entry.get("relative_path") or "")
        doc.meta = {k: v for k, v in {
            "type": str(entry.get("file_type") or Path(name_of(entry)).suffix.lstrip(".")).lower(),
            "folder": rel.rsplit("/", 1)[0] if "/" in rel else "",
            "added": str(entry.get("parsed_at") or "")[:10],
            "pages": str(entry.get("page_count") or ""),
        }.items() if v}
        apply_export_catalog(doc, entry)
        docs.append(doc)
    if not docs:
        raise KnowledgeBaseError("The export lists no documents")
    return docs


def load(root: Path, source_format: str) -> list[Doc]:
    if source_format == "pageindex":
        return load_pageindex(root)
    if source_format == "markdown":
        return load_markdown(root)
    if source_format == "export":
        return load_export(root)
    if source_format == "semkb":  # a sem-ai-kb folder (kb.json, modules/, ...): see kbfolder.py
        from .kbfolder import load_semkb
        return load_semkb(root)
    raise KnowledgeBaseError(f"Unknown knowledge-base format {source_format!r}")
