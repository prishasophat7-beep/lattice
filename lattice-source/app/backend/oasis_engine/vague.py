"""A first question too vague to answer ("What is the limit?", "How long?"): ask what is meant,
offering what the documents have, instead of guessing one meaning or saying "not covered".

In the behaviour audit of 14 Oct 2026, "what is the limit" was answered with the first limit found
(an invitation's 7 days), and "how long" and "tell me about it" with "the documents don't cover
this". Found without the model: the options are the sections that mention the word, so this is
instant and works the same on every model. A follow-up is never vague (it leans on the
conversation), and a word naming a document's subject ("What is TaskFlow?") is a question about it.
"""
from __future__ import annotations

import re
from collections import Counter

from .focus import heading
from .models import Doc
from .pipeline import Clarification, ClarifyOption
from .plan import language_of
from .search import SectionIndex, tokenize
from .support import _ASKING

MAX_OPTIONS = 4
CLEAR_LEAD = 1.6  # the best section scores this much above the next: one meaning stands out
SNIPPET_CHARS = 90
KIND_HITS_CAP = 3  # figures of the kind asked that count for a section (more is not more relevant)


# words that point at the question, not at a topic ("tell me about it")
_POINTING = frozenset("me my mine us we our it its this that these those he she they them one ones".split())
# words whose meaning depends on what they are about: "the limit" of what? A name or an
# abbreviation ("TaskFlow", "PL", "E17") is never vague
_ATTRIBUTES = frozenset(
    "limit maximum minimum max min deadline due date period duration cost price fee charge rate amount "
    "size number count total version status process procedure rule requirement approval approver contact "
    "owner responsible eligibility entitlement notice penalty threshold frequency interval schedule".split())


def _topic(question: str) -> list[str]:
    return list(dict.fromkeys(t for t in tokenize(question) if len(t) >= 2 and t not in _ASKING
                              and t not in _POINTING and not t.isdigit()))


def vague_clarification(question: str, index: SectionIndex, allowed: set[int] | None = None,
                        topicless: bool = True) -> Clarification | None:
    """Which of the documents' meanings is asked about, or None if the question is clear enough.
    topicless: also ask back when the question names no topic at all ("How long?"); Chat leaves those
    to its assistant ("What can you do?", "Summarize it")."""
    if language_of(question):
        # "छुट्टी नीति क्या है?" has no words the search knows: not vague, just not in Latin letters
        # (it was asked back "What would you like to know?"; final computer-use check)
        return None
    docs = index.docs
    in_scope = [i for i in range(len(docs)) if allowed is None or i in allowed]
    words = _topic(question)
    if not words:  # "How long?", "Tell me about it": nothing to look up
        if not in_scope or not topicless:
            return None
        by_kind = _by_kind(question, index, in_scope)
        if by_kind is not None:
            return by_kind
        return Clarification(
            prompt="What would you like to know? Ask about a topic, or start with one of the documents:",
            options=[ClarifyOption(label=docs[i].display_title, detail=_short(docs[i].description),
                                   doc_ids=[docs[i].doc_id], question=f"What does {docs[i].display_title} cover?")
                     for i in in_scope[:MAX_OPTIONS]])
    if len(words) > 1 or words[0] not in _ATTRIBUTES:
        return which_part(question, words, index, in_scope)
    word = words[0]
    if any(word in tokenize(docs[i].display_title) for i in in_scope):
        return None  # "What are the requirements?": a document is about it (requirements.txt)
    picked: list[tuple[int, str, float]] = []
    for h in index.search(question):
        if (allowed is not None and h.doc_index not in allowed) or (h.doc_index, h.node_id) in index.replaced:
            continue  # a clause an amendment replaced is not one of the meanings
        doc, sec = docs[h.doc_index], docs[h.doc_index].sections[h.node_id]
        if word not in tokenize(f"{sec.title} {sec.own_text}"):
            continue  # matched through a subsection: that one is offered instead
        if any(d == h.doc_index and _related(doc, n, h.node_id) for d, n, _ in picked):
            continue
        picked.append((h.doc_index, h.node_id, h.score))
        if len(picked) == MAX_OPTIONS:
            break
    if len(picked) < 2 or picked[0][2] >= CLEAR_LEAD * picked[1][2]:
        return None
    one_doc = len({di for di, _, _ in picked}) == 1
    options: list[ClarifyOption] = []
    for di, nid, _ in picked:
        # a title other sections share ("Train Test Split" in every module) is named with its part
        # ("Object Detection › Train Test Split"); the three of them looked the same
        part = _part_of(docs[di], nid, index)
        label = f"{part} › {_name(docs[di], nid)}" if part else _name(docs[di], nid)
        snippet = _snippet(index, di, nid, word)
        detail = snippet if one_doc else f"{docs[di].display_title}: {snippet}"
        if any(o.label == label for o in options):
            continue  # the same part twice (a manual that repeats a chapter)
        options.append(ClarifyOption(label=label, detail=detail, doc_ids=[docs[di].doc_id],
                                     question=f"What does “{label}” say about {word}?", section=nid))
    if len(options) < 2:
        return None
    return Clarification(prompt=f"“{word}” comes up in several places. Which one do you mean?", options=options)


MAX_PARTS = 6  # parts offered for a task many parts of a manual have
SEARCHED = 15  # search results that show which shared title a question is about
# a heading that names a step rather than a part of the product: "Creating a MultiX solution",
# "Adding Data", "Training"
_STEP = re.compile(r"^\W*(?:\d+(?:\.\d+)*\.?\)?\s+)?\w{3,}ing\b", re.IGNORECASE)  # as focus.STEP


def _plain(title: str) -> str:
    """A title without its number and punctuation: "15 Model Training:" -> "model training"."""
    return " ".join(re.sub(r"^[\d.\s]+|[^\w\s]", " ", title).lower().split())


def _shared_titles(index: SectionIndex) -> dict[str, list[tuple[int, str]]]:
    """Titles several sections share ("Training", "Train Test Split"): their sections, by title."""
    cached = getattr(index, "_shared_titles", None)
    if cached is None:
        by_title: dict[str, list[tuple[int, str]]] = {}
        for di, doc in enumerate(index.docs):
            for nid, s in doc.sections.items():
                if _plain(s.title):
                    by_title.setdefault(_plain(s.title), []).append((di, nid))
        cached = {t: secs for t, secs in by_title.items() if len(secs) > 1}
        index._shared_titles = cached
    return cached


def _part_of(doc: Doc, nid: str, index: SectionIndex) -> str:
    """The part of the manual a section with a shared title belongs to: the nearest heading above it
    that is not a step ("Single X Classification" for its "Training", past "Creating a SingleX
    solution"); "" when the title is the section's alone."""
    if _plain(doc.sections[nid].title) not in _shared_titles(index):
        return ""
    for title in reversed(doc.path(nid)[:-1]):
        if not _STEP.match(title):
            return heading(title)
    return ""


def _same_word(a: str, b: str) -> bool:
    """One word, in the search's spelling or not: "adding" (a heading) and "add" (a question)."""
    return a == b or (min(len(a), len(b)) >= 3 and (a.startswith(b) or b.startswith(a)))


def which_part(question: str, words: list[str], index: SectionIndex, in_scope: list[int]) -> Clarification | None:
    """A task several parts of a manual have, asked without saying which part ("How do I train a
    classification model?" with Training under Single X, Multi X, ...): which one. Found without
    the model, from the manual's own headings: the sections whose shared title the question asks
    about, named by their part; the question's other words keep the parts they name."""
    asked = set(words)
    # what the search finds: "how to split the dataset" is about the "Train Test Split" sections it
    # finds, not the "Dataset" ones its words name
    found = Counter(_plain(index.docs[h.doc_index].sections[h.node_id].title)
                    for h in index.search(question)[:SEARCHED] if h.doc_index in in_scope)
    # the question's most telling word ("export" in "how do I export the model"): a title the search
    # finds is only what is asked about if it has that word ("Model Info" shares just "model")
    idf = index.bm25.idf if index.bm25 else {}
    telling = max(asked, key=lambda w: idf.get(w, 0.0), default="")
    groups = []
    for title, secs in _shared_titles(index).items():
        title_words = set(tokenize(title))
        matched = {a for a in asked if any(_same_word(a, t) for t in title_words)}
        covers = bool(title_words) and all(any(_same_word(t, a) for a in asked) for t in title_words)
        if not (covers or (found[title] >= 2 and telling in matched)):
            continue
        secs = [(di, nid) for di, nid in secs if di in in_scope and (di, nid) not in index.replaced]
        # parts of one manual (the one with the most); the same title in different manuals is asked
        # as "which manual?" (pipeline), by their names
        by_doc: dict[int, dict[str, tuple[int, str, str]]] = {}
        for di, nid in secs:
            part = _part_of(index.docs[di], nid, index)
            if part:
                by_doc.setdefault(di, {}).setdefault(part.lower(), (di, nid, part))
        parts = max(by_doc.values(), key=len, default={})
        if len(parts) < 2:
            continue
        rest = asked - matched
        # the parts the question's other words name ("classification": Single X and Multi X)
        naming = {k: len(rest & set(tokenize(v[2]))) for k, v in parts.items()}
        best = max(naming.values(), default=0)
        named = {k: v for k, v in parts.items() if best and naming[k] == best}
        # words neither the title nor a part explains say what is meant already ("what happens to my
        # features when I import a feature configuration JSON" is not any part's "Configuration")
        explained = {w for w in rest for v in named.values() if w in tokenize(v[2])}
        if len(rest - explained) > 1:
            continue
        groups.append((bool(named), found[title], len(matched), title, named or parts))
    if not groups:
        return None
    # a title whose parts the question's other words narrow down, then the one the search finds
    # most, then the most specific ("model training" before "training")
    groups.sort(key=lambda g: g[:3], reverse=True)
    parts = dict(groups[0][4])
    if not groups[0][0]:
        # the same step under two titles ("Adding Data" in Single X and Multi X, "Adding data to dataset" in
        # Object Detection ...): "How do I add data?" offered only the second (office format, 27 Oct 2026).
        # Titles the question matches as well are one step: their parts together, in the manual's order
        for g in groups[1:]:
            if not g[0] and g[2] == groups[0][2]:
                for key, value in g[4].items():
                    parts.setdefault(key, value)
        order = {(di, nid): k for di, doc in enumerate(index.docs) for k, nid in enumerate(doc.sections)}
        parts = dict(sorted(parts.items(), key=lambda kv: (kv[1][0], order.get((kv[1][0], kv[1][1]), 0))))
    if len(parts) < 2:
        return None  # one part named: the question is clear (its search finds that part)
    picked = list(parts.values())[:MAX_PARTS]
    base = question.strip().rstrip("?").strip()
    return Clarification(
        prompt="This is in several parts of the manual. Which one do you mean?",
        options=[ClarifyOption(label=part, detail=_short(index.docs[di].sections[nid].summary or ""),
                               doc_ids=[index.docs[di].doc_id], question=f"{base} in {part}?", section=nid)
                 for di, nid, part in picked])


def _name(doc: Doc, nid: str) -> str:
    """The section's title, or the document's when the title is markup ("<code_scheme ...>")."""
    title = heading(doc.sections[nid].title)
    return doc.display_title if not title or any(c in title for c in "<>{}=") else title


def _related(doc: Doc, a: str, b: str) -> bool:
    """One section is the other or contains it."""
    return a in {s.node_id for s in doc.subtree(b)} or b in {s.node_id for s in doc.subtree(a)}


_FIGURE = re.compile(r"\[Figure(?::[^\]]*)?\]")


def _snippet(index: SectionIndex, di: int, nid: str, word: str) -> str:
    """Where the section says the word; its summary when only a picture's description does (the
    words on a screenshot, "SET, Off, +, -, Class Size Limit", said nothing about which one it is)."""
    sec = index.docs[di].sections[nid]
    if word not in tokenize(_FIGURE.sub(" ", sec.own_text)) and sec.summary:
        return _short(sec.summary, SNIPPET_CHARS)
    return index.snippet(di, nid, word, width=SNIPPET_CHARS).strip(". ")


def _short(text: str, limit: int = 90) -> str:
    # without pictures: a summary taken from the section's start had "![Figure](images/figure_244.png)"
    text = _FIGURE.sub(" ", re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text or ""))
    text = " ".join(text.split())
    return text if len(text) <= limit else text[:limit].rsplit(" ", 1)[0] + "..."


# "How long?", "How much?", "When?", "How often?" with no topic: the sections that state such a thing
# are the meanings (the final computer-use check offered the list of documents for "How long?")
_KINDS = [
    (re.compile(r"\bhow\s+long\b|\bhow\s+many\s+(days|hours|weeks|months|years)\b", re.I), "durations",
     re.compile(r"\b\d[\d,.]*(?:\s*-\s*\d+)?\s*(?:working\s+|calendar\s+)?(?:minutes?|hours?|days?|weeks?|months?|years?)\b", re.I)),
    (re.compile(r"\bhow\s+much\b|\bwhat\s+(?:does\s+it|do\s+they)\s+cost\b", re.I), "amounts",
     re.compile(r"(?:₹|\$|€|£|\b(?:inr|rs\.?|usd|eur|gbp)\b)\s*\d|\b\d[\d,.]*\s*(?:inr|rupees|usd|dollars|euros?)\b", re.I)),
    (re.compile(r"\bhow\s+often\b", re.I), "how often",
     re.compile(r"\bevery\s+\d[\d,.]*\s*\w+|\b(?:daily|weekly|monthly|quarterly|yearly|annually|once a \w+)\b", re.I)),
    (re.compile(r"^\W*(?:by\s+)?when\b", re.I), "deadlines",
     re.compile(r"\bwithin\s+\d[\d,.]*\s*\w+|\bdeadline\b|\bbefore\s+\w+|\bno later than\b|\bat least \d+ \w+ (?:before|in advance)\b", re.I)),
]


def _by_kind(question: str, index: SectionIndex, in_scope: list[int]) -> Clarification | None:
    kind = next(((name, pattern) for asks, name, pattern in _KINDS if asks.search(question)), None)
    if kind is None:
        return None
    name, pattern = kind
    found: list[tuple[int, int, str, str]] = []  # (matches, doc index, node id, first match in context)
    for di in in_scope:
        doc = index.docs[di]
        for nid, s in doc.sections.items():
            if (di, nid) in index.replaced or not s.own_text:
                continue
            hits = list(pattern.finditer(s.own_text))
            if hits:
                m = hits[0]
                # from a word's start, not mid-word ("...ve Employee of the Year"; final check)
                start = max(0, m.start() - 40)
                if start:
                    space = s.own_text.find(" ", start, m.start())
                    start = space + 1 if space >= 0 else m.start()
                end = m.end() + 40  # ... and to a word's end ("available once during employment, to b")
                if end < len(s.own_text):
                    space = s.own_text.rfind(" ", m.end(), end)
                    end = space if space >= 0 else m.end()
                # without table rules, pipes and bold marks ("| --- |", "**Marriage leave:**")
                context = " ".join(re.sub(r"\|?\s*:?-{3,}:?\s*|\*\*|__|`|\|", " ",
                                          s.own_text[start:end]).split())
                found.append((len(hits), di, nid, context))
    # a few such figures in a short section over many in a long one, one document at a time first
    found.sort(key=lambda f: (-min(f[0], KIND_HITS_CAP), len(index.docs[f[1]].sections[f[2]].own_text)))
    picked: list[tuple[int, str, str]] = []
    for one_per_doc in (True, False):
        for _, di, nid, context in found:
            if len(picked) == MAX_OPTIONS:
                break
            if any(d == di and (one_per_doc or _related(index.docs[di], n, nid)) for d, n, _ in picked):
                continue
            picked.append((di, nid, context))
    if len(picked) < 2:
        return None
    docs = index.docs
    return Clarification(
        prompt=f"Several parts of the documents give {name}. Which one do you mean?",
        options=[ClarifyOption(label=_name(docs[di], nid), detail=f"{docs[di].display_title}: ...{_short(context, SNIPPET_CHARS)}",
                               doc_ids=[docs[di].doc_id],
                               question=f"What does “{_name(docs[di], nid)}” say about {name}?")
                 for di, nid, context in picked])
