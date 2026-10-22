"""The web platform's own export format: page_index.json + files/<name>/<name>.md|.structured.json|
.bundle.zip (the Markdown and the images). Built here the way the web team's sample is built."""
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from conftest import ScriptedModel
from oasis_engine import bundle
from oasis_engine.api import create_app
from oasis_engine.llm import LLMConfig
from oasis_engine.settings import Settings

PNG = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154"
                    "789c6360000002000154a24f5d0000000049454e44ae426082")  # a 1x1 picture

REPORT_MD = """# Introduction

Testing of the MCN5 sensor at the Suwon lab.

## Background

The MCN5 sensor was tested from 10 to 1,000 lux.

![Figure](images/figure_000.png)

# Results

| Test | Result | Target |
| --- | --- | --- |
| Distortion | 1.2% | <= 1.0% |
"""
BUDGET_MD = """# budget.xlsx

## Sheet: Summary

| Category | FY2027 ($K) |
| --- | --- |
| Equipment | 186 |

![Figure](images/figure_000.png)

## Sheet: Notes

Travel is reduced.
"""


def export_zip(path, generated_at="2026-09-25T08:21:53+00:00", report_summary="Sensor test report.",
               report_md=REPORT_MD, report_images=("figure_000.png",), flat=False):
    """flat: the current export layout, "bundle_zip": "files/<name>.bundle.zip" and no separate
    Markdown or structured.json; otherwise the earlier "files": {...} layout."""
    docs = [
        ("report.pdf", "1e642f4a", report_md, list(report_images), report_summary, [
            {"node_id": "0001", "title": "Introduction", "type": "section", "level": 1, "start_page": 1,
             "end_page": 2, "summary": "Introduces the MCN5 test campaign."},
            {"node_id": "0002", "title": "Background", "type": "section", "level": 2, "start_page": 1,
             "end_page": 2, "summary": "Lighting range 10-1,000 lux, with an SNR figure."},
            {"node_id": "0003", "title": "Results", "type": "section", "level": 1, "start_page": 3,
             "end_page": 4, "summary": "Image quality results against targets."},
            {"node_id": "0004", "title": "Table (2 rows x 3 cols)", "type": "table", "level": 2,
             "start_page": 3, "end_page": 3, "summary": "Distortion 1.2% fails the 1.0% target."}]),
        ("budget.xlsx", "aea89354", BUDGET_MD, ["figure_000.png"], "FY2027 budget workbook.", [
            {"node_id": "0001", "title": "budget.xlsx", "type": "xlsx", "level": 1, "summary": "Budget workbook."},
            {"node_id": "0002", "title": "Summary", "type": "sheet", "level": 2,
             "summary": "FY2027 budget by category, with a bar chart."},
            {"node_id": "0003", "title": "Notes", "type": "sheet", "level": 2, "summary": "Why travel is reduced."}]),
    ]
    index = {"kb_id": "kb1", "kb_name": "Sample Knowledge Base", "generated_at": generated_at,
             "documents": []}
    with zipfile.ZipFile(path, "w") as zf:
        for name, file_id, md, images, summary, sections in docs:
            base = f"files/{name}/{name}"
            inner = io.BytesIO()
            with zipfile.ZipFile(inner, "w") as b:
                b.writestr(f"{name}.md", md)
                for img in images:
                    b.writestr(f"images/{img}", PNG)
                b.writestr("../evil.png", PNG)  # must never be extracted
            entry = {"file_id": file_id, "filename": name, "relative_path": name, "doc_summary": summary,
                     "sections": sections}
            if flat:
                zf.writestr(f"files/{name}.bundle.zip", inner.getvalue())
                entry["bundle_zip"] = f"files/{name}.bundle.zip"
            else:
                zf.writestr(base + ".md", md)
                zf.writestr(base + ".bundle.zip", inner.getvalue())
                zf.writestr(base + ".structured.json", json.dumps(
                    {"file_id": "other-id", "bundle_url": "https://ocr.example/bundle.zip?token=SECRET"}))
                entry["files"] = {"markdown": base + ".md", "structured_content": base + ".structured.json",
                                  "bundle": base + ".bundle.zip"}
            index["documents"].append(entry)
        zf.writestr("page_index.json", json.dumps(index))
    return path


def test_export_is_imported_with_summaries_pages_tables_and_images(tmp_path):
    data = tmp_path / "data"
    m = bundle.import_bundle(export_zip(tmp_path / "kb.zip"), data)
    assert m.kb_version == "sample-knowledge-base-2026.09.25-0821" and m.source_format == "export"
    stored = [p.relative_to(data).as_posix() for p in data.rglob("*") if p.is_file()]
    assert not any("structured" in p or "evil" in p for p in stored)  # no tokens, no escaping paths
    assert not any("SECRET" in (data / p).read_bytes().decode("latin-1") for p in stored)
    _, docs = bundle.load_active(data)
    report, budget = docs
    background = next(s for s in report.sections.values() if s.title == "Background")
    results = next(s for s in report.sections.values() if s.title == "Results")
    assert background.summary.startswith("Lighting range") and background.images == ["figure_000.png"]
    assert report.locator(background.node_id) == "Introduction > Background (pp. 1-2)"
    assert "Distortion 1.2% fails" in results.summary  # the table's summary joins its section
    assert "[Figure]" in background.own_text and "images/" not in background.own_text
    sheet = next(s for s in budget.sections.values() if s.title == "Sheet: Summary")
    assert sheet.summary.startswith("FY2027 budget") and not sheet.summary_is_fallback
    assert report.description == "Sensor test report."
    # both documents have images/figure_000.png: kept apart
    assert report.assets_dir != budget.assets_dir


DESCRIBED_MD = REPORT_MD + """
## Image Descriptions

### figure_000.png

GLOBAL SCENE: A line chart of SNR against illuminance. KEY OBJECTS: SNR rises from 18 dB at 10 lux
to 41 dB at 1,000 lux.

### figure_009.png

GLOBAL SCENE: A photo of the test bench.
"""


def test_image_descriptions_are_read_where_the_picture_is(tmp_path):
    # the web platform puts every description in a chapter at the end, titled by file name: search and
    # citations must land on the section that shows the picture, not on a section called "figure_000.png"
    data = tmp_path / "data"
    bundle.import_bundle(export_zip(tmp_path / "kb.zip", report_md=DESCRIBED_MD, flat=True,
                                    report_images=("figure_000.png", "figure_009.png")), data)
    report = bundle.load_active(data)[1][0]
    titles = [s.title for s in report.sections.values()]
    assert "figure_000.png" not in titles
    background = next(s for s in report.sections.values() if s.title == "Background")
    assert background.images == ["figure_000.png"]
    # the platform's labels ("GLOBAL SCENE:", "KEY OBJECTS:") are left out
    assert "[Figure: A line chart of SNR against illuminance. SNR rises from 18 dB" in background.own_text
    # a picture the text never shows keeps its description, and is shown with it
    bench = next(s for s in report.sections.values() if s.title == "figure_009.png")
    assert bench.images == ["figure_009.png"] and "test bench" in bench.own_text
    assert report.sections[bench.parent].children == [bench.node_id]

    # all pictures placed: the chapter goes
    data2 = tmp_path / "data2"
    placed = DESCRIBED_MD.split("### figure_009.png")[0]
    bundle.import_bundle(export_zip(tmp_path / "kb2.zip", report_md=placed, flat=True), data2)
    report = bundle.load_active(data2)[1][0]
    assert not any("Image Descriptions" in s.title or s.title.endswith(".png") for s in report.sections.values())
    assert all(c in report.sections for s in report.sections.values() for c in s.children)


def test_example_questions_skip_pictures_markup_and_generic_headings(tmp_path):
    # seen with the web team's sample: "What does the manual say about figure_000.png?", "... about about?"
    report = DESCRIBED_MD.replace("# Results", "## About\n\nAbout the lab.\n\n## <option name=\"TABS\" value=\"4\" />\n\n"
                                  "Indent setting.\n\n# Results")
    client = client_for(tmp_path, ScriptedModel(select=[], answer=[]), report_md=report, flat=True,
                        report_images=("figure_000.png", "figure_009.png"))
    suggestions = client.get("/api/suggestions").json()
    assert suggestions, suggestions
    assert not any(".png" in s or "<" in s or "about about" in s.lower() for s in suggestions), suggestions
    from oasis_engine.api import _unsuggestable
    names = {"leave_policy.pdf", "leave_policy", "leave policy"}
    for title in ("figure_000.png", '<option name="TABS" value="4" />', "About", "LEAVE POLICY", "leave_policy.pdf"):
        assert _unsuggestable(title, names), title
    assert not _unsuggestable("Leave Approval Procedure", names)


def test_a_caption_and_a_description_are_both_read():
    from oasis_engine.loaders import _figure
    assert _figure("Dashboard", "Four KPI cards.") == "[Figure: Dashboard. Four KPI cards.]"
    assert _figure("Figure", "Four KPI cards.") == "[Figure: Four KPI cards.]"
    assert _figure("Chart", None) == "[Figure: Chart]" and _figure("image", None) == "[Figure]"


def test_current_export_layout_reads_the_markdown_and_images_from_each_bundle_zip(tmp_path):
    data = tmp_path / "data"
    m = bundle.import_bundle(export_zip(tmp_path / "kb.zip", flat=True), data)
    assert [d.title for d in m.documents] == ["report.pdf", "budget.xlsx"] and not m.skipped
    stored = [p.relative_to(data).as_posix() for p in data.rglob("*") if p.is_file()]
    assert not any("evil" in p for p in stored)
    report, budget = bundle.load_active(data)[1]
    background = next(s for s in report.sections.values() if s.title == "Background")
    assert background.summary.startswith("Lighting range") and background.images == ["figure_000.png"]
    assert report.locator(background.node_id) == "Introduction > Background (pp. 1-2)"
    assert any(s.title == "Sheet: Summary" for s in budget.sections.values())


def test_same_export_in_the_current_layout_is_not_refused_over_the_earlier_one(tmp_path):
    data = tmp_path / "data"
    bundle.import_bundle(export_zip(tmp_path / "old.zip"), data)
    m = bundle.import_bundle(export_zip(tmp_path / "new.zip", flat=True), data)
    assert m.kb_version == bundle.active_version(data) == "sample-knowledge-base-2026.09.25-0821"
    with pytest.raises(bundle.BundleError, match="different knowledge base"):
        bundle.import_bundle(export_zip(tmp_path / "changed.zip", flat=True, report_summary="Changed."), data)


def test_same_export_again_switches_to_it_and_a_changed_one_is_refused(tmp_path):
    data = tmp_path / "data"
    bundle.import_bundle(export_zip(tmp_path / "a.zip"), data)
    bundle.import_bundle(export_zip(tmp_path / "b.zip"), data)  # identical: fine
    with pytest.raises(bundle.BundleError, match="different knowledge base"):
        bundle.import_bundle(export_zip(tmp_path / "c.zip", report_summary="Changed."), data)
    bundle.import_bundle(export_zip(tmp_path / "d.zip", generated_at="2026-09-26T07:00:00+00:00"), data)
    assert bundle.active_version(data) == "sample-knowledge-base-2026.09.26-0700"


def client_for(tmp_path, script, **export):
    client = TestClient(create_app(Settings(data_dir=tmp_path / "data", llm=LLMConfig(output_mode="prompted")),
                                   model=script.model))
    kb = export_zip(tmp_path / "kb.zip", **export)
    assert client.post("/api/kb/import", json={"path": str(kb)}).status_code == 200
    return client


def test_answer_from_a_section_with_a_picture_shows_it(tmp_path):
    script = ScriptedModel(select=[{"section_ids": ["A2"]}],
                           answer=[{"found": True, "answer": "From 10 to 1,000 lux.", "section_ids": ["A2"]}])
    client = client_for(tmp_path, script)
    r = client.post("/api/ask", json={"question": "Which lux range was tested?"}).json()
    assert r["citations"][0]["title"] == "Background" and r["citations"][0]["locator"].endswith("(pp. 1-2)")
    assert r["figures"] == [{"citation": 0, "page": 1, "image": "figure_000.png", "caption": None, "description": None}]
    doc = r["citations"][0]["doc_id"]
    image = client.get(f"/api/docs/{doc}/images/figure_000.png")
    assert image.status_code == 200 and image.content == PNG
    assert client.get(f"/api/docs/{doc}/images/..%2Fdoc.md").status_code == 404


def test_picture_request_lists_the_extracted_images(tmp_path):
    client = client_for(tmp_path, ScriptedModel(select=[], answer=[], route=[{"kind": "pictures", "topic": ""}]))
    r = client.post("/api/ask", json={"question": "show me some images"}).json()
    assert r["pictures_only"] and [f["image"] for f in r["figures"]] == ["figure_000.png", "figure_000.png"]
    assert {c["doc_name"] for c in r["citations"]} == {"report.pdf", "budget.xlsx"}


def test_different_documents_on_the_same_subject_are_answered_not_asked_back():
    from oasis_engine.loaders import parse_markdown
    from oasis_engine.pipeline import Pipeline
    report = parse_markdown(
        "# Results\n\n## Image Quality Metrics\n\n| Test | Result | Target |\n| --- | --- | --- |\n"
        "| Distortion | 1.2% | <= 1.0% |\n| MTF50 | 0.42 | >= 0.38 |\n\nOnly distortion failed.", "r", "report.pdf")
    notes = parse_markdown(
        "# Weekly sync\n\n## MCN5 Test Results\n\nA. Patel presented the report. The distortion result was "
        "1.2% against a 1.0% target; the team will fix it in the ISP.", "n", "notes.docx")
    pipe = Pipeline([report, notes], ScriptedModel(select=[], answer=[]).model, output_mode="prompted")
    assert pipe._which_manual("What was the distortion result?", None) is None


def test_suggestions_name_the_exported_documents(tmp_path):
    client = client_for(tmp_path, ScriptedModel(select=[], answer=[]))
    suggestions = client.get("/api/suggestions").json()
    assert suggestions and all(s.startswith(("What does the report say", "What does the budget say"))
                               for s in suggestions[:-1]), suggestions
    assert not any("Sheet:" in s for s in suggestions)
    # the documents have pictures: the last example asks for them (the first document, on a tie)
    assert suggestions[-1] == "Show me the pictures in the report"


def test_task_headings_become_how_do_i_questions():
    from oasis_engine.api import _example_question
    assert _example_question("Creating a Project", "the manual") == "How do I create a project?"
    assert _example_question("Setting Up VPN", "the manual") == "How do I set up VPN?"
    assert _example_question("Configuring TaskFlow Sync", "the manual") == "How do I configure TaskFlow sync?"
    assert _example_question("Installation", "the manual") == "What does the manual say about installation?"
    assert _example_question("Creating", "the manual") == "What does the manual say about creating?"


def test_figures_carry_caption_and_description_and_a_chapter_shows_only_relevant_pictures(tmp_path):
    md = """# Manual

## Features

### Dashboard

KPI cards and charts.

![Dashboard](images/dash.png)

### Creating a Project

Click New Project, enter a name and a key.

![Create project dialog](images/create.png)

### Reports

Burndown and velocity.

![Sprint burndown](images/burn.png)

## Image Descriptions

### dash.png

GLOBAL SCENE: The dashboard with KPI cards.

### create.png

GLOBAL SCENE: The Create New Project dialog with key WEB.

### burn.png

GLOBAL SCENE: A burndown line chart.
"""
    images = ("dash.png", "create.png", "burn.png")
    script = ScriptedModel(select=[], answer=[], route=[{"kind": "pictures", "topic": "create project dialog"}])
    client = client_for(tmp_path, script, report_md=md, flat=True, report_images=images)
    r = client.post("/api/ask", json={"question": "show me the picture of the create project dialog"}).json()
    figure = r["figures"][0]
    assert figure["image"] == "create.png" and figure["caption"] == "Create project dialog"
    assert figure["description"] == "The Create New Project dialog with key WEB."

    # an answer citing the whole "Features" chapter for the create-project steps shows that picture only
    from oasis_engine.api import Engine
    doc = bundle.load_active(tmp_path / "data")[1][0]
    features = next(s.node_id for s in doc.sections.values() if s.title == "Features")
    refs = [(0, s.node_id, None, name) for s in doc.sections.values() for name in s.images]
    shown = Engine._relevant(refs, doc, features, "How do I create a new project?", set())
    assert [r[3] for r in shown] == ["create.png"]
    # a subsection the answer cites too keeps its picture
    dash = next(s.node_id for s in doc.sections.values() if s.title == "Dashboard")
    shown = Engine._relevant(refs, doc, features, "How do I create a new project?", {(doc.doc_id, dash)})
    assert [r[3] for r in shown] == ["dash.png", "create.png"]


def messy_export(path):
    """What a real export may hold beyond the sample: CRLF front matter, an image name with a
    space (written %20 in the Markdown), a document the platform could not convert, a corrupt
    picture bundle, and two files of the same name in different folders."""
    def entry(file_id, name, rel, base, summary="A document."):
        return {"file_id": file_id, "filename": name, "relative_path": rel, "doc_summary": summary,
                "files": {"markdown": base + ".md", "bundle": base + ".bundle.zip"}, "sections": []}
    report = "---\r\ndoc_id: x\r\n---\r\n# Results\r\n\r\nDistortion 1.2%.\r\n\r\n![Chart](images/MTF%20curve.png)\r\n"
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as b:
        b.writestr("report.pdf.md", report)
        b.writestr("images/MTF curve.png", PNG)
        b.writestr("images/../../evil.png", PNG)
    docs = [entry("a1", "report.pdf", "report.pdf", "files/report.pdf/report.pdf"),
            entry("b2", "scan.pdf", "scan.pdf", "files/scan.pdf/scan.pdf"),
            entry("c3", "notes.docx", "2025/notes.docx", "files/2025/notes.docx/notes.docx"),
            entry("d4", "notes.docx", "2026/notes.docx", "files/2026/notes.docx/notes.docx")]
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("files/report.pdf/report.pdf.md", report)
        zf.writestr("files/report.pdf/report.pdf.bundle.zip", inner.getvalue())
        zf.writestr("files/scan.pdf/scan.pdf.md", "  \n")  # nothing could be read from it
        for year in ("2025", "2026"):
            zf.writestr(f"files/{year}/notes.docx/notes.docx.md", f"# Sync {year}\n\nYield was 9{year[-1]}%.")
            zf.writestr(f"files/{year}/notes.docx/notes.docx.bundle.zip", b"not a zip")
        zf.writestr("page_index.json", json.dumps({"kb_name": "Messy", "generated_at": "2026-10-01T09:00:00Z",
                                                   "documents": docs}))
    return path


def test_a_real_world_export_imports_what_it_can_and_says_what_it_left_out(tmp_path):
    client = TestClient(create_app(Settings(data_dir=tmp_path / "data", llm=LLMConfig(output_mode="prompted")),
                                   model=ScriptedModel(select=[], answer=[]).model))
    r = client.post("/api/kb/import", json={"path": str(messy_export(tmp_path / "kb.zip"))}).json()
    assert r["skipped"] == ["scan.pdf"] and r["documents"] == 3
    kb = client.get("/api/kb").json()
    assert kb["skipped"] == ["scan.pdf"]
    assert [d["title"] for d in kb["documents"]] == ["report.pdf", "2025/notes.docx", "2026/notes.docx"]
    report = kb["documents"][0]["doc_id"]
    section = next(s for s in client.get(f"/api/docs/{report}/toc").json()["sections"] if s["title"] == "Results")
    view = client.get(f"/api/docs/{report}/sections/{section['node_id']}").json()
    assert [im["image"] for im in view["images"]] == ["MTF curve.png"]
    assert "doc_id" not in view["text"] and "[Figure: Chart]" in view["text"]
    image = client.get(f"/api/docs/{report}/images/MTF%20curve.png")
    assert image.status_code == 200 and image.content == PNG
    assert not list((tmp_path / "data").rglob("evil.png"))



def test_the_picture_a_section_describes_comes_before_one_that_only_mentions_the_topic(tmp_path):
    # the results table mentions SNR three times; the background section *has* the SNR figure
    report = REPORT_MD.replace("| Distortion | 1.2% | <= 1.0% |", "| Distortion | 1.2% | <= 1.0% |\n"
                               "| SNR | 38 dB | SNR target 36 dB, SNR pass |\n\n![Figure](images/figure_001.png)")
    script = ScriptedModel(select=[], answer=[], route=[{"kind": "pictures", "topic": "SNR chart"}])
    client = client_for(tmp_path, script, report_md=report, report_images=("figure_000.png", "figure_001.png"))
    r = client.post("/api/ask", json={"question": "show me the SNR chart"}).json()
    first = r["citations"][r["figures"][0]["citation"]]
    assert first["title"] == "Background" and r["figures"][0]["image"] == "figure_000.png"


def test_searching_a_code_lists_the_sections_that_have_it_whole(tmp_path):
    # "WEB-142" also matched every section with "WEB" or "142" in it (7 results, 2 with the code)
    md = ("# Manual\n\n## Keys\n\nTasks get the project key as prefix, e.g. WEB-142.\n\n"
          "## Web client\n\nThe WEB client runs in a browser.\n\n## Limits\n\nUp to 142 projects.\n")
    client = client_for(tmp_path, ScriptedModel(select=[], answer=[]), report_md=md, flat=True)
    hits = client.get("/api/search", params={"q": "WEB-142"}).json()
    assert [h["title"] for h in hits] == ["Keys"]


def test_a_follow_up_knows_which_pictures_the_last_answer_showed(tmp_path):
    # "are there any images?" then "what is this project dialog?": the earlier answer's text only says
    # "Pictures in the documents. Select one to see it larger."
    md = ("# Manual\n\n## Creating a Project\n\nClick New Project.\n\n![Create project dialog](images/create.png)\n\n"
          "## Image Descriptions\n\n### create.png\n\nA modal dialog titled Create New Project.\n")
    script = ScriptedModel(select=[], answer=[], route=[{"kind": "pictures", "topic": ""}])
    client = client_for(tmp_path, script, report_md=md, flat=True, report_images=("create.png",))
    client.post("/api/ask", json={"question": "are there any images?", "conversation_id": "c1"})
    from oasis_engine.api import _with_pictures
    entry = client.get("/api/history", params={"conversation_id": "c1"}).json()[0]
    text = _with_pictures(entry)
    assert "Pictures shown: Create project dialog (report.pdf > Creating a Project): A modal dialog" in text


def test_a_summary_about_missing_content_is_not_the_documents_card_text(tmp_path):
    """The catalog keeps the export's summary as given; the card must not show one written from
    empty headings ("Section 4 is titled Options but contains no descriptive content")."""
    bad = ("--- Introduction Functionalities Section 4 is titled Options but contains no descriptive "
           "content in the excerpt. Section 6 is titled Train Options.")
    client = client_for(tmp_path, ScriptedModel(select=[], answer=[]), report_summary=bad)
    report = next(d for d in client.get("/api/docs").json() if "report" in d["name"].lower())
    assert "no descriptive content" not in (report.get("description") or "")
