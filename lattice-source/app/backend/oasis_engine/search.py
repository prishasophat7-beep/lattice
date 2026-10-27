"""Keyword search (BM25) over sections: no LLM, no vectors.

Used to pick which documents to show the model and, for large documents, which
parts of the table of contents to show. The tokenizer keeps part numbers and
codes such as "DT-450", "E17" or "M6" intact, since assembly-line questions
often hinge on them.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from difflib import get_close_matches

from .models import Doc

_PAGE_LABEL = re.compile(r"\[page \d+\]")
# a picture in a section's text, with its caption and description (see loaders._figure)
_FIGURE_MARK = re.compile(r"\[Figure(?::[^\]]*)?(?:\]|$)")
_TOKEN = re.compile(r"[a-z0-9]+(?:[-./][a-z0-9]+)*")
_STOP = frozenset(
    "a about an and are as at be by did do does for from how i in is it its of on or the this that to "
    "was were what when where which who why will with".split())


# a table of contents: "Training        20", "1.15  Model Training  217", "Scope ........ 4"
_CONTENTS_LINE = re.compile(r"^[^\n]{2,160}?(?:\t|[ \u00a0]{2,}|\.{3,}\s*)\d{1,4}[ \t]*$", re.MULTILINE)


def without_contents(text: str) -> str:
    """The text without its table of contents, when most of it is one: a manual's index table names
    every section and outranked them all (Frontier manual, 19 Oct 2026)."""
    lines = [ln for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("![")]
    entries = len(_CONTENTS_LINE.findall(text))
    if entries < 8 or entries < len(lines) / 2:
        return text
    return _CONTENTS_LINE.sub("", text)


_MD_MARKUP = re.compile(r"(?m)^\s{0,3}(#{1,6}\s+|[-*+]\s+|>\s?)|\|?\s*:?-{3,}:?\s*(?=\||$)|[|*_`]+")


_DERIVED = (("tenance", "tain"), ("ance", ""), ("ence", ""), ("ery", "er"), ("ment", ""), ("ness", ""))


def _stem(word: str) -> str:
    """The word's base for matching: plural to singular ("bolts" -> "bolt", "assemblies" -> "assembly"),
    "delivered" / "delivering" / "delivery" -> "deliver", "maintenance" -> "maintain", "leveling" ->
    "level"; codes and numbers are left alone."""
    if len(word) <= 3 or not word.isalpha():
        return word
    if word.endswith("ies"):
        word = word[:-3] + "y"
    elif word.endswith("s") and not word.endswith(("ss", "us", "is")):
        word = word[:-1]
    for suffix in ("ed", "ing"):  # a stem of at least four letters: "used", "bring" stay
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            word = word[:-len(suffix)]
            if len(word) >= 4 and word[-1] == word[-2] and word[-1] not in "lsz":
                word = word[:-1]  # "planning" -> "plan"
            break
    for suffix, base in _DERIVED:  # "delivery" -> "deliver", "maintenance" -> "maintain"
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            return word[:-len(suffix)] + base
    return word


def _edits(a: str, b: str) -> int:
    """Letters added, removed, changed or swapped with a neighbour to turn a into b."""
    d = [[i + j if i * j == 0 else 0 for j in range(len(b) + 1)] for i in range(len(a) + 1)]
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + (a[i - 1] != b[j - 1]))
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[len(a)][len(b)]


def tokenize(text: str) -> list[str]:
    tokens = []
    raw = _TOKEN.findall(text.lower())
    for tok in raw:
        if tok in _STOP:
            continue
        tok = _stem(tok)
        tokens.append(tok)
        parts = re.split(r"[-./]", tok)
        if len(parts) > 1:  # "dt-450" also matches "dt" and "450"
            # ... stemmed like any word: "late-delivery" gave "delivery", the documents' "delivery" is
            # "deliver", and the two never matched (27 Oct 2026)
            tokens.extend(_stem(p) for p in parts if p and p not in _STOP)
    return tokens


def compounds(text: str, known) -> list[tuple[str, str, str]]:
    """Neighbouring words that the documents also write as one word ("data set" and "dataset",
    "Multi X" and "MultiX"): (joined, first, second), only where the joined word is in `known`. A pair
    joined regardless made "Line C" "linec" and put the wrong sections first."""
    raw = _TOKEN.findall(text.lower())
    out = []
    for a, b in zip(raw, raw[1:]):
        if a in _STOP or b in _STOP or not (a.isalpha() and b.isalpha()):
            continue
        joined = _stem(a + b)
        if joined in known and joined not in (_stem(a), _stem(b)):
            out.append((joined, _stem(a), _stem(b)))
    return out


class BM25:
    """Okapi BM25 with Lucene's always-positive idf. (The classic idf is zero or negative for words
    in half or more of the sections, which is common here: a parent section contains its children's
    text, so in a small manual a word from one subsection is in half the sections.)"""

    def __init__(self, corpus: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tfs = [Counter(doc) for doc in corpus]
        self.lens = [len(doc) for doc in corpus]
        self.avgdl = (sum(self.lens) / len(corpus)) if corpus else 0.0
        df = Counter(term for tf in self.tfs for term in tf)
        n = len(corpus)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def get_scores(self, query: list[str]) -> list[float]:
        terms = [t for t in dict.fromkeys(query) if t in self.idf]
        scores = []
        for tf, dl in zip(self.tfs, self.lens):
            norm = self.k1 * (1 - self.b + self.b * dl / self.avgdl) if self.avgdl else self.k1
            scores.append(sum(self.idf[t] * tf[t] * (self.k1 + 1) / (tf[t] + norm) for t in terms if t in tf))
        return scores


@dataclass(frozen=True)
class Hit:
    doc_index: int
    node_id: str
    score: float


# "2.2 Payment terms (replaces 1.5)": an amendment's clause and the clause it replaces
_REPLACES = re.compile(r"\b(?:replaces|supersedes|replacing|superseding)\s+(?:clause\s+|section\s+|§\s*)?(\d+(?:\.\d+)*)",
                       re.IGNORECASE)


def replacements(docs: list[Doc]) -> dict[tuple[int, str], tuple[int, str]]:
    """(doc index, node id) of each replaced section -> the section that replaces it, from titles
    that say so ("2.3 Late delivery penalty (replaces 1.6)"). An amended agreement keeps both
    clauses; the search finds the old one as readily as the new one."""
    out = {}
    for di, doc in enumerate(docs):
        for nid, s in doc.sections.items():
            m = _REPLACES.search(s.title)
            if not m:
                continue
            number = re.compile(rf"^\s*{re.escape(m.group(1))}(?![\d.]*\d)[\s.):]")
            old = next((n for n, o in doc.sections.items() if n != nid and number.match(o.title)), None)
            if old is not None:
                out[(di, old)] = (di, nid)
    return out


_CODE = re.compile(r"[a-z]+-?\d[\w-]*")


def is_code(token: str) -> bool:
    """A model, part, alarm or order code ("a302", "g2", "hx-700", "po-26-0503"), not a figure with
    a word ("3-night", "13-hour")."""
    return bool(_CODE.fullmatch(token))


PICTURE_WEIGHT = 0.5  # a section matched only by what its pictures show, against one matched by its text
CODE_SECTIONS = 3  # a code in this many sections or fewer is specific: its sections come first
DOC_NAME_SHARE = 0.5  # of the best score, added to the sections of the manual a question names
CHUNK_CHARS = 2000  # a long section is also searched in pieces of this size ...
CHUNK_OVERLAP = 200  # ... overlapping by this much (a sentence cut at a piece boundary is in both)


SHARED_TITLE_CONTEXT = 2  # headings above a section whose title others share, searched with it


class SectionIndex:
    TEXT_CHARS = 2000

    def __init__(self, docs: list[Doc]):
        self.docs = docs
        self.keys: list[tuple[int, str]] = []
        corpus, texts, weights = [], [], []
        # a title many sections share ("Training", "Configuration" under each module) says nothing about
        # which one is meant: such a section also gets the headings above it ("Multi X Classification
        # Creating a MultiX solution")
        repeated = Counter(s.title.strip().lower() for doc in docs for s in doc.sections.values())
        for di, doc in enumerate(docs):
            for nid, s in doc.sections.items():
                body = _FIGURE_MARK.sub(" ", _PAGE_LABEL.sub(" ", without_contents(doc.section_text(nid, max_chars=self.TEXT_CHARS))))
                context = (" ".join(doc.path(nid)[:-1][-SHARED_TITLE_CONTEXT:])
                           if repeated[s.title.strip().lower()] > 1 else "")
                texts.append(f"{s.title} {s.title} {context} {s.summary} {body}")
                corpus.append(tokenize(texts[-1]))
                self.keys.append((di, nid))
                weights.append(1.0)
                # what its pictures show, as described by a machine ("GLOBAL SCENE: A screenshot of the
                # SEM-AI FRONTIER window ..."): searched apart and counted for less than the text. In the
                # text, every screenshot of the app's title bar matched "What is the Frontier application?"
                # over the manual's own introduction (office machine, 26 Oct 2026)
                pictures = " ".join(_FIGURE_MARK.findall(s.own_text))
                if len(pictures) > len("[Figure]"):
                    texts.append(f"{s.title} {context} {pictures}")
                    corpus.append(tokenize(texts[-1]))
                    self.keys.append((di, nid))
                    weights.append(PICTURE_WEIGHT)
                # the rest of a long section, in pieces that each point back to it: only its start was
                # searched, so "the 72-hour dry run" 28,000 characters into a section, or part HX9-4471
                # in row 157 of a 500-row table, could never be found (large-document test, 16 Oct 2026)
                rest = _FIGURE_MARK.sub(" ", _PAGE_LABEL.sub(" ", without_contents(s.own_text)[self.TEXT_CHARS - CHUNK_OVERLAP:]))
                for start in range(0, len(rest), CHUNK_CHARS - CHUNK_OVERLAP):
                    piece = rest[start:start + CHUNK_CHARS]
                    if len(piece) > CHUNK_OVERLAP:
                        texts.append(f"{s.title} {s.title} {context} {piece}")
                        corpus.append(tokenize(texts[-1]))
                        self.keys.append((di, nid))
                        weights.append(1.0)
        # words the documents write both apart and together ("Multi X" here, "MultiX" there): a question
        # in one spelling also searches the other. Only at question time: added to the documents too,
        # a section saying "TaskFlow Pro" counted the name three times and its introduction beat the
        # requirements it was asked about
        known = {t for c in corpus for t in c}
        self._together: dict[str, tuple[str, str]] = {}
        self._apart: dict[tuple[str, str], str] = {}
        for text in texts:
            for joined, a, b in compounds(text, known):
                self._together.setdefault(joined, (a, b))
                self._apart.setdefault((a, b), joined)
        self.bm25 = BM25(corpus) if corpus else None
        self._weights = weights
        self._tokens = [set(c) for c in corpus]
        # each section's words, all its pieces together
        self._section_tokens: dict[tuple[int, str], set[str]] = {}
        for key, toks in zip(self.keys, self._tokens):
            self._section_tokens.setdefault(key, set()).update(toks)
        self._sections_with: Counter = Counter(t for toks in self._section_tokens.values() for t in toks)
        # headings with nothing under them: the web export's document title ("## HX-900 Spare Parts
        # Catalogue", then "## Parts list" beside it) ranked first on its title and gave the model
        # nothing to read (large-document test)
        self._empty = {(di, nid) for di, doc in enumerate(docs) for nid, s in doc.sections.items()
                       if doc.kind != "pages" and not s.own_text.strip() and not s.children}
        # a code in a document's title ("HX-500") names the manual, not one of its sections
        titles = " ".join(f"{doc.display_title} {s.title}" for doc in docs
                          for s in doc.sections.values() if s.depth == 0)
        # the codes in documents' titles ("HX-500", "FIN-07") and their parts ("hx", "500"): names of
        # documents. Ordinary title words ("March", "maintenance") stay search words
        codes = {t for t in tokenize(titles) if any(c.isdigit() for c in t) and any(c.isalpha() for c in t)}
        self.title_words = codes | {p for c in codes for p in re.split(r"[-_.]", c) if p}
        self._doc_names = [{t for s in doc.sections.values() if s.depth == 0
                            for t in tokenize(f"{doc.display_title} {s.title}")} & self.title_words
                           | {p for t in tokenize(doc.display_title) for p in re.split(r"[-_.]", t) if p}
                           & self.title_words for doc in docs]
        self.replaced = replacements(docs)
        self._spelled: dict[str, str] = {}
        self.words = sorted(t for t in (self.bm25.idf if self.bm25 else {}) if t.isalpha() and len(t) >= 4)

    def spell(self, text: str) -> str:
        """The text with misspelt words replaced by the documents' own words ("budjet for softwar" ->
        "budget for software"). Only words the documents do not contain are changed. For searching
        only: the model reads the question as it was asked."""
        if text in self._spelled:
            return self._spelled[text]
        known = self.bm25.idf if self.bm25 else {}

        def fix(m: re.Match) -> str:
            word = m.group(0)
            low = word.lower()
            if len(low) < 4 or low in _STOP or _stem(low) in known:
                return word
            # a typo seldom changes the first letter ("atended" is not "intended")
            # and a typo is a letter or two: "weather" is not "watcher", nor "write" "written"
            close = [w for w in get_close_matches(_stem(low), self.words, n=3, cutoff=0.8)
                     if w[0] == low[0] and _edits(_stem(low), w) <= (1 if len(low) <= 7 else 2)
                     # in a short word one changed letter is often another word ("write", "white";
                     # "cost", "coat"): only a missing, extra or swapped letter counts
                     and (len(low) > 5 or len(w) != len(_stem(low)) or sorted(w) == sorted(_stem(low)))
                     # ... nor is a short word with a letter more: "reset" is not "rest" (the 0.8B,
                     # told "spelling corrected: How do I rest my password", answered nonsense)
                     and not (len(low) <= 6 and len(w) < len(_stem(low)))]
            return close[0] if close else word

        if len(self._spelled) > 256:
            self._spelled.clear()
        self._spelled[text] = re.sub(r"[A-Za-z]+", fix, text)
        return self._spelled[text]

    def expand(self, words: list[str]) -> list[str]:
        """Short words the documents do not contain, with the document words they abbreviate
        ("org" -> "org organization"); a word that starts too many document words stays alone."""
        known = self.bm25.idf if self.bm25 else {}
        out = []
        for w in words:
            out.append(w)
            if 3 <= len(w) <= 5 and w.isalpha() and w not in known:
                longer = [v for v in self.words if v.startswith(w) and len(v) >= len(w) + 3]
                if len(longer) <= 3:
                    out += longer
        return out

    def query_spellings(self, query: str) -> list[list[str]]:
        """The query's search words (spelling corrected), and the same words with the other spelling of
        words the documents write both apart and together ("multi x" <-> "multix"). Searched one at a
        time, each section scoring by the spelling it uses: both together counted a name the
        documents also join ("TaskFlow Pro", "TaskFlowPro") twice, and its introduction outranked
        the requirements it was asked about."""
        terms = tokenize(self.spell(query))
        other, i = [], 0
        while i < len(terms):
            pair = tuple(terms[i:i + 2])
            if pair in self._apart:
                other.append(self._apart[pair])
                i += 2
                continue
            other.extend(self._together.get(terms[i], (terms[i],)))
            i += 1
        return [terms] if other == terms else [terms, other]

    def query_terms(self, query: str) -> list[str]:
        """Every search word of the query, in either spelling."""
        return list(dict.fromkeys(t for spelling in self.query_spellings(query) for t in spelling))

    def names_code(self, question: str) -> bool:
        """Does the question name a code few sections have ("E-217", "HX9-4471")? Then keyword
        search is the better guide to its section; otherwise search by meaning is."""
        return any(is_code(t) and 0 < self._sections_with[t] <= CODE_SECTIONS and t not in self.title_words
                   for t in set(tokenize(question)))

    def search(self, query: str) -> list[Hit]:
        """All sections with a positive score, best first."""
        if self.bm25 is None:
            return []
        spellings = self.query_spellings(query)
        terms = self.query_terms(query)
        # words of a document's title ("HX-500") pick the manual, not a section of it: counted, they put
        # its opening sections, full of the name, over the one asked about ("before opening the
        # electrical cabinet of an HX-500?" found "About this manual", final test). Kept when the
        # question has nothing else
        topical = [t for t in terms if t not in self.title_words]
        scores = [max(per) for per in zip(*(
            self.bm25.get_scores([t for t in sp if t not in self.title_words] or sp) for sp in spellings))]
        scores = [sc * w for sc, w in zip(scores, self._weights)]
        named = set(terms) - set(topical)
        if topical and named:
            # ... but they still say which manual: its sections go ahead of the other manuals' (the
            # same E17 in "station-4.md" and "station-7.md": "on station 7" picks one)
            # the manual(s) with the most of the name ("station" and "7" for station-7, not "station"
            # alone for station-4)
            most = max(len(named & names) for names in self._doc_names)
            bonus = DOC_NAME_SHARE * max(max(scores), 1.0)
            scores = [sc + bonus if sc > 0 and most and len(named & self._doc_names[di]) == most else sc
                      for sc, (di, _) in zip(scores, self.keys)]
        # a code few sections have ("A302", "E17", "PO-26-0503") says which section is meant: "What
        # does alarm A302 mean on the HX-500 and what should the operator do?" found the manual's
        # opening sections, full of "HX-500" and "operator", over the alarm table (final test)
        rare = {t for t in terms if is_code(t)
                and 0 < self._sections_with[t] <= CODE_SECTIONS and t not in self.title_words}
        if rare:
            # ahead of every section without it (whatever its own score: in a small corpus a code in
            # two of three sections weighs nothing in BM25)
            lead = max(max(scores), 1.0)
            scores = [max(sc, 0.0) + lead if rare & toks else sc for sc, toks in zip(scores, self._tokens)]
        best: dict[tuple[int, str], float] = {}  # a section scores as its best piece
        for key, sc in zip(self.keys, scores):
            if key in self._empty:
                continue
            if sc > 0 and sc > best.get(key, 0.0):
                best[key] = float(sc)
        hits = [Hit(di, nid, sc) for (di, nid), sc in best.items()]
        return sorted(hits, key=lambda h: -h.score)

    def snippet(self, doc_index: int, node_id: str, query: str, width: int = 220) -> str:
        """The stretch of the section's text that contains the most distinct query words."""
        doc = self.docs[doc_index]
        text = _PAGE_LABEL.sub(" ", doc.section_text(node_id, max_chars=20_000))
        if doc.kind == "markdown":
            text = _MD_MARKUP.sub(" ", text)
        text = " ".join(text.split())
        terms = [t for t in dict.fromkeys(tokenize(query)) if len(t) > 1]

        def hits_in(text: str) -> list[tuple[int, str]]:
            return sorted((m.start(), t) for t in terms
                          for m in re.finditer(rf"(?<![a-z0-9]){re.escape(t)}", text, re.IGNORECASE))
        # the manual's words, a picture as "(picture)": its description was written by the platform's
        # model, and a screenshot's is long ("[Figure: A digital user interface (UI) window for ...")
        plain = " ".join(_FIGURE_MARK.sub(" (picture) ", text).split())
        hits = hits_in(plain)
        if hits or not hits_in(text):
            text = plain
        else:  # only a picture's description has the words searched for
            hits = hits_in(text)
        best, best_count = 0, 0
        for i, (pos, _) in enumerate(hits):  # window starting at each hit
            count = len({t for p, t in hits[i:] if p < pos + width * 2 // 3})
            if count > best_count:
                best, best_count = pos, count
        start = max(0, best - width // 3) if hits else 0
        end = start + width
        # whole words at both ends ("...ch 38,437" was "...switch 38,437"; final check)
        if start:
            space = text.find(" ", start, best if hits else start + width)
            start = space + 1 if space >= 0 else start
        if end < len(text):
            space = text.rfind(" ", max(start, best + 1 if hits else start), end)
            end = space if space > start else end
        return ("..." if start else "") + text[start:end] + ("..." if end < len(text) else "")

    def matched_terms(self, doc_index: int, node_id: str, query: str) -> set[str]:
        """The query's words that occur in this section (as indexed)."""
        words = self._section_tokens.get((doc_index, node_id), set())
        return {t for t in self.query_terms(query) if t in words}

    def rank_docs(self, query: str) -> list[int]:
        """Document indexes ordered by their best-matching section; unmatched docs last."""
        best: dict[int, float] = {}
        for h in self.search(query):
            best.setdefault(h.doc_index, h.score)
        return sorted(range(len(self.docs)), key=lambda i: (-best.get(i, 0.0), i))
