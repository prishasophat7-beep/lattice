"""Where a conversation is in the documents: the section its last answer came from, and the part of
the manual around it ("Multi X Classification" for its "Training").

Follow-ups are read there first. "How do I test it?" after Multi X training was answered from
Single X's "Testing", which the keyword search ranked first (Frontier manual, 20 Oct 2026); in the
part the conversation is in, "Testing" is Multi X's. "next" and "go back" step through the manual
in its own order. All of it is found without the model.
"""
from __future__ import annotations

import re

from pydantic import BaseModel

from .models import Doc
from .search import SectionIndex, tokenize
from .support import _ASKING

# a heading's number: "4.2.1 ", "2. ", "(3) "
_NUMBER = r"^\W*(?:\d+(?:\.\d+)*\.?\)?\s+)?"
# a heading that is a step, not a part of the manual: "Creating a MultiX solution", "4.2 Importing images"
STEP = re.compile(_NUMBER + r"\w{3,}ing\b", re.IGNORECASE)
_NEXT = re.compile(
    r"^\s*(?:ok(?:ay)?[,.]?\s+|and\s+|so\s+)?(?:"
    r"next(?:\s+(?:step|one|section|part|topic))?(?:\s+please)?|go\s+(?:on|ahead)(?:\s+please)?|"
    r"what(?:'s|\s+is|\s+comes)?\s+next|what\s+(?:do\s+i\s+do|should\s+i\s+do|comes)\s+(?:next|after\s+(?:that|this))|"
    r"then\s+what|and\s+then|continue|keep\s+going|carry\s+on|move\s+on|show\s+(?:me\s+)?the\s+next(?:\s+step)?"
    r")\s*(?::\s*(?P<title>.+?))?\s*[?.!]*\s*$", re.IGNORECASE)
_BACK = re.compile(
    r"^\s*(?:ok(?:ay)?[,.]?\s+)?(?:"
    r"previous(?:\s+(?:step|one|section|part|topic))?|prev|back|go\s+back|step\s+back|before\s+that|"
    r"what\s+(?:was|comes)\s+before(?:\s+(?:that|this))?|show\s+(?:me\s+)?the\s+previous(?:\s+step)?"
    r")\s*(?::\s*(?P<title>.+?))?\s*[?.!]*\s*$", re.IGNORECASE)


# subsections that are alternatives, not steps: "Type 1" / "Type 2", "Option A" / "Option B"
_VARIANT = re.compile(_NUMBER + r"(type|option|method|mode|way|case|variant|approach|scenario|alternative|format|choice)"
                      r"\s*[-#:]?\s*(?:\d+|[a-z]|[ivx]+)\b", re.IGNORECASE)
_WALK = re.compile(r"\b(?:walk|take|guide|lead)\s+me\s+through\b|\bstep[\s-]+by[\s-]+step\b|\bone step at a time\b",
                   re.IGNORECASE)
_PICTURE_NOTE = re.compile(r"!\[[^\]]*\]\([^)]*\)|\[(?:Figure|Picture|Image)[^\]]*\]", re.IGNORECASE)
MAX_ELSEWHERE = 4


class Elsewhere(BaseModel):
    """The same step in another part of the manual ("Training" in Single X Classification)."""
    label: str      # the part: "Single X Classification"
    node_id: str
    title: str = ""  # the step: "Training"


class Focus(BaseModel):
    """The place shown above the question box: "Working in: <path>", with the sections before and
    after it in the manual (for the Back / Next buttons)."""
    doc_id: str
    node_id: str
    path: list[str]               # the manual's title, then the headings down to the section
    previous: str | None = None   # the title of the section "go back" reads
    next: str | None = None       # the title of the section "next" reads
    # sections of the same title in other parts of the manual, nearest first (buttons under the answer)
    elsewhere: list[Elsewhere] = []


def heading(title: str) -> str:
    """A heading as a label: without its number and trailing colon ("3.2.1 Option A: From a
    folder" -> "Option A: From a folder", "2 Image Classification:" -> "Image Classification")."""
    return re.sub(_NUMBER, "", title.strip()).strip().rstrip(":").strip() or title.strip()


def plain(title: str) -> str:
    """A title without its number and punctuation: "15 Model Training:" -> "model training"."""
    return " ".join(re.sub(r"^[\d.\s]+|[^\w\s]", " ", title).lower().split())


def same_word(a: str, b: str) -> bool:
    """One word, in the search's spelling or not: "adding" (a heading) and "add" (a question);
    "annotate" and "annotation", "configure" and "configuration" (all but two letters of the
    shorter word in common, at least five)."""
    if a == b or (min(len(a), len(b)) >= 3 and (a.startswith(b) or b.startswith(a))):
        return True
    common = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
    return common >= max(5, min(len(a), len(b)) - 2)


def navigation(question: str) -> tuple[str, str] | None:
    """ "next" / "what's next?" -> ("next", ""); "Next: Testing" -> ("next", "Testing"); "go back" /
    "previous step" -> ("back", ""). None for any other question."""
    for way, pattern in (("next", _NEXT), ("back", _BACK)):
        m = pattern.match(question)
        if m:
            return way, (m.group("title") or "").strip()
    return None


def step_to(doc: Doc, nid: str, way: str, title: str = "") -> str | None:
    """The section "next" / "go back" reads from nid. A title given with it (the app's Next and Back
    buttons say where they go) wins when a section of that title is near: the closest one in that
    direction, else the closest one at all."""
    target = neighbour(doc, nid, way)
    if not title or (target is not None and plain(doc.sections[target].title) == plain(title)):
        return target
    order = list(doc.sections)
    here = order.index(nid)
    named = [i for i, n in enumerate(order) if plain(doc.sections[n].title) == plain(title)]
    if not named:
        return target
    ahead = [i for i in named if (i > here if way == "next" else i < here)]
    pick = min(ahead or named, key=lambda i: abs(i - here))
    return order[pick]


def _has_text(doc: Doc, nid: str) -> bool:
    return doc.kind == "pages" or any(s.own_text.strip() for s in doc.subtree(nid))


def _top(doc: Doc, nid: str) -> bool:
    """The document's own title heading, or above it: never a place to step to."""
    return nid == doc._title_root


def neighbour(doc: Doc, nid: str, direction: str) -> str | None:
    """The section "next" or "go back" reads, in the manual's order. Next: the first section after
    this one and everything under it (an answer reads a section with its subsections). Back: the
    section before it at the same level, else the one it is under."""
    order = list(doc.sections)
    if nid not in doc.sections:
        return None
    if direction == "next":
        inside = {s.node_id for s in doc.subtree(nid)}
        return next((n for n in order[order.index(nid) + 1:]
                     if n not in inside and not _top(doc, n) and _has_text(doc, n)), None)
    s = doc.sections[nid]
    siblings = doc.sections[s.parent].children if s.parent is not None else \
        [n for n, x in doc.sections.items() if x.parent is None]
    before = siblings[:siblings.index(nid)] if nid in siblings else []
    for n in reversed(before):
        if _has_text(doc, n):
            return n
    if s.parent is not None and not _top(doc, s.parent) and _has_text(doc, s.parent):
        return s.parent
    return None


def focus_of(doc: Doc, nid: str) -> Focus:
    after, before = neighbour(doc, nid, "next"), neighbour(doc, nid, "back")
    return Focus(doc_id=doc.doc_id, node_id=nid, path=[doc.display_title, *doc.short_path(nid)],
                 next=doc.sections[after].title if after else None,
                 previous=doc.sections[before].title if before else None,
                 elsewhere=elsewhere(doc, nid))


def elsewhere(doc: Doc, nid: str) -> list[Elsewhere]:
    """The other sections of this manual with this section's title, by the part each is in, nearest
    in the manual first: "Training" in Single X Classification, Object Detection, ..."""
    title = plain(doc.sections[nid].title)
    if len(title) < 3:
        return []
    order = list(doc.sections)
    here = order.index(nid)
    mine = part_root(doc, nid)
    seen = {doc.sections[mine].title.strip().casefold()} if mine else set()
    out = []
    for other in sorted((n for n in doc.sections if n != nid and plain(doc.sections[n].title) == title),
                        key=lambda n: abs(order.index(n) - here)):
        if not _has_text(doc, other):
            continue
        root = part_root(doc, other)
        label = heading(doc.sections[root].title if root else doc.short_path(other)[0])
        if label.casefold() in seen or plain(label) == title:
            continue
        seen.add(label.casefold())
        out.append(Elsewhere(label=label, node_id=other, title=heading(doc.sections[other].title)))
        if len(out) == MAX_ELSEWHERE:
            break
    return out


def variants(doc: Doc, nid: str) -> list[str]:
    """Subsections that are different ways of doing the section's task ("Adding Data": "Type 1",
    "Type 2"): asked back, rather than both mixed into one answer. Steps ("Step 1", "Step 2") are
    read together, as are subsections of any other kind."""
    kids = [c for c in doc.sections[nid].children if _has_text(doc, c)]
    words = {m.group(1).lower() if (m := _VARIANT.match(doc.sections[c].title)) else None for c in kids}
    return kids if len(kids) >= 2 and len(words) == 1 and None not in words else []


def named_one(question: str, doc: Doc, kids: list[str]) -> str | None:
    """The one the question already names, by its whole title or by its kind and number ("... with
    Type 2", "with method 2" for "4.2.2 Method 2: Assisted annotation"), else None."""
    asked = f" {plain(question)} "
    for c in kids:
        title = doc.sections[c].title
        key = _VARIANT.match(title)
        names = {plain(title), plain(heading(title)), plain(re.sub(_NUMBER, "", key.group(0))) if key else ""}
        if any(n and f" {n} " in asked for n in names):
            return c
    return None


def short_text(doc: Doc, nid: str, limit: int = 110) -> str:
    """The start of what a section says, without picture notes: an option's description."""
    s = doc.sections[nid]
    text = " ".join(_PICTURE_NOTE.sub(" ", s.own_text or s.summary or "").replace("#", " ").split())
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + " ..."


def walk_request(question: str) -> str | None:
    """ "Walk me through creating a solution", "... step by step": one step at a time. The task
    asked about ("creating a solution"), else None."""
    if not _WALK.search(question):
        return None
    return " ".join(_WALK.sub(" ", question).replace("?", " ").split()) or None


def task_sections(task: str, index: SectionIndex, scope: set[int] | None = None) -> list[tuple[int, str]]:
    """The sections with steps a task names ("creating a MultiX solution"): of the sections with
    steps whose title names some of the task (the search's best matches first), the one naming
    most (the search ranked "Binary Multi X Segmentation" first, on its many mentions of Multi X),
    and every other section of that same title (Multi X Classification has a "Creating a MultiX solution" too: which?)."""
    asked = [t for t in dict.fromkeys(tokenize(task)) if len(t) > 2 and t not in _ASKING]

    def score(di: int, nid: str) -> int:
        doc = index.docs[di]
        title = [w for w in tokenize(plain(doc.sections[nid].title)) if len(w) > 2]
        # words naming the document name no section of it: "operating the HX-500" walked through the
        # whole HX-500 manual's chapters ("Step 1 of 9: About this manual"), not its Operation
        named_doc = {w for w in tokenize(doc.display_title) if len(w) > 2}
        return sum(any(same_word(a, w) for w in title) for a in asked if not any(same_word(a, d) for d in named_doc))

    # the search's best matches, then every section whose own title names the task: the search ranked
    # sections by their mentions of "HX-500", and "5. Operation" was not among its first 20
    found = list(dict.fromkeys([*((h.doc_index, h.node_id) for h in index.search(task)[:20]),
                                *((di, nid) for di, doc in enumerate(index.docs) for nid in doc.sections)]))
    best, best_score = None, 0
    for at in found:
        if (scope is not None and at[0] not in scope) or not steps(index.docs[at[0]], at[1]):
            continue
        points = score(*at)
        if points > best_score:  # ties: the search's order
            best, best_score = at, points
    if best is None:  # no section with steps is titled for the task ("maintaining" for "Maintenance",
        return []     # the whole manual's chapters, Safety): answered as a question, not walked through
    named = plain(index.docs[best[0]].sections[best[1]].title)
    same = [(di, n) for di, n in _by_title(index).get(named, []) if (di, n) != best
            and (scope is None or di in scope) and steps(index.docs[di], n)]
    return [best, *same]


def mixed_parts(docs: list[Doc], cited: list[tuple[int, str]]) -> list[str]:
    """The parts of the manual an answer's steps come from, when they come from several: the cited
    sections that are a step several parts have ("Dataset Format", "Training", "Testing"), from
    different parts. A reworded question was answered with GAN's dataset format, Semi Auto's training
    and Binary Segmentation's testing as one recipe (real-model test, 20 Oct 2026). A section only
    one part has ("Train Options", read with a module's Training) does not count."""
    parts = []
    for di, nid in cited:
        if elsewhere(docs[di], nid):
            label = part_label(docs[di], nid)
            if label not in parts:
                parts.append(label)
    return parts if len(parts) > 1 else []


def part_label(doc: Doc, nid: str) -> str:
    """The part of the manual a section is in, as a label: "Multi X Classification"."""
    s = doc.sections[nid]
    root = part_root(doc, s.parent) if s.parent is not None and not _top(doc, s.parent) else None
    return heading(doc.sections[root].title if root else doc.short_path(nid)[0])


def steps(doc: Doc, nid: str) -> list[str]:
    """The subsections with text of a section that has several (a task's steps), else []."""
    kids = [c for c in doc.sections[nid].children if _has_text(doc, c)]
    return kids if len(kids) >= 2 else []


def part_root(doc: Doc, nid: str) -> str | None:
    """The part of the manual a section is in: the section itself when it has sections under it and
    is not a step ("Multi X Classification"), else the nearest heading above it that is not a step
    ("Multi X Classification" for its "Training", past "Creating a MultiX solution"). None at the
    top of the manual."""
    s = doc.sections[nid]
    if s.children and not _is_step(doc, nid):
        return nid
    cur = s.parent
    while cur is not None and not _top(doc, cur):
        if not _is_step(doc, cur):
            return cur
        cur = doc.sections[cur].parent
    return None


def _is_step(doc: Doc, nid: str) -> bool:
    """A step of a part, not a part: "Creating a solution", or a step done in several ways
    ("4.2 Import images", with "Option A" and "Option B" under it)."""
    return bool(STEP.match(doc.sections[nid].title)) or bool(variants(doc, nid))


def _by_title(index: SectionIndex) -> dict[str, list[tuple[int, str]]]:
    """Every section, by its plain title (cached on the index)."""
    cached = getattr(index, "_focus_titles", None)
    if cached is None:
        cached = {}
        for di, doc in enumerate(index.docs):
            for nid, s in doc.sections.items():
                cached.setdefault(plain(s.title), []).append((di, nid))
        index._focus_titles = cached
    return cached


def _part_title_words(doc: Doc, nid: str) -> set[str]:
    root = part_root(doc, nid)
    return set(tokenize(plain(doc.sections[root].title))) if root else set()


def near(question: str, index: SectionIndex, di: int, nid: str) -> str | None:
    """The section a follow-up means: in the part the conversation is in, else in the part of the
    same manual the question names ("How do I test it in Single X?" from Multi X)."""
    return in_part(question, index, di, nid) or \
        in_named_part(question, index, di, same_as=index.docs[di].sections[nid].title)


def in_named_part(question: str, index: SectionIndex, di: int, same_as: str = "") -> str | None:
    """A section of the part of a manual the question names by the words only that part's title
    has ("single" for "Single X Classification", not "classification", which Multi X's has too).
    same_as: the title of the section the conversation is at; a question naming only the part ("and
    in Single X?") means that section's namesake there."""
    doc = index.docs[di]
    asked = [t for t in dict.fromkeys(tokenize(question)) if len(t) > 2 and t.isalpha() and t not in _ASKING]
    roots = {part_root(doc, n) for n in doc.sections} - {None}
    named = []
    for root in roots:
        words = {w for w in tokenize(plain(doc.sections[root].title)) if len(w) > 2}
        hits = sum(any(same_word(a, w) for w in words) for a in asked)
        if hits:  # the fuller match: "Single X Classification" over "... for both SingleX, MultiX ..."
            named.append(((hits, sum(any(same_word(a, w) for a in asked) for w in words) / len(words)), root))
    if not named:
        return None
    best = max(score for score, _ in named)
    order = list(doc.sections)
    found = {}  # part title -> the first section found under a part of that title, in the manual's order
    for root in sorted((root for score, root in named if score == best), key=order.index):
        hit = in_part(question, index, di, root, same_as)
        if hit is not None and hit != root:
            found.setdefault(plain(doc.sections[root].title), hit)
    # parts of different names both have it ("classification": Single X's or Multi X's?): unclear
    return next(iter(found.values())) if len(found) == 1 else None


def in_part(question: str, index: SectionIndex, di: int, nid: str, same_as: str = "") -> str | None:
    """The section of the conversation's part a follow-up asks about by name: "How do I test it?"
    in Multi X Classification -> its "Testing". None when no heading there names what is asked,
    when the question names another part ("... in Single X?"), or when words of it are nowhere in
    that section (a new topic)."""
    doc = index.docs[di]
    root = part_root(doc, nid)
    if root is None:
        return None
    asked = [t for t in dict.fromkeys(tokenize(question)) if len(t) > 2 and t.isalpha() and t not in _ASKING]
    if not asked:
        return None
    part_words = set(tokenize(" ".join(plain(t) for t in doc.path(root))))
    # words naming the part ("single" in "test it in Single X") name no section of it
    naming = [a for a in asked if not any(same_word(a, w) for w in part_words)]
    if not naming and same_as:  # "and in Single X?": the same step there
        naming = [w for w in tokenize(plain(same_as)) if len(w) > 2]
    idf = index.bm25.idf if index.bm25 else {}
    best, best_score = None, (0, 0.0, 0.0)
    for s in doc.subtree(root)[1:]:
        title = [w for w in tokenize(plain(s.title)) if len(w) > 2]
        matched = [a for a in naming if any(same_word(a, w) for w in title)]
        # the rarer word names the task ("split" over "dataset" in "how do I split the dataset?");
        # then the fuller match ("test": "Testing" over "Train Test Split"); then the manual's order
        score = (len(matched), sum(idf.get(a, 0.0) for a in matched),
                 sum(any(same_word(a, w) for a in naming) for w in title) / len(title) if title else 0.0)
        if matched and score > best_score:
            best, best_score = s.node_id, score
    if best is None:
        return None
    title = [w for w in tokenize(plain(doc.sections[best].title)) if len(w) > 2]
    text = set().union(*(index._section_tokens.get((di, s.node_id), set()) for s in doc.subtree(root)))
    # the parts other sections with this title are in ("Single X Classification" for another
    # "Testing"): a question naming one of them is about that part, not this one
    others = set()
    for odi, onid in _by_title(index).get(plain(doc.sections[best].title), []):
        if (odi, onid) != (di, best):
            others |= _part_title_words(index.docs[odi], onid)
    others -= part_words
    for a in asked:
        if any(same_word(a, w) for w in title) or any(same_word(a, w) for w in part_words):
            continue
        if a in others or (a not in text and not any(same_word(a, w) for w in text)):
            return None
    return best
