"""Files a user attaches to a Chat-mode conversation (the read_attachment tool).

The file is converted to text on this machine and kept with the conversation under
<data_dir>/attachments/<conversation id>/; the model reads it through read_attachment, in pieces.
Supported: PDF (text layer), Word .docx, Excel .xlsx, and plain text (.txt .md .csv .json .log).
Scanned PDFs have no text layer and are refused with a message saying so.
"""
from __future__ import annotations

import io
import json
import re
import secrets
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree

MAX_BYTES = 15 * 1024**2
MAX_CHARS = 300_000       # of text kept from one file
READ_CHARS = 8_000        # given to the model per read_attachment call
MAX_PER_CONVERSATION = 10
ABANDONED_AFTER_S = 24 * 3600  # files of a conversation never asked in are kept this long
TEXT_TYPES = {".txt", ".md", ".csv", ".json", ".log", ".tsv"}
SUPPORTED = TEXT_TYPES | {".pdf", ".docx", ".xlsx"}
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_S = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


class AttachmentError(ValueError):
    pass


@dataclass
class Attachment:
    id: str
    name: str
    chars: int


def _pdf_text(data: bytes) -> str:
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(data)
    try:
        pages = []
        for n in range(len(pdf)):
            page = pdf[n]
            text = page.get_textpage().get_text_range()
            pages.append(f"[page {n + 1}]\n{text.strip()}")
        return "\n\n".join(pages)
    finally:
        pdf.close()


def _docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        root = ElementTree.fromstring(z.read("word/document.xml"))
    out = []
    for block in root.iter(f"{_W}p"):  # paragraphs, including those inside table cells
        text = "".join(t.text or "" for t in block.iter(f"{_W}t"))
        style = block.find(f"{_W}pPr/{_W}pStyle")
        level = re.match(r"Heading(\d)", style.get(f"{_W}val", "")) if style is not None else None
        if text.strip():
            out.append(("#" * int(level.group(1)) + " " if level else "") + text)
    return "\n\n".join(out)


def _xlsx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        shared = []
        if "xl/sharedStrings.xml" in names:
            for si in ElementTree.fromstring(z.read("xl/sharedStrings.xml")).iter(f"{_S}si"):
                shared.append("".join(t.text or "" for t in si.iter(f"{_S}t")))
        workbook = ElementTree.fromstring(z.read("xl/workbook.xml"))
        titles = [s.get("name") for s in workbook.iter(f"{_S}sheet")]
        sheets = sorted((n for n in names if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n)),
                        key=lambda n: int(re.search(r"(\d+)", n.rsplit("/", 1)[1]).group(1)))
        out = []
        for idx, name in enumerate(sheets):
            rows = []
            for row in ElementTree.fromstring(z.read(name)).iter(f"{_S}row"):
                cells = []
                for c in row.iter(f"{_S}c"):
                    v = c.find(f"{_S}v")
                    inline = c.find(f"{_S}is")
                    if c.get("t") == "s" and v is not None and v.text and v.text.isdigit() and int(v.text) < len(shared):
                        cells.append(shared[int(v.text)])
                    elif inline is not None:
                        cells.append("".join(t.text or "" for t in inline.iter(f"{_S}t")))
                    else:
                        cells.append(v.text if v is not None and v.text else "")
                if any(cells):
                    rows.append("| " + " | ".join(x.replace("|", "\\|") for x in cells) + " |")
            if rows:
                title = titles[idx] if idx < len(titles) else f"Sheet {idx + 1}"
                width = rows[0].count(" | ") + 1
                rows.insert(1, "| " + " | ".join(["---"] * width) + " |")
                out.append(f"## Sheet: {title}\n\n" + "\n".join(rows))
        return "\n\n".join(out)


def to_text(name: str, data: bytes) -> str:
    """The file's text; AttachmentError when it cannot be read."""
    suffix = Path(name).suffix.lower()
    if suffix not in SUPPORTED:
        raise AttachmentError(f"{suffix or 'This file type'} files can't be read. Attach a PDF, Word (.docx), "
                              "Excel (.xlsx) or text file.")
    if len(data) > MAX_BYTES:
        raise AttachmentError(f"The file is larger than {MAX_BYTES // 1024**2} MB.")
    try:
        if suffix in TEXT_TYPES:
            text = data.decode("utf-8-sig", errors="replace")
        elif suffix == ".pdf":
            text = _pdf_text(data)
        elif suffix == ".docx":
            text = _docx_text(data)
        else:
            text = _xlsx_text(data)
    except AttachmentError:
        raise
    except Exception as e:  # a damaged or password-protected file
        raise AttachmentError(f"The file could not be read ({type(e).__name__}); is it damaged or protected?") from e
    if not re.sub(r"\[page \d+\]", "", text).strip():
        raise AttachmentError("No text found in the file. A scanned document needs OCR first.")
    return text[:MAX_CHARS]


class AttachmentStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def _dir(self, conversation_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", conversation_id):
            raise AttachmentError("invalid conversation id")
        return self.root / conversation_id

    def add(self, conversation_id: str, name: str, data: bytes) -> Attachment:
        folder = self._dir(conversation_id)
        name = Path(name.replace("\\", "/")).name[:200] or "file"
        if len(self.list(conversation_id)) >= MAX_PER_CONVERSATION:
            raise AttachmentError(f"A conversation can have at most {MAX_PER_CONVERSATION} attachments.")
        text = to_text(name, data)
        att = Attachment(id="F" + secrets.token_hex(3).upper(), name=name, chars=len(text))
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f"{att.id}.txt").write_text(text, encoding="utf-8")
        (folder / f"{att.id}.json").write_text(json.dumps(att.__dict__), encoding="utf-8")
        return att

    def list(self, conversation_id: str | None) -> list[Attachment]:
        if not conversation_id:
            return []
        folder = self._dir(conversation_id)
        out = []
        for meta in sorted(folder.glob("F*.json"), key=lambda p: p.stat().st_mtime) if folder.is_dir() else []:
            try:
                out.append(Attachment(**json.loads(meta.read_text(encoding="utf-8"))))
            except (ValueError, TypeError):
                continue
        return out

    def text(self, conversation_id: str, attachment_id: str) -> str | None:
        if not re.fullmatch(r"F[0-9A-F]{6}", attachment_id):
            return None
        path = self._dir(conversation_id) / f"{attachment_id}.txt"
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def prune(self, kept: set[str], older_than_s: float = ABANDONED_AFTER_S) -> int:
        """Delete the attachments of conversations that were never asked in (a file attached, then
        a new conversation started) or are gone from the history, once they are this old; returns
        how many conversations. Nothing removed them before."""
        if not self.root.is_dir():
            return 0
        removed = 0
        for folder in self.root.iterdir():
            if (folder.is_dir() and folder.name not in kept and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", folder.name)
                    and time.time() - folder.stat().st_mtime > older_than_s):
                removed += bool(self.remove(folder.name)) or not folder.exists()
        return removed

    def remove(self, conversation_id: str) -> int:
        """Delete a conversation's attachments (History: delete a conversation); returns how many."""
        folder = self._dir(conversation_id)
        if not folder.is_dir():
            return 0
        removed = 0
        for path in folder.glob("F*.*"):
            if re.fullmatch(r"F[0-9A-F]{6}\.(txt|json)", path.name):
                path.unlink()
                removed += path.suffix == ".json"
        try:
            folder.rmdir()
        except OSError:
            pass  # something else in it: left as it is
        return removed
