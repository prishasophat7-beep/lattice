"""The format-independent knowledge-base model every loader produces.

A PageIndex document and a Markdown document both become a Doc: an ordered
tree of Sections, each with a title, a summary and a way to fetch its text.
Retrieval, the API and the UI only ever see this model.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Literal

FALLBACK_SUMMARY_CHARS = 300
MAX_SECTION_CHARS = 12_000


def clean(text: str) -> str:
    """Collapse whitespace (PDF text is full of tabs and hard line breaks)."""
    return " ".join((text or "").split())


# words a file name joins to the one before it ("UserManual", "InstallGuide")
_DOC_WORDS = ("Manual", "Guide", "Handbook", "Policy", "Policies", "Report", "Notes", "Specification",
              "Instructions", "Procedure", "Procedures", "Overview", "Reference", "Tutorial")


def readable_name(title: str) -> str:
    """A document title that is a file name cluttered with a document code or a copy mark, as people
    would write it: "[B-ACD-E1]SEM_AI_Frontier7.0_UserManual (1).docx" -> "SEM AI Frontier 7.0 User
    Manual". Anything else is returned as it is: an ordinary file name ("employee_handbook_2026.pdf"
    read better than "employee handbook 2026"), a title someone wrote, a path that tells two files apart."""
    m = re.fullmatch(r"([^/\\]+)\.[A-Za-z][A-Za-z0-9]{1,4}", title.strip())
    if not m:
        return title
    name = m.group(1)
    name = re.sub(r"^\s*(?:\[[^\]]{1,24}\]|\([^)]{1,24}\))\s*(?=\S)", "", name)  # a leading document code
    name = re.sub(r"\s*(?:\(\d{1,2}\)|-\s*Copy(?:\s*\(\d{1,2}\))?)\s*$", "", name, flags=re.IGNORECASE)  # a copy
    if name == m.group(1):
        return title
    name = re.sub(r"(?<![A-Za-z0-9])(v\d+)_(\d{1,2})(?![0-9])", r"\1.\2", name, flags=re.IGNORECASE)  # "v3_2"
    name = re.sub(r"_+", " ", name)
    name = re.sub(r"(?<=[A-Za-z]{3})(?=\d+\.\d)", " ", name)  # "Frontier7.0" -> "Frontier 7.0"
    name = re.sub(r"(?<=[a-z])(?=(?:" + "|".join(_DOC_WORDS) + r")\b)", " ", name)
    name = " ".join(name.split())
    return name if len(name) >= 3 else title



@dataclass
class Section:
    node_id: str
    title: str
    summary: str
    depth: int
    parent: str | None = None
    children: list[str] = field(default_factory=list)
    # PDF-backed documents: 1-based inclusive page range
    start_page: int | None = None
    end_page: int | None = None
    # Markdown documents: the section's own text (up to the next heading) and its line
    own_text: str = ""
    line: int | None = None
    summary_is_fallback: bool = False
    # pictures placed in this section (file names under the document's assets_dir/images)
    images: list[str] = field(default_factory=list)
    # what the export says about them: file name -> {"caption": ..., "description": ...}
    image_notes: dict[str, dict[str, str]] = field(default_factory=dict)


@dataclass
class Doc:
    doc_id: str
    name: str
    description: str
    kind: Literal["pages", "markdown"]
    sections: dict[str, Section]  # insertion order is document order
    pages: dict[int, str] = field(default_factory=dict)
    title: str = ""  # display title from the bundle catalog; falls back to name
    assets_dir: Path | None = None  # web-platform exports: the folder holding images/
    # what is known about the file, for listing and filtering: "type" (pdf, docx ...), "folder",
    # "added" (ISO date the platform processed it), "pages", "tags"
    meta: dict[str, str] = field(default_factory=dict)

    @property
    def display_title(self) -> str:
        return readable_name(self.title or self.name)

    @cached_property
    def _title_root(self) -> str | None:
        """A Markdown document's single top heading (its title), left out of locators."""
        roots = [nid for nid, s in self.sections.items() if s.parent is None and s.title != "Preface"]
        return roots[0] if len(roots) == 1 else None

    # ── navigation ──
    def path(self, node_id: str) -> list[str]:
        """Titles from the root down to this section, e.g. ['Troubleshooting', 'Error E17']."""
        titles = []
        cur: str | None = node_id
        while cur is not None:
            s = self.sections[cur]
            titles.append(s.title)
            cur = s.parent
        return list(reversed(titles))

    def subtree(self, node_id: str) -> list[Section]:
        out, stack = [], [node_id]
        while stack:
            s = self.sections[stack.pop()]
            out.append(s)
            stack.extend(reversed(s.children))
        return out

    # ── text ──
    def section_text(self, node_id: str, max_chars: int = MAX_SECTION_CHARS) -> str:
        """The text a model reads for this section, labelled so it can cite it."""
        s = self.sections[node_id]
        if self.kind == "pages":
            parts = [f"[page {p}]\n{self.pages.get(p, '')}"
                     for p in range(s.start_page or 1, (s.end_page or s.start_page or 1) + 1)]
        else:
            parts = [f"{'#' * (sub.depth + 1)} {sub.title}\n{sub.own_text}".strip()
                     for sub in self.subtree(node_id)]
        text = "\n\n".join(parts)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n[... section truncated ...]"
        return text

    def short_path(self, node_id: str) -> list[str]:
        """path() without a Markdown document's title heading, which repeats the manual's name."""
        path = self.path(node_id)
        return path[1:] if len(path) > 1 and self._title_root is not None else path

    def locator(self, node_id: str) -> str:
        """Human-readable place in the source, for citations."""
        s = self.sections[node_id]
        if self.kind == "pages":
            if s.start_page == s.end_page:
                return f"page {s.start_page}"
            return f"pages {s.start_page}-{s.end_page}"
        where = " > ".join(self.short_path(node_id))  # "Error codes > E17", not "Manual > Error codes > E17"
        if s.start_page:  # a converted PDF: the page range the web platform recorded
            pages = f"p. {s.start_page}" if s.end_page in (None, s.start_page) else f"pp. {s.start_page}-{s.end_page}"
            return f"{where} ({pages})"
        return where


def fallback_summary(text: str) -> str:
    text = clean(text)
    if len(text) <= FALLBACK_SUMMARY_CHARS:
        return text
    return text[:FALLBACK_SUMMARY_CHARS].rsplit(" ", 1)[0] + " ..."
