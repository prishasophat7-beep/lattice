#!/usr/bin/env python3
"""convert_kb.py: turn a KB Manager export into per-document interactive knowledge bases.

    python3 convert_kb.py <export.zip> <out-dir> [--name "KB name"] [--image-names page|figure]

Input: the export the KB Manager web platform produces after its parser has run:
    page_index.json                 the parser's index: one entry per document, with its headings
    files/<name>.bundle.zip         per document: <name>.md, images/figure_NNN.png
    (older exports: files/<name>/<name>.md next to files/<name>/<name>.bundle.zip)

Output:
    README.md                       master index: the documents with their counts, the layout, how to regenerate
    page_index.json                 master page index: the parser's, without download links; each document
                                    points at its folder, each heading at the section file holding it
    <document>/                     one self-contained knowledge base per document:
        AGENTS.md                   agent behaviour: interview first, then a step-by-step walkthrough
        README.md                   setup and use for the document
        index.md                    modules with page ranges and one-line summaries
        kb.json                     modules, sections (text, pictures, pages, summaries), picture descriptions
        needs-review.md             auto-detected gaps (between markers) plus hand-written notes
        validate_kb.py              checks structure and decision tree, walks every interview path
        interaction/decision-tree.json      clarifying questions (auto-generated, "_draft": true)
        references/decision-tree-guide.md   how to refine the draft tree
        modules/<module>/NN-<slug>.md       section files with front matter (id, module, title, pages)
        images/                     pictures (page_NNN_imgK.png, or the parser's figure_NNN.png names)

Rebuilding rewrites modules/, images/, kb.json, index.md, validate_kb.py, page_index.json and the
top README.md; it keeps AGENTS.md, each document's README.md, the decision trees, the guide and the
hand-written part of every needs-review.md.

Standard library only, Python 3.9+.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import re
import shutil
import sys
import unicodedata
import zipfile
from collections import Counter
from datetime import date
from pathlib import Path, PurePosixPath

FORMAT_VERSION = 2
IMAGE_EXT = re.compile(r"\.(png|jpe?g|webp|gif)$", re.IGNORECASE)
FILE_NAME_HEADING = re.compile(r"\.(pdf|docx?|xlsx?|pptx?|csv|txt|json|xml|md)\s*$", re.IGNORECASE)
NUMBERED = re.compile(r"^\s*\d+(?:\.\d+)*\.?\s")
# a sub-heading that is one way of doing its section's task: "2.2.1 Option A: From a folder"
VARIANT = re.compile(r"^(?:\d+(?:\.\d+)*\.?\s+)?((?:option|method|type|mode|way|variant|approach|alternative)\s+[A-Z0-9]{1,3}\b.*)$",
                     re.IGNORECASE)
# codes worth finding a file by: E-217, HX-500, FW-1822, KA-0388, PO-26-0503, R7, X2
CODE = re.compile(r"\b(?=[A-Z0-9-]*\d)(?=[A-Z0-9-]*[A-Z])[A-Z][A-Z0-9]{0,4}(?:-[A-Z0-9]{1,6}){0,2}\b")
STOP = set("""a an the of to in is are was were be been being and or for on with as at by from this that these those it its
into than then there their they them we our you your he she his her not no yes can could should would will may might must
do does did done have has had having if when where which who whom what why how all any each every some such more most other
also only just about over under after before between within without per via etc i e g eg ie page pages section sections
chapter document manual guide policy table figure image picture shows show see use used using new one two three
ask low high get set make click select open first last next step steps note""".split())

# ── small helpers ──────────────────────────────────────────────────────────────────────────────


def slug(text: str, limit: int = 60) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().replace("&", " and ")
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:limit].rstrip("-") or "section"


def without_number(title: str) -> str:
    return re.sub(r"^\s*(?:chapter\s+)?\d+(?:\.\d+)*\.?\s+", "", title, flags=re.IGNORECASE).strip() or title.strip()


def norm(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", without_number(title).lower()).strip()


def pages_label(pages) -> str:
    if not pages:
        return "pages not recorded"
    return f"p. {pages[0]}" if pages[0] == pages[1] else f"pp. {pages[0]}–{pages[1]}"


def readable(file_name: str) -> str:
    name = re.sub(r"\.[a-z0-9]+$", "", file_name, flags=re.IGNORECASE).replace("_", " ").strip()
    return name[:1].upper() + name[1:]


def plain_text(md: str) -> str:
    md = re.sub(r"^!\[[^\]]*\]\([^)]*\)\s*$", "", md, flags=re.MULTILINE)
    md = re.sub(r"^Image path: .*$", "", md, flags=re.MULTILINE)
    md = re.sub(r"^Image description: ", "", md, flags=re.MULTILINE)
    md = re.sub(r"^#{1,6}\s+", "", md, flags=re.MULTILINE)
    md = re.sub(r"\*\*([^*]+)\*\*", r"\1", md)
    md = re.sub(r"(?<!\w)[*_]([^*_\n]+)[*_](?!\w)", r"\1", md)
    md = re.sub(r"`([^`]+)`", r"\1", md)
    md = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", md)
    md = re.sub(r"^\s*\|?\s*-{3,}[-|\s:]*$", "", md, flags=re.MULTILINE)
    md = md.replace("|", " ")
    md = re.sub(r"^\s*[-*]\s+", "", md, flags=re.MULTILINE)
    return re.sub(r"\s+", " ", md).strip()


def whole_sentences(text: str) -> str:
    """The parser cuts summaries at 500 characters, mid-word; keep the sentences that are complete."""
    text = text.strip()
    if not text or text[-1] in ".!?)":
        return text
    cut = max(text.rfind(". "), text.rfind("! "), text.rfind("? "))
    return text[:cut + 1] if cut > 0 else text


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def write_json(path: Path, data) -> None:
    write(path, json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def write_once(path: Path, text: str) -> bool:
    """Hand-maintained files: written only when they do not exist yet."""
    if path.exists():
        return False
    write(path, text)
    return True


AUTO_START = "<!-- auto-detected gaps: rewritten by convert_kb.py on every build -->"
AUTO_END = "<!-- end of auto-detected gaps -->"


def write_needs_review(path: Path, title: str, gaps: list[str]) -> None:
    """Keep what people wrote; rewrite only the block between the markers. A file without the markers
    (written entirely by hand) is left alone."""
    block = f"{AUTO_START}\n\n" + ("\n".join(f"- {g}" for g in gaps) if gaps else "- none found") + f"\n\n{AUTO_END}"
    if not path.exists():
        write(path, f"# Needs review: {title}\n\nKnown gaps. Write below what people find; the block between the "
                    f"markers lists what the converter detected (for maintainers) and is rewritten on every build.\n\n{block}\n")
        return
    text = path.read_text(encoding="utf-8")
    a, b = text.find(AUTO_START), text.find(AUTO_END)
    if a >= 0 and b > a:
        write(path, text[:a] + block + text[b + len(AUTO_END):])

# ── reading the export ─────────────────────────────────────────────────────────────────────────


class Export:
    def __init__(self, zip_path: Path):
        self.zf = zipfile.ZipFile(zip_path)
        self.names = set(self.zf.namelist())
        if "page_index.json" not in self.names:
            raise SystemExit(f"{zip_path}: no page_index.json; is this a KB Manager export?")
        self.index = json.loads(self.zf.read("page_index.json").decode("utf-8-sig"))

    def document(self, entry: dict) -> tuple[str | None, dict[str, bytes]]:
        """The file's Markdown and its pictures (from its bundle zip, or the loose .md of older exports)."""
        md, images = None, {}
        bundle = entry.get("bundle_zip") or (entry.get("files") or {}).get("bundle") or next(
            (n for n in self.names if n.endswith(".bundle.zip") and entry.get("filename", "\0") in n), None)
        if bundle and bundle in self.names:
            try:
                inner = zipfile.ZipFile(io.BytesIO(self.zf.read(bundle)))
                for n in inner.namelist():
                    if n.lower().endswith(".md") and "/" not in n and md is None:
                        md = inner.read(n).decode("utf-8-sig", "replace")
                    elif n.startswith("images/") and IMAGE_EXT.search(n):
                        images[PurePosixPath(n).name] = inner.read(n)
            except zipfile.BadZipFile:
                pass
        if md is None:
            loose = (entry.get("files") or {}).get("markdown") or next(
                (n for n in self.names if n.endswith(f"{entry.get('filename', '')}.md")), None)
            if loose and loose in self.names:
                md = self.zf.read(loose).decode("utf-8-sig", "replace")
        if md is not None:
            md = re.sub(r"\A---\n.*?\n---\n", "", md, count=1, flags=re.DOTALL)
        return md, images

# ── splitting one file into modules and sections ───────────────────────────────────────────────


def take_image_descriptions(md: str) -> tuple[str, dict[str, str]]:
    """The "## Image Descriptions" chapter some exports end with (one "### figure_000.png" each)."""
    m = re.search(r"^#{1,3}\s+Image Descriptions\s*$", md, flags=re.IGNORECASE | re.MULTILINE)
    if not m:
        return md, {}
    described = {}
    for part in re.split(r"^#{2,4}\s+", md[m.end():], flags=re.MULTILINE)[1:]:
        name, _, text = part.partition("\n")
        if IMAGE_EXT.search(name.strip()) and text.strip():
            described[name.strip()] = re.sub(r"\s+", " ", text).strip()
    return md[:m.start()].rstrip() + "\n", described


def headings(lines: list[str]) -> list[tuple[int, str, int]]:
    out, fence = [], False
    for i, line in enumerate(lines):
        if re.match(r"^\s*```", line):
            fence = not fence
            continue
        h = None if fence else re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if h:
            out.append((len(h.group(1)), h.group(2).strip(), i))
    return out


def demote(text: str, base: int) -> str:
    """Sub-headings inside a section, re-levelled so the section's own heading is "#"."""
    return re.sub(r"^(#{1,6})\s+", lambda m: "#" * max(2, len(m.group(1)) - base + 1) + " ", text, flags=re.MULTILINE).strip()


def split_document(file_name: str, md: str) -> list[dict]:
    """[{title, sections: [{title, body}], whole}]: a user manual's chapters as modules, or a short
    document (a guide, a report, a policy) as one module with its chapters as sections."""
    lines = md.split("\n")
    hs = headings(lines)
    if hs and (hs[0][1] == file_name or FILE_NAME_HEADING.search(hs[0][1])):
        hs = hs[1:]  # "# InspectAI_Studio_4_User_Guide.pdf": the file's name, not content
    preamble = None
    # one heading above all others ("# TaskFlow Pro 4.2 - User Manual") is the title: split below it
    while len(hs) > 1 and sum(1 for h in hs if h[0] == hs[0][0]) == 1 and all(h[0] >= hs[0][0] for h in hs):
        body = "\n".join(lines[hs[0][2] + 1:hs[1][2]]).strip()
        if body:
            preamble = {"title": hs[0][1], "body": body}
        hs = hs[1:]
    if not hs:
        return [{"title": readable(file_name), "sections": [{"title": readable(file_name), "body": md.strip()}], "whole": True}]
    top = min(h[0] for h in hs)
    deeper = [h[0] for h in hs if h[0] > top]
    second = min(deeper) if deeper else None

    def body(a: int, b: int) -> str:
        return "\n".join(lines[a + 1:b]).strip()

    def end_at(i: int, level: int) -> int:
        return next((hs[j][2] for j in range(i + 1, len(hs)) if hs[j][0] <= level), len(lines))

    def first_child(i: int) -> int:
        return hs[i + 1][2] if i + 1 < len(hs) and hs[i + 1][0] > hs[i][0] else end_at(i, hs[i][0])

    def subs(i: int) -> list[int]:
        out = []
        for j in range(i + 1, len(hs)):
            if hs[j][0] <= top:
                break
            if hs[j][0] == second:
                out.append(j)
        return out

    tops = [i for i, h in enumerate(hs) if h[0] == top]
    by_chapter = len(tops) > 1  # every top-level chapter is a module
    if not by_chapter:
        sections = []
        lead = re.sub(r"^#{1,6}\s+[^\n]*" + FILE_NAME_HEADING.pattern, "", body(-1, hs[0][2]),
                      flags=re.IGNORECASE | re.MULTILINE).strip()
        if preamble:
            sections.append(preamble)
        elif lead:
            sections.append({"title": readable(file_name), "body": lead})
        for i in tops:
            if second is None or not subs(i):
                sections.append({"title": hs[i][1], "body": demote(body(hs[i][2], end_at(i, top)), top)})
                continue
            intro = body(hs[i][2], first_child(i))
            if intro:
                sections.append({"title": hs[i][1], "body": intro})
            for j in subs(i):
                sections.append({"title": hs[j][1], "body": demote(body(hs[j][2], end_at(j, second)), second)})
        return [{"title": readable(file_name), "sections": [s for s in sections if s["body"]], "whole": True}]
    modules = []
    if preamble:
        modules.append({"title": "Overview", "sections": [preamble], "whole": False})
    for i in tops:
        sections = []
        intro = body(hs[i][2], first_child(i))
        if intro:
            sections.append({"title": hs[i][1], "body": intro})
        for j in subs(i):
            sections.append({"title": hs[j][1], "body": demote(body(hs[j][2], end_at(j, second)), second)})
        if sections:
            modules.append({"title": hs[i][1], "sections": sections, "whole": False})
    # an unnumbered first chapter ahead of numbered ones is the manual's introduction
    if len(modules) > 1 and not NUMBERED.match(modules[0]["title"]) and any(NUMBERED.match(m["title"]) for m in modules[1:]):
        modules[0]["title"] = "Overview"
    return modules

# ── one document ──────────────────────────────────────────────────────────────────────────────


class DocBuild:
    def __init__(self, entry: dict, md: str, images: dict[str, bytes], out: Path, doc_id: str, image_names: str):
        self.entry, self.images, self.dir, self.id = entry, images, out / doc_id, doc_id
        self.image_names = image_names
        self.name = str(entry.get("filename") or doc_id)
        self.title = readable(self.name)
        self.md, self.described = take_image_descriptions(md)
        self.gaps: list[str] = []
        self.modules: list[dict] = []
        self.sections: list[dict] = []
        self.pictures: list[dict] = []
        self.variants: list[dict] = []
        self._per_page: Counter = Counter()
        # the parser's records for each heading, taken in document order: "2.2 Importing Images" and
        # "3.2 Importing Images" have the same title, so a lookup by title alone mixes them up
        self._records: dict[str, list[dict]] = {}
        for rec in entry.get("sections") or []:
            self._records.setdefault(norm(str(rec.get("title") or "")), []).append(rec)

    def record_for(self, title: str) -> dict | None:
        found = self._records.get(norm(title))
        if not found:
            return None
        num = (re.match(r"^\s*(\d+(?:\.\d+)*)", title) or [None, None])[1]
        at = next((k for k, r in enumerate(found) if num and (r.get("section_number") == num
                                                                 or str(r.get("title", "")).startswith(num + " "))), 0)
        return found.pop(at)

    def build(self) -> None:
        shutil.rmtree(self.dir / "modules", ignore_errors=True)
        shutil.rmtree(self.dir / "images", ignore_errors=True)
        (self.dir / "images").mkdir(parents=True, exist_ok=True)
        used: set[str] = set()
        for mod in split_document(self.name, self.md):
            base = slug(without_number(mod["title"]))
            mid, k = base, 2
            while mid in used:
                mid, k = f"{base}-{k}", k + 1
            used.add(mid)
            rows = [self.section(mid, mod, n, sec) for n, sec in enumerate(mod["sections"], 1)]
            known = [r["pages"] for r in rows if r["pages"]]
            pages = [min(p[0] for p in known), max(p[1] for p in known)] if known else None
            summary = next((r["summary"] for r in rows if r.get("summary") and norm(r["title"]) == norm(mod["title"])), None)
            if summary is None and rows and norm(rows[0]["title"]) != norm(mod["title"]):
                # a chapter with no text of its own ("4 Common Issues" straight into 4.1): its record
                # still has the chapter's summary, and its heading points at the first section
                rec = self.record_for(mod["title"])
                if rec is not None:
                    rec["_section"] = rows[0]["id"]
                    summary = rec.get("summary") if rec.get("summary") and norm(rec["summary"]) != norm(mod["title"]) else None
            summary = summary or next((r["summary"] for r in rows if r.get("summary")), None)
            if mod["whole"] and self.entry.get("doc_summary"):
                summary = whole_sentences(self.entry["doc_summary"])
            self.modules.append({"id": mid, "title": without_number(mod["title"]), "heading": mod["title"], "pages": pages,
                                 "sections": [r["id"] for r in rows], **({"summary": summary} if summary else {})})
        # the short title block above the first chapter has no record of its own: it is on the first page
        for s in self.sections:
            if s["pages"] is None and any(x["pages"] for x in self.sections):
                first = next(x["pages"] for x in self.sections if x["pages"])
                if self.sections.index(s) == 0:
                    s["pages"] = [first[0], first[0]]
                    self.rewrite_pages(s)
        if not any(s["pages"] for s in self.sections):
            self.gaps.append("the parser gave no page numbers for this document, so citations name the module and section")

    def rewrite_pages(self, s: dict) -> None:
        path = self.dir / s["file"]
        text = path.read_text(encoding="utf-8")
        text = text.replace("pages: null", f"pages: {json.dumps(s['pages'])}", 1).replace("_Manual pages not recorded_", f"_Manual {pages_label(s['pages'])}_", 1)
        path.write_text(text, encoding="utf-8")
        for m in self.modules:
            if s["id"] in m["sections"] and not m["pages"]:
                m["pages"] = s["pages"]

    def section(self, mid: str, mod: dict, n: int, sec: dict) -> dict:
        nn = f"{n:02d}"
        sslug = slug(without_number(sec["title"]))
        rel = f"modules/{mid}/{nn}-{sslug}.md"
        sid = f"{mid}/{nn}-{sslug}"
        rec = self.record_for(sec["title"])
        pages = [rec["start_page"], rec.get("end_page") or rec["start_page"]] if rec and rec.get("start_page") is not None else None
        summary = rec.get("summary") if rec and rec.get("summary") and norm(rec["summary"]) != norm(sec["title"]) else None
        pictures = []
        # a caption right under the picture ("*Figure 1: R7 front panel ...*") stands in when the export
        # has no description for it
        captions = {m.group(1): m.group(2).strip() for m in re.finditer(
            r"!\[[^\]]*\]\((?:\./)?images/([^)\s]+)\)\s*\n\s*[*_]((?:figure|fig\.|image|screenshot)[^*_\n]+)[*_]", sec["body"], flags=re.IGNORECASE)}

        def picture(m: re.Match) -> str:
            alt, name = m.group(1), m.group(2)
            data = self.images.get(name)
            if data is None:
                self.gaps.append(f"**{rel}**: picture `{name}` is referenced but missing from the export")
                return m.group(0)
            page = pages[0] if pages else 0
            if self.image_names == "figure":
                target = name
            else:
                self._per_page[page] += 1
                target = f"page_{page:03d}_img{self._per_page[page]}{Path(name).suffix.lower() or '.png'}"
            (self.dir / "images" / target).write_bytes(data)
            pictures.append(f"images/{target}")
            what, how = self.described.get(name), "description"
            if not what and captions.get(name):
                what, how = captions[name], "caption"
                self.gaps.append(f"**{rel}** ({pages_label(pages)}): picture {target} has only its caption as description; "
                                 "questions about details in it cannot be answered")
            elif not what:
                how = None
                self.gaps.append(f"**{rel}** ({pages_label(pages)}): picture {target} has no description")
            if not pages and self.image_names != "figure":
                self.gaps.append(f"**{rel}**: picture {target} is named page 000 (no page number)")
            self.pictures.append({"path": f"images/{target}", "original": name, "section": sid, "page": pages[0] if pages else None,
                                  "description": what, "description_from": how})
            return (f"![screenshot {'p.' + str(page) if pages else (alt or 'figure')}](../../images/{target})\n\n"
                    f"Image path: images/{target}" + (f"\n\nImage description: {what}" if what else ""))

        text = re.sub(r"!\[([^\]]*)\]\((?:\./)?images/([^)\s]+)\)", picture, sec["body"])
        if re.search(r"\b(TBD|TODO|XXX|\?\?\?)\b", text):
            line = re.search(r"[^\n]*\b(TBD|TODO|XXX|\?\?\?)\b[^\n]*", text).group(0).strip()[:120]
            self.gaps.append(f"**{rel}** ({pages_label(pages)}): value not documented (TBD): `{line}`")
        if pictures and len(plain_text(text)) < 80:
            self.gaps.append(f"**{rel}** ({pages_label(pages)}): section is only a figure; its content depends on the picture's description")
        ways = [VARIANT.match(m.group(1).strip()).group(1) for m in re.finditer(r"^#{2,6}\s+(.+)$", text, flags=re.MULTILINE)
                if VARIANT.match(m.group(1).strip())]
        if len(ways) >= 2:
            self.variants.append({"module": mid, "section": sid, "title": sec["title"], "ways": ways})
        front = "\n".join(["---", f"id: {sid}", f"module: {mid}", f"title: {json.dumps(sec['title'], ensure_ascii=False)}",
                           f"pages: {json.dumps(pages)}", "---"])
        write(self.dir / rel, f"{front}\n\n# {sec['title']}\n\n_Manual {pages_label(pages)}_\n\n{text}\n")
        row = {"id": sid, "module": mid, "title": sec["title"], "pages": pages, "file": rel, "images": pictures,
               **({"summary": summary} if summary else {}), "text": plain_text(text)}
        self.sections.append(row)
        if rec is not None:
            rec["_section"] = sid
        return row

    # ── this document's files ──

    def kb_json(self) -> dict:
        return {
            "format_version": FORMAT_VERSION,
            "generator": "convert_kb.py",
            "document": {"id": self.id, "title": self.title, "filename": self.name,
                         "type": str(self.entry.get("file_type") or Path(self.name).suffix).lstrip(".").lower(),
                         "pages": self.entry.get("page_count"),
                         "summary": whole_sentences(self.entry.get("doc_summary") or "")},
            "modules": self.modules,
            "sections": self.sections,
            "images": self.pictures,
        }

    def tree(self) -> dict:
        """A draft interview from the headings: which part of the document, then which way of doing a
        step when the document describes several. Review it, then remove "_draft"."""
        nodes, results = {}, {}

        def module_target(m: dict) -> str:
            results[m["id"]] = {"modules": [m["id"]], "summary": m["title"], "start_hint": m["sections"][0], "ask_next": []}
            ways = [v for v in self.variants if v["module"] == m["id"]]
            for k, v in enumerate(ways, 1):
                opts = []
                for j, w in enumerate(v["ways"], 1):
                    rid = f"{v['section']}--{j}"
                    results[rid] = {"modules": [m["id"]], "summary": f"{m['title']}: {without_number(v['title'])}, {w}",
                                    "start_hint": v["section"], "way": w, "ask_next": []}
                    opts.append({"label": w, "next": f"result:{rid}"})
                nodes[f"{m['id']}--ways-{k}"] = {
                    "ask": f'The manual describes "{without_number(v["title"])}" in {len(v["ways"])} ways. Which one are you using?',
                    "options": opts + [{"label": "Not sure", "next": f"result:{m['id']}"}]}
            if not ways:
                return f"result:{m['id']}"
            if len(ways) == 1:
                return f"{m['id']}--ways-1"
            nodes[f"{m['id']}--topic"] = {"ask": f"What are you working on in {m['title']}?",
                                          "options": [{"label": without_number(v["title"]), "next": f"{m['id']}--ways-{k}"}
                                                      for k, v in enumerate(ways, 1)] + [{"label": f"Something else in {m['title']}", "next": f"result:{m['id']}"}]}
            return f"{m['id']}--topic"

        mods = self.modules
        if len(mods) <= 8:
            nodes["start"] = {"ask": f"Which part of {self.title} is your question about?",
                              "options": [{"label": m["title"], "next": module_target(m)} for m in mods] + [{"label": "Something else", "next": "open"}]}
        else:  # too many modules for one question: ask the group first, in document order
            size = math.ceil(len(mods) / math.ceil(len(mods) / 7))
            groups = [mods[i:i + size] for i in range(0, len(mods), size)]
            opts = []
            for g, part in enumerate(groups, 1):
                nodes[f"group-{g}"] = {"ask": "Which of these is it?",
                                       "options": [{"label": m["title"], "next": module_target(m)} for m in part] + [{"label": "None of these", "next": "start"}]}
                label = part[0]["title"] if len(part) == 1 else f"{part[0]['title']} … {part[-1]['title']}"
                opts.append({"label": label, "next": f"group-{g}"})
            nodes["start"] = {"ask": f"Which part of {self.title} is your question about?", "options": opts + [{"label": "Something else", "next": "open"}]}
        return {"_draft": True, "start": "start", "nodes": nodes, "resolve": {}, "results": results}

    def index_md(self) -> str:
        lines = [f"# {self.title}", "", f"Source file: `{self.name}`" + (f" · {self.entry['page_count']} pages" if self.entry.get("page_count") else ""), ""]
        if self.entry.get("doc_summary"):
            lines += [whole_sentences(self.entry["doc_summary"]), ""]
        lines += ["| Module | Pages | Summary |", "| --- | --- | --- |"]
        for m in self.modules:
            first = next(s for s in self.sections if s["id"] == m["sections"][0])
            lines.append(f"| [{m['title']}]({first['file']}) | {pages_label(m['pages'])} | {(m.get('summary') or '').replace('|', '/')} |")
        lines += ["", "## Sections", ""]
        for m in self.modules:
            lines.append(f"**{m['title']}**")
            lines += [f"- [{s['title']}]({s['file']}) — {pages_label(s['pages'])}" for s in self.sections if s["module"] == m["id"]]
            lines.append("")
        return "\n".join(lines)

    def write_files(self, kb_name: str, others: list[str]) -> None:
        write_json(self.dir / "kb.json", self.kb_json())
        write(self.dir / "index.md", self.index_md())
        write(self.dir / "validate_kb.py", VALIDATOR)
        write_once(self.dir / "references" / "decision-tree-guide.md", TREE_GUIDE)
        tree_path = self.dir / "interaction" / "decision-tree.json"
        write_once(tree_path, json.dumps(self.tree(), indent=2, ensure_ascii=False) + "\n")
        try:
            if json.loads(tree_path.read_text(encoding="utf-8")).get("_draft"):
                self.gaps.append("interaction/decision-tree.json is an auto-generated draft (`_draft: true`); review it "
                                 "(see references/decision-tree-guide.md)")
        except json.JSONDecodeError:
            self.gaps.append("interaction/decision-tree.json is not valid JSON")
        write_once(self.dir / "AGENTS.md", agents_template(self, kb_name, others))
        write_once(self.dir / "README.md", doc_readme(self))
        write_needs_review(self.dir / "needs-review.md", self.title, list(dict.fromkeys(self.gaps)))

# ── templates ──────────────────────────────────────────────────────────────────────────────────


def agents_template(d: DocBuild, kb_name: str, others: list[str]) -> str:
    mods = [m["title"] for m in d.modules if m["title"] != "Overview"][:4] or [d.title]
    listed = ", ".join(mods[:-1]) + (" or " if len(mods) > 1 else "") + mods[-1]
    v = d.variants[0] if d.variants else None
    example_ways = (f"""
> **User:** How do I {without_number(v['title']).lower()}?
> **You:** The manual describes {len(v['ways'])} ways: {', '.join(v['ways'][:3])}. Which one are you using?
> **User:** {v['ways'][0]}.
> **You:** {v['ways'][0]}, then. Step 1: ...
""" if v else "")
    other_docs = ("\n## Other documents\n\nThis document is one of several in **" + kb_name + "**: " + ", ".join(others) +
                  ". `../README.md` lists them and `../page_index.json` has their outlines. When a question is about one of "
                  "them, or needs facts from several (a cause in one, the fix in another), look each part up in the document "
                  "that covers it and cite each.\n") if others else ""
    return f"""# {d.title} assistant

You are an interactive guide for **{d.title}**{(': ' + whole_sentences(d.entry.get('doc_summary') or '').split('. ')[0].rstrip('.')) if d.entry.get('doc_summary') else ''}.

You are a guide, not a search box: **interview first, then walk the user through it step by step.**

## How to find things

1. `index.md` lists the modules with their pages and a one-line summary; `kb.json` has every
   section's text, pages and pictures. Pick the module, then the section.
2. `modules/<module>/NN-<section>.md` holds a section's full text. Read it whole before quoting
   figures from it; a table may continue past a search excerpt.
3. `interaction/decision-tree.json` has the clarifying questions to ask for vague requests.
{other_docs}
## Core behaviour: clarify before answering

1. When a request is vague → ask 1-2 clarifying questions with 2-5 options + "something else"
2. Facts that change the answer → establish before giving steps
3. Reuse what the user already told you. Never re-ask.
4. After the interview → say what you understood in one line → ask follow-ups → proceed

## Interview tone

- Friendly and brief: one question at a time, options as a short list, no lecture before the question.
- Use the manual's own names for things, so the user recognises them.
- When the user's answer already settles a question, skip it and say what you understood.

Example:

> **User:** I need some help with {d.title}.
> **You:** Sure. Which part is it about: {listed}? Or something else?
> **User:** {mods[0]}.
> **You:** Got it: {mods[0]}. What are you trying to do?
{example_ways}
## Core behaviour: guided walkthrough

- Present one step at a time → confirm before continuing
- Offer the next logical step when a step finishes
- If stuck → ask what they see on screen, which step, what they clicked

## Images

- A picture's `Image description:` line says what it shows; answer questions about the picture from it
- Include the screenshot path as `Screenshot: <path>` when relevant; pick the one for the current step

## Grounding rules

- Cite the source: *(Module › Section, p. N)*; without a page number, *(Module › Section)*
- If the manual doesn't say → say so, then answer from general knowledge if that helps, marked
  "From general knowledge". Never invent UI labels, values or defaults and present them as the manual's.
- Check needs-review.md before asserting values
- Work out figures (totals, percentages, penalties) step by step from the values the sections give

## Maintenance

- modules/, images/, kb.json and index.md are regenerated by convert_kb.py (edits there are overwritten)
- needs-review.md is hand-maintained; only its auto-detected block is rewritten by a build
- Edit interaction/decision-tree.json to change the clarifying questions (references/decision-tree-guide.md),
  then run `python validate_kb.py`
"""


def doc_readme(d: DocBuild) -> str:
    return f"""# {d.title}

Interactive knowledge base for `{d.name}`, generated by `convert_kb.py` from the KB Manager export.

| File | Purpose |
| --- | --- |
| `AGENTS.md` | How the assistant behaves: interview first, then a step-by-step walkthrough |
| `index.md` | Modules with page ranges and one-line summaries |
| `kb.json` | Modules, sections (text, pictures, pages, summaries) and picture descriptions |
| `needs-review.md` | Known gaps: hand-written notes plus an auto-detected block |
| `interaction/decision-tree.json` | Clarifying questions; options lead to results (modules and sections) |
| `references/decision-tree-guide.md` | How to refine the draft decision tree |
| `modules/<module>/NN-<section>.md` | Section text with front matter (id, module, title, pages) |
| `images/` | The document's pictures; each one's description sits beside it in its section |
| `validate_kb.py` | Checks the structure and the decision tree, and walks every interview path |

Use: point an agent at this folder with `AGENTS.md` as its instructions, or import the knowledge
base (the parent folder, zipped) in Lattice. After editing, run `python validate_kb.py`.
"""


def top_readme(kb_name: str, description: str, docs: list[DocBuild], skipped: list[str], export_name: str) -> str:
    total = lambda key: sum(len(getattr(d, key)) for d in docs)  # noqa: E731
    rows = []
    for d in docs:
        try:
            t = json.loads((d.dir / "interaction" / "decision-tree.json").read_text(encoding="utf-8"))
            nn, nr = len(t.get("nodes", {})), len(t.get("results", {}))
            tree = f"{nn} node{'s' * (nn != 1)} → {nr} result{'s' * (nr != 1)}"
            status = "Draft" if t.get("_draft") else "Reviewed"
        except (OSError, json.JSONDecodeError):
            tree, status = "invalid", "Fix"
        rows.append(f"| [{d.title}]({d.id}/index.md) | `{d.id}/` | {len(d.modules)} | {len(d.sections)} | {len(d.pictures)} | {tree} | {status} |")
    return f"""# {kb_name}

{description}

{len(docs)} documents. Each folder is a self-contained interactive knowledge base with the same layout;
`page_index.json` is the master index of all of them (the parser's outline of every document, each
heading pointing at the section file that holds it).

| Document | Folder | Modules | Sections | Images | Decision tree | Status |
| --- | --- | --- | --- | --- | --- | --- |
{chr(10).join(rows)}
| **Total** | | {total('modules')} | {total('sections')} | {total('pictures')} | | |
{(chr(10) + 'Left out (the export has no text for them): ' + ', '.join(skipped) + chr(10)) if skipped else ''}
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
python convert_kb.py {export_name} <this-folder>
```

Regenerating rewrites `modules/`, `images/`, `kb.json`, `index.md`, `page_index.json` and this README;
it keeps `AGENTS.md`, each document's `README.md`, the decision trees, the decision-tree guide and
the hand-written part of each `needs-review.md`.
"""


TREE_GUIDE = """# Refining the decision tree

`interaction/decision-tree.json` is the interview the assistant runs when a request is vague. The
converter writes a **draft** (`"_draft": true`) from the headings: which part of the document, and,
where the manual describes a step in several ways ("Option A / Option B", "Method 1 / Method 2"),
which way. A person who knows the product should turn it into the real interview.

## Shape

```json
{
  "_draft": true,
  "start": "start",
  "nodes": {
    "start": {
      "ask": "What are you trying to do?",
      "options": [
        {"label": "Train a model", "next": "train-type"},
        {"label": "Something else", "next": "open"}
      ]
    }
  },
  "resolve": {},
  "results": {
    "train-classification": {
      "modules": ["image-classification"],
      "summary": "Training an image-classification model",
      "start_hint": "image-classification/03-training",
      "ask_next": []
    }
  }
}
```

- **nodes**: `ask` is the question, as the assistant should say it; `options` are 2-5 answers plus
  "Something else". `next` is another node id, `result:<id>`, or `open` (answer freely).
- **results**: what the interview settled. `modules` (folder names under `modules/`) limit where to
  look; `start_hint` is the section to begin with; `ask_next` lists follow-up questions to ask
  before giving steps.
- **resolve** (optional): variables to fill from earlier answers, e.g.
  `"resolve_keys": ["task", "variant"]` with results keyed `"<task>|<variant>"`.

## Making it good

1. Start from the user's goal ("What are you trying to do?"), not the manual's chapter list.
2. Ask only questions whose answer changes the steps (project type, method, product model).
3. Use the manual's own words in options, so users recognise them.
4. Keep it shallow: two or three questions to a result.
5. Every node needs a way out ("Something else" → `open`).
6. When it is right, remove `"_draft": true` and run `python validate_kb.py`; it checks every `next`
   and `modules` reference and prints every path through the interview.
"""


VALIDATOR = '''#!/usr/bin/env python3
"""validate_kb.py: check this document's knowledge base (generated by convert_kb.py).

    python validate_kb.py            structure + decision tree + a summary of the interview paths
    python validate_kb.py --paths    also print every path through the interview

Exits 1 when an error is found. Standard library only.
"""
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
errors, warnings = [], []


def err(msg):
    errors.append(msg)


def front_matter(text):
    m = re.match(r"---\\n(.*?)\\n---\\n", text, re.DOTALL)
    if not m:
        return None
    out = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip()
    return out


def check_structure():
    for name in ("AGENTS.md", "README.md", "index.md", "kb.json", "needs-review.md", "interaction/decision-tree.json"):
        if not (HERE / name).is_file():
            err(f"missing {name}")
    try:
        kb = json.loads((HERE / "kb.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        err(f"kb.json unreadable: {e}")
        return None
    modules = {m["id"] for m in kb.get("modules", [])}
    ids = set()
    referenced = set()
    for s in kb.get("sections", []):
        ids.add(s["id"])
        if s["module"] not in modules:
            err(f"section {s['id']}: unknown module {s['module']}")
        path = HERE / s["file"]
        if not path.is_file():
            err(f"section {s['id']}: file {s['file']} missing")
            continue
        fm = front_matter(path.read_text(encoding="utf-8"))
        if fm is None:
            err(f"{s['file']}: no front matter")
        else:
            for key in ("id", "module", "title", "pages"):
                if key not in fm:
                    err(f"{s['file']}: front matter has no {key}")
            if fm.get("id") != s["id"]:
                err(f"{s['file']}: front matter id {fm.get('id')} != kb.json id {s['id']}")
        for img in s.get("images", []):
            referenced.add(img)
            if not (HERE / img).is_file():
                err(f"section {s['id']}: picture {img} missing")
        if not s.get("text"):
            warnings.append(f"section {s['id']} has no text")
    for m in kb.get("modules", []):
        for sid in m.get("sections", []):
            if sid not in ids:
                err(f"module {m['id']}: unknown section {sid}")
    on_disk = {f"images/{p.name}" for p in (HERE / "images").glob("*") if p.is_file()} if (HERE / "images").is_dir() else set()
    for extra in sorted(on_disk - referenced):
        warnings.append(f"{extra} is not used by any section")
    for p in kb.get("images", []):
        if not p.get("description"):
            warnings.append(f"{p['path']} has no description")
    return kb


def check_tree(kb):
    try:
        tree = json.loads((HERE / "interaction" / "decision-tree.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        err(f"decision-tree.json unreadable: {e}")
        return None
    nodes, results = tree.get("nodes", {}), tree.get("results", {})
    modules = {m["id"] for m in kb.get("modules", [])} if kb else set()
    sections = {s["id"] for s in kb.get("sections", [])} if kb else set()
    if tree.get("start") not in nodes:
        err(f"decision tree: start node {tree.get('start')!r} does not exist")
    for nid, node in nodes.items():
        if not node.get("ask"):
            err(f"node {nid}: no question (ask)")
        opts = node.get("options", [])
        if len(opts) < 2:
            err(f"node {nid}: needs at least 2 options")
        for o in opts:
            nxt = o.get("next", "")
            if nxt == "open" or nxt in nodes or nxt.startswith("file:"):
                continue
            if nxt == "resolve":  # the result comes from the variables the answers set (tree "resolve")
                if not tree.get("resolve"):
                    err(f"node {nid}, option {o.get('label')!r}: next is resolve but the tree has no resolve table")
                continue
            if nxt.startswith("result:"):
                if nxt[7:] not in results:
                    err(f"node {nid}, option {o.get('label')!r}: unknown result {nxt[7:]}")
            else:
                err(f"node {nid}, option {o.get('label')!r}: unknown next {nxt!r}")
    for key, rid in (tree.get("resolve") or {}).items():
        if rid not in results:
            err(f"resolve {key!r}: unknown result {rid}")
    for rid, r in results.items():
        for m in r.get("modules", []):
            if kb and m not in modules:
                err(f"result {rid}: unknown module {m}")
        hint = r.get("start_hint")
        if hint and kb and hint not in sections and "/" in hint:
            warnings.append(f"result {rid}: start_hint {hint} is not a section id")
    return tree


def simulate(tree, show):
    """Walk every answer path from the start; report results never reached and loops."""
    nodes, results = tree.get("nodes", {}), tree.get("results", {})
    paths, reached, seen_nodes = [], set(), set()

    def walk(nid, trail, on_path):
        if len(on_path) > 12:
            err("decision tree: a path is more than 12 questions deep")
            return
        seen_nodes.add(nid)
        node = nodes[nid]
        for o in node.get("options", []):
            nxt = o.get("next", "")
            step = trail + [f"{node.get('ask')} → {o.get('label')}"]
            if nxt in nodes:
                if nxt in on_path:  # "None of these" back to an earlier question
                    paths.append((step, f"(back to {nxt})"))
                else:
                    walk(nxt, step, on_path | {nxt})
            elif nxt == "resolve":
                reached.update(v for v in (tree.get("resolve") or {}).values())
                paths.append((step, "result chosen by the answers (resolve table)"))
            elif nxt.startswith("result:"):
                reached.add(nxt[7:])
                r = results.get(nxt[7:], {})
                paths.append((step, f"result {nxt[7:]}: {r.get('summary', '')} [modules: {', '.join(r.get('modules', []))}]"))
            else:
                paths.append((step, nxt))

    if tree.get("start") in nodes:
        walk(tree["start"], [], {tree["start"]})
    for nid in nodes:
        if nid not in seen_nodes:
            warnings.append(f"node {nid} is never reached")
    for rid in results:
        if rid not in reached:
            warnings.append(f"result {rid} is never reached")
    depth = max((len(p[0]) for p in paths), default=0)
    print(f"interview: {len(nodes)} questions, {len(results)} results, {len(paths)} paths, longest {depth} questions"
          + ("  (draft)" if tree.get("_draft") else ""))
    if show:
        for step, end in paths:
            print("  " + "  /  ".join(step) + f"  ⇒ {end}")


def main():
    kb = check_structure()
    tree = check_tree(kb)
    if kb:
        print(f"structure: {len(kb.get('modules', []))} modules, {len(kb.get('sections', []))} sections, "
              f"{len(kb.get('images', []))} pictures")
    if tree:
        simulate(tree, "--paths" in sys.argv)
    for w in warnings:
        print(f"warning: {w}")
    for e in errors:
        print(f"ERROR: {e}")
    print("OK" if not errors else f"{len(errors)} error(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
'''

# ── the knowledge base ─────────────────────────────────────────────────────────────────────────


def topics(docs: list[DocBuild], per_doc: int = 8) -> dict[str, list[str]]:
    """Words that tell one document from the others (TF-IDF over section titles and text)."""
    counts = {}
    for d in docs:
        words = []
        for s in d.sections:
            words += [w for w in re.findall(r"[a-z][a-z-]{2,}", ((without_number(s["title"]) + " ") * 3 + s["text"]).lower()) if w not in STOP]
        counts[d.id] = Counter(words)
    n = len(counts)
    df = Counter(w for c in counts.values() for w in c)
    out = {}
    for did, c in counts.items():
        total = sum(c.values()) or 1
        score = {w: (k / total) * math.log(1 + n / df[w]) for w, k in c.items() if k >= 2}
        out[did] = [w for w, _ in sorted(score.items(), key=lambda x: -x[1])[:per_doc]]
    return out


def codes_of(d: DocBuild, limit: int = 15) -> list[str]:
    c = Counter(m.group(0) for s in d.sections for m in CODE.finditer(f"{s['title']} {s['text']}"))
    c = Counter({k: v for k, v in c.items() if len(k) >= 2 and not re.fullmatch(r"(Q[1-4]|H[12])", k)})
    return [k for k, _ in c.most_common(limit)]


def relations(docs: list[DocBuild], codes: dict[str, list[str]]) -> dict[str, list[dict]]:
    """How documents relate: one names another or shares its product codes."""
    out: dict[str, list[dict]] = {d.id: [] for d in docs}
    for a in docs:
        text = " ".join(s["text"] for s in a.sections).lower()
        for b in docs:
            if a is b:
                continue
            shared = sorted(set(codes[a.id]) & set(codes[b.id]))
            if b.title.lower() in text or b.name.lower() in text:
                out[a.id].append({"document": b.id, "relation": "mentions"})
            elif shared:
                out[a.id].append({"document": b.id, "relation": "shares codes", "codes": shared[:5]})
    return out


def master_page_index(index: dict, docs: list[DocBuild], by_entry: dict[int, DocBuild]) -> dict:
    """The parser's page_index.json without download links; each document points at its folder and
    each heading at the section file that holds it (headings inside a section point at that section)."""
    drop = ("files", "bundle_zip", "download_url", "bundle_url", "download_link")
    out = {k: v for k, v in index.items() if k != "documents"}
    kb_topics = topics(docs)
    kb_codes = {d.id: codes_of(d) for d in docs}
    rel = relations(docs, kb_codes)
    out["documents"] = []
    for n, entry in enumerate(index.get("documents") or []):
        d = by_entry.get(n)
        e = {k: v for k, v in entry.items() if k not in drop}
        if d is None:
            e["folder"] = None
            out["documents"].append(e)
            continue
        e["folder"] = f"{d.id}/"
        e["topics"], e["codes"], e["related"] = kb_topics.get(d.id, []), kb_codes.get(d.id, []), rel.get(d.id, [])
        e["modules"] = [{"id": m["id"], "title": m["title"], "pages": m["pages"]} for m in d.modules]
        files = {s["id"]: s["file"] for s in d.sections}
        current, recs = None, []
        for rec in entry.get("sections") or []:
            rec = {k: v for k, v in rec.items() if k != "_section"}
            sid = entry["sections"][len(recs)].get("_section")
            current = sid or current
            if current:
                rec["section_id"], rec["section_file"] = current, f"{d.id}/{files[current]}"
            recs.append(rec)
        e["sections"] = recs
        out["documents"].append(e)
    return out


def build(export_zip: Path, out: Path, name: str | None = None, image_names: str = "page") -> dict:
    ex = Export(export_zip)
    index = ex.index
    kb_name = name or index.get("kb_name") or "Knowledge base"
    description = index.get("kb_description") or ""
    out.mkdir(parents=True, exist_ok=True)
    docs, skipped, used, by_entry = [], [], set(), {}
    for n, entry in enumerate(index.get("documents") or []):
        md, images = ex.document(entry)
        fname = str(entry.get("filename") or "document")
        if not md or not md.strip():
            skipped.append(fname)
            continue
        base = slug(re.sub(r"\.[a-z0-9]+$", "", fname, flags=re.IGNORECASE))
        did, k = base, 2
        while did in used or did in ("references", "interaction", "modules", "images"):
            did, k = f"{base}-{k}", k + 1
        used.add(did)
        d = DocBuild(entry, md, images, out, did, image_names)
        d.build()
        docs.append(d)
        by_entry[n] = d
    for d in docs:
        d.write_files(kb_name, [o.title for o in docs if o is not d])
    # document folders from an earlier build whose file is no longer in the export
    for old in out.iterdir():
        if old.is_dir() and old.name not in used and (old / "kb.json").is_file():
            try:
                if json.loads((old / "kb.json").read_text(encoding="utf-8")).get("generator") == "convert_kb.py":
                    shutil.rmtree(old)
            except json.JSONDecodeError:
                pass
    write_json(out / "page_index.json", master_page_index(index, docs, by_entry))
    write(out / "README.md", top_readme(kb_name, description, docs, skipped, export_zip.name))
    return {"documents": len(docs), "modules": sum(len(d.modules) for d in docs), "sections": sum(len(d.sections) for d in docs),
            "images": sum(len(d.pictures) for d in docs), "skipped": skipped}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Convert a KB Manager export to the per-document knowledge-base layout.")
    p.add_argument("export", type=Path, help="the export .zip (page_index.json + files/)")
    p.add_argument("out", type=Path, help="the knowledge-base folder to write")
    p.add_argument("--name", help="the knowledge base's name (default: kb_name from the export)")
    p.add_argument("--image-names", choices=("page", "figure"), default="page",
                   help="page: images/page_NNN_imgK.png (default); figure: keep the parser's figure_NNN.png names")
    a = p.parse_args(argv)
    r = build(a.export, a.out, a.name, a.image_names)
    print(f"built {a.out}: {r['documents']} documents, {r['modules']} modules, {r['sections']} sections, {r['images']} images"
          + (f"; skipped (no text): {', '.join(r['skipped'])}" if r["skipped"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
