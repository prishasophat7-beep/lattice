"""Where a conversation is in a manual: follow-ups read there first; "next" / "go back" step on."""
from fastapi.testclient import TestClient

from conftest import ScriptedModel
from oasis_engine import bundle
from oasis_engine.api import create_app
from oasis_engine.focus import focus_of, navigation, near, neighbour, step_to
from oasis_engine.llm import LLMConfig
from oasis_engine.loaders import load_markdown
from oasis_engine.search import SectionIndex
from oasis_engine.settings import Settings

MANUAL = """# Vision Tool Manual

## Single Image Classification

### Creating a single image solution

#### Adding Data

Click Add Folder and choose the parent folder of the class folders.

#### Training

Set Epochs to 50 and Batch Size to 16, then click Train.

#### Testing

Click Test and pick a folder of images the model has not seen.

## Multi Image Classification

### Creating a multi image solution

#### Adding Data

There are two ways to add image groups.

##### Type 1

Click Add Image Group and pick a folder with one subfolder per group.

##### Type 2

Drag the grouped images onto the Image Groups list.

#### Training

Set Epochs to 80 and Batch Size to 8, then click Train Group.

#### Train Test Split

Choose 80/20 to keep a fifth of the image groups for testing.

#### Testing

Click Test Group; each group gets one verdict.

## Defect Detection

Draw the boxes first, then click Train Detector.
"""


def index_of(tmp_path):
    (tmp_path / "kb").mkdir()
    (tmp_path / "kb" / "vision.md").write_text(MANUAL, encoding="utf-8")
    return SectionIndex(load_markdown(tmp_path / "kb"))


def node(doc, *path):
    return next(n for n in doc.sections if doc.short_path(n) == list(path))


def test_a_follow_up_is_read_in_the_part_the_conversation_is_in(tmp_path):
    index = index_of(tmp_path)
    doc = index.docs[0]
    training = node(doc, "Multi Image Classification", "Creating a multi image solution", "Training")
    where = lambda nid: doc.short_path(nid) if nid else None  # noqa: E731
    # Multi's own Testing, not Single's (which comes first in the manual)
    assert where(near("How do I test it?", index, 0, training))[0] == "Multi Image Classification"
    assert where(near("How do I test it?", index, 0, training))[-1] == "Testing"
    # "test" names "Testing" more fully than "Train Test Split"; "split" names the split
    assert where(near("How do I split the images?", index, 0, training))[-1] == "Train Test Split"
    # another part named: that part's section; only the part named: the same step there
    assert where(near("How do I test it in single image?", index, 0, training))[:1] == ["Single Image Classification"]
    assert where(near("And in single image?", index, 0, training)) == \
        ["Single Image Classification", "Creating a single image solution", "Training"]
    # nothing in this part by that name: searched as usual
    for other in ("How do I draw boxes?", "What is the weather?", "What are the parameters?"):
        assert near(other, index, 0, training) is None, other


def test_next_and_back_follow_the_manuals_order(tmp_path):
    doc = index_of(tmp_path).docs[0]
    training = node(doc, "Multi Image Classification", "Creating a multi image solution", "Training")
    assert doc.sections[neighbour(doc, training, "next")].title == "Train Test Split"
    assert doc.sections[neighbour(doc, training, "back")].title == "Adding Data"
    place = focus_of(doc, training)
    assert place.path[1:] == ["Multi Image Classification", "Creating a multi image solution", "Training"]
    assert (place.previous, place.next) == ("Adding Data", "Train Test Split")
    # the last step of a part: next is the next part
    testing = node(doc, "Multi Image Classification", "Creating a multi image solution", "Testing")
    assert doc.sections[neighbour(doc, testing, "next")].title == "Defect Detection"
    # a button's title wins when it names a section nearby
    assert doc.sections[step_to(doc, training, "next", "Testing")].title == "Testing"
    assert navigation("What's next?") == ("next", "") and navigation("Next: Testing") == ("next", "Testing")
    assert navigation("go back") == ("back", "") and navigation("Next week's schedule?") is None


def test_a_conversation_keeps_its_place(tmp_path):
    (tmp_path / "kb").mkdir()
    (tmp_path / "kb" / "vision.md").write_text(MANUAL, encoding="utf-8")
    bundle.build_bundle(tmp_path / "kb", "markdown", "v1", tmp_path / "kb.zip")
    answer = {"found": True, "answer": "See the section.", "section_ids": []}
    script = ScriptedModel(select=[], answer=[answer] * 3, route=[{"kind": "question", "topic": ""}] * 4)
    client = TestClient(create_app(Settings(data_dir=tmp_path / "data", llm=LLMConfig(output_mode="prompted")),
                                   model=script.model))
    assert client.post("/api/kb/import", json={"path": str(tmp_path / "kb.zip")}).status_code == 200
    ask = lambda q, **kw: client.post("/api/ask", json={"question": q, "conversation_id": "c1", **kw}).json()  # noqa: E731

    asked_back = ask("How do I train a classification model?")
    multi = next(o for o in asked_back["clarify"]["options"] if o["label"] == "Multi Image Classification")
    first = ask(multi["question"], doc_ids=multi["doc_ids"], section=multi["section"],
                replaces=asked_back["history_id"])
    assert first["focus"]["path"][-1] == "Training" and first["focus"]["next"] == "Train Test Split"
    # read straight from Multi's Testing: no selection step (one model call)
    calls = len(script.prompts)
    second = ask("How do I test it?")
    assert second["citations"][0]["locator"].startswith("Multi Image Classification")
    assert second["focus"]["path"][-1] == "Testing"
    assert [step for step, _ in script.prompts[calls:]] == ["answer"]
    # "go back": the section before, in the manual's order, without a search
    back = ask("go back")
    assert back["citations"][0]["title"] == "Train Test Split" and back["focus"]["path"][-1] == "Train Test Split"
    # a reopened conversation shows where it was
    turns = client.get("/api/history", params={"conversation_id": "c1"}).json()
    assert turns[-1]["focus"]["path"][-1] == "Train Test Split"
    # the user closed the "Working in" line: "next" has nowhere to go from
    off = ask("next", focus_off=True)
    assert not off["found"] and "Ask about a topic first" in off["answer"]


def client_with(tmp_path, script):
    (tmp_path / "kb").mkdir()
    (tmp_path / "kb" / "vision.md").write_text(MANUAL, encoding="utf-8")
    bundle.build_bundle(tmp_path / "kb", "markdown", "v1", tmp_path / "kb.zip")
    client = TestClient(create_app(Settings(data_dir=tmp_path / "data", llm=LLMConfig(output_mode="prompted")),
                                   model=script.model))
    assert client.post("/api/kb/import", json={"path": str(tmp_path / "kb.zip")}).status_code == 200
    return client


def test_the_conversation_is_steered_by_the_manuals_headings(tmp_path):
    answer = {"found": True, "answer": "See the section.", "section_ids": []}
    script = ScriptedModel(select=[{"section_ids": []}] * 3, answer=[answer] * 5,
                           route=[{"kind": "question", "topic": ""}] * 6)
    client = client_with(tmp_path, script)
    ask = lambda q, cid="c1", **kw: client.post(  # noqa: E731
        "/api/ask", json={"question": q, "conversation_id": cid, **kw}).json()

    asked_back = ask("How do I train a classification model?")
    multi = next(o for o in asked_back["clarify"]["options"] if o["label"] == "Multi Image Classification")
    first = ask(multi["question"], doc_ids=multi["doc_ids"], section=multi["section"],
                replaces=asked_back["history_id"])
    # the same step in the manual's other parts, to ask about with one click
    assert [e["label"] for e in first["focus"]["elsewhere"]] == ["Single Image Classification"]
    # a step the manual describes in two ways: which one? (asked without the model)
    calls = len(script.prompts)
    which = ask("How do I add data?")
    assert [o["label"] for o in which["clarify"]["options"]] == ["Type 1", "Type 2"]
    assert which["clarify"]["options"][1]["detail"].startswith("Drag the grouped images")
    assert which["focus"]["path"][-1] == "Adding Data" and len(script.prompts) == calls
    picked = which["clarify"]["options"][1]
    type_2 = ask(picked["question"], doc_ids=picked["doc_ids"], section=picked["section"], replaces=which["history_id"])
    assert type_2["found"] and type_2["citations"][0]["title"] == "Type 2"
    # named already: no question back
    assert ask("How do I add data with Type 1?")["citations"][0]["title"] == "Type 1"

    # "walk me through ...": the first step, then Next for the others
    walk = ask("Walk me through creating a multi image solution", cid="c2")
    assert walk["citations"][0]["title"] == "Adding Data"
    assert walk["answer"].startswith("**Step 1 of 4: Adding Data**") and "Next step: Training" in walk["answer"]
    assert walk["focus"]["next"] == "Training"


def test_an_answer_not_in_the_documents_offers_the_closest_sections(tmp_path):
    script = ScriptedModel(select=[{"section_ids": []}], route=[{"kind": "question", "topic": ""}] * 2,
                           answer=[{"found": False, "answer": "", "section_ids": []}] * 2)
    client = client_with(tmp_path, script)
    r = client.post("/api/ask", json={"question": "What does a Train Group licence cost?", "conversation_id": "c1"}).json()
    assert not r["found"] and r["considered"]
    assert any(c["title"] == "Training" for c in r["considered"])


def test_chat_mode_reads_a_picked_section_and_asks_which_way_without_the_model(tmp_path):
    from oasis_engine.config import AppConfig, save_config
    (tmp_path / "kb").mkdir()
    (tmp_path / "kb" / "vision.md").write_text(MANUAL, encoding="utf-8")
    bundle.build_bundle(tmp_path / "kb", "markdown", "v1", tmp_path / "kb.zip")
    save_config(tmp_path / "data", AppConfig(profile="office", default_mode="chat"))
    script = ScriptedModel(select=[], answer=[{"found": True, "answer": "Set Epochs to 80.", "section_ids": []}] * 2)
    client = TestClient(create_app(Settings(data_dir=tmp_path / "data", llm=LLMConfig(output_mode="prompted")),
                                   model=script.model))
    assert client.post("/api/kb/import", json={"path": str(tmp_path / "kb.zip")}).status_code == 200
    doc = client.app.state.engine.docs[0]
    training = node(doc, "Multi Image Classification", "Creating a multi image solution", "Training")
    # an option picked after a question back: exactly that section is read (the assistant, told
    # which one was picked, read a namesake elsewhere in a real-model test)
    first = client.post("/api/ask", json={"question": "How do I train it in Multi Image Classification?",
                                          "conversation_id": "c1", "mode": "chat", "doc_ids": [doc.doc_id],
                                          "section": training}).json()
    assert first["mode"] == "chat" and first["citations"][0]["node_id"] == training
    assert [step for step, _ in script.prompts] == ["answer"]
    which = client.post("/api/ask", json={"question": "How do I add data?", "conversation_id": "c1", "mode": "chat",
                                          "focus": {"doc_id": doc.doc_id, "node_id": training}}).json()
    assert [o["label"] for o in which["clarify"]["options"]] == ["Type 1", "Type 2"]
    assert which["mode"] == "chat" and len(script.prompts) == 1  # no model call for the question back
    # a follow-up naming a section of the part: that section (the assistant cited another module's)
    test = client.post("/api/ask", json={"question": "How do I test it?", "conversation_id": "c1", "mode": "chat",
                                         "focus": {"doc_id": doc.doc_id, "node_id": training}}).json()
    assert test["mode"] == "chat" and test["citations"][0]["locator"].startswith("Multi Image Classification")
    assert test["citations"][0]["title"] == "Testing"


def test_an_answer_mixing_parts_of_a_manual_says_so_and_stays_put(tmp_path):
    script = ScriptedModel(select=[{"section_ids": []}], answer=[{"found": True, "answer": "Click Train.", "section_ids": []}],
                           route=[{"kind": "question", "topic": ""}] * 2)
    client = client_with(tmp_path, script)
    engine = client.app.state.engine
    doc = engine.docs[0]
    single = node(doc, "Single Image Classification", "Creating a single image solution", "Training")
    multi = node(doc, "Multi Image Classification", "Creating a multi image solution", "Testing")
    from oasis_engine.pipeline import AskResult
    result = AskResult(question="How do I teach it?", found=True, answer="Click Train, then Test Group.",
                       citations=[engine.pipeline.citation(0, single), engine.pipeline.citation(0, multi)])
    engine.place_after(result, None)
    engine.note_mixed_parts(result)
    assert result.focus is None  # not "working in" either part
    assert "different parts of the manual (Single Image Classification, Multi Image Classification)" in result.answer
    # naming the parts, or comparing them: no note
    for question in ("How do I train in single image and test in multi image classification?",
                     "What is the difference between the two?"):
        named = AskResult(question=question, found=True, answer="...", citations=result.citations)
        engine.note_mixed_parts(named)
        assert named.answer == "...", question


def test_a_model_the_service_no_longer_offers_is_said_plainly():
    from pydantic_ai import ModelHTTPError
    from oasis_engine.pipeline import refusal
    gone = refusal(ModelHTTPError(404, "qwen/qwen3.8-27b:free", {"message": "This model is unavailable for free."}))
    assert 'does not offer the model "qwen/qwen3.8-27b:free"' in str(gone) and "Manage" in str(gone)
    assert "API key" in str(refusal(ModelHTTPError(401, "m", {})))
    from oasis_engine.pipeline import ProviderBusy
    upstream = refusal(ModelHTTPError(404, "m", {"message": "Provider returned error", "code": 404}))
    assert isinstance(upstream, ProviderBusy)  # the provider's own failure: try again
