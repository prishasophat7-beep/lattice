"""Tables in a section's Markdown as rows and columns (the read_table tool).

Spreadsheets and reports arrive from the web platform as GitHub-style Markdown tables. Read as prose,
a wide table is easy to misread ("which category decreased?"); as columns and rows the model can
compare, sort and sum reliably, with calculate for the arithmetic.
"""
from __future__ import annotations

import re

MAX_ROWS = 200
_RULE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")


def _cells(line: str) -> list[str]:
    line = line.strip()
    if line.startswith("|"):
        line = line[1:]
    if line.endswith("|") and not line.endswith("\\|"):
        line = line[:-1]
    cells = re.split(r"(?<!\\)\|", line)
    return [re.sub(r"\*\*(.*?)\*\*", r"\1", c.strip()).replace("\\|", "|") for c in cells]


def markdown_tables(text: str, max_rows: int | None = MAX_ROWS) -> list[dict]:
    """Every table in the text: {"columns": [...], "rows": [[...], ...], "title": the line before it}.
    max_rows: rows kept per table (what a model is given); None for all (what code works out from)."""
    lines = text.splitlines()
    tables, i = [], 0
    while i < len(lines) - 1:
        if "|" in lines[i] and _RULE.match(lines[i + 1]):
            columns = _cells(lines[i])
            title = next((lines[j].strip().lstrip("#").strip() for j in range(i - 1, -1, -1) if lines[j].strip()), "")
            rows, j = [], i + 2
            while j < len(lines) and "|" in lines[j] and lines[j].strip():
                row = _cells(lines[j])
                rows.append((row + [""] * len(columns))[:len(columns)])
                j += 1
            cut = max_rows is not None and len(rows) > max_rows
            tables.append({"title": title, "columns": columns, "rows": rows[:max_rows] if cut else rows,
                           **({"note": f"{len(rows) - max_rows} more rows not shown"} if cut else {})})
            i = j
        else:
            i += 1
    return tables
