"""Is an answer drawn from the documents, or made up about something they do not cover?

Asked "How do I reset my password?" of a manual that never mentions passwords, models write
plausible steps ("Click Forgot Password?, enter your email ...") and cite the manual; asked a price
it does not give, they invent one ("$19.99 per user"). Two signs together mark such an answer:
the question's own topic words are nowhere in the documents ("password", "reset", "cost"), and the
answer's words are mostly not in what the model read. Measured on the sample knowledge base: real
answers share 0.88-1.0 of their words with the documents, made-up ones 0.5-0.7. Either sign alone
is not enough: a reworded question ("how do I onboard a teammate?") has words the documents lack
but gets an answer in the documents' own words.
"""
from __future__ import annotations

import re

from .numbers import figures_in, numbers_in
from .search import SectionIndex, tokenize

# a question whose answer is an amount: the figure is the whole answer
_AMOUNT = re.compile(r"\b(how (much|many|long|often|far|big|heavy)|cost|price|fee|charge|salary|budget|"
                     r"rate|amount|number of|percentage|limit)\b", re.IGNORECASE)

SUPPORTED_SHARE = 0.8  # share of the answer's words found in what was read
MIN_WORDS = 4  # an answer this short is not judged by its words
# question words that name no topic: present or not, they say nothing about coverage. Nouns that
# can name a feature ("Image Type", "Report List", "Procedure") are topic words, not these
_ASKING = frozenset(
    "much many does tell show give explain need want please thing things like know find make "
    "there here able possible should could would can best way ways also "
    "about into over more most other some any every each your mine their them they then than "
    "just only very really often long needed required happen work works used summarize summarise "
    "describe compare mean means meaning difference "
    "stand stands abbreviation abbreviations acronym acronyms abbreviated full form expand expansion short "
    "you get set use put see new all our has had not way look looks "
    "say says said state states stated mention mentions mentioned according regarding "
    "document documents manual manuals guide file files section sections page pages".split())
# words an answer is wrapped in, not taken from what was read ("According to the manual, ...")
_FILLER = frozenset(tokenize(
    "according manual document documents section sections states stated mentioned mentions currently "
    "following listed shown provided described explains allows option options available specified"))


def _words(text: str, shortest: int = 4) -> set[str]:
    return {t for t in tokenize(text) if len(t) >= shortest and t.isalpha()}


# words about how to answer, not what about ("apart from the documents", "in general")
_ABOUT_ASKING = frozenset("apart aside besides outside beyond general generally documents document docs manual "
                          "manuals knowledge simple terms speaking concept layman".split())


def _topic(question: str, index: SectionIndex) -> set[str]:
    """The question's topic words ("bit" counts: "change the bit"), as typed: spelling them the
    documents' way would make "reset" their "rest". A typo ("sik leave") counts as missing, but an
    answer about sick leave is in the documents' words, so it is not taken for a made-up one."""
    # (with an acronym of two capitals: "What does PL stand for?" had the topic "stand", in no document, and
    # "PL stands for Privilege Leave [S2]" was taken for made up; Nemotron, 28 Oct 2026)
    acronyms = {w.lower() for w in re.findall(r"\b[A-Z]{2}\b", question)}
    return {w for w in _words(question, 3) | acronyms if w not in _ASKING and w not in _ABOUT_ASKING}


def missing_topic(question: str, index: SectionIndex) -> list[str]:
    """The question's topic words that no document contains ("password", "reset"). The documents'
    names and descriptions count: "What does the SEM AI Frontier7.0 UserManual say about Image
    Type?" names a document by words its text never uses."""
    known = set(index.bm25.idf if index.bm25 else {}) | _names(index)
    return sorted(w for w in _topic(question, index) if w not in known)


def _names(index: SectionIndex) -> set[str]:
    key = id(index)
    if _NAMES_CACHE.get("key") != key:
        words = set()
        for doc in index.docs:
            name = f"{doc.display_title} {getattr(doc, 'name', '')} {doc.description or ''}"
            # "UserManual", "Frontier7.0" are words too: split at case changes and digits
            words |= set(tokenize(name)) | set(tokenize(re.sub(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Za-z])(?=\d)", " ", name)))
            words |= {t.replace(" ", "") for t in tokenize(name)}
        _NAMES_CACHE.update(key=key, words=words)
    return _NAMES_CACHE["words"]


_NAMES_CACHE: dict = {}


def support(answer: str, sources: str) -> float:
    """Share of the answer's words that occur in the sources (1.0 for an answer of few words)."""
    words = _words(answer) - _FILLER
    if len(words) < MIN_WORDS:
        return 1.0
    return len(words & set(tokenize(sources))) / len(words)


def made_up(question: str, answer: str, sources: str, index: SectionIndex, cited: str = "") -> bool:
    """An answer about a topic the documents lack (most of the question's topic words are in no
    document: one may be a rewording, "change the bit" for "replace"), not written from what was read.
    cited: the text of the sections the answer cites; when they are about what was asked (they hold
    the question's topic words that the documents have), the answer is not made up."""
    topic = _topic(question, index)
    missing = set(missing_topic(question, index))
    if len(missing) * 2 <= len(topic) or support(answer, sources) >= SUPPORTED_SHARE:
        return False
    present = topic - missing
    # one unknown word (a model name, a typo) and cited sections about the rest: answered from them.
    # Not "reset my password in TaskFlow": two of its three topic words are nowhere
    if cited and present and len(missing) <= 1 and len(present & set(tokenize(cited))) * 2 >= len(present):
        return False
    return True


def only_unchecked_figures(answer: str, flagged: list[str], question: str = "") -> bool:
    """A question for an amount, and every figure the answer gives is one the documents do not
    have ("$1,200 per user" when no price is given anywhere): the answer stands on an invented
    number. An answer that also explains something keeps its other part, with the figure flagged."""
    if not flagged or not _AMOUNT.search(question):
        return False
    given = figures_in(answer) - figures_in(question)
    return 0 < len(given) <= len(flagged)


_VERDICT = re.compile(r"\W*(yes|no)\b", re.IGNORECASE)
STATED_SHARE = 0.95  # of an answer's words found in what it cites, to count as stated there
# words an answer uses about where a fact is, or to read a table row out ("per year" for "Annual")
_ABOUT_THE_DOCUMENT = frozenset(tokenize("table tables policy policies guide rate year years yearly annual month "
                                         "months monthly week weeks entitlement column row rows"))


def stated_as_is(answer: str, sources: str, question: str = "") -> bool:
    """Is the answer in the sources' own words and figures? Then it is stated there, not worked out
    ("45 days" for the carry-forward limit was labelled "Worked out ... not stated in these words").
    A yes or no, or a case with its own figures ("Can I start at 200 bar?"), applies a rule: worked out."""
    if _VERDICT.match(answer) or numbers_in(question) - numbers_in(sources):
        return False
    # (the question's own words, and words about the document, are no inference: "Employees get 12 days ...
    # The entitlement table in the leave policy lists ..." was labelled worked out; 27 Oct 2026)
    words = _words(answer) - _FILLER - _ABOUT_THE_DOCUMENT - set(tokenize(question))
    if len(words & set(tokenize(sources))) < STATED_SHARE * len(words):
        return False
    return numbers_in(answer) <= numbers_in(sources)


# "How much does an HX-500 machine cost?" answered "5,800 kg" (its weight: final test). A price was
# asked for, a measurement given, with no currency
_MONEY_QUESTION = re.compile(r"\b(cost|costs|price|prices|priced|pricing|fee|fees|charge|charges|tariff|"
                             r"subscription|licen[cs]e fee)\b", re.IGNORECASE)
_CURRENCY = re.compile(r"(₹|\$|€|£|\b(inr|rs|rupees?|usd|dollars?|eur|euros?|gbp|pounds?|lakhs?|crores?)\b)",
                       re.IGNORECASE)
_MEASURE = re.compile(r"\d[\d,.]*\s*(kg|g|mm|cm|m|km|rpm|bar|°c|kw|w|v|hz|hours?|days?|weeks?|months?|"
                      r"years?|liters?|litres?|gb|mb|mbps|nm|kn|tonnes?|m/min)(?![a-z])", re.IGNORECASE)


def wrong_kind_of_figure(question: str, answer: str) -> bool:
    """A price asked for and only a measurement given ("5,800 kg"): not the answer. A currency in a
    label does not make it one ("Cost ($K): 5,800 kg", final test): only a figure that carries it."""
    return bool(_MONEY_QUESTION.search(question) and _MEASURE.search(answer) and not _PRICED.search(answer))


# a figure with its currency next to it ("$1,200", "INR 5,000", "12 lakh", "5,000 rupees")
_PRICED = re.compile(r"(₹|\$|€|£|\b(inr|rs\.?|usd|eur|gbp))\s*\d|\d[\d,.]*\s*(k\b|m\b|mn\b|bn\b)?\s*"
                     r"(₹|\$|€|£|\b(inr|rs|rupees?|usd|dollars?|eur|euros?|gbp|pounds?|lakhs?|crores?)\b)",
                     re.IGNORECASE)
