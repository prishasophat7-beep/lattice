"""A question that names only a topic ("receipts", "what is the leave policy?", "tell me about
lockout"): an overview of the section or document with that name, from the documents themselves.

In the final computer-use check the 0.8B answered "receipts" with one sentence (the GST number) and
"what is the leave policy?" with the policy's title line. A small model asked for everything about
a topic picks one line; the section's summary and its statements, or the document's summary and its
chapters, are what was asked for. Found without the model: instant, and the same on every model.
"""
from __future__ import annotations

import re

from .doctools import statements
from .models import Doc
from .search import SectionIndex, _stem, is_code, tokenize
from .support import _ASKING
from .vague import _ATTRIBUTES

MAX_POINTS = 6  # statements listed under a section's summary
MAX_CHAPTERS = 8  # chapters listed under a document's summary
POINT_CHARS = 240
MAX_TOPIC_WORDS = 3

# "receipts", "what is the leave policy?", "tell me about lockout/tagout", "overview of the warranty"
_SHAPE = re.compile(
    r"^\W*(?:(?:what\s+(?:is|are)|what's|tell\s+me\s+(?:about|everything\s+about)|explain|describe|"
    r"(?:give\s+(?:me\s+)?)?(?:an?\s+)?(?:overview|summary)\s+of|summari[sz]e)\s+)?"
    r"(?:the\s+|our\s+|a\s+|an\s+)?(?P<topic>[^?.!]{2,60}?)\s*[?.!]*\s*$", re.IGNORECASE)
# a question about something in particular is not a topic: "how", "when", "can I", figures
_PARTICULAR = re.compile(r"\b(how|when|where|why|who|whom|which|can|could|should|may|must|is|are|do|does|did|"
                         r"will|would|if|for|per|than)\b|\d", re.IGNORECASE)
_FILE_WORDS = frozenset("pdf docx doc xlsx xls csv txt md json xml pptx".split())
# words that say what kind of thing a product is, not which: "What is Frontier application?" names Frontier
_KIND_WORDS = frozenset(tokenize("application app software tool product system platform program"))
# a document's opening section about itself
_ABOUT = re.compile(r"^\W*(?:\d+(?:\.\d+)*\.?\s+)?(?:about\b|introduction\b|overview\b|what is\b)", re.IGNORECASE)
ABOUT_MIN_CHARS = 120  # an opening section with less text than this is only a heading


def _stems(text: str) -> set[str]:
    return {_stem(t) for t in tokenize(text) if t not in _FILE_WORDS and not t.isdigit()}


def _title_stems(doc: Doc) -> set[str]:
    """The words of a document's title as a person reads them: "[B-ACD-E1]SEM_AI_Frontier7.0_UserManual
    (1).docx" is "SEM AI Frontier 7.0 User Manual" (office machine, 26 Oct 2026)."""
    out = set()
    for text in (doc.display_title, doc.name):
        out |= _stems(text)
        out |= _stems(re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=\d)|(?<=\d)(?=[A-Za-z])|[_\[\]()]", " ", text))
    return out


def about_section(doc: Doc) -> str | None:
    """The document's first section about itself ("About", "Introduction", "Overview") with text."""
    return next((nid for nid, s in doc.sections.items()
                 if _ABOUT.match(s.title) and len(s.own_text.strip()) >= ABOUT_MIN_CHARS), None)


def topic_of(question: str) -> list[str] | None:
    """The topic words of a question that names only a topic, else None."""
    m = _SHAPE.match(question.strip())
    if not m or _PARTICULAR.search(m.group("topic")):
        return None
    tokens = tokenize(m.group("topic"))
    parts = {p for t in tokens if "-" in t for p in t.split("-")}  # "SEM-AI" is one word, not three
    words = [t for t in tokens if t not in _ASKING and len(t) > 1 and t not in parts]
    if not 1 <= len([t for t in words if t not in _KIND_WORDS]) <= MAX_TOPIC_WORDS or any(is_code(t) or t in _ATTRIBUTES for t in words):
        return None
    return words


def overview(question: str, index: SectionIndex, allowed: set[int] | None = None) -> tuple[str, int, str] | None:
    """(answer, doc index, node id) for a question naming a section's or a document's topic, else None."""
    picked = _option(question, index, allowed)
    if picked is not None:
        return picked
    words = topic_of(question)
    if words is None:
        return None
    asked = {_stem(t) for t in words}
    docs = index.docs
    in_scope = [i for i in range(len(docs)) if allowed is None or i in allowed]
    # a document named by its title ("the leave policy": leave_policy.pdf; "Frontier application":
    # SEM_AI_Frontier7.0_UserManual.docx): what it says about itself, else its summary and chapters
    product = {_stem(p) for w in asked if w not in _KIND_WORDS for p in (w.split("-") if "-" in w else [w])} or asked
    named = [i for i in in_scope if product <= _title_stems(docs[i])]
    if len(named) == 1:
        di = named[0]
        about = about_section(docs[di])
        text = _section(docs[di], about) if about is not None else None
        if text:
            return text, di, about
        root = next((nid for nid, s in docs[di].sections.items() if s.depth == 0), None)
        if root is not None:
            return _document(docs[di]), di, root
    # a section named by its title ("receipts": "7. Receipts"), the best-matching one
    for h in index.search(" ".join(words)):
        if h.doc_index not in in_scope or (h.doc_index, h.node_id) in index.replaced:
            continue
        s = docs[h.doc_index].sections[h.node_id]
        if s.depth > 0 and asked <= _stems(s.title):
            text = _section(docs[h.doc_index], h.node_id)
            if text:
                return text, h.doc_index, h.node_id
        break  # only the best hit: a title match further down is about something else
    return None


def _section(doc: Doc, nid: str) -> str | None:
    s = doc.sections[nid]
    # not what a picture shows: a machine's description of a screenshot is not the manual's statement
    points = [p for p in statements(doc, nid) if len(p) > 20 and p.strip() != s.title
              and not p.startswith("Picture: ")][:MAX_POINTS]
    summary = s.summary if s.summary and not s.summary_is_fallback and _plain(s.summary) != _plain(s.title) else ""
    if not points and not summary:
        return None
    lines = [f"**{_plain(s.title)}** ({doc.display_title})"]
    if summary and not any(summary[:60] in p for p in points):
        lines.append(summary)
    lines += [f"- {_short(p)}" for p in points]
    return "\n".join(lines)


def _document(doc: Doc) -> str:
    chapters = [s.title for s in doc.sections.values()
                if s.depth == 1 and (s.own_text.strip() or s.children)][:MAX_CHAPTERS]
    lines = [f"**{doc.display_title}**"]
    if doc.description:
        lines.append(doc.description)
    if chapters:
        lines.append("It covers: " + "; ".join(_plain(c) for c in chapters) + ".")
    return "\n".join(lines)


def _plain(title: str) -> str:
    return re.sub(r"[:\s]+$", "", title.strip())


def _short(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= POINT_CHARS else text[:POINT_CHARS].rsplit(" ", 1)[0] + " ..."


# the questions an ask-back's options send (see vague.py): 'What does “Leave Approval Procedure” say
# about approval?', 'What does leave_policy.pdf cover?'. The option names the section exactly; a small
# model answered it with one sentence, twice (final computer-use check)
_SECTION_OPTION = re.compile(r"^What does “(?P<title>[^”]{1,200})” say about [^?]{1,60}\?$")
_DOCUMENT_OPTION = re.compile(r"^What does (?P<title>.{1,200}) cover\?$")


def _option(question: str, index: SectionIndex, allowed: set[int] | None) -> tuple[str, int, str] | None:
    docs = index.docs
    in_scope = [i for i in range(len(docs)) if allowed is None or i in allowed]
    m = _SECTION_OPTION.match(question.strip())
    if m:
        for di in in_scope:
            for nid, s in docs[di].sections.items():
                if _plain(s.title) == _plain(m.group("title")) and (di, nid) not in index.replaced:
                    text = _section(docs[di], nid)
                    if text:
                        return text, di, nid
        return None
    m = _DOCUMENT_OPTION.match(question.strip())
    if m:
        for di in in_scope:
            if docs[di].display_title == m.group("title"):
                root = next((nid for nid, s in docs[di].sections.items() if s.depth == 0), None)
                if root is not None:
                    return _document(docs[di]), di, root
    return None
