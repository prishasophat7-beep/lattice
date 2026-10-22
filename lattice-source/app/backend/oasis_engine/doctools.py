"""Reading tools over the document tree, shared by the Chat agent and the Manuals pipeline.

What a model needs to reason over a large document, beyond "search" and "read the whole section":
- read: a section's own text (not its whole subtree), its subsections listed so the model can
  go deeper, or the passages around a phrase. Reading a parent no longer pulls in (and gets
  cited for) everything under it.
- query_table: filter, sort and add up a table in code. Models misread and miscompare rows read
  as prose (the 0.8B ranked a 4-row margin table wrongly); stating the operation is reliable.
- find_text: where exact words occur ("45 days"), line by line.
- find_facts: the sentences and table rows that state limits, amounts, deadlines and conditions
  about a topic, grouped by section; this answers "how long / how much / am I allowed" and tells
  whether a vague question has one meaning or several.
- doc_map: a document's tree with summaries and markers for tables and pictures.
- verify_quote: does a quote really occur in a section (the evidence ledger's check).

All results are short: token budgets matter on a 4B model on a laptop.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from .evidence import _markdown_units, _page_units
from .models import Doc
from .search import SectionIndex, is_code, tokenize
from .tables import markdown_tables

READ_CHARS = 4_000      # one page of a section's own text
AROUND_CHARS = 700      # either side... in total, around each match
MAX_AROUND = 3
SUMMARY_CHARS = 120
MAX_FACTS = 8
FACT_CHARS = 220
MAX_TEXT_HITS = 10
TABLE_ROWS = 20
WHOLE_TABLE_CHARS = 2_000_000  # a section read whole when code works out from its tables
MAP_CHARS = 6_000

_PAGE_LABEL = re.compile(r"\[page \d+\]")
_NUMBER = re.compile(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?|-?\d*\.\d+|-?\d+")


def units(doc: Doc, nid: str, whole: bool = False) -> list[str]:
    """Sentences and table rows of a section's own text (with whole: of its subtree too)."""
    text = doc.section_text(nid, 60_000) if whole or doc.kind == "pages" else doc.sections[nid].own_text
    return _page_units(text) if doc.kind == "pages" else _markdown_units(text)


# ── read ──

def read_view(doc: Doc, nid: str, part: int = 1, around: str = "", whole: bool = False, ref_for=None) -> str:
    """A section to read: its own text (a page of it), then its subsections by title and summary.
    around: the passages of the section and its subsections around these words instead.
    whole: the section with all its subsections (the old read). ref_for: node id -> the reference
    the model uses for a subsection."""
    ref_for = ref_for or (lambda n: n)
    s = doc.sections[nid]
    head = f"{doc.display_title} > {' > '.join(doc.short_path(nid))} ({doc.locator(nid)})"
    if around.strip():
        return head + "\n" + passages(doc, nid, around)
    if whole or doc.kind == "pages":
        text = doc.section_text(nid, 60_000)
    else:
        text = s.own_text.strip()
    pages = max(1, -(-len(text) // READ_CHARS))
    part = min(max(1, part), pages)
    piece = text[(part - 1) * READ_CHARS: part * READ_CHARS]
    out = [head, piece or "(no text of its own: see its subsections)"]
    if part < pages:
        out.append(f"[part {part} of {pages}: continue with part={part + 1}]")
    if not whole and s.children:
        out.append("Subsections (read them by their ref):")
        out += [f"- [{ref_for(c)}] {doc.sections[c].title}: {_short(doc.sections[c].summary, SUMMARY_CHARS)}"
                for c in s.children]
    return "\n".join(out)


def passages(doc: Doc, nid: str, words: str, chars: int = AROUND_CHARS, most: int = MAX_AROUND) -> str:
    """The stretches of a section (subsections included) around the given words, best first."""
    text = " ".join(_PAGE_LABEL.sub(" ", doc.section_text(nid, 200_000)).split())
    terms = [t for t in dict.fromkeys(tokenize(words)) if len(t) > 1]
    if not terms:
        return text[:chars]
    # a word's start finds its forms: "invite" finds "invitation", "expire" "expires"
    hits = sorted((m.start(), t) for t in terms
                  for m in re.finditer(rf"(?<![a-z0-9]){re.escape(t if len(t) < 6 else t[:-1])}", text, re.I))
    if not hits:
        return text[:chars] + ("..." if len(text) > chars else "")
    scored = []  # (distinct terms in the window, start)
    for pos, _ in hits:
        start = max(0, pos - chars // 3)
        scored.append((len({t for p, t in hits if start <= p < start + chars}), start))
    picked: list[int] = []
    for _, start in sorted(scored, key=lambda x: (-x[0], x[1])):
        if all(abs(start - p) >= chars for p in picked):
            picked.append(start)
        if len(picked) == most:
            break
    return "\n...\n".join(("..." if p else "") + text[p:p + chars] + ("..." if p + chars < len(text) else "")
                          for p in sorted(picked))


# ── tables ──

_CODE_CELL = re.compile(r"(?!(?:inr|rs|usd|eur|gbp|aed|sgd|cad|aud)\.?\s?\d)[A-Za-z]{1,6}-?\d[\w./-]*",
                        re.IGNORECASE)

_CURRENCY_LEAD = re.compile(r"^(?:inr|rs|usd|eur|gbp|aed|sgd|cad|aud)\.?\s*(?=[\d(])", re.IGNORECASE)

def number(cell: str) -> float | None:
    """The value of a table cell: "1,870" 1870, "+31.0%" 31.0, "(2)" -2, "18-27 days" 18 (a range:
    its lower end), "$4.5K" 4.5. None for text."""
    text = cell.strip().replace("−", "-")
    if _CODE_CELL.fullmatch(text):
        return None  # a part or order number ("HX9-4002", "PO-26-0503", "A302"), not the value 9
    text = _CURRENCY_LEAD.sub("", text)  # "Rs.500" is 500, not .500
    negative = text.startswith("(") and text.endswith(")")
    m = _NUMBER.search(text)
    if not m:
        return None
    try:
        value = float(m.group(0).replace(",", ""))
    except ValueError:
        return None
    return -abs(value) if negative else value


_OPS = {"=", "==", "!=", "<", "<=", ">", ">=", "~", "contains"}


def _match(cell: str, op: str, value) -> bool:
    if op in ("~", "contains"):
        return str(value).casefold() in cell.casefold()
    if op in ("=", "=="):
        a, b = number(cell), number(str(value))
        if b is not None and re.fullmatch(r"[-+\d.,%$()\s]+", str(value)):  # a figure: compared as one
            return a == b
        return cell.strip().casefold() == str(value).strip().casefold()
    if op == "!=":
        return not _match(cell, "=", value)
    a, b = number(cell), number(str(value))
    if a is None or b is None:
        return False
    return {"<": a < b, "<=": a <= b, ">": a > b, ">=": a >= b}[op]


def _column(columns: list[str], name: str) -> int | None:
    if not name:
        return None
    key = name.casefold().strip()
    for i, c in enumerate(columns):
        if c.casefold().strip() == key:
            return i
    best = max(range(len(columns)), key=lambda i: SequenceMatcher(None, key, columns[i].casefold()).ratio(),
               default=None)
    if best is not None and (key in columns[best].casefold()
                             or SequenceMatcher(None, key, columns[best].casefold()).ratio() >= 0.6):
        return best
    return None


def query_table(doc: Doc, nid: str, table: int = 0, where: list | None = None, columns: list | None = None,
                sort: str = "", descending: bool = False, limit: int = TABLE_ROWS,
                aggregate: dict | None = None) -> tuple[str, list[str]]:
    """Filter, sort and add up one table of a section. Returns (text for the model, computed
    values). where: [[column, op, value], ...] with op = < <= > >= = != ~ (contains). aggregate:
    {"sum"|"avg"|"min"|"max"|"count": column}. Units come from the column names."""
    tables = markdown_tables(doc.section_text(nid, WHOLE_TABLE_CHARS), max_rows=None)
    if not tables:
        return "This section has no table that can be read as rows.", []
    if not 0 <= table < len(tables):
        return f"There are {len(tables)} tables here (table=0..{len(tables) - 1}).", []
    t = tables[table]
    cols, rows = t["columns"], t["rows"]
    problems = []
    for cond in where or []:
        if not isinstance(cond, (list, tuple)) or len(cond) != 3 or str(cond[1]) not in _OPS:
            problems.append(f"bad condition {cond!r} (use [column, op, value], op one of {sorted(_OPS)})")
            continue
        ci = _column(cols, str(cond[0]))
        if ci is None:
            problems.append(f"no column {cond[0]!r}")
            continue
        rows = [r for r in rows if _match(r[ci], str(cond[1]), cond[2])]
    if sort:
        ci = _column(cols, sort)
        if ci is None:
            problems.append(f"no column {sort!r} to sort by")
        else:
            numeric = all(number(r[ci]) is not None for r in rows if r[ci].strip())
            rows = sorted(rows, key=(lambda r: number(r[ci]) if number(r[ci]) is not None else float("-inf"))
                          if numeric else (lambda r: r[ci].casefold()), reverse=descending)
    computed: list[str] = []
    lines = [f"Table {table}: {t.get('title') or 'untitled'} ({len(t['rows'])} rows; columns: {', '.join(cols)})"]
    for agg, col in (aggregate or {}).items():
        ci = _column(cols, str(col)) if agg != "count" or col else None
        if agg == "count":
            computed.append(str(len(rows)))
            lines.append(f"count = {len(rows)}")
            continue
        if ci is None or agg not in ("sum", "avg", "min", "max"):
            problems.append(f"cannot {agg} {col!r}")
            continue
        values = [v for v in (number(r[ci]) for r in rows) if v is not None]
        if not values:
            problems.append(f"no numbers in {cols[ci]!r}")
            continue
        result = {"sum": sum(values), "avg": sum(values) / len(values), "min": min(values), "max": max(values)}[agg]
        shown = f"{round(result, 4):.12g}"  # 1607600, 200950, 29.4667: never "1.6e+06"
        computed.append(shown)
        lines.append(f"{agg}({cols[ci]}) over {len(values)} rows = {shown}")
    keep = [ci for ci in (_column(cols, str(c)) for c in (columns or [])) if ci is not None] or list(range(len(cols)))
    lines.append(" | ".join(cols[i] for i in keep))
    lines += [" | ".join(r[i] for i in keep) for r in rows[:max(1, min(limit, 200))]]
    if len(rows) > limit:
        lines.append(f"... {len(rows) - limit} more matching rows (raise limit or filter)")
    if not rows:
        lines.append("(no rows match)")
    if problems:
        lines.append("Note: " + "; ".join(problems))
    sample = _SAMPLE_ROWS.search(doc.section_text(nid, 200_000))
    if sample:
        lines.append(f"Warning: the document holds only the first {sample.group(1)} of {sample.group(2)} rows; "
                     "results cover those rows only. Say so, and use the document's own summary if it has one.")
    return "\n".join(lines), computed


_RANKING = re.compile(
    r"\b(best|worst|highest|lowest|most|least|largest|smallest|biggest|longest|shortest|cheapest|fewest|greatest|costliest|priciest|slowest|fastest|quickest|heaviest|lightest|hottest|coldest|top|bottom|maximum|minimum|rank\w*|"
    r"compar\w*|vs\.?|versus|more|less|fewer|greater|total|sum|average|mean|increase\w*|decrease\w*|"
    r"grow\w*|growth|chang\w*|differ\w*|higher|lower|better|worse)\b", re.IGNORECASE)
_SAMPLE_ROWS = re.compile(r"\b(?:showing|shows|first)\s+(?:the\s+)?(?:first\s+)?(\d[\d,]*)\s+(?:of|out of)\s+"
                          r"(\d[\d,]*)\s+rows\b", re.IGNORECASE)  # "of 2,000 rows" too
_TOTAL_ROW = re.compile(r"\b(total|sum|overall|grand|all)\b", re.IGNORECASE)
# words that rank a column without naming it: "the most expensive part" ranks "Price (INR)" (the 0.8B
# answered a part at 36,105 INR; the 412,000 INR one was in the table; final check)
_COLUMN_HINTS = (
    (re.compile(r"\b(expensive|cheap\w*|cost(ly|liest)|pric(ey|iest)|afford\w*)\b", re.IGNORECASE),
     {"price", "cost", "amount", "rate", "fee", "value"}),
    (re.compile(r"\b(slowest|fastest|quickest|soonest|wait\w*)\b", re.IGNORECASE), {"time", "lead", "duration"}),
    (re.compile(r"\b(heaviest|lightest)\b", re.IGNORECASE), {"weight", "mass"}),
    (re.compile(r"\b(hottest|coldest|warmest|coolest)\b", re.IGNORECASE), {"temperature", "temp"}),
)


# ... and a column asked for without its name: "what does the part cost?" reads "Price (INR)"
_CELL_HINTS = (
    (re.compile(r"\b(cost|costs|how much|pay|charged?)\b", re.IGNORECASE), {"price", "cost", "amount", "rate", "fee"}),
)


def column_words(question: str) -> set[str]:
    """The words a column the question asks for may be named by."""
    words = ranking_words(question)
    for pattern, columns in _CELL_HINTS:
        if pattern.search(question):
            words |= columns
    return words


def ranking_words(question: str) -> set[str]:
    """The question's words, with the column words its ranking word stands for."""
    words = set(tokenize(question))
    for pattern, columns in _COLUMN_HINTS:
        if pattern.search(question):
            words |= columns
    return words


def _plain(x: float) -> str:
    """A worked-out value as the figure check reads it: 25800, 29.47."""
    return f"{round(x, 2):.12g}"


def _shown(x: float) -> str:
    """A worked-out value as people read it: 25,800, 29.47."""
    return f"{round(x, 2):,.12g}"


def table_highlights(doc: Doc, nid: str, question: str) -> tuple[list[str], list[str]]:
    """For a question that ranks, compares or adds up ("which month had the best margin?", "March
    vs January"), the answer worked out in code from the section's tables: highest and lowest row
    of each column the question names, total and average, and the differences between the rows it
    names. Returns (lines for the model, computed values). Totals rows are left out of rankings."""
    if not _RANKING.search(question):
        return [], []
    text = doc.section_text(nid, WHOLE_TABLE_CHARS)
    sample = _SAMPLE_ROWS.search(text)
    if sample:
        # "Showing first 10 of 96 rows": a ranking of the rows shown is not the file's (its highest
        # response time was 133 ms in the rows shown, 1840.6 ms in the file); the note may say more
        return [f"Only the first {sample.group(1)} of {sample.group(2)} rows are in the document: highest, "
                "lowest, totals and averages cannot be worked out from them. Use the summary the document "
                "gives, if any, and say that the full data is not included."], []
    asked = ranking_words(question)
    lines, computed = [], []
    for t in markdown_tables(text, max_rows=None)[:3]:
        cols, rows = t["columns"], t["rows"]
        if len(rows) < 2 or len(cols) < 2:
            continue
        numeric = [ci for ci in range(len(cols)) if sum(number(r[ci]) is not None for r in rows) >= max(2, len(rows) // 2)]
        label = next((ci for ci in range(len(cols)) if ci not in numeric), 0)
        named = [ci for ci in numeric if set(tokenize(cols[ci])) & asked] or (numeric if len(numeric) == 1 else [])
        if not named:
            continue
        body = [r for r in rows if not _TOTAL_ROW.search(r[label])]
        # the rows the question names, in its order ("March vs January": March minus January)
        order = tokenize(question)
        picked_rows = sorted((r for r in body if set(tokenize(r[label])) & asked),
                             key=lambda r: min(order.index(t) for t in set(tokenize(r[label])) & asked))
        for ci in named[:3]:
            vals = [(r[label], number(r[ci])) for r in body if number(r[ci]) is not None]
            if len(vals) < 2:
                continue
            hi, lo = max(vals, key=lambda v: v[1]), min(vals, key=lambda v: v[1])
            total = sum(v for _, v in vals)
            avg = total / len(vals)
            share = "%" in cols[ci] or any("%" in r[ci] for r in body)  # percentages do not add up
            # one plain fact per line: a small model reads "Lowest Expenses: January" better than a
            # sentence with several (it answered "February" to "the lowest expenses" from that)
            col = cols[ci]
            lines += [f"Highest {col}: {hi[0]} ({_shown(hi[1])})", f"Lowest {col}: {lo[0]} ({_shown(lo[1])})"]
            if not share:
                lines.append(f"Total {col} over the {len(vals)} rows: {_shown(total)}")
            lines.append(f"Average {col} over the {len(vals)} rows: {_shown(avg)}")
            computed += [_plain(avg)] if share else [_plain(total), _plain(avg)]
            if len(picked_rows) == 2:
                a, b = picked_rows
                va, vb = number(a[ci]), number(b[ci])
                if va is not None and vb is not None:
                    line = f"{a[label]} minus {b[label]} in {col}: {_shown(va - vb)}"
                    computed.append(_plain(va - vb))
                    if vb:
                        line += f" ({_shown((va - vb) / abs(vb) * 100)}% of {b[label]})"
                        computed.append(_plain((va - vb) / abs(vb) * 100))
                    lines.append(line)
                    # "the increase between January and March", "grow from January to February":
                    # the change from the first named to the second, said as a rise or a fall
                    if va:
                        rise = vb - va
                        lines.append(f"{'Increase' if rise >= 0 else 'Decrease'} from {a[label]} to {b[label]} "
                                     f"in {col}: {_shown(abs(rise))} ({_shown(abs(rise) / abs(va) * 100)}%)")
                        computed += [_plain(abs(rise)), _plain(abs(rise) / abs(va) * 100)]
        # "which line had the highest total downtime?": the columns are what is ranked (Line A, B, C)
        shares = "%" in doc.sections[nid].title or "percent" in doc.sections[nid].title.casefold()
        totals = [(cols[ci], sum(number(r[ci]) for r in body if number(r[ci]) is not None)) for ci in named
                  if not shares and "%" not in cols[ci] and not _TOTAL_ROW.search(cols[ci])
                  and not any("%" in r[ci] for r in body)]
        if len(totals) >= 2 and not picked_rows:
            hi, lo = max(totals, key=lambda v: v[1]), min(totals, key=lambda v: v[1])
            lines += [f"Highest column total: {hi[0]} ({_shown(hi[1])})", f"Lowest column total: {lo[0]} ({_shown(lo[1])})"]
            computed += [_plain(hi[1]), _plain(lo[1])]
    return lines, computed


def partial_table_note(text: str) -> str | None:
    """The note under a table given only in part ("Showing first 10 of 96 rows. ... mean response
    time 169.6 ms; one 503 spike at 13:30Z (1840.6 ms)."), or None for a complete table."""
    m = _SAMPLE_ROWS.search(text)
    if not m:
        return None
    start = max(text.rfind("\n", 0, m.start()) + 1, 0)
    return " ".join(text[start:].replace("*", " ").split())


def table_cell(doc: Doc, nid: str, question: str) -> tuple[str, str] | None:
    """The one cell a question names by its row and its column ("the revenue in March"), as
    ("Month: March · Revenue: 291,400", "291,400"); None when it names no single cell."""
    cell = scored_table_cell(doc, nid, question)
    return cell[1:] if cell else None


def scored_table_cell(doc: Doc, nid: str, question: str) -> tuple[int, str, str] | None:
    """table_cell with how strongly the question names it: the words its row and column labels
    share with the question, a range the question's figure falls in counting double (to pick
    between sections that each name a cell)."""
    # words, and short codes ("G3"), not "am"
    asked = {t for t in tokenize(question) if len(t) > 2 or is_code(t)}
    found = []
    for t in markdown_tables(doc.section_text(nid, WHOLE_TABLE_CHARS), max_rows=None)[:3]:
        cols, rows = t["columns"], t["rows"]
        named_cols = [ci for ci in range(1, len(cols)) if set(tokenize(cols[ci])) & asked]
        if not named_cols:  # "what does it cost?": the price column
            hinted = column_words(question) - set(tokenize(question))
            named_cols = [ci for ci in range(1, len(cols)) if set(tokenize(cols[ci])) & hinted]
        if len(named_cols) > 1:
            # "a Tier 2 city": "Tier 2 city" over "Tier 1 city" (the number counts here)
            words = set(tokenize(question))
            shared = {ci: len(set(tokenize(cols[ci])) & words) for ci in named_cols}
            named_cols = [ci for ci in named_cols if shared[ci] == max(shared.values())]
        # columns told apart by a range the question's figure falls in: "the flight takes 9 hours"
        # is "Air (international, over 6 hours)", not "up to 6 hours" (final test)
        col_ranged = _in_ranges(cols[1:], question)
        if col_ranged:
            named_cols = [ci + 1 for ci in col_ranged if ci + 1 in named_cols or len(col_ranged) == 1]
        elif not named_cols:
            # a column named through the section's list: "Pune" is in "Tier 1 cities: Mumbai, ...,
            # Pune", so the "Tier 1 city" column (final test: Jaipur got the Tier 1 rate)
            listed = _listed_column(cols, doc.section_text(nid, 200_000), asked)
            if listed is not None:
                named_cols, col_ranged = [listed], [listed]
        if not named_cols and len(cols) == 2:
            named_cols = [1]  # "Expense | Approval needed": the one column there is to read
        named_rows = [r for r in rows if set(tokenize(r[0])) & asked]
        if len(named_rows) > 1:
            # "part HX9-4471": the row with the whole code, not every "HX9-..." row (a code's pieces
            # are words too)
            shared = [len(set(tokenize(r[0])) & asked) for r in named_rows]
            named_rows = [r for r, n in zip(named_rows, shared) if n == max(shared)]
        # rows the same way: INR 30,000 is "Single expense above INR 25,000", not "above INR 1,00,000"
        row_ranged = _in_ranges([r[0] for r in rows], question)
        if row_ranged:
            named_rows = [rows[ri] for ri in row_ranged]
        if len(named_cols) == 1 and len(named_rows) == 1:
            r, ci = named_rows[0], named_cols[0]
            score = (len(set(tokenize(r[0])) & asked) + len(set(tokenize(cols[ci])) & asked)
                     + 2 * bool(col_ranged) + 2 * bool(row_ranged))
            found.append((score, f"{cols[0]}: {r[0]} · {cols[ci]}: {r[ci]}", r[ci]))
    return found[0] if len(found) == 1 else None


_RANGE = re.compile(r"\b(up to|under|below|less than|within|over|above|more than|at least)\s+"
                    r"(?:(inr|rs\.?|usd|eur|₹|\$)\s*)?(\d[\d,]*(?:\.\d+)?)\s*([a-z]+)?", re.IGNORECASE)
_FIGURE = re.compile(r"(?:(inr|rs\.?|usd|eur|₹|\$)\s*)?(\d[\d,]*(?:\.\d+)?)\s*([a-z]+)?", re.IGNORECASE)


_LIST = re.compile(r"([A-Za-z][\w ]{1,40}?):\s*([^.:\n]+(?:,[^.:\n]+)+)")


def _listed_column(cols: list[str], text: str, asked: set[str]) -> int | None:
    """The column whose name heads a list in the text ("Tier 1 cities: Mumbai, ..., Pune") that has
    a word of the question, when exactly one does."""
    hits = set()
    for label, items in _LIST.findall(text):
        if not {t for item in items.split(",") for t in tokenize(item)} & asked:
            continue
        named = set(tokenize(label))
        shared = {ci: len(set(tokenize(cols[ci])) & named) for ci in range(1, len(cols))}
        most = max(shared.values(), default=0)
        best = [ci for ci, n in shared.items() if n == most]
        if most >= min(2, len(named)) and len(best) == 1:
            hits.add(best[0])
    return next(iter(hits)) if len(hits) == 1 else None


def _kind(currency: str | None, unit: str | None) -> str:
    """What a figure counts: its currency, else its unit ("hours" and "hour" alike)."""
    if currency:
        return {"₹": "inr", "rs": "inr", "rs.": "inr", "$": "usd"}.get(currency.lower(), currency.lower())
    return (unit or "").lower().rstrip("s")


def _in_ranges(labels: list[str], question: str) -> list[int]:
    """The labels with a range ("over 6 hours", "above INR 25,000") that a figure in the question, of
    the same kind, falls in; of several, the narrowest ("above 25,000" and "above 1,00,000": 2,00,000
    is the second)."""
    figures = [(_kind(c, u), float(n.replace(",", ""))) for c, n, u in _FIGURE.findall(question)]
    hits = []
    for li, label in enumerate(labels):
        m = _RANGE.search(label)
        if not m:
            continue
        word, kind, limit = m.group(1).lower(), _kind(m.group(2), m.group(4)), float(m.group(3).replace(",", ""))
        for k, x in figures:
            if k != kind or not kind:
                continue
            upper = word in ("up to", "within", "under", "below", "less than")
            inside = (x <= limit if word in ("up to", "within") else x < limit if upper
                      else x >= limit if word == "at least" else x > limit)
            if inside:
                hits.append((li, -limit if upper else limit))
            break
    best = max((h[1] for h in hits), default=None)
    return [li for li, bound in hits if bound == best]


def statements(doc: Doc, nid: str) -> list[str]:
    """What a section states, one fact each: its sentences, and each cell of a table row with the
    row's first cell ("Leave Type: Privilege Leave (PL) · Encashment: Up to 15 days at year-end")."""
    out = []
    for unit in units(doc, nid, whole=True):
        cells = unit.split(" · ")
        out += [f"{cells[0]} · {c}" for c in cells[1:]] if len(cells) > 2 else [unit]
    return out


def excerpt(doc: Doc, nid: str, question: str, chars: int) -> str:
    """A section for the model to answer from, within chars: whole when it fits, else the passages
    that match the question (cutting a long section at its start lost what was asked about)."""
    text = doc.section_text(nid, chars)
    if not text.endswith("[... section truncated ...]"):
        return text
    head = f"{'#' * (doc.sections[nid].depth + 1)} {doc.sections[nid].title}\n" if doc.kind != "pages" else ""
    return head + "(passages of a long section)\n" + passages(doc, nid, question, chars=max(400, chars // 3), most=3)


# ── find ──

def find_text(index: SectionIndex, text: str, allowed: set[int] | None = None,
              document: int | None = None) -> list[tuple[int, str, str]]:
    """(doc index, node id, line) where the exact words occur (case-insensitive), deepest section
    first. Words are matched whole ("45 days" is not "145 days")."""
    phrase = " ".join(text.split())
    if not phrase:
        return []
    pattern = re.compile(rf"(?<![\w]){re.escape(phrase)}(?![\w])", re.IGNORECASE)
    out = []
    for di, doc in enumerate(index.docs):
        if (allowed is not None and di not in allowed) or (document is not None and di != document):
            continue
        for nid, s in doc.sections.items():
            own = doc.section_text(nid, 200_000) if doc.kind == "pages" else f"{s.title}\n{s.own_text}"
            for line in own.splitlines():
                if pattern.search(line):
                    out.append((di, nid, _short(" ".join(line.split()), FACT_CHARS, around=pattern.search(line).start())))
                    if len(out) >= MAX_TEXT_HITS:
                        return out
    return out


_KINDS = {
    "limit": r"\b(up to|at most|no more than|not more than|maximum|max\.?|minimum|min\.?|at least|limit|cap|"
             r"exceed|within|cannot|can not|not allowed|may not|must not|not permitted|prohibited|never|only)\b",
    "amount": r"(\d|\b(cost|price|fee|budget|allowance|rate|salary|total)\b)",
    "deadline": r"\b(before|after|within|by|deadline|due|days?|weeks?|months?|years?|hours?|notice)\b",
    "condition": r"\b(if|unless|must|required|requires|only|eligible|approval|approve[sd]?|subject to|provided)\b",
    "definition": r"\b(means|is defined|refers to|stands for|abbreviation)\b",
}


def find_facts(index: SectionIndex, topic: str, kind: str = "any", allowed: set[int] | None = None,
               hits: list | None = None) -> list[tuple[int, str, str, str]]:
    """(doc index, node id, sentence or table row, kind) stating limits, amounts, deadlines,
    conditions or definitions about a topic, from the sections that match it best."""
    kinds = [k for k in _KINDS if kind in ("any", "", k)] or list(_KINDS)
    terms = {t for t in tokenize(index.spell(topic)) if len(t) > 1}
    candidates = hits if hits is not None else [h for h in index.search(topic)
                                                if allowed is None or h.doc_index in allowed][:12]
    out, seen = [], set()
    for h in candidates:
        doc = index.docs[h.doc_index]
        for unit in units(doc, h.node_id):
            words = set(tokenize(unit))
            if not words & terms and not (terms & set(tokenize(doc.sections[h.node_id].title))):
                continue
            found = next((k for k in kinds if re.search(_KINDS[k], unit, re.IGNORECASE)), None)
            if found is None or unit in seen:
                continue
            seen.add(unit)
            out.append((h.doc_index, h.node_id, _short(unit, FACT_CHARS), found))
            if len(out) == MAX_FACTS:
                return out
    return out


# ── map ──

def doc_map(doc: Doc, ref_for, depth: int = 3, chars: int = MAP_CHARS) -> str:
    """The document's sections as a tree: [ref] title (place): summary, with ▦ for a table (its
    columns and rows) and ▣ for a picture."""
    lines, used = [f"{doc.display_title}: {doc.description or 'no description'}"], 0
    for s in doc.sections.values():
        if s.depth >= depth:
            continue
        marks = []
        for t in markdown_tables(s.own_text)[:2]:
            marks.append(f"▦ table: {' | '.join(t['columns'][:6])} ({len(t['rows'])} rows)")
        if s.image_notes:
            caption = next(iter(s.image_notes.values())).get("caption", "")
            marks.append(f"▣ {len(s.image_notes)} picture{'s' if len(s.image_notes) > 1 else ''}"
                         + (f": {_short(caption, 60)}" if caption else ""))
        hidden = len(s.children) if s.depth + 1 >= depth and s.children else 0
        place = doc.locator(s.node_id)
        place = "" if place in (s.title, doc.display_title) or doc.kind != "pages" else f" ({place})"
        line = (f"{'  ' * s.depth}[{ref_for(s.node_id)}] {s.title}{place}: "
                f"{_short(s.summary, SUMMARY_CHARS)}" + "".join(f" {m}" for m in marks)
                + (f" (+{hidden} subsections)" if hidden else ""))
        if used + len(line) > chars:
            lines.append("  ... (more sections: search for them, or map one part with section=ref)")
            break
        lines.append(line)
        used += len(line)
    return "\n".join(lines)


# ── the evidence ledger ──

def verify_quote(doc: Doc, nid: str, quote: str, threshold: float = 0.9) -> tuple[bool, str]:
    """Does the quote occur in the section (subsections included)? Spacing, case and Markdown marks
    are ignored; a close match (a word changed) counts at the threshold. Returns (ok, the closest
    sentence or row)."""
    def norm(text: str) -> str:
        return " ".join(re.sub(r"[*_`|]+", " ", text).casefold().split())
    q = norm(quote)
    if len(q) < 3:
        return False, ""
    whole = norm(_PAGE_LABEL.sub(" ", doc.section_text(nid, 200_000)))
    if q in whole:
        return True, quote.strip()
    best, best_ratio = "", 0.0
    for unit in units(doc, nid, whole=True):
        u = norm(unit)
        ratio = SequenceMatcher(None, q, u).ratio() if len(u) < 3 * len(q) + 40 else \
            max((SequenceMatcher(None, q, u[i:i + len(q) + 20]).ratio() for i in range(0, max(1, len(u) - len(q)), 20)),
                default=0.0)
        if ratio > best_ratio:
            best, best_ratio = unit, ratio
    return best_ratio >= threshold, best


def _short(text: str, limit: int, around: int = 0) -> str:
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    start = max(0, min(around - limit // 3, len(text) - limit))
    return ("..." if start else "") + text[start:start + limit].strip() + "..."
