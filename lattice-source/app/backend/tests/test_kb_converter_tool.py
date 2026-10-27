"""Knowledge bases written by kb_converter_tool (the office's converter): pictures with their
description as a blockquote under them, and descriptions listed in kb.json as "image_descriptions"."""
from pathlib import Path

from oasis_engine.kbfolder import section_body
from oasis_engine.loaders import mark_loose_descriptions
from oasis_engine.search import SectionIndex

SECTION = """---
id: about-frontier/01-about
module: about-frontier
title: "About"
pages: [11, 11]
---
# About
_Manual pp. 11–11_

The Frontier application trains inspection models from labelled images.

![Figure](../../images/figure_029.png)
> - **GLOBAL SCENE**: A screenshot of a professional software application titled
>   "SEM-AI FRONTIER", a Configuration pop-up over a data analysis dashboard.
> - **KEY OBJECTS**: a Configuration pop-up.

![Figure](../../images/figure_030.png)

> Note: save the solution before closing.
"""


def _body(tmp_path: Path, described: dict) -> str:
    path = tmp_path / "modules" / "about-frontier" / "01-about.md"
    path.parent.mkdir(parents=True)
    path.write_text(SECTION, encoding="utf-8")
    return section_body(tmp_path, {"file": "modules/about-frontier/01-about.md"}, described)


def test_a_quoted_picture_description_is_the_pictures_not_the_text(tmp_path):
    described: dict[str, str] = {}
    body = _body(tmp_path, described)
    assert "GLOBAL SCENE" not in body and "KEY OBJECTS" not in body
    assert described["figure_029.png"].startswith("- **GLOBAL SCENE**: A screenshot")
    assert "![Figure](images/figure_029.png)" in body
    # a quoted note under a picture is the manual's own text
    assert "> Note: save the solution before closing." in body and "figure_030.png" not in described


def test_kb_json_descriptions_are_used_when_the_section_has_none(tmp_path):
    described = {"figure_030.png": "- **GLOBAL SCENE**: The Solution List screen."}
    _body(tmp_path, described)
    assert described["figure_030.png"] == "- **GLOBAL SCENE**: The Solution List screen."


def test_a_quoted_description_no_loader_marked_is_still_a_picture():
    from tests.test_search_quality import _doc_with
    doc = _doc_with([("Setup", "Intro.\n\n> - **GLOBAL SCENE**: A screenshot of the window.\n>   It has a button.\n\nAfter.")])
    mark_loose_descriptions([doc])
    text = doc.sections["0000"].own_text
    assert "GLOBAL SCENE" not in text and "[Figure: A screenshot of the window." in text and text.endswith("After.")
    assert SectionIndex([doc]).search("window button")  # still findable, as a picture
