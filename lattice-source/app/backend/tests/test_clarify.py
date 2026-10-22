"""Asking which manual is meant when a question fits several equally (two stations, same tool)."""
import asyncio

from fastapi.testclient import TestClient

from conftest import ChatScript, ScriptedModel, TORQUE_MANUAL
from oasis_engine import bundle
from oasis_engine.api import create_app
from oasis_engine.config import AppConfig, save_config
from oasis_engine.llm import LLMConfig
from oasis_engine.loaders import load_markdown
from oasis_engine.pipeline import Pipeline
from oasis_engine.settings import Settings


def two_stations(root):
    root.mkdir(parents=True)
    (root / "station-4.md").write_text(TORQUE_MANUAL, encoding="utf-8")
    (root / "station-7.md").write_text(TORQUE_MANUAL.replace("Station 4", "Station 7").replace("line 3", "line 5"),
                                       encoding="utf-8")
    return root


def ask(docs, script, question, **kw):
    return asyncio.run(Pipeline(docs, script.model, output_mode="prompted").ask(question, **kw))


def test_same_question_fits_two_manuals_asks_which(tmp_path):
    docs = load_markdown(two_stations(tmp_path / "kb"))
    r = ask(docs, ScriptedModel(select=[], answer=[]), "What does E17 mean?")  # no model call
    assert not r.found and r.clarify is not None and r.answer == r.clarify.prompt
    assert {o.doc_ids[0] for o in r.clarify.options} == {d.doc_id for d in docs}
    assert {o.detail for o in r.clarify.options} == {"Error E17"}


def test_picked_manual_is_answered_without_asking_again(tmp_path):
    docs = load_markdown(two_stations(tmp_path / "kb"))
    script = ScriptedModel(select=[{"section_ids": ["A6"]}],
                           answer=[{"found": True, "answer": "Torque not reached.", "section_ids": ["A6"]}])
    r = ask(docs, script, "What does E17 mean?", doc_filter={1}, clarify=False)
    assert r.found and r.clarify is None and r.citations[0].doc_id == docs[1].doc_id


def test_one_manual_or_a_distinct_question_is_not_asked_back(tmp_path, markdown_kb):
    one = load_markdown(markdown_kb)
    script = ScriptedModel(select=[{"section_ids": ["A6"]}],
                           answer=[{"found": True, "answer": "Torque not reached.", "section_ids": ["A6"]}])
    assert ask(one, script, "What does E17 mean?").clarify is None
    two = load_markdown(two_stations(tmp_path / "kb"))
    pipe = Pipeline(two, script.model, output_mode="prompted")
    assert pipe._which_manual("What does E17 mean on station 7?", None) is None
    assert pipe._which_manual("What does E17 mean?", {0}) is None  # a station filter already decides


def test_api_asks_back_then_answers_from_the_picked_manual(tmp_path):
    kb = two_stations(tmp_path / "kb")
    bundle.build_bundle(kb, "markdown", "v1", tmp_path / "kb.zip", documents=[
        {"id": "station-4.md", "title": "Station 4 torque tool"}, {"id": "station-7.md", "title": "Station 7 torque tool"}])
    script = ScriptedModel(select=[{"section_ids": ["A6"]}],
                           answer=[{"found": True, "answer": "Torque not reached.", "section_ids": ["A6"]}])
    client = TestClient(create_app(Settings(data_dir=tmp_path / "data", llm=LLMConfig(output_mode="prompted")),
                                   model=script.model))
    assert client.post("/api/kb/import", json={"path": str(tmp_path / "kb.zip")}).status_code == 200
    first = client.post("/api/ask", json={"question": "What does E17 mean?", "conversation_id": "c1"}).json()
    labels = [o["label"] for o in first["clarify"]["options"]]
    assert sorted(labels) == ["Station 4 torque tool", "Station 7 torque tool"]
    seven = next(o for o in first["clarify"]["options"] if o["label"] == "Station 7 torque tool")
    second = client.post("/api/ask", json={"question": "What does E17 mean?", "conversation_id": "c1",
                                           "doc_ids": seven["doc_ids"], "replaces": first["history_id"]}).json()
    assert second["found"] and second["citations"][0]["doc_name"] == "Station 7 torque tool"
    history = client.get("/api/history", params={"conversation_id": "c1"}).json()
    assert [h["answer"] for h in history] == ["Torque not reached."]  # the question back was replaced


def test_chat_greeting_is_not_marked_as_a_general_answer(tmp_path):
    data = tmp_path / "data"
    from conftest import write_markdown_kb
    bundle.build_bundle(write_markdown_kb(tmp_path / "md"), "markdown", "v1", tmp_path / "kb.zip")
    save_config(data, AppConfig(profile="office"))
    client = TestClient(create_app(Settings(data_dir=data, llm=LLMConfig(output_mode="prompted")),
                                   model=ChatScript([{"text": "Hello! How can I help?"}]).model))
    client.post("/api/kb/import", json={"path": str(tmp_path / "kb.zip")})
    r = client.post("/api/ask", json={"question": "hi", "mode": "chat"}).json()
    assert r["smalltalk"] and r["answer"].startswith("Hello! I answer questions")  # fixed, on every model


def test_summary_after_an_answer_from_two_manuals_asks_which_of_them(tmp_path):
    kb = two_stations(tmp_path / "kb")
    bundle.build_bundle(kb, "markdown", "v1", tmp_path / "kb.zip", documents=[
        {"id": "station-4.md", "title": "Station 4 torque tool"}, {"id": "station-7.md", "title": "Station 7 torque tool"}])
    script = ScriptedModel(select=[{"section_ids": ["A6", "B6"]}], route=[{"kind": "summary", "topic": ""}] * 2,
                           answer=[{"found": True, "answer": "Torque not reached.", "section_ids": ["A6", "B6"]}],
                           summary=[{"overview": "Station 7 manual.", "points": ["Setup"]}])
    client = TestClient(create_app(Settings(data_dir=tmp_path / "data", llm=LLMConfig(output_mode="prompted")),
                                   model=script.model))
    client.post("/api/kb/import", json={"path": str(tmp_path / "kb.zip")})
    ids = [d["doc_id"] for d in client.get("/api/docs").json()]
    both = client.post("/api/ask", json={"question": "What does E17 mean?", "conversation_id": "c",
                                         "doc_ids": ids}).json()
    assert len({c["doc_id"] for c in both["citations"]}) == 2, both
    r = client.post("/api/ask", json={"question": "summarize this doc", "conversation_id": "c"}).json()
    assert r["clarify"]["prompt"] == "Which document should I summarize?"
    seven = next(o for o in r["clarify"]["options"] if o["label"] == "Station 7 torque tool")
    r = client.post("/api/ask", json={"question": "summarize this doc", "conversation_id": "c",
                                      "doc_ids": seven["doc_ids"], "replaces": r["history_id"]}).json()
    assert r["summary"] and r["answer"].startswith("Station 7 manual.")


# one manual, the same steps under several parts (as a web export of a 250-page tool manual has them)
MODULES_MANUAL = """# Vision Tool Manual

## Single Image Classification

### Creating a single image solution

#### Adding Data

Click Add Folder and choose the parent folder of the class folders.

#### Training

Set Epochs to 50 and Batch Size to 16, then click Train.

## Multi Image Classification

### Creating a multi image solution

#### Adding Data

Click Add Image Group and pick the grouped images.

#### Training

Set Epochs to 80 and Batch Size to 8, then click Train Group.

## Defect Detection

### Creating a detection solution

#### Training

Draw the boxes first, then click Train Detector.

#### Export

Click Export ONNX to save the trained detector.
"""


def modules_kb(root):
    root.mkdir(parents=True)
    (root / "vision.md").write_text(MODULES_MANUAL, encoding="utf-8")
    return root


def test_a_task_several_parts_have_is_asked_back_by_part(tmp_path):
    from oasis_engine.search import SectionIndex
    from oasis_engine.vague import vague_clarification
    index = SectionIndex(load_markdown(modules_kb(tmp_path / "kb")))
    c = vague_clarification("How do I train a classification model?", index)
    # the parts its other word names ("classification"), by their own headings, past the step
    # "Creating a ... solution"; each option stands for its part's Training section
    assert c is not None and [o.label for o in c.options] == ["Single Image Classification", "Multi Image Classification"]
    assert all(index.docs[0].sections[o.section].title == "Training" for o in c.options)
    c = vague_clarification("How do I train?", index)
    assert c is not None and len(c.options) == 3  # no part named: every part that has the step
    # clear already: a part named, or a step only one part has
    for clear in ("How do I train a multi image classification model?", "How do I export the detector?",
                  "What batch size do I set for training single image classification?"):
        assert vague_clarification(clear, index) is None, clear


def test_a_picked_part_is_answered_from_its_section(tmp_path):
    bundle.build_bundle(modules_kb(tmp_path / "kb"), "markdown", "v1", tmp_path / "kb.zip")
    script = ScriptedModel(select=[], answer=[{"found": True, "answer": "Set Epochs to 80.", "section_ids": []}],
                           route=[{"kind": "question", "topic": ""}] * 2)
    client = TestClient(create_app(Settings(data_dir=tmp_path / "data", llm=LLMConfig(output_mode="prompted")),
                                   model=script.model))
    assert client.post("/api/kb/import", json={"path": str(tmp_path / "kb.zip")}).status_code == 200
    first = client.post("/api/ask", json={"question": "How do I train a classification model?",
                                          "conversation_id": "c1"}).json()
    multi = next(o for o in first["clarify"]["options"] if o["label"] == "Multi Image Classification")
    second = client.post("/api/ask", json={"question": multi["question"], "conversation_id": "c1",
                                           "doc_ids": multi["doc_ids"], "section": multi["section"],
                                           "replaces": first["history_id"]}).json()
    assert second["found"] and second["citations"][0]["locator"].endswith("Multi Image Classification > "
                                                                           "Creating a multi image solution > Training")
