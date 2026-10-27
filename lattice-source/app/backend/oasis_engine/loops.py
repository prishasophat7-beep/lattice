"""A model stuck writing the same thing over and over (small models do), told apart from a long answer."""
from __future__ import annotations

import re

LOOP_MIN_CHARS = 600   # a reply this long is checked for a block it keeps repeating
LOOP_BLOCK = 160       # the last characters must not already appear twice before
_DIGITS = re.compile(r"\d+")


class Looping(Exception):
    """The model keeps writing the same block; carries the text worth keeping."""

    def __init__(self, text: str):
        super().__init__("repeating")
        self.text = text


def repeating_from(text: str) -> int | None:
    """Where a reply starts repeating an earlier block, or None. Numbers are ignored: a list that
    counts on ("78. Click "Change Password". 79. Click "Change Password". ...") repeats too."""
    if len(text) < LOOP_MIN_CHARS:
        return None
    # the text with each number as "#", and where each of its characters came from
    plain, origin, last = [], [], 0
    for m in _DIGITS.finditer(text):
        plain.append(text[last:m.start()])
        origin.extend(range(last, m.start()))
        plain.append("#")
        origin.append(m.start())
        last = m.end()
    plain.append(text[last:])
    origin.extend(range(last, len(text)))
    flat = "".join(plain)
    block = flat[-LOOP_BLOCK:]
    if len(set(re.findall(r"[a-z]{3,}", block.lower()))) < 6:  # table rules, dots: not a repeated sentence
        return None
    first = flat.find(block)
    second = flat.find(block, first + 1)
    if not 0 <= first < second < len(flat) - LOOP_BLOCK:
        return None
    line_start = flat.rfind("\n", first, second)  # cut at a line start, not mid-sentence
    return origin[line_start if line_start > first else second]


REPEAT_MIN_CHARS = 25  # a sentence this long, said again word for word, is a repeat ("Yes." is not)
REPEAT_MIN_WORDS = 4  # ... and one with this many words, all said before, is too
# words that add nothing to a sentence said again ("also", "the document states that ...")
_FILLER = frozenset("a an the that this it is are was also too as well additionally further furthermore "
                    "again moreover so then document documents states state stated says say said according to "
                    "policy manual section".split())
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


_HEADING = re.compile(r"^\s*(?:#{1,6}\s+\S.*|[*_]{2}[^*_\n]{1,120}[*_]{2}:?|[^.!?\n]{1,80}:)\s*$")


_LIST_ITEM = re.compile(r"^\s*(?:\d{1,2}[.)]|[-*•])\s+")


def without_repeated_sentences(text: str) -> str:
    """The text with each sentence said only once: a 0.8B wrote "Requests longer than 5 working days
    also require HOD approval." four times in a row (seen in the final test). Short sentences, and
    numbered steps whose words differ only in the number, are kept."""
    seen, said, lines = set(), [], []
    for line in text.split("\n"):
        if _HEADING.match(line):
            # a heading or label ("**For Firmware Update Failure (Error E-217):**", "### Part 2") names
            # what follows; it repeats words said before by design. Dropped, part 2 of a two-part answer
            # lost its label (4B test, 24 Oct)
            lines.append(line)
            continue
        kept, dropped = [], False
        # a step or point of a list restates what a summary before it said, by design: only an exact
        # repeat of it goes (steps 1 and 4 of the MLOps setup were dropped as "said before" by the
        # opening sentence, leaving 2, 3, 5, 6; 4B final check, 27 Oct 2026)
        item = bool(_LIST_ITEM.match(line))
        for sentence in _SENTENCE_END.split(line):
            # the same words, whatever the punctuation or bold marks ("**HOD** approval." / "HOD approval")
            key = " ".join(re.sub(r"[^\w\s]", " ", sentence).split()).casefold()
            # ... or nothing a sentence before did not say: "Requests longer than 5 working days also
            # require HOD approval." after "The document states that requests longer than 5 working
            # days require HOD approval." (final computer-use check)
            content = set(key.split()) - _FILLER
            if len(key) >= REPEAT_MIN_CHARS and (key in seen or (
                    not item and len(content) >= REPEAT_MIN_WORDS and any(content <= before for before in said))):
                dropped = True
                continue
            seen.add(key)
            said.append(content)
            kept.append(sentence)
        joined = " ".join(kept)
        if not dropped:
            lines.append(line)  # nothing repeated: the line as it was ("6,000" is an answer)
        elif kept and not re.fullmatch(r"[\W\d]*", joined):  # not a bare "2." left of a repeated step
            lines.append(joined)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
