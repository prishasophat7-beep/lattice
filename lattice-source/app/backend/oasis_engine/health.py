"""What Lattice understood of a knowledge base, checked on every load.

A converter that changes its output does not make the import fail: the app loads what it recognises
and answers worse, silently. The office's converter wrote picture descriptions as blockquotes and
listed them in kb.json as "image_descriptions"; neither was read, and every screenshot description
was searched and quoted as the manual's text (26 Oct 2026). These checks say so at import, in
Manage, and in the --diagnose report.
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .models import Doc

# kb.json fields the loader reads, or knowingly leaves (the same content as a field it reads)
KB_FIELDS = frozenset({
    "name", "kb_name", "description", "generator", "format_version", "version", "generated_at", "created_at",
    "document", "documents", "source", "sources", "modules", "sections", "images", "image_descriptions",
    "codes", "related"})
SECTION_FIELDS = frozenset({
    "id", "module", "title", "pages", "file", "summary", "source", "start_hint", "heading", "codes", "related",
    "text", "images", "has_text", "has_figures"})  # text, images, has_*: what the section file holds
MODULE_FIELDS = frozenset({"id", "slug", "title", "heading", "sections", "pages", "summary", "source", "parent",
                           "parent_l1", "level"})

# a picture description that no rule marked as a picture: still the manual's "text"
_LOOSE = re.compile(r"(?m)^[ \t]*>?[ \t]*(?:[-*][ \t]+)?(?:Image description:)?[ \t]*\**GLOBAL SCENE\**[ \t]*:")
_FIGURE = re.compile(r"\[Figure(?::([^\]]*))?\]")
EMPTY_SHARE = 0.25  # more sections than this without text: the converter lost text ...
EMPTY_MIN = 3       # ... and at least this many (a spreadsheet's title heading over its one table is not)


@dataclass
class DocumentHealth:
    title: str
    sections: int
    empty_sections: int
    pictures: int
    described: int
    loose_descriptions: int


@dataclass
class Check:
    level: str     # "bad": answers are affected; "warn": some may be; "info": worth knowing
    message: str


@dataclass
class Health:
    documents: list[DocumentHealth] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"documents": [asdict(d) for d in self.documents], "checks": [asdict(c) for c in self.checks]}


def check(docs: list[Doc], content: Path | None = None) -> Health:
    """The knowledge base's documents as loaded, and the bundle's files at `content` (when known)."""
    health = Health()
    for doc in docs:
        texts = [s.own_text for s in doc.sections.values()]
        figures = [m.group(1) or "" for t in texts for m in _FIGURE.finditer(t)]
        empty = sum(1 for s in doc.sections.values() if not s.own_text.strip() and not s.children and s.depth > 0)
        d = DocumentHealth(title=doc.display_title, sections=len(doc.sections), empty_sections=empty,
                           pictures=len(figures), described=sum(1 for f in figures if len(f.strip()) >= 20),
                           loose_descriptions=sum(len(_LOOSE.findall(t)) for t in texts))
        health.documents.append(d)
        if not d.sections:
            health.checks.append(Check("bad", f"{d.title}: no sections were read, so nothing in it can be found."))
            continue
        if d.loose_descriptions:
            health.checks.append(Check("bad", f"{d.title}: {d.loose_descriptions} picture descriptions are in the text "
                                              "as if the manual said them; answers may quote them."))
        countable = sum(1 for s in doc.sections.values() if s.depth > 0 and not s.children)
        if empty >= EMPTY_MIN and empty > EMPTY_SHARE * countable:
            health.checks.append(Check("warn", f"{d.title}: {empty} of {countable} sections have no text "
                                               "(only a heading); questions about them cannot be answered."))
        if d.pictures and not d.described and any(x.described for x in health.documents[:-1]):
            health.checks.append(Check("info", f"{d.title}: its {d.pictures} pictures have no descriptions."))
    if content is not None:
        health.checks += _bundle_checks(Path(content))
    return health


def _bundle_checks(content: Path) -> list[Check]:
    """kb.json fields the loader does not read, and decision trees it cannot use."""
    out: list[Check] = []
    for kb in sorted(content.glob("*/kb.json")) or sorted(content.glob("kb.json")):
        where = kb.parent.name if kb.parent != content else "kb.json"
        try:
            data = json.loads(kb.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as e:
            out.append(Check("bad", f"{where}: kb.json cannot be read ({type(e).__name__})."))
            continue
        if not isinstance(data, dict):
            continue
        unread = sorted(set(data) - KB_FIELDS)
        sections = [s for s in data.get("sections") or [] if isinstance(s, dict)]
        modules = [m for m in data.get("modules") or [] if isinstance(m, dict)]
        unread_s = sorted({k for s in sections for k in s} - SECTION_FIELDS)
        unread_m = sorted({k for m in modules for k in m} - MODULE_FIELDS)
        parts = [f"kb.json: {', '.join(unread)}" if unread else "",
                 f"its sections: {', '.join(unread_s)}" if unread_s else "",
                 f"its modules: {', '.join(unread_m)}" if unread_m else ""]
        if any(parts):
            out.append(Check("warn", f"{where}: fields Lattice does not read ({'; '.join(p for p in parts if p)}). "
                                     "The converter may have changed; tell the Lattice developers."))
        tree = kb.parent / "interaction" / "decision-tree.json"
        if tree.is_file():
            try:
                t = json.loads(tree.read_text(encoding="utf-8-sig"))
                if not isinstance(t, dict) or t.get("start") not in (t.get("nodes") or {}):
                    out.append(Check("warn", f"{where}: the decision tree has no start question; it is not used."))
            except (OSError, ValueError):
                out.append(Check("warn", f"{where}: the decision tree cannot be read; it is not used."))
    return out
