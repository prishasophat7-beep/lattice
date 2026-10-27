"""Model replies as plain text, read by forgiving parsers: the default reply format.

Every step used to ask for a structured reply (native JSON, a tool call, or JSON in the text), and
each model failed it its own way: Nemotron wrote the tool call as text ("Please include your
response in a tool call"), Qwen 3.5 thought until its limit, the 0.8B filled the ids with "DOCUMENT
A: performance". Any model can write a line of ids, or an answer and a line naming its sources. So
each step says in a few words how to lay the reply out, and the parsers below take what comes:
thinking in <think> tags, Markdown fences, ids in brackets or prose, labels in any case, and JSON
or a tool call written as text by a model that was trained to reply so.
"""
from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

# a model's thinking, when the server leaves it in the text; an unclosed one is cut off thinking
_THINK = re.compile(r"<(think|thinking|reasoning)>.*?(?:</\1>|\Z)", re.IGNORECASE | re.DOTALL)
_FENCE = re.compile(r"^```[\w-]*\s*$", re.MULTILINE)
# a table-of-contents id as the prompts write them: "A12", "B-0003", "C-INTRO"
_ID = re.compile(r"(?<![\w-])([A-Z](?:\d{1,5}|-[A-Z0-9][A-Z0-9-]*))(?![\w-])")
_LABEL = r"^\W*{}\W*:\s*"
_SOURCES = re.compile(_LABEL.format(r"(?:sources?|sections?|ids?|excerpts?(?: used)?|cited)"), re.IGNORECASE | re.MULTILINE)
_QUESTION = re.compile(_LABEL.format(r"(?:question|standalone(?: question)?)"), re.IGNORECASE | re.MULTILINE)
_NOT_FOUND = re.compile(r"^\W*not\s+found\W*", re.IGNORECASE)
_WORKED_OUT = re.compile(r"(?m)^[^\w\n]*worked\s+out[^\w\n]*?(?::|$)[ \t]*", re.IGNORECASE)
_NONE = re.compile(r"^\W*(none|no sections?|nothing|-)\W*$", re.IGNORECASE)
_BULLET = re.compile(r"^\s*(?:[-*•]|\d{1,2}[.)])\s+")
_ROUTE_KINDS = {"question": "question", "pictures": "pictures", "picture": "pictures", "images": "pictures",
                "summary": "summary", "all_manuals": "all_manuals", "all manuals": "all_manuals",
                "allmanuals": "all_manuals"}

# how to lay out each step's reply, appended to its instructions in the "text" reply format
SELECT_FORMAT = ("\n\nReply with the chosen ids on one line, separated by commas, for example: A6, A12. "
                 "Write NONE if no section could contain the answer. Write nothing else.")
FOLLOWUP_FORMAT = ("\n\nReply in exactly two lines:\nQUESTION: the question as it reads on its own\n"
                   "SECTIONS: the chosen ids separated by commas, or NONE")
ANSWER_FORMAT = ("\n\nWrite the answer itself, then a last line \"SOURCES:\" with the ids of the excerpts you "
                 "used, for example: SOURCES: A6, A12. If the excerpts do not contain the answer, write "
                 "\"NOT FOUND:\" and one sentence saying the documents do not cover it. If the answer is not "
                 "stated in the excerpts but follows clearly from them, start it with \"WORKED OUT:\".")
ROUTE_FORMAT = ("\n\nReply with one word, QUESTION, PICTURES, SUMMARY or ALL_MANUALS, followed on the same "
                "line by the subject or manual named, if any. For example: PICTURES wiring diagram")
SUMMARY_FORMAT = ("\n\nWrite one sentence on what the document covers, then 3 to 6 points, each on its own "
                  "line starting with \"- \".")


def clean(text: str) -> str:
    """The reply without thinking and Markdown fences."""
    return _FENCE.sub("", _THINK.sub("", text or "")).strip()


def _from_json(text: str, cls) -> Any | None:
    """A reply written as JSON, or as a tool call in JSON text, as `cls`; None when it is not one."""
    body = text.strip()
    if body[:1] not in "[{":
        return None
    try:
        data = json.loads(body)
    except ValueError:
        return None
    call = data[0] if isinstance(data, list) and data else data
    if not isinstance(call, dict):
        return None
    fields = call.get("parameters") or call.get("arguments") or call
    if isinstance(fields, str):
        try:
            fields = json.loads(fields)
        except ValueError:
            return None
    try:
        return cls.model_validate(fields)
    except ValidationError:
        return None


def ids_in(text: str) -> list[str]:
    """The ids a reply names, in order, each once ("A6, [A12]" -> ["A6", "A12"])."""
    return list(dict.fromkeys(_ID.findall(text or "")))


def parse_choice(text: str, cls):
    """SectionChoice: the ids on the SECTIONS line, else anywhere in the reply."""
    text = clean(text)
    found = _from_json(text, cls)
    if found is not None:
        return found
    m = _SOURCES.search(text)
    line = text[m.end():].split("\n", 1)[0] if m else text
    return cls(section_ids=[] if _NONE.match(line) else ids_in(line))


def parse_followup(text: str, cls):
    """FollowupChoice: the QUESTION line and the SECTIONS line (or, unlabelled, the first line and the ids)."""
    text = clean(text)
    found = _from_json(text, cls)
    if found is not None:
        return found
    q = _QUESTION.search(text)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    question = text[q.end():].split("\n", 1)[0].strip() if q else (lines[0].strip() if lines else "")
    m = _SOURCES.search(text)
    rest = text[m.end():].split("\n", 1)[0] if m else "\n".join(lines[1:])
    return cls(standalone_question=question, section_ids=[] if _NONE.match(rest) else ids_in(rest))


_SOURCE_LABEL = re.compile(_LABEL.format(r"(sources?|sections?|ids?|excerpts?(?: used)?|cited)"),
                           re.IGNORECASE | re.MULTILINE)


def _sources_line(text: str) -> re.Match | None:
    """The last line that lists the answer's sources. "SOURCES:" counts with any ids after it; the
    other labels only before ids alone: "Sections: Setup, Run and Maintenance are covered ..." is a
    line of the answer, and everything after it was cut off as its sources."""
    for m in reversed(list(_SOURCE_LABEL.finditer(text))):
        rest = text[m.end():]
        if _NONE.match(rest.split("\n", 1)[0]):
            return m
        if not ids_in(rest):
            continue
        if m.group(1).lower().startswith("source") or len(re.findall(r"[A-Za-z]{3,}", _ID.sub(" ", rest))) <= 3:
            return m
    return None


def parse_answer(text: str, cls):
    """DraftAnswer: the answer, its SOURCES line, and NOT FOUND / WORKED OUT if it says so. Also used
    on a partial reply while it streams: a half-written last line that may become "SOURCES:" is held back."""
    text = clean(text)
    found = _from_json(text, cls)
    if found is not None:
        return found
    ids: list[str] = []
    m = _sources_line(text)
    if m is not None:
        ids = ids_in(text[m.end():])
        text = text[:m.start()].rstrip()
    else:
        last = text.rsplit("\n", 1)[-1].strip().lower()
        if last and len(last) < 9 and "sources:".startswith(last):  # "SOUR" while streaming
            text = text[: len(text) - len(text.rsplit("\n", 1)[-1])].rstrip()
    not_found = bool(_NOT_FOUND.match(text))
    if not_found:
        text = _NOT_FOUND.sub("", text, count=1).lstrip(" :-")
    # "WORKED OUT:" at the start, or on a line of its own further down (the 4B put it last, with the
    # reasoning after it; final check, 27 Oct 2026): the label goes, the reasoning stays
    worked = bool(_WORKED_OUT.search(text))
    if worked:
        text = _WORKED_OUT.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return cls(found=bool(text) and not not_found, answer=text, worked_out=worked and not not_found,
               section_ids=ids)


def parse_route(text: str, cls):
    """Route: the first of QUESTION, PICTURES, SUMMARY, ALL_MANUALS, and the words after it."""
    text = clean(text)
    found = _from_json(text, cls)
    if found is not None:
        return found
    low = text.lower()
    best = None
    for word, kind in _ROUTE_KINDS.items():
        i = re.search(rf"(?<![a-z_]){re.escape(word)}(?![a-z_])", low)
        if i and (best is None or i.start() < best[0]):
            best = (i.start(), i.end(), kind)
    if best is None:
        return cls(kind="question", topic="")
    topic = text[best[1]:].split("\n", 1)[0].strip(" :-–\"'.")
    return cls(kind=best[2], topic=topic)


def parse_summary(text: str, cls):
    """SummaryDraft: the first line or paragraph, then the bulleted points."""
    text = clean(text)
    found = _from_json(text, cls)
    if found is not None:
        return found
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    points = [_BULLET.sub("", ln) for ln in lines if _BULLET.match(ln)]
    overview = " ".join(ln for ln in lines if not _BULLET.match(ln))
    return cls(overview=overview, points=points)
