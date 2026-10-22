"""Quotes from the manual that back an answer, picked without the model.

A small model's answer can be thin ("110 Nm", "E22 - Angle out of window"). Under it we show the
one or two sentences (or table rows) of the cited sections that share the most figures and words
with the answer and the question, so the operator reads the manual's own words, tolerance and
next steps included.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import Doc
from .numbers import numbers_in, without_step_numbers
from .search import is_code, tokenize

MAX_QUOTES = 2
MAX_QUOTE_CHARS = 320
MIN_SCORE = 2.0
SECOND_QUOTE_SHARE = 0.6  # a second quote must score at least this share of the best one
QUOTED_SECTION_CHARS = 400_000  # a cited section is searched whole for quotes (row 322 of a parts list)
CODE_WEIGHT = 6.0  # a code of the answer ("HX9-4970") in a quote counts this much
SHORT_SECTION_CHARS = 400  # a short cited section (e.g. one error code) is quoted whole if nothing matches

_PAGE_LABEL = re.compile(r"\[page \d+\]")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9(\"'])")
# "a task prefix, e.g. WEB-142" is one sentence: no break after these
_ABBREVIATION = re.compile(r"(?:\b(?:e\.g|i\.e|etc|vs|approx|no|nos|fig|figs|p|pp|ca|cf|incl|max|min|ref)|\b[A-Z])\.$",
                           re.IGNORECASE)
# a picture where the text had one, with its caption and description: "[Figure: Sprint burndown. A line ...]"
_FIGURE_LINE = re.compile(r"^\[Figure(?::\s*(.*))?\]$")


def _sentences(text: str) -> list[str]:
    out: list[str] = []
    for part in _SENTENCE_END.split(text):
        if out and _ABBREVIATION.search(out[-1]):
            out[-1] += " " + part
        else:
            out.append(part)
    return [s.strip() for s in out if s.strip()]
_TABLE_SEPARATOR = re.compile(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?$")


@dataclass
class Quote:
    text: str
    citation: int  # index into the answer's citations


def _cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _markdown_units(text: str) -> list[str]:
    """Sentences and table rows of a Markdown section; a row is spelled out with its header
    ("Joint: Hub cap screws · Fastener: M6 · Target torque: 9 Nm")."""
    units, header = [], None
    lines = text.splitlines()
    for i, raw in enumerate(lines):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("```"):
            header = None if not line.startswith("|") else header
            continue
        if line.startswith("|"):
            if _TABLE_SEPARATOR.match(line):
                continue
            nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
            if header is None and _TABLE_SEPARATOR.match(nxt):
                header = _cells(line)
                continue
            cells = _cells(line)
            if header and len(header) == len(cells):
                units.append(" · ".join(f"{h}: {c}" for h, c in zip(header, cells) if c))
            else:
                units.append(" · ".join(c for c in cells if c))
            continue
        header = None
        figure = _FIGURE_LINE.match(line)
        if figure:  # what the picture shows, quoted whole and as such (not as "[Figure: ...]")
            if figure.group(1):
                units.append("Picture: " + figure.group(1))
            continue
        line = re.sub(r"^([-*+]|\d{1,2}[.)])\s+", "", line)
        units += _sentences(line)
    return units


def _page_units(text: str) -> list[str]:
    """Sentences of PDF page text. PDF lines break mid-sentence, so prose lines are joined; short
    lines full of figures (table rows) stay on their own."""
    units, prose = [], []

    def flush():
        if prose:
            units.extend(_sentences(" ".join(prose)))
            prose.clear()

    for raw in _PAGE_LABEL.sub("\n", text).splitlines():
        line = " ".join(raw.split())
        if not line:
            flush()
            continue
        words = line.split()
        if len(numbers_in(line)) >= 2 and len(words) <= 14:  # a table row
            flush()
            units.append(line)
        else:
            prose.append(line)
            if line.endswith((".", "!", "?", ":")):
                flush()
    flush()
    return units


def quotes(answer: str, question: str, cited: list[tuple[Doc, str]]) -> list[Quote]:
    """The best-supporting sentences from the cited sections (doc, node id), best first."""
    # step numbers ("3. Create a new solution") are not figures: as figures, they put any text
    # with a 3, 4 or 12 in it under the answer
    figures = numbers_in(without_step_numbers(answer))
    answer_words = {t for t in tokenize(answer) if not t.isdigit()}
    # whole codes the answer gives ("HX9-4970", not its piece "hx9"): a quote with one backs the
    # answer; a row that only shares its figure ("In stock: 95" for a 95-day lead time) does not
    codes = {t for t in answer_words if is_code(t) and any(c.isdigit() for c in t)}
    codes = {t for t in codes if not any(t != u and t in re.split(r"[-_./]", u) for u in codes)}
    question_words = {t for t in tokenize(question) if not t.isdigit()} - answer_words
    scored: list[tuple[float, int, str]] = []
    whole: list[tuple[int, str]] = []  # short sections, as a fallback
    for ci, (doc, node_id) in enumerate(cited):
        text = doc.section_text(node_id, QUOTED_SECTION_CHARS)
        units = _markdown_units(text) if doc.kind == "markdown" else _page_units(text)
        body = " ".join(u for u in units if len(u) >= 12)
        if body and len(body) <= SHORT_SECTION_CHARS:
            whole.append((ci, body))
        for unit in units:
            if len(unit) < 12:
                continue
            words = set(tokenize(unit))
            # a shared figure alone is no support: "10 years of service" does not back "10 working days"
            if not (answer_words | question_words) & words:
                continue
            score = (3.0 * len(figures & numbers_in(unit)) + 1.0 * len(answer_words & words)
                     + 0.5 * len(question_words & words) + CODE_WEIGHT * len(codes & words))
            if score >= MIN_SCORE and (answer_words & words or figures & numbers_in(unit)):
                scored.append((score, ci, unit))
    if codes and any(codes & set(tokenize(u)) for _, _, u in scored):
        scored = [x for x in scored if codes & set(tokenize(x[2]))]
    # the manual's own words before a picture's description (written by the platform's model, and
    # long): a screenshot's description names every button of the window and so matches any step
    if any(not u.startswith("Picture: ") for _, _, u in scored):
        scored = [x for x in scored if not x[2].startswith("Picture: ")]
    scored.sort(key=lambda s: -s[0])
    if not scored:
        return [Quote(text=body, citation=ci) for ci, body in whole[:1]]
    best = scored[0][0]
    out: list[Quote] = []
    for score, ci, unit in scored:
        if out and score < SECOND_QUOTE_SHARE * best:
            break
        text = unit if len(unit) <= MAX_QUOTE_CHARS else unit[:MAX_QUOTE_CHARS].rsplit(" ", 1)[0] + " ..."
        if any(text == q.text for q in out):
            continue
        out.append(Quote(text=text, citation=ci))
        if len(out) == MAX_QUOTES:
            break
    return out
