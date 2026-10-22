import json

import pytest
from fastapi.testclient import TestClient

from conftest import ScriptedModel, write_markdown_kb
from oasis_engine import bundle
from oasis_engine.api import create_app
from oasis_engine.llm import LLMConfig
from oasis_engine.settings import Settings


@pytest.fixture
def client_for(tmp_path):
    def make(content_dir, fmt, script=None, originals=None, documents=None):
        bundle.build_bundle(content_dir, fmt, "v1", tmp_path / "kb.zip", originals_dir=originals, documents=documents)
        settings = Settings(data_dir=tmp_path / "data", llm=LLMConfig(output_mode="prompted"))
        client = TestClient(create_app(settings, model=(script or ScriptedModel([], [])).model))
        assert client.post("/api/kb/import", json={"path": str(tmp_path / "kb.zip")}).status_code == 200
        return client
    return make


def test_docs_toc_section_and_original_pdf(pageindex_kb, tmp_path, client_for):
    originals = tmp_path / "originals"
    originals.mkdir()
    (originals / "press-manual.pdf").write_bytes(b"%PDF-1.4 fake")
    client = client_for(pageindex_kb, "pageindex", originals=originals,
                        documents=[{"id": "pi-test", "title": "Hydraulic press", "tags": ["Line 3"]}])

    [doc] = client.get("/api/docs").json()
    assert doc["title"] == "Hydraulic press" and doc["tags"] == ["Line 3"] and doc["has_original"]
    assert client.get("/api/tags").json() == ["Line 3"]

    toc = client.get("/api/docs/pi-test/toc").json()
    assert [(s["title"], s["depth"], s["has_children"]) for s in toc["sections"]] == [
        ("Safety", 0, False), ("Operation", 0, True), ("Start-up", 1, False), ("Shutdown", 1, False)]

    sec = client.get("/api/docs/pi-test/sections/0002").json()
    assert sec["path"] == ["Operation", "Start-up"] and sec["pages"] == [2] and "180 bar" in sec["text"]
    assert client.get("/api/docs/pi-test/sections/9999").status_code == 404
    assert client.get("/api/docs/nope/toc").status_code == 404

    pdf = client.get("/api/docs/pi-test/original")
    assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")


def test_original_missing_is_404(pageindex_kb, client_for):
    client = client_for(pageindex_kb, "pageindex")
    assert client.get("/api/docs/pi-test/original").status_code == 404


def test_search_finds_codes_with_snippets(markdown_kb, client_for):
    client = client_for(markdown_kb, "markdown")
    hits = client.get("/api/search", params={"q": "E17"}).json()
    assert hits[0]["title"] == "Error E17"
    assert "E17 means torque not reached" in hits[0]["snippet"]
    assert hits[0]["path"][-2:] == ["Troubleshooting", "Error E17"]


def test_tag_filter_limits_search_and_answers(tmp_path, client_for):
    root = write_markdown_kb(tmp_path / "md")
    (root / "paint-booth.md").write_text("# Paint booth\n## Filters\nChange filters every 200 hours.\n")
    script = ScriptedModel(select=[{"section_ids": []}],
                           answer=[{"found": True, "answer": "Every 200 hours.", "section_ids": []}])
    client = client_for(root, "markdown", script=script, documents=[
        {"id": "torque-tool.md", "tags": ["Station 4"]}, {"id": "paint-booth.md", "tags": ["Paint"]}])

    assert client.get("/api/tags").json() == ["Paint", "Station 4"]
    hits = client.get("/api/search", params={"q": "filters", "tags": ["Station 4"]}).json()
    assert hits == []
    assert client.get("/api/search", params={"q": "filters", "tags": ["Paint"]}).json()[0]["title"] == "Filters"

    r = client.post("/api/ask", json={"question": "How often to change filters?", "tags": ["Paint"]}).json()
    select_prompt = script.prompts[0][1]
    assert "Paint booth" in select_prompt and "Station 4 Torque" not in select_prompt
    # the model chose nothing, but keyword search points at Filters: it is read and cited
    assert r["found"] and [c["title"] for c in r["citations"]] == ["Filters"]
    assert all("Station 4 Torque" not in prompt for _, prompt in script.prompts)

    none = client.post("/api/ask", json={"question": "Anything?", "tags": ["No such station"]}).json()
    assert not none["found"] and "No documents" in none["answer"]


def test_suggestions_come_from_summarised_leaf_sections(tmp_path, client_for):
    root = write_markdown_kb(tmp_path / "md", summaries={
        "Station 4 Torque Tool Manual > Setup > Calibration": "How often to calibrate and which torque to set.",
        "Station 4 Torque Tool Manual > Troubleshooting > Error E17": "What error E17 means."})
    client = client_for(root, "markdown")
    assert client.get("/api/suggestions").json() == [
        "What does the manual say about calibration?", "What does the manual say about error E17?"]


def test_history_feedback_and_export(pageindex_kb, tmp_path, client_for):
    script = ScriptedModel(select=[{"section_ids": ["A2"]}],
                           answer=[{"found": True, "answer": "Set pressure to 180 bar.", "section_ids": ["A2"]}])
    client = client_for(pageindex_kb, "pageindex", script=script)
    result = client.post("/api/ask", json={"question": "Start-up pressure?"}).json()
    assert result["history_id"]

    [h] = client.get("/api/history").json()
    assert h["id"] == result["history_id"] and h["question"] == "Start-up pressure?" and h["found"]

    assert client.post("/api/feedback", json={"history_id": "missing", "rating": "wrong"}).status_code == 404
    assert client.post("/api/feedback", json={"history_id": h["id"], "rating": "meh"}).status_code == 422
    ok = client.post("/api/feedback", json={"history_id": h["id"], "rating": "wrong", "comment": "It is 190 bar"})
    assert ok.status_code == 200
    assert client.get("/api/feedback/summary").json() == {"total": 1, "helpful": 0, "wrong": 1}

    out = tmp_path / "feedback.json"
    assert client.post("/api/feedback/export", json={"path": str(tmp_path / "x.txt")}).status_code == 422
    exported = client.post("/api/feedback/export", json={"path": str(out)}).json()
    assert exported["count"] == 1
    [vote] = json.loads(out.read_text(encoding="utf-8"))["feedback"]
    assert vote["question"] == "Start-up pressure?" and vote["comment"] == "It is 190 bar"


def test_stream_result_carries_history_id(pageindex_kb, client_for):
    script = ScriptedModel(select=[{"section_ids": ["A2"]}],
                           answer=[{"found": True, "answer": "180 bar.", "section_ids": ["A2"]}])
    client = client_for(pageindex_kb, "pageindex", script=script)
    with client.stream("POST", "/api/ask/stream", json={"question": "Pressure?"}) as r:
        events = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]
    done = events[-1]
    assert done["stage"] == "done" and done["result"]["history_id"]


def test_catalog_title_and_description_are_used_and_bad_ids_reported(tmp_path, client_for):
    root = write_markdown_kb(tmp_path / "md")
    client = client_for(root, "markdown", documents=[
        {"id": "torque-tool.md", "title": "DT-450 manual", "description": "Station 4 torque driver", "tags": ["S4"]},
        {"id": "missing.md", "title": "Not in the bundle"}])
    [doc] = client.get("/api/docs").json()
    assert doc["title"] == "DT-450 manual" and doc["description"] == "Station 4 torque driver"
    kb = client.get("/api/kb").json()
    assert kb["documents"][0]["title"] == "DT-450 manual" and kb["catalog_unmatched"] == ["missing.md"]


def test_a_catalog_description_that_is_a_flattened_table_is_made_readable(tmp_path, client_for):
    # final check: the spare-parts card read "Part number Description Price (INR) In stock HX9 4002 ..."
    root = tmp_path / "md"
    root.mkdir()
    (root / "parts.md").write_text("# parts.md\n\n## Parts list\n| Part number | Description | Price (INR) |\n|---|---|---|\n"
                                   "| HX9-4002 | gasket | 3,140 |\n| HX9-4004 | cable | 36,105 |\n| HX9-4007 | sensor | 44,154 |\n",
                                   encoding="utf-8")
    client = client_for(root, "markdown", documents=[{"id": "parts.md", "title": "parts.md", "description":
                                                      "Part number Description Price (INR) HX9 4002 gasket 3,140 HX9 4004"}])
    [doc] = client.get("/api/kb").json()["documents"]
    assert doc["description"] == "A table of 3 rows (Part number, Description, Price (INR))."


def test_markdown_description_falls_back_to_the_introduction(tmp_path, client_for):
    client = client_for(write_markdown_kb(tmp_path / "md"), "markdown")
    [doc] = client.get("/api/docs").json()
    assert doc["description"] == "Covers the DT-450 torque driver."


def test_suggestions_use_section_titles_when_there_are_no_summaries(tmp_path, client_for):
    client = client_for(write_markdown_kb(tmp_path / "md"), "markdown")
    suggestions = client.get("/api/suggestions").json()
    assert suggestions and all(s.startswith("What does the manual say about") for s in suggestions)
    assert not any("Preface" in s for s in suggestions)


def test_voting_again_replaces_the_earlier_vote(pageindex_kb, client_for):
    script = ScriptedModel(select=[{"section_ids": ["A2"]}],
                           answer=[{"found": True, "answer": "180 bar.", "section_ids": ["A2"]}])
    client = client_for(pageindex_kb, "pageindex", script=script)
    hid = client.post("/api/ask", json={"question": "Pressure?"}).json()["history_id"]
    client.post("/api/feedback", json={"history_id": hid, "rating": "wrong"})
    client.post("/api/feedback", json={"history_id": hid, "rating": "helpful"})
    assert client.get("/api/feedback/summary").json() == {"total": 1, "helpful": 1, "wrong": 0}


def test_blank_question_is_rejected(pageindex_kb, client_for):
    client = client_for(pageindex_kb, "pageindex")
    assert client.post("/api/ask", json={"question": "   "}).status_code == 422
    assert client.get("/api/history").json() == []


def test_greetings_are_saved_to_history_but_not_read_as_context(pageindex_kb, client_for):
    # a conversation of only instant replies was missing from Recent (final computer-use check)
    client = client_for(pageindex_kb, "pageindex")
    r = client.post("/api/ask", json={"question": "hi", "conversation_id": "g1"}).json()
    assert r["smalltalk"] and r["history_id"]
    saved = client.get("/api/history").json()
    assert [(e["question"], e["smalltalk"]) for e in saved] == [("hi", True)]
    assert client.app.state.engine.context_for("g1") == []


def test_a_conversation_can_be_deleted(pageindex_kb, client_for):
    client = client_for(pageindex_kb, "pageindex")
    client.post("/api/ask", json={"question": "hi", "conversation_id": "d1"})
    client.post("/api/ask", json={"question": "hello", "conversation_id": "d2"})
    assert client.delete("/api/history", params={"conversation_id": "d1"}).json() == {"deleted": 1}
    assert [e["conversation_id"] for e in client.get("/api/history").json()] == ["d2"]
    assert client.delete("/api/history", params={"conversation_id": "d1"}).status_code == 404
    # the desktop app's page is another origin: its browser asks first whether DELETE is allowed
    # (it was not, so History's delete failed in the app; final check)
    preflight = client.options("/api/history", params={"conversation_id": "d2"}, headers={
        "Origin": "http://tauri.localhost", "Access-Control-Request-Method": "DELETE"})
    assert preflight.status_code == 200 and "DELETE" in preflight.headers["access-control-allow-methods"]


def test_conversation_context_and_history(pageindex_kb, client_for):
    script = ScriptedModel(
        select=[{"section_ids": ["A2"]},
                {"standalone_question": "What pressure at shutdown?", "section_ids": ["A3"]},
                {"section_ids": ["A0"]}],
        answer=[{"found": True, "answer": "180 bar.", "section_ids": ["A2"]},
                {"found": True, "answer": "Release pressure.", "section_ids": ["A3"]},
                {"found": True, "answer": "Apply lockout.", "section_ids": ["A0"]}])
    client = client_for(pageindex_kb, "pageindex", script=script)
    client.post("/api/ask", json={"question": "Start-up pressure?", "conversation_id": "c1"})
    second = client.post("/api/ask", json={"question": "and at shutdown?", "conversation_id": "c1"}).json()
    assert second["standalone_question"] == "What pressure at shutdown?"
    assert "Q: Start-up pressure?" in script.prompts[2][1]  # the follow-up's selection saw turn 1
    client.post("/api/ask", json={"question": "Lockout?", "conversation_id": "c2"})
    assert "CONVERSATION" not in script.prompts[4][1]  # a new conversation starts clean

    turns = client.get("/api/history", params={"conversation_id": "c1"}).json()
    assert [t["question"] for t in turns] == ["Start-up pressure?", "and at shutdown?"]  # oldest first
    assert all(t["conversation_id"] == "c1" for t in turns)
    assert client.post("/api/ask", json={"question": "x", "conversation_id": "../bad"}).status_code == 422


def test_a_question_stays_in_history_when_its_answer_never_comes(pageindex_kb, client_for):
    """Seen on an 8 GB laptop with the 4B model: the app closed mid-answer and the conversation was gone."""
    from oasis_engine.api import UNANSWERED
    script = ScriptedModel(select=[{"section_ids": ["A2"]}] * 2,
                           answer=[{"found": True, "answer": "180 bar.", "section_ids": ["A2"]}] * 2)
    client = client_for(pageindex_kb, "pageindex", script=script)
    with client.stream("POST", "/api/ask/stream", json={"question": "Pressure?", "conversation_id": "c1"}) as r:
        done = [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")][-1]
    [entry] = client.get("/api/history", params={"conversation_id": "c1"}).json()
    assert entry["id"] == done["result"]["history_id"] and entry["answer"] == "180 bar."  # placeholder replaced
    # the app closes while the next question is answered: only its placeholder was saved
    engine = client.app.state.engine
    from oasis_engine.api import AskRequest
    engine.hold_place(AskRequest(question="And at shutdown?", conversation_id="c1"))
    entries = client.get("/api/history", params={"conversation_id": "c1"}).json()
    assert [e["question"] for e in entries] == ["Pressure?", "And at shutdown?"]
    assert entries[1]["answer"] == UNANSWERED and not entries[1]["found"]
    # nor is it taken for an earlier answer by the next question
    assert engine.context_for("c1") == [("Pressure?", "180 bar.")]


def test_an_instant_reply_leaves_no_placeholder(pageindex_kb, client_for):
    client = client_for(pageindex_kb, "pageindex")
    with client.stream("POST", "/api/ask/stream", json={"question": "hello", "conversation_id": "c2"}) as r:
        list(r.iter_lines())
    saved = client.get("/api/history", params={"conversation_id": "c2"}).json()
    assert [(e["question"], e.get("pending", False), e.get("smalltalk")) for e in saved] == [("hello", False, True)]


def test_a_documents_noun_keeps_its_acronyms_and_its_title_block_is_not_suggested():
    # Helix start page: "What does the it security standard say about Helix Precision - IT Security
    # Standard (ITS-03, version 2.1)?"
    from oasis_engine.api import _doc_noun, _names_of, _unsuggestable
    from oasis_engine.loaders import parse_markdown
    doc = parse_markdown("# it_security_standard.docx\n\n## Helix Precision - IT Security Standard (ITS-03)\nIntro.\n\n"
                         "## 1. Passwords\nAt least 14 characters.\n", "i", "it_security_standard.docx")
    noun = _doc_noun(doc)
    assert noun == "the IT security standard"
    assert _unsuggestable("Helix Precision - IT Security Standard (ITS-03)", _names_of(doc, noun))
    assert not _unsuggestable("1. Passwords", _names_of(doc, noun))


def test_example_questions_name_documents_as_written_and_skip_their_title_sections():
    # final check: "the HX 500 manual" (hyphen lost), "the plant performance h1.2026", and "say about
    # HX-900 Horizontal Machining Center - Service Manual?" / "spindle log say about spindle log?"
    from oasis_engine.api import _doc_noun, _names_of, _unsuggestable
    from oasis_engine.loaders import parse_markdown

    def noun(name):
        return _doc_noun(parse_markdown(f"# {name}\n\n## A\ntext\n", "x", name))
    assert noun("HX-500_manual.pdf") == "the HX-500 manual"
    assert noun("plant_performance_h1_2026.xlsx") == "the plant performance h1 2026"
    assert noun("notes_v3_2.docx") == "the notes v3.2"
    assert noun("meeting-notes.docx") == "the meeting notes"
    service = parse_markdown("# HX-900_service_manual.pdf\n\n## A\ntext\n", "s", "HX-900_service_manual.pdf")
    names = _names_of(service, _doc_noun(service))
    assert _unsuggestable("HX-900 Horizontal Machining Center - Service Manual", names)
    assert not _unsuggestable("Rotary table service", names)
    log = parse_markdown("# HX-900_spindle_log.csv\n\n## A\ntext\n", "l", "HX-900_spindle_log.csv")
    assert _unsuggestable("Spindle log", _names_of(log, _doc_noun(log)))
