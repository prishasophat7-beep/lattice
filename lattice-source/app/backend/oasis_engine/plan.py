"""The parts of a question that asks several things, found without the model.

"Which Python packages does the API use and which leave needs HR approval?" searched as one
string finds the sections for whichever half has the rarer words; the other half never reaches the
model, and its answer is dropped (seen in the behaviour audit of 14 Oct 2026). Split into parts,
each part is searched on its own and the model is told to answer every part.

Only clear joins are split: separate questions ("...? And ...?"), or "and"/"also" followed by a
question word ("... and which leave ..."). "Cost and benefits of X" is one question.
"""
from __future__ import annotations

import re

from .search import tokenize
from .support import _ASKING

MAX_PARTS = 4
MIN_TOPIC_WORDS = 2
_QUESTION_WORD = r"(?:what|which|who|whom|whose|when|where|why|how|is|are|was|were|do|does|did|can|could|" \
                 r"should|will|would|may|must|has|have|list|give|tell|explain|compare|summari[sz]e)"
_JOIN = re.compile(rf"(?:[,;]\s*|\s+)(?:and|also|plus|as well as|and also)\s+(?={_QUESTION_WORD}\b)", re.IGNORECASE)
_ASKS = re.compile(rf"\s*{_QUESTION_WORD}\b", re.IGNORECASE)
_SENTENCE = re.compile(r"(?<=[?])\s+|(?<=[.!])\s+(?=[A-Z])")


def question_parts(question: str) -> list[str]:
    """The separate questions in a question, or [] when it asks one thing."""
    parts: list[str] = []
    told = ""  # a statement ("I am 30 years old.") belongs to the question after it
    for sentence in _SENTENCE.split(question.strip()):
        if not sentence.rstrip().endswith("?") and not _ASKS.match(sentence):
            told += sentence.strip() + " "
            continue
        pieces = [p.strip(" ,;") for p in _JOIN.split(sentence) if p.strip(" ,;")]
        if pieces and told:
            pieces[0], told = told + pieces[0], ""
        parts += pieces
    parts = [p for p in parts if len([t for t in tokenize(p) if len(t) > 2 and t not in _ASKING]) >= MIN_TOPIC_WORDS]
    return parts[:MAX_PARTS] if len(parts) >= 2 else []


def parts_note(parts: list[str]) -> str:
    """The line telling the model to answer every part."""
    listed = "; ".join(f"({i}) {p.rstrip('?. ')}" for i, p in enumerate(parts, 1))
    return (f"This question has {len(parts)} parts: {listed}. Answer every part, each from its own sources; "
            "say for a part the documents do not cover that they do not.")


# ── follow-ups ──
# "What was the revenue in January?" then "And in March?": the same question about March. A small
# model rewrote that as "the profit in March" (seen in the reasoning eval of 14 Oct 2026); the
# swap is made here instead, in code.
_PREP = r"(?:in|for|on|at|during|of|with|from|under)"
_ELLIPTIC = re.compile(rf"^\W*(?:and|what about|how about|and what about|same for)\s+(?:(?P<prep>{_PREP})\s+)?"
                       rf"(?P<what>[^?.!]+?)\W*$", re.IGNORECASE)
_SLOT = re.compile(rf"\b(?P<prep>{_PREP})\s+(?P<what>[^?.!,;]+?)\s*[?.!]*\s*$", re.IGNORECASE)
_PHRASE = re.compile(rf"\b(?P<prep>{_PREP})\s+(?P<what>[^?.!,;]+?)(?=\s+{_PREP}\b|\s*[?.!,;]|\s*$)", re.IGNORECASE)
_BOTH = re.compile(r"\b(the two|both|them|these two|those two)\b", re.IGNORECASE)
MAX_ELLIPTIC_WORDS = 4


def _swap(earlier: str, question: str) -> str | None:
    m = _ELLIPTIC.match(question)
    if not m or len(m["what"].split()) > MAX_ELLIPTIC_WORDS or _ASKS.match(m["what"]):
        return None
    if not m["prep"] and not re.search(r"\d|\b[A-Z]", m["what"]):
        # "what about the approval?" after "How many days of PL ...?" is a new topic, not "days of
        # the approval" (seen in the final test): without a preposition only a name or code is swapped
        return None
    # "the hotel limit for a G2 engineer in Pune?" + "And for a G4?": the "for" phrase is swapped,
    # "in Pune" stays; with no preposition ("what about M10?") the last phrase is
    phrases = list(_PHRASE.finditer(earlier))
    same = [p for p in phrases if m["prep"] and p["prep"].casefold() == m["prep"].casefold()]
    slot = (same or ([] if m["prep"] and len(phrases) > 1 else phrases))[-1:]
    if not slot:
        return None
    s = slot[0]
    return f"{earlier[:s.start()]}{m['prep'] or s['prep']} {m['what'].strip()}{earlier[s.end():].rstrip(' ?.!')}?"


def follow_up(earlier: list[str], question: str) -> str | None:
    """The question in full when it only swaps the subject of the one before ("And in March?"),
    or asks about the two before it ("the increase between the two?"); else None."""
    resolved: list[str] = []
    for q in earlier:
        resolved.append(_swap(resolved[-1], q) if resolved and _swap(resolved[-1], q) else q)
    if not resolved:
        return None
    swapped = _swap(resolved[-1], question)
    if swapped:
        return swapped
    both = _BOTH.search(question)
    if both and len(resolved) >= 2:
        a, b = _SLOT.search(resolved[-2]), _SLOT.search(resolved[-1])
        if a and b and resolved[-2][:a.start()].casefold() == resolved[-1][:b.start()].casefold() \
                and a["what"].casefold() != b["what"].casefold():
            about = resolved[-1][:b.start()].strip()
            return f"{question[:both.start()]}{a['what']} and {b['what']}{question[both.end():]} (about: {about})"
    return None


# a question that leans on the one before: "Is it paid?", "And for a G4?", "What about Jaipur?", "Why?"
_LEANS_BACK = re.compile(r"\b(it|its|that|this|these|those|they|them|their|there|same|also|else|other|another|"
                         r"instead|then|previous|above|earlier)\b|^\W*(and|but|so|or|what about|how about|why)\b",
                         re.IGNORECASE)
FRESH_MIN_WORDS = 3  # topic words a question needs to stand on its own
FRESH_ANSWER_CHARS = 600  # of the last answer, compared with a new question


def starts_fresh(question: str, context: list[tuple[str, str]]) -> bool:
    """A question that stands on its own in a conversation: it points back at nothing, and none
    of its topic words were in the last question or answer. "Which lines met the scrap rate target
    in every month?" right after a hotel limit was answered as a follow-up of it: the hotel question
    searched along, the 0.8B rewrote it with the conversation, and it answered "Line A, Line B, and
    Line C" (Line A alone when asked first; final test)."""
    if not context:
        return True
    if _LEANS_BACK.search(question):
        return False
    asked = {t for t in tokenize(question) if len(t) > 2 and t.isalpha() and t not in _ASKING}
    if len(asked) < FRESH_MIN_WORDS:
        return False
    last_q, last_a = context[-1]
    before = set(tokenize(f"{last_q} {last_a[:FRESH_ANSWER_CHARS]}"))
    return not asked & before


# the language of a question written in a script other than Latin, by its letters
_SCRIPTS = [("Hindi", "ऀ", "ॿ"), ("Bengali", "ঀ", "৿"), ("Gujarati", "઀", "૿"),
            ("Punjabi", "਀", "੿"), ("Tamil", "஀", "௿"), ("Telugu", "ఀ", "౿"),
            ("Kannada", "ಀ", "೿"), ("Malayalam", "ഀ", "ൿ"), ("Odia", "଀", "୿"),
            ("Arabic", "؀", "ۿ"), ("Russian", "Ѐ", "ӿ"), ("Thai", "฀", "๿"),
            ("Japanese", "぀", "ヿ"), ("Korean", "가", "힯"), ("Chinese", "一", "鿿")]
LANGUAGE_SHARE = 0.5  # of a question's letters in one script for it to be in that language


def language_of(question: str) -> str | None:
    """"Hindi" for "छुट्टी कितने दिन की है?"; None for a question in Latin letters (its language is
    left to the model) or a mix with few such letters ("What is 'छुट्टी'?")."""
    letters = [c for c in question if c.isalpha()]
    if not letters:
        return None
    for name, low, high in _SCRIPTS:
        if sum(low <= c <= high for c in letters) >= LANGUAGE_SHARE * len(letters):
            return name
    return None
