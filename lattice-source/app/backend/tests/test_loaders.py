import json

import pytest

from conftest import write_markdown_kb
from oasis_engine.loaders import KnowledgeBaseError, load_markdown, load_pageindex


def titles(doc):
    return [(s.depth, s.title) for s in doc.sections.values()]


def test_markdown_builds_heading_tree(markdown_kb):
    [doc] = load_markdown(markdown_kb)
    assert doc.kind == "markdown"
    assert titles(doc) == [
        (0, "Preface"), (0, "Station 4 Torque Tool Manual"), (1, "Setup"), (2, "Calibration"),
        (2, "Bit replacement"), (1, "Troubleshooting"), (2, "Error E17"), (2, "Error E22")]


def test_markdown_ignores_headings_inside_code_blocks(markdown_kb):
    [doc] = load_markdown(markdown_kb)
    assert not any("not a heading" in s.title for s in doc.sections.values())
    e17 = next(s for s in doc.sections.values() if s.title == "Error E17")
    assert "# this is not a heading" in e17.own_text


def test_markdown_parent_text_includes_children_and_locator_is_heading_path(markdown_kb):
    [doc] = load_markdown(markdown_kb)
    trouble = next(nid for nid, s in doc.sections.items() if s.title == "Troubleshooting")
    text = doc.section_text(trouble)
    assert "E17 means torque not reached" in text and "E22 indicates sensor fault" in text
    e22 = next(nid for nid, s in doc.sections.items() if s.title == "Error E22")
    # the single title heading is the document itself, so locators leave it out
    assert doc.locator(e22) == "Troubleshooting > Error E22"


def test_markdown_uses_summaries_sidecar_and_falls_back_to_text(tmp_path):
    root = write_markdown_kb(tmp_path / "md", summaries={
        "Station 4 Torque Tool Manual > Troubleshooting > Error E17": "What error E17 means and how to fix it."})
    [doc] = load_markdown(root)
    e17 = next(s for s in doc.sections.values() if s.title == "Error E17")
    assert e17.summary == "What error E17 means and how to fix it." and not e17.summary_is_fallback
    calib = next(s for s in doc.sections.values() if s.title == "Calibration")
    assert calib.summary_is_fallback and calib.summary.startswith("Calibrate every 500 cycles")


def test_pageindex_loads_tree_pages_and_fills_missing_summaries(pageindex_kb):
    [doc] = load_pageindex(pageindex_kb)
    assert doc.name == "press-manual.pdf" and doc.kind == "pages"
    assert titles(doc) == [(0, "Safety"), (0, "Operation"), (1, "Start-up"), (1, "Shutdown")]
    shutdown = doc.sections["0003"]
    assert shutdown.summary_is_fallback and "Release pressure" in shutdown.summary
    assert doc.locator("0001") == "pages 2-3"
    assert "[page 2]" in doc.section_text("0001") and "[page 3]" in doc.section_text("0001")


def test_pageindex_rejects_sections_outside_the_document(pageindex_kb):
    tree_file = pageindex_kb / "docs" / "pi-test" / "tree.json"
    tree = json.loads(tree_file.read_text())
    tree[0]["end_index"] = 9
    tree_file.write_text(json.dumps(tree))
    with pytest.raises(KnowledgeBaseError, match="outside the document"):
        load_pageindex(pageindex_kb)
