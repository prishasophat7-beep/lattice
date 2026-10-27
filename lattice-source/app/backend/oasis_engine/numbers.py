"""Number check: flag figures in an answer that do not appear in its sources.

Small models sometimes calculate, round or invent a figure instead of quoting it (in testing, one
answered "27.75%" by dividing two numbers when the manual states 27.8%; another said "2 million" where
the text has no such figure). On an assembly line a wrong torque value or pressure matters, so every
number in an answer must appear in the excerpts it was based on, or in the question itself.

A number followed by a scale or unit ("2 million", "27.8%", "12 Nm") must appear in the source with the
same scale or unit; a bare "2" elsewhere in the text does not count. Numbers that fail are returned for
the UI to flag; the answer is not blocked, because a unit conversion can be legitimate.

Limitation: a real figure attached to the wrong thing (quoting "$15 million" from a footnote about
something else) passes, since the figure does appear in the source. That needs a semantic check.
"""
from __future__ import annotations

import re

# "9,20,000" (Indian grouping: lakh, crore) before "1,016.50"
_NUM = r"\d{1,2}(?:,\d{2})+,\d{3}(?:\.\d+)?|\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d*\.\d+|\d+"
# Scales and units whose meaning changes the figure; synonyms map to one canonical form.
_UNITS = {
    "%": "%", "percent": "%", "per cent": "%", "percentage points": "pp", "percentage point": "pp",
    "thousand": "k", "k": "k", "million": "m", "millions": "m", "mn": "m", "m": "m",
    "billion": "bn", "billions": "bn", "bn": "bn", "b": "bn", "trillion": "tn",
    "nm": "nm", "n·m": "nm", "bar": "bar", "psi": "psi", "mm": "mm", "cm": "cm", "kg": "kg",
    "°c": "degc", "degc": "degc", "rpm": "rpm", "kw": "kw", "hz": "hz",
    "cycles": "cycles", "hours": "h", "hrs": "h", "minutes": "min", "min": "min", "seconds": "s", "sec": "s",
}
# Tables often give bare figures under a header such as "($ in millions)" or "FY2027 ($K)"; only such
# a header (not the word "million" anywhere) lets a bare table figure verify a scaled one.
_SCALE_HEADERS = {"k": ("in thousands", "($k)", "(k)", "(000s)"),
                  "m": ("in millions", "($m)", "($mm)", "(m)"),
                  "bn": ("in billions", "($bn)", "($b)", "(bn)")}
_UNIT_ALT = "|".join(sorted((re.escape(u) for u in _UNITS), key=len, reverse=True))
# number, then optionally a unit that is not the start of a longer word, or ") %" (tables write
# negatives as "(2) %"); only "%" may follow the ")", so "(due 2026-10-10) M. Chen" is no "10 million"
_FIGURE = re.compile(rf"({_NUM})(?:\)\s?(%)|\s?({_UNIT_ALT})(?![a-z]))?", re.IGNORECASE)


def _unit(m: re.Match) -> str:
    raw = m.group(2) or m.group(3)
    return _UNITS[raw.lower()] if raw else ""
_BARE = re.compile(_NUM)
# "1." / "2)" at the start of a line: list numbering, not a figure
_LIST_MARKER = re.compile(r"(?m)^\s*(?:[-*+]\s*)?\**\d{1,2}[.)]\**\s")
# steps numbered inside a paragraph: "1) Click the Train tab. 2) Select ..." (a large model wrote them
# so, and 1 to 5 were flagged as figures missing from the manual; real-model test, 20 Oct 2026)
_INLINE_STEP = re.compile(r"(?:(?<=^)|(?<=[\s:;.,]))\(?\d{1,2}\)\s")


def without_step_numbers(text: str) -> str:
    """The text without the numbers of its steps: at line starts, and inside a paragraph when it
    numbers two or more ("1) ... 2) ..."; a lone "see figure 5) " stays a figure)."""
    text = _LIST_MARKER.sub(" ", text)
    return _INLINE_STEP.sub(" ", text) if len(_INLINE_STEP.findall(text)) >= 2 else text


def _normalize(raw: str) -> str:
    """'1,016.50' -> '1016.5', '.5' -> '0.5', '007' -> '7', '25.10' -> '25.1'."""
    whole, _, frac = raw.replace(",", "").partition(".")
    whole = whole.lstrip("0") or "0"
    frac = frac.rstrip("0")
    return f"{whole}.{frac}" if frac else whole


def _clean(text: str) -> str:
    return " ".join((text or "").split())  # PDF text is full of tabs and line breaks


# what comes right before a number that names something rather than measures it: a model or
# error code ("R7", "M3", "E-217", "r7_router"), or a section, step, figure, page or version number
_LABEL_BEFORE = re.compile(r"(?:[A-Za-z_]|[A-Za-z]-|\d\.?|\b(?:section|sections|chapter|step|figure|fig\.|table|"
                           r"page|pages|pp?\.|item|rev\.?|revision|version|v|§)\s?|>\s?)$", re.IGNORECASE)


def _is_label(text: str, start: int) -> bool:
    return _LABEL_BEFORE.search(text[max(0, start - 12):start]) is not None


def figures_in(text: str) -> set[tuple[str, str]]:
    """(number, canonical unit or '') for every figure in the text."""
    text = _clean(text)
    return {(_normalize(m.group(1)), _unit(m))
            for m in _FIGURE.finditer(text) if not _is_label(text, m.start())}


def numbers_in(text: str) -> set[str]:
    text = text or ""
    return {_normalize(m.group(0)) for m in _BARE.finditer(text) if not _is_label(text, m.start())}


def _written_out(number: str, unitless: set[str], source_words: str) -> bool:
    """Is this bare figure a scaled table figure written out in full (92,000 for 92 in a "($K)"
    column, 24,690,000,000 for 24,690 "in millions")?"""
    try:
        value = float(number)
    except ValueError:
        return False
    for unit, factor in (("k", 1e3), ("m", 1e6), ("bn", 1e9)):
        if value >= factor and any(h in source_words for h in _SCALE_HEADERS[unit]):
            scaled = value / factor
            if _normalize(f"{scaled:.6f}") in unitless:
                return True
    return False


def _computed(number: str, results: list[float]) -> bool:
    """Is this figure one of the calculated results, as written or rounded ("53.5" for 53.506494)?"""
    try:
        value = float(number)
    except ValueError:
        return False
    decimals = len(number.partition(".")[2])
    return any(abs(round(r, decimals) - value) < 1e-9 or abs(r - value) < 1e-9 for r in results)


def _unit_named(unit: str, source_words: str) -> bool:
    """Does the source name the unit in words, as a table's title or column does ("Downtime hours")?
    Scales and percentages have their own checks."""
    if unit in _SCALE_HEADERS or unit == "%":
        return False
    return any(len(raw) > 2 and raw.isalpha() and _UNITS[raw] == unit and re.search(rf"\b{raw}\b", source_words)
               for raw in _UNITS)


_NUMBER_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
    "seventeen eighteen nineteen twenty".split())} | {"thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
                                                    "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100}
_NUMBER_WORD = re.compile(r"\b(" + "|".join(_NUMBER_WORDS) + r")\b", re.IGNORECASE)


def _spelled(text: str) -> set[tuple[str, str]]:
    """Numbers the text writes as words, as figures: "Six people clicked the link; one entered their
    password" has 6 and 1 (an answer saying "1 employee" was held back as not checking out; Nemotron
    Bolt, 28 Oct 2026)."""
    return {(str(_NUMBER_WORDS[m.group(1).lower()]), "") for m in _NUMBER_WORD.finditer(text or "")}


def unverified_numbers(answer: str, sources: str, question: str = "", computed: list[str] = ()) -> list[str]:
    """Figures in `answer` found neither in `sources` nor in `question`, in answer order.
    computed: results of exact calculations (the calculate tool); they match with any unit and
    rounded to the answer's precision."""
    results = []
    for c in computed:
        try:
            results.append(float(c))
        except ValueError:
            pass
    known = figures_in(sources) | figures_in(question) | _spelled(sources)
    any_unit = {n for n, _ in known}           # a bare "12" in the answer matches "12 Nm"
    unitless = {n for n, u in known if not u}  # table cells: "24,690", not "2 %"
    source_words = _clean(sources).lower()
    body = _clean(without_step_numbers(answer or ""))
    checked = [*results, *(v for n, u in figures_in(question) for v in _values(n, u))]
    open_: list[tuple[str, str]] = []  # (number, shown) not found yet
    for m in _FIGURE.finditer(body):
        if _is_label(body, m.start()):  # "R7", "E-217", "section 4.3": names, not figures
            continue
        number, unit = _normalize(m.group(1)), _unit(m)
        headers = _SCALE_HEADERS.get(unit, ())
        if ((number, unit) in known or (not unit and number in any_unit)
                # "24,690 million" against a table of 24,690 "($ in millions)"
                or (number in unitless and any(h in source_words for h in headers))
                # "31.9%" against a "Margin %" column of bare 31.9
                or (unit == "%" and number in unitless and "%" in source_words)
                # "196 hours" against a bare 196 in a table titled "Downtime hours by cause"
                or (unit and number in unitless and _unit_named(unit, source_words))
                # "$92,000" against 92 under "Cost ($K)"
                or (not unit and _written_out(number, unitless, source_words))
                # "53.5%" from calculate("412 / 770 * 100") = 53.506494
                or _computed(number, results)):
            checked += _values(number, unit)
        else:
            open_.append((number, m.group(0).strip(), unit))
    # one step of arithmetic on the answer's own checked figures: "an increase of 73" between 697
    # and 770, "10% of 38 is 3.8"; the answer has few figures, so a made-up one rarely matches
    # ... and the answer's own written calculations whose every figure is checked: "9,20,000 x 3% =
    # 27,600", then "27,600 + 11,700 + 9,750 = 49,050" (a penalty worked out over three orders was
    # flagged as not in the document: DeepSeek, 26 Oct)
    # (without bold marks: "27,600 + 11,700 + 9,750 = **49,050 INR**" was no calculation, and the right
    # total was flagged; Nemotron 3.5 Lightning, 27 Oct 2026)
    shown_sums = _equations(re.sub(r"\*{1,3}|(?<!\w)_{1,3}|_{1,3}(?!\w)", "", body))
    while open_:
        derived = _one_step(checked) + _checked_equations(shown_sums, checked)
        found = [x for x in open_ if _computed(x[0], derived)]
        if not found:
            break
        for n, _, u in found:
            checked += _values(n, u)
        open_ = [x for x in open_ if x not in found]
    out: list[str] = []
    for _, shown, _ in open_:
        if shown not in out:
            out.append(shown)
    # codes are checked whole: "E18" when the manual only has E17 is wrong, "R7" in "the R7 guide" is not
    known_codes = codes_in(sources) | codes_in(question)
    for code, shown in _codes(answer or ""):
        if code not in known_codes and shown not in out:
            out.append(shown)
    return out


# a model, error or part code: up to 4 letters, an optional hyphen, digits ("E17", "E-217", "R7", "T30")
_CODE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{1,4})-?(\d+)(?![\d.,]?\d)")


def _codes(text: str) -> list[tuple[str, str]]:
    return [((m.group(1) + m.group(2)).lower(), m.group(0)) for m in _CODE.finditer(text)]


def codes_in(text: str) -> set[str]:
    return {code for code, _ in _codes(text or "")}


_EQUATION = re.compile(r"((?:[(\s]*(?:" + _NUM + r")\s*%?[)\s]*[-+*/×x÷]\s*)+[(\s]*(?:" + _NUM + r")\s*%?[)\s]*)=\s*(?:INR|Rs\.?|₹|\$|€)?\s*(" + _NUM + r")")


def _equations(text: str) -> list[tuple[str, float]]:
    """(left side, value) of each calculation the text writes out ("7,80,000 × 1.5% = 11,700")."""
    from .calc import CalcError, calculate
    out = []
    for m in _EQUATION.finditer(text):
        left = m.group(1).replace("x", "*").strip()
        try:
            value = float(calculate(left))
        except (CalcError, ValueError):
            continue
        out.append((left, value))
    return out


def _checked_equations(equations: list[tuple[str, float]], checked: list[float]) -> list[float]:
    """The values of the written calculations whose every figure is already checked."""
    known = {round(v, 6) for v in checked}
    out = []
    for left, value in equations:
        figures = []
        for f in re.finditer(r"(" + _NUM + r")\s*(%?)", left):
            v = float(_normalize(f.group(1)))
            figures.append(v / 100 if f.group(2) else v)
        # a count the answer works out itself ("1.5 % × 2 = 3.0 %": 14 days late is 2 full weeks) is no
        # figure of the documents; the chain to the total broke on it (Nemotron 3.5 Lightning, 27 Oct 2026)
        if figures and all(round(v, 6) in known or round(v * 100, 6) in known
                           or (v == int(v) and 0 < v <= SMALL_COUNT) for v in figures) \
                and any(not (v == int(v) and v <= SMALL_COUNT) for v in figures):
            out.append(value)
    return out


MAX_DERIVED_FROM = 24  # figures combined pairwise: beyond this, too much would match by chance
SMALL_COUNT = 12  # a whole number up to this, in a written calculation, may be a count the answer works out


def _values(number: str, unit: str) -> list[float]:
    try:
        v = float(number)
    except ValueError:
        return []
    return [v, v / 100] if unit == "%" else [v]


def _one_step(values: list[float]) -> list[float]:
    vs = list(dict.fromkeys(values))[:MAX_DERIVED_FROM]
    out = []
    for i, a in enumerate(vs):
        for b in vs[i + 1:]:
            out += [a + b, abs(a - b), a * b]
            if b:  # a ratio, or the same as a percentage: "6,500 over 92,000, about 7.1%"
                out += [a / b, a / b * 100]
            if a:
                out += [b / a, b / a * 100]
    return out
