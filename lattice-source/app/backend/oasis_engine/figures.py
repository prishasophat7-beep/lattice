"""Pages of the original PDF manuals that hold a figure, and those pages rendered as pictures.

Many procedures depend on a diagram the page text cannot carry. When an answer cites PDF pages and
the bundle ships the original PDF (originals/), the answer card shows the cited pages that contain
an embedded picture. Vector drawings are not detected: table rules and underlines are vector paths
too, so counting paths would flag most pages.
"""
from __future__ import annotations

import hashlib
import re
import threading
from dataclasses import dataclass
from difflib import get_close_matches
from pathlib import Path

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_c

from .search import tokenize

MIN_FIGURE_SHARE = 0.03  # an embedded picture must cover this share of the page to count as a figure
MAX_FIGURES = 3          # per answer
MAX_LISTED = 6           # when the question asks for pictures ("show me the diagrams")
RENDER_WIDTH = 1600      # pixels across a whole page: sharp in the large view on high-DPI screens
CROP_WIDTH = 900         # pixels across a figure cut out of its page (for thumbnails)
CROP_MARGIN = 0.02       # share of the page kept around a figure
# a picture to show: (doc index, node id of its section, page or None, image file name or None)
PictureRef = tuple[int, str, "int | None", "str | None"]
Box = tuple[float, float, float, float]  # left, bottom, right, top in PDF points (origin bottom-left)

_lock = threading.Lock()  # pdfium is not thread-safe; the API serves requests from a thread pool
_known: dict[tuple[str, float], dict[int, Box]] = {}
# words for a picture, as tokenize() leaves them (lowercase, plural -> singular)
_VISUAL = frozenset("image picture diagram figure photo photograph drawing illustration schematic sketch "
                    "graphic visual pic img fig screenshot layout chart graph plot curve histogram "
                    "flowchart dashboard infographic".split())
# typos are recognised only for these: "chair" is not a misspelt "chart"
_FUZZY_VISUAL = _VISUAL - {"chart", "graph", "plot", "curve", "dashboard"}
_REQUEST = frozenset("show shoe shwo sohw hsow display see view find list give open get any where me want need".split())
_WH = frozenset("what how why when which who does".split())
_FILLER = _REQUEST | frozenset("some all manual please can you there have page picture of one say about tell "
                                "describe explain any other more".split())

def figure_boxes(pdf: Path) -> dict[int, Box]:
    """Page (1-based) -> where its pictures are, for pages holding an embedded picture of a useful
    size. Scanned once per file."""
    key = (str(pdf), pdf.stat().st_mtime)
    if key not in _known:
        with _lock:
            doc = pdfium.PdfDocument(str(pdf))
            try:
                boxes: dict[int, Box] = {}
                for i in range(len(doc)):
                    p = doc[i]
                    width, height = p.get_size()
                    big = [obj.get_bounds() for obj in p.get_objects(filter=[pdfium_c.FPDF_PAGEOBJ_IMAGE], max_depth=3)]
                    big = [b for b in big if (b[2] - b[0]) * (b[3] - b[1]) >= MIN_FIGURE_SHARE * width * height]
                    if big:  # all the page's pictures together (a figure may be several images)
                        boxes[i + 1] = (min(b[0] for b in big), min(b[1] for b in big),
                                        max(b[2] for b in big), max(b[3] for b in big))
                _known[key] = boxes
            finally:
                doc.close()
    return _known[key]


def figure_pages(pdf: Path) -> frozenset[int]:
    """The pages (1-based) that hold an embedded picture of a useful size."""
    return frozenset(figure_boxes(pdf))


def has_figure(pdf: Path, page: int) -> bool:
    return page in figure_boxes(pdf)


def is_visual(token: str) -> bool:
    """A picture word, typos included ("imges", "diagarm", "pics")."""
    return token in _VISUAL or (len(token) >= 5 and bool(get_close_matches(token, _FUZZY_VISUAL, n=1, cutoff=0.8)))


@dataclass
class PictureRequest:
    only_pictures: bool  # "show me the diagrams": answer with the pictures, no model needed
    topic: list[str]     # what the pictures should be about ("tool", "head"); may be empty


def picture_request(question: str) -> PictureRequest | None:
    """Does the question ask for pictures, or about one? None when it mentions no picture.

    A request ("show me images", "any pics of the booth?", "diagrams for station 4") is answered
    with the pages that hold figures. A question about a picture's content ("What does the wiring
    diagram show?") still gets a written answer; the matching pictures are added to it."""
    tokens = tokenize(question)
    if not any(is_visual(t) for t in tokens):
        return None
    parts = {p for t in tokens if "-" in t for p in t.split("-")}  # "start-up" also yields "start", "up"
    topic = list(dict.fromkeys(t for t in tokens if not is_visual(t) and t not in _FILLER and t not in parts))
    first = question.strip().split(maxsplit=1)[0].lower() if question.strip() else ""
    asks_about_content = first in _WH and bool(topic)
    return PictureRequest(only_pictures=not asks_about_content, topic=topic)


def fix_typos(text: str) -> str:
    """Misspelt picture and summary words spelled right ("diagarm" -> "diagram"), so a small model
    recognises the request."""
    known = _VISUAL | {"summary", "summarize", "overview"}

    def fix(m) -> str:
        word = m.group(0)
        low = word.lower()
        stem = low[:-1] if low.endswith("s") else low
        if len(low) < 5 or low in known or stem in known:
            return word
        close = get_close_matches(stem, known, n=1, cutoff=0.8)
        return close[0] + ("s" if low.endswith("s") and not close[0].endswith("s") else "") if close else word

    return re.sub(r"[A-Za-z]+", fix, text)


def topic_tokens(text: str) -> list[str]:
    """The subject words of a picture topic ("the tool head" -> ["tool", "head"])."""
    tokens = tokenize(text)
    parts = {p for t in tokens if "-" in t for p in t.split("-")}
    return list(dict.fromkeys(t for t in tokens if not is_visual(t) and t not in _FILLER and t not in parts))


def render_page(pdf: Path, page: int, cache_dir: Path, crop: bool = False) -> Path:
    """The page as a PNG, rendered once and kept in cache_dir. crop: only the figure on it, with a
    small margin (the whole page when it holds none)."""
    box = figure_boxes(pdf).get(page) if crop else None
    name = hashlib.sha256(f"{pdf.resolve()}|{pdf.stat().st_mtime}".encode()).hexdigest()[:16]
    out = cache_dir / f"{name}-{page}{'-figure' if box else ''}.png"
    if out.is_file():
        return out
    with _lock:
        doc = pdfium.PdfDocument(str(pdf))
        try:
            if not 1 <= page <= len(doc):
                raise ValueError(f"page {page} is outside the document")
            p = doc[page - 1]
            width, height = p.get_size()
            if box is None:
                image = p.render(scale=RENDER_WIDTH / width).to_pil()
            else:
                mx, my = CROP_MARGIN * width, CROP_MARGIN * height
                left, bottom = max(0.0, box[0] - mx), max(0.0, box[1] - my)
                right, top = min(width, box[2] + mx), min(height, box[3] + my)
                scale = min(4.0, CROP_WIDTH / max(right - left, 1.0))
                full = p.render(scale=scale).to_pil()
                image = full.crop((round(left * scale), round((height - top) * scale),
                                   round(right * scale), round((height - bottom) * scale)))
            cache_dir.mkdir(parents=True, exist_ok=True)
            tmp = out.with_suffix(".tmp")
            image.save(tmp, "PNG", optimize=True)
            tmp.replace(out)
        finally:
            doc.close()
    return out
