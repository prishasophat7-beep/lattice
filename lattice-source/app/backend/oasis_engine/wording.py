"""Plain sentences for the facts code reads out as answers.

The lines the code works out for the model are written for a model to read ("Highest Revenue: March
(291,400)", "Leave Type: Privilege Leave (PL) · Carry Forward: Up to 45 days"). Shown as an answer they
read like a program's output ("Worked out from the table: R7 and R9 routers minus M3 mesh nodes in
Warranty period: 1 (50% of M3 mesh nodes)"); here they become sentences a person would write.
"""
from __future__ import annotations

import re

# a value's first word that reads as part of the sentence ("is up to 45 days", "is not permitted"); a
# name ("Bosch Rexroth") keeps its capital
_LOWER_FIRST = {"up", "not", "only", "every", "within", "above", "below", "at", "before", "after", "none",
                "yes", "no", "max", "min", "maximum", "minimum", "allowed", "permitted", "required", "optional",
                "available", "included", "excluded", "free", "unlimited", "same", "as", "per", "from", "to",
                "until", "on", "in", "by", "with", "without", "all", "any", "one", "two", "three", "half"}

_RANKED = re.compile(r"^(Highest|Lowest) (.+?): (.+) \(([^()]+)\)$")
_COLUMN_TOTAL = re.compile(r"^(Highest|Lowest) column total: (.+) \(([^()]+)\)$")
_OVER_ROWS = re.compile(r"^(Total|Average) (.+) over the (\d+) rows: (.+)$")
_MINUS = re.compile(r"^(.+?) minus (.+?) in (.+?): (-?[\d.,]+(?: [a-z]+)?)(?: \((-?[\d.,]+)% of .+\))?$")
_CHANGE = re.compile(r"^(Increase|Decrease) from (.+?) to (.+?) in (.+?): ([\d.,]+)(?: \(([\d.,]+)%\))?$")
_CELL = re.compile(r"^([^:·]+): ([^·]+?) · ([^:·]+): (.+)$")
_ROW_CELL = re.compile(r"^([^:·]+?) · ([^:·]+): (.+)$")


def _noun(name: str) -> str:
    """A column name inside a sentence: "Carry Forward" -> "carry forward"; "OEE", "Q3" kept."""
    return " ".join(w.lower() if len(w) > 1 and w[0].isupper() and w[1:].islower() else w
                    for w in name.strip().split(" "))


def _value(text: str) -> str:
    text = text.strip()
    first = re.split(r"\W", text, maxsplit=1)[0]
    return text[0].lower() + text[1:] if first.lower() in _LOWER_FIRST and first[:1].isupper() else text


def _sentence(text: str) -> str:
    text = text.strip()
    if not text:
        return text
    text = text[0].upper() + text[1:]
    return text if text[-1] in ".!?:)\"'*" or text.endswith("**") else text + "."


def as_sentence(line: str) -> str:
    """The line as a sentence; a line of another shape (already a sentence from the document) is only
    given its capital and full stop."""
    line = line.strip()
    if m := _COLUMN_TOTAL.match(line):
        return f"{m.group(2)} has the {m.group(1).lower()} total ({m.group(3)})."
    if m := _RANKED.match(line):
        return f"{m.group(3)} has the {m.group(1).lower()} {_noun(m.group(2))} ({m.group(4)})."
    if m := _OVER_ROWS.match(line):
        return f"The {m.group(1).lower()} {_noun(m.group(2))} over the {m.group(3)} rows is {m.group(4)}."
    if m := _MINUS.match(line):
        a, b, col, diff, pct = m.groups()
        more = not diff.startswith("-")
        diff, pct = diff.lstrip("-"), (pct or "").lstrip("-")
        if diff in ("0", "0.0"):
            return f"{a} and {b} have the same {_noun(col)}."
        timed = re.search(r" (?:year|month|week|day|hour|minute|second|min|hr|h|s)s?$", diff)
        word = ("longer" if more else "shorter") if timed else ("higher" if more else "lower")
        tail = f" ({pct}% {word})" if pct else ""
        return f"The {_noun(col)} for {a} is {diff} {word} than for {b}{tail}."
    if m := _CHANGE.match(line):
        kind, a, b, col, diff, pct = m.groups()
        tail = f" ({pct}%)" if pct else ""
        return f"The {_noun(col)} {'rose' if kind == 'Increase' else 'fell'} by {diff} from {a} to {b}{tail}."
    if m := _CELL.match(line):
        _key, row, col, value = m.groups()
        return f"For {row.strip()}, the {_noun(col)} is {_value(value)}" + ("" if value.rstrip()[-1:] in ".!?" else ".")
    if m := _ROW_CELL.match(line):
        row, col, value = m.groups()
        return f"For {row.strip()}, the {_noun(col)} is {_value(value)}" + ("" if value.rstrip()[-1:] in ".!?" else ".")
    return _sentence(line)


_TEX_SYMBOLS = {"times": "×", "cdot": "·", "approx": "≈", "le": "≤", "leq": "≤", "ge": "≥", "geq": "≥",
                "neq": "≠", "ne": "≠", "pm": "±", "div": "÷", "to": "→", "rightarrow": "→", "Rightarrow": "⇒",
                "%": "%", "degree": "°", "circ": "°", "infty": "∞", "sum": "Σ", "Delta": "Δ", "mu": "µ"}
_TEX_WORDS = re.compile(r"\\(?:text|mathrm|textbf|mathbf|operatorname|textit|mathit)\{([^{}]*)\}")
_TEX_FRAC = re.compile(r"\\[dt]?frac\{([^{}]*)\}\{([^{}]*)\}")
_TEX_DISPLAY = re.compile(r"\$\$(.+?)\$\$|\\\[(.+?)\\\]|\\\((.+?)\\\)", re.S)
_TEX_INLINE = re.compile(r"(?<![\w$])\$([^$\n]*\\[A-Za-z%][^$\n]*)\$(?![\w$])")
_TEX_COMMAND = re.compile(r"\\([A-Za-z]+|%)")
_FENCED = re.compile(r"(```.*?```|`[^`\n]*`)", re.S)


def _frac_part(text: str) -> str:
    text = text.strip()
    return text if re.fullmatch(r"[\w.,%×·]+", text) else f"({text})"


def _plain_tex(tex: str) -> str:
    tex = _TEX_WORDS.sub(r"\1", tex)
    for _ in range(3):  # nested fractions, from the inside out
        tex = _TEX_FRAC.sub(lambda m: f"{_frac_part(m.group(1))} / {_frac_part(m.group(2))}", tex)
    tex = re.sub(r"\\left|\\right|\\[,;:! ]|\\quad|\\qquad", " ", tex)
    tex = _TEX_COMMAND.sub(lambda m: _TEX_SYMBOLS.get(m.group(1), m.group(0)), tex)
    return re.sub(r"[ \t]{2,}", " ", tex.replace("{", "").replace("}", "")).strip()


def plain_math(answer: str) -> str:
    """Formulas a model writes in LaTeX, as the text a reader expects: "OEE = \\frac{Output × Target
    cycle time}{Available time}" was shown as written (Nemotron, a plant-performance table, 27 Oct 2026).
    Prices ("$5 and $10") and code are left alone."""
    if "\\" not in answer:
        return answer
    parts = _FENCED.split(answer)
    for i in range(0, len(parts), 2):  # outside code
        text = _TEX_DISPLAY.sub(lambda m: _plain_tex(next(g for g in m.groups() if g is not None)), parts[i])
        text = _TEX_INLINE.sub(lambda m: _plain_tex(m.group(1)), text)
        if "\\frac" in text or _TEX_WORDS.search(text) or re.search(r"\\(?:times|approx|cdot|le|ge|neq|pm)\b", text):
            # written without delimiters: the commands alone
            text = _plain_tex(text) if "\n" not in text else "\n".join(
                _plain_tex(ln) if "\\" in ln else ln for ln in text.split("\n"))
        parts[i] = text
    return "".join(parts)


# the notes Lattice writes under an answer, by their opening words; the app shows them as badges on
# the answer card, not as italic lines inside it ("*(The answer was cut off.)*" read like part of it)
_NOTES = (("unsourced", "(The assistant did not name its sources"),
          ("cut_off", "(The answer was cut off"),
          ("partial_table", "Note: the document includes only part of this table"),
          ("mixed_parts", "These steps come from different parts of the manual"))
_LAST_PARAGRAPH = re.compile(r"(?:\A|\n\s*\n)\s*\*([^*\n](?:[^\n]*[^*\n])?)\*\s*\Z")


def lift_notes(answer: str) -> tuple[str, list[dict]]:
    """The answer without the notes written at its end, and the notes ({"kind", "text"}) in order."""
    notes: list[dict] = []
    while (m := _LAST_PARAGRAPH.search(answer)) is not None:
        inner = m.group(1).strip()
        kind = next((k for k, start in _NOTES if inner.startswith(start)), None)
        if kind is None:
            break
        text = inner[1:-1] if inner.startswith("(") and inner.endswith(")") else inner
        text = text.removeprefix("Note: ").strip()
        notes.insert(0, {"kind": kind, "text": text[:1].upper() + text[1:]})
        answer = answer[:m.start()].rstrip()
    return answer, notes


_SHOUTED_VERDICT = re.compile(r"^(\**)(YES|NO|Yes|No)\1?\s*[:\-–—]\s*\1?\s*(?=\S)")


def tidy_verdict(answer: str) -> str:
    """"NO: The policy states a maximum of 45 days ..." (Nemotron, 27 Oct 2026) -> "No. The policy ..." """
    m = _SHOUTED_VERDICT.match(answer.lstrip())
    if m is None:
        return answer
    rest = answer.lstrip()[m.end():]
    return f"{m.group(2).capitalize()}. {rest[:1].upper()}{rest[1:]}"


def with_verdict(verdict: str, rule: str) -> str:
    """A yes or no with the rule it rests on: "No. Contractors may not drive Helix forklifts." """
    return f"{verdict.strip().rstrip('.')}. {as_sentence(rule)}"
