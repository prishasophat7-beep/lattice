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


def _stems(text: str) -> set[str]:
    return {_stem(t) for t in tokenize(text) if t not in _FILE_WORDS and not t.isdigit()}


def topic_of(question: str) -> list[str] | None:
    """The topic words of a question that names only a topic, else None."""
    m = _SHAPE.match(question.strip())
    if not m or _PARTICULAR.search(m.group("topic")):
        return None
    words = [t for t in tokenize(m.group("topic")) if t not in _ASKING and len(t) > 1]
    if not 1 <= len(words) <= MAX_TOPIC_WORDS or any(is_code(t) or t in _ATTRIBUTES for t in words):
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
    # a document named by its title ("the leave policy": leave_policy.pdf)
    named = [i for i in in_scope if asked <= _stems(docs[i].display_title)]
    if len(named) == 1:
        di = named[0]
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
    points = [p for p in statements(doc, nid) if len(p) > 20 and p.strip() != s.title][:MAX_POINTS]
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
