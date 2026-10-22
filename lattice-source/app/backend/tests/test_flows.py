"""Whole flows of the app: a failed or stopped answer, History, a walk-through, cleaning up."""
import json
import os
import time

import pytest
from fastapi.testclient import TestClient

from conftest import ScriptedModel
from oasis_engine import bundle
from oasis_engine.api import create_app
from oasis_engine.attachments import AttachmentStore
from oasis_engine.llm import LLMConfig
from oasis_engine.pipeline import ProviderBusy
from oasis_engine.settings import Settings
from oasis_engine.store import JsonlLog
from test_focus import client_with

ANSWER = {"found": True, "answer": "Check oil, then set 180 bar.", "section_ids": ["A2"]}


@pytest.fixture
def client_for(tmp_path):
    def make(content_dir, fmt, script=None):
        bundle.build_bundle(content_dir, fmt, "v1", tmp_path / "kb.zip")
        settings = Settings(data_dir=tmp_path / "data", llm=LLMConfig(output_mode="prompted"))
        client = TestClient(create_app(settings, model=(script or ScriptedModel([], [])).model))
        assert client.post("/api/kb/import", json={"path": str(tmp_path / "kb.zip")}).status_code == 200
        return client
    return make


def stream(client, body) -> list[dict]:
    with client.stream("POST", "/api/ask/stream", json=body) as r:
        return [json.loads(line[6:]) for line in r.iter_lines() if line.startswith("data: ")]


def turns(client, cid) -> list[tuple]:
    return [(e["question"], bool(e.get("pending")), e["answer"])
            for e in client.get("/api/history", params={"conversation_id": cid}).json()]


def busy(engine):
    """The model service fails from now on."""
    async def fail(*_a, on_progress=None, **_k):
        if on_progress:
            await on_progress({"stage": "selecting"})
        raise ProviderBusy("The language model is busy or still loading; try again shortly")
    engine.pipeline.ask = fail


def test_a_failed_regenerate_keeps_the_answer_it_was_to_replace(pageindex_kb, client_for):
    """It was replaced by "No answer: the question was stopped" for good (audit, 20 Oct 2026)."""
    client = client_for(pageindex_kb, "pageindex", script=ScriptedModel(select=[{"section_ids": ["A2"]}],
                                                                        answer=[ANSWER]))
    done = stream(client, {"question": "Start-up?", "conversation_id": "c1"})[-1]
    busy(client.app.state.engine)
    events = stream(client, {"question": "Start-up?", "conversation_id": "c1",
                             "replaces": done["result"]["history_id"]})
    assert events[-1]["stage"] == "error" and events[-1]["status"] == 503
    assert not any(e["stage"] == "held" for e in events)  # Regenerate saves no placeholder
    good = done["result"]["answer"]
    assert turns(client, "c1") == [("Start-up?", False, good)]
    # nor is the earlier answer lost to the next question
    assert client.app.state.engine.context_for("c1") == [("Start-up?", good)]


def test_try_again_after_an_error_leaves_one_turn(pageindex_kb, client_for):
    """A failed question left a "stopped" turn in History before the answer Try again gave."""
    script = ScriptedModel(select=[{"section_ids": ["A2"]}], answer=[ANSWER])
    client = client_for(pageindex_kb, "pageindex", script=script)
    engine = client.app.state.engine
    working = engine.pipeline.ask
    busy(engine)
    events = stream(client, {"question": "Start-up?", "conversation_id": "c2"})
    assert events[0]["stage"] == "held" and events[-1]["stage"] == "error"
    assert turns(client, "c2") == []  # the placeholder went with the error
    engine.pipeline.ask = working
    again = stream(client, {"question": "Start-up?", "conversation_id": "c2"})[-1]
    assert turns(client, "c2") == [("Start-up?", False, again["result"]["answer"])]


def test_ask_again_after_stop_replaces_the_stopped_turn(pageindex_kb, client_for):
    """Stopped (or the app closed): the question stays in History; Ask again replaces it."""
    from oasis_engine.api import UNANSWERED
    client = client_for(pageindex_kb, "pageindex", script=ScriptedModel(select=[{"section_ids": ["A2"]}],
                                                                        answer=[ANSWER]))
    engine = client.app.state.engine
    held = engine.hold_place(type("R", (), {"conversation_id": "c3", "mode": "manuals", "replaces": None,
                                            "question": "Start-up?", "tags": []})())
    assert turns(client, "c3") == [("Start-up?", True, UNANSWERED)]
    # the app sends the placeholder's id (from the "held" event) as the turn Ask again replaces
    again = stream(client, {"question": "Start-up?", "conversation_id": "c3", "replaces": held})[-1]
    assert turns(client, "c3") == [("Start-up?", False, again["result"]["answer"])]


def test_history_is_read_again_only_when_it_changed(tmp_path):
    """Every question read its conversation some seven times: 230 ms each at 2,000 entries."""
    log = JsonlLog(tmp_path / "h.jsonl")
    log.append({"question": "a"})
    first = log.read()
    reads = []
    original = json.loads
    try:
        json.loads = lambda s, *a, **k: (reads.append(1), original(s, *a, **k))[1]
        assert log.read() == first and not reads  # unchanged: not parsed again
        log.append({"question": "b"})
        assert [e["question"] for e in log.read()] == ["b", "a"] and reads
        log.remove(lambda e: e["question"] == "a")
        assert [e["question"] for e in log.read()] == ["b"]
    finally:
        json.loads = original
    read = log.read()
    read.append({"question": "mine"})  # a caller's list is its own
    assert len(log.read()) == 1


def test_an_answer_in_an_old_conversation_can_be_voted_on(pageindex_kb, client_for):
    """Only the last 500 entries were searched: "That answer is no longer in the history"."""
    client = client_for(pageindex_kb, "pageindex", script=ScriptedModel(select=[{"section_ids": ["A2"]}],
                                                                        answer=[ANSWER]))
    old = client.post("/api/ask", json={"question": "Start-up?", "conversation_id": "old"}).json()
    history = client.app.state.engine.history
    for i in range(600):
        history.append({"conversation_id": f"x{i}", "question": "q", "answer": "a", "found": True})
    vote = client.post("/api/feedback", json={"history_id": old["history_id"], "rating": "helpful"})
    assert vote.status_code == 200


def test_a_walk_through_numbers_every_step_and_says_when_it_is_done(tmp_path):
    answer = {"found": True, "answer": "See the section.", "section_ids": []}
    script = ScriptedModel(select=[], answer=[answer] * 8, route=[{"kind": "question", "topic": ""}] * 8)
    client = client_with(tmp_path, script)
    ask = lambda q: client.post("/api/ask", json={"question": q, "conversation_id": "w"}).json()  # noqa: E731

    assert ask("Walk me through creating a multi image solution")["answer"].startswith("**Step 1 of 4: Adding Data**")
    two = ask("Next: Training")
    assert two["answer"].startswith("**Step 2 of 4: Training**") and "Next step: Train Test Split" in two["answer"]
    assert ask("next")["answer"].startswith("**Step 3 of 4: Train Test Split**")
    last = ask("next")
    assert last["answer"].startswith("**Step 4 of 4: Testing**")
    assert 'That was the last step of "Creating a multi image solution"' in last["answer"]
    after = ask("next")  # on into the next section of the manual, saying the task is done
    assert after["citations"][0]["title"] == "Defect Detection"
    assert after["answer"].startswith('*You have finished "Creating a multi image solution"')
    assert not ask("go back")["answer"].startswith("**Step")  # no longer walking through it

    assert not ask("next")["answer"].startswith("**Step")  # nor after it


def test_old_knowledge_base_versions_can_be_removed(tmp_path, markdown_kb):
    client = TestClient(create_app(Settings(data_dir=tmp_path / "data", llm=LLMConfig(output_mode="prompted")),
                                   model=ScriptedModel(select=[], answer=[]).model))
    for v in ("v1", "v2"):
        bundle.build_bundle(markdown_kb, "markdown", v, tmp_path / f"{v}.zip")
        assert client.post("/api/kb/import", json={"path": str(tmp_path / f"{v}.zip")}).status_code == 200
    assert client.get("/api/kb/versions").json() == {"active": "v2", "installed": ["v1", "v2"]}
    refused = client.delete("/api/kb/versions/v2")
    assert refused.status_code == 422 and "in use" in refused.json()["detail"]
    assert client.delete("/api/kb/versions/../v2").status_code in (404, 422)
    assert client.delete("/api/kb/versions/v1").json()["installed"] == ["v2"]
    assert not (tmp_path / "data" / "versions" / "v1").exists()
    assert client.delete("/api/kb/versions/v1").status_code == 422  # gone already


def test_a_new_knowledge_base_waits_for_the_answer_being_written(tmp_path, markdown_kb):
    client = TestClient(create_app(Settings(data_dir=tmp_path / "data", llm=LLMConfig(output_mode="prompted")),
                                   model=ScriptedModel(select=[], answer=[]).model))
    bundle.build_bundle(markdown_kb, "markdown", "v1", tmp_path / "v1.zip")
    assert client.post("/api/kb/import", json={"path": str(tmp_path / "v1.zip")}).status_code == 200
    client.app.state.engine.answering = 1
    refused = client.post("/api/kb/import", json={"path": str(tmp_path / "v1.zip")})
    assert refused.status_code == 409 and "being written" in refused.json()["detail"]
    assert client.post("/api/kb/activate", json={"kb_version": "v1"}).status_code == 409
    client.app.state.engine.answering = 0
    assert client.post("/api/kb/activate", json={"kb_version": "v1"}).status_code == 200


def test_files_of_abandoned_conversations_are_cleaned_up(tmp_path):
    store = AttachmentStore(tmp_path / "att")
    for cid in ("asked", "abandoned", "fresh"):
        store.add(cid, "note.txt", b"belt replaced")
    day_ago = time.time() - 2 * 24 * 3600
    for cid in ("asked", "abandoned"):
        os.utime(tmp_path / "att" / cid, (day_ago, day_ago))
    assert store.prune({"asked"}) == 1
    assert [cid for cid in ("asked", "abandoned", "fresh") if store.list(cid)] == ["asked", "fresh"]


def test_a_walk_through_of_a_chapter_is_not_one_of_the_whole_manual(tmp_path):
    """"Walk me through operating the HX-500" stepped through the manual's chapters (installed app)."""
    from oasis_engine.focus import task_sections
    from oasis_engine.loaders import load_markdown
    from oasis_engine.search import SectionIndex
    (tmp_path / "kb").mkdir()
    # "HX-500" all through the manual: the search ranks those mentions, and Operation was not
    # among its first 20 matches
    notes = "".join(f"\n\n## {i}. Note {i}\n\nThe HX-500 HX-500 spindle.\n\n### Check\n\nA.\n\n### Log\n\nB."
                    for i in range(10, 40))
    (tmp_path / "kb" / "HX-500_manual.md").write_text(
        "# HX-500 Manual\n\nThe HX-500 operator manual for the HX-500.\n\n## 1. About this manual\n\n"
        "Read the safety chapter first.\n\n"
        "## 2. Safety\n\nWear goggles.\n\n## 5. Operation\n\nOperating the HX-500.\n\n"
        "### 5.1 Start-up\n\nSwitch on the main isolator.\n\n### 5.2 Running a program\n\nPress Cycle Start.\n\n"
        "### 5.3 Shutdown\n\nPress Emergency Stop." + notes, encoding="utf-8")
    index = SectionIndex(load_markdown(tmp_path / "kb"))
    [(di, nid)] = task_sections("operating the HX-500", index)
    assert index.docs[di].sections[nid].title == "5. Operation"
    # no section titled for it: answered as a question, not a walk through the manual's chapters
    assert task_sections("maintaining the HX-500", index) == []


def test_an_unusable_reply_keeps_the_answer_in_the_conversations_part(tmp_path):
    """Regenerate of "How do I test it?" in Multi X answered from Object Detection's Testing: the
    section was read straight away, the model's reply was unusable, and the search that took over
    knew nothing of where the conversation was (Nemotron on OpenRouter, real-model test, 20 Oct 2026)."""
    from oasis_engine.pipeline import AskResult
    bogus = {"bogus": True}  # an unusable reply
    good = {"found": True, "answer": "See the section.", "section_ids": []}
    script = ScriptedModel(select=[bogus, bogus], answer=[good, good], route=[{"kind": "question", "topic": ""}] * 4)
    # another part's Testing is the keyword search's best match, as Object Detection's was
    import test_focus
    (tmp_path / "kb").mkdir()
    (tmp_path / "kb" / "vision.md").write_text(test_focus.MANUAL.replace(
        "Click Test and pick a folder of images the model has not seen.",
        "To test it, click Test: the test runs on test images the model has not seen."), encoding="utf-8")
    bundle.build_bundle(tmp_path / "kb", "markdown", "v1", tmp_path / "kb.zip")
    client = TestClient(create_app(Settings(data_dir=tmp_path / "data", llm=LLMConfig(output_mode="prompted")),
                                   model=script.model))
    assert client.post("/api/kb/import", json={"path": str(tmp_path / "kb.zip")}).status_code == 200
    engine = client.app.state.engine
    doc = engine.docs[0]
    assert doc.short_path(engine.pipeline.index.search("How do I test it?")[0].node_id)[0] == "Single Image Classification"
    training = next(n for n in doc.sections if doc.short_path(n) ==
                    ["Multi Image Classification", "Creating a multi image solution", "Training"])
    ask = lambda q, **kw: client.post("/api/ask", json={"question": q, "conversation_id": "u", **kw}).json()  # noqa: E731
    assert ask("How do I train it?", doc_ids=[doc.doc_id], section=training)["found"]

    read: list[list[str]] = []  # the sections each answer step was shown, first one first
    real = engine.pipeline._answer

    async def answer(question, standalone, shown, *a, **kw):
        read.append([" > ".join(doc.short_path(nid)) for _, nid in shown.by_id.values()])
        if len(read) == 1:  # the section read straight away: the model's reply was unusable
            return AskResult(question=question, found=False, answer="")
        return await real(question, standalone, shown, *a, **kw)
    engine.pipeline._answer = answer
    tested = ask("How do I test it?", focus={"doc_id": doc.doc_id, "node_id": training})
    multi_testing = "Multi Image Classification > Creating a multi image solution > Testing"
    assert read[0] == [multi_testing]
    assert read[1][0] == multi_testing  # the search's own picks after it
    assert tested["citations"][0]["locator"].startswith("Multi Image Classification")


def test_show_me_the_pictures_after_a_summary_is_all_of_them(tmp_path):
    """'No section about "summarize document" has a picture', after a summary request (flow audit)."""
    engine = client_with(tmp_path, ScriptedModel(select=[], answer=[])).app.state.engine
    before = [("Summarize the user manual", "It covers projects and boards.")]
    assert engine.picture_topic("", "show me the pictures", before).topic == []
    assert engine.picture_topic("", "show me a picture of it", [("How do I set up the camera?", "...")]).topic == ["set", "up", "camera"]


def test_which_documents_are_there_lists_them_when_the_model_cannot_sort_it(tmp_path):
    """The model's sorting failed (demo mode, a small model's unusable reply): the keyword rules
    took "Which documents are there?" for a question and asked "What would you like to know?"."""
    client = client_with(tmp_path, ScriptedModel(select=[], answer=[], route=[{"bogus": True}] * 4))
    for q in ("Which documents are there?", "list the manuals"):
        listed = client.post("/api/ask", json={"question": q}).json()
        assert listed["found"] and "This knowledge base has" in listed["answer"], (q, listed["answer"])


def test_the_embedding_server_test_says_what_is_wrong_plainly(tmp_path):
    client = client_with(tmp_path, ScriptedModel(select=[], answer=[]))
    said = lambda body: client.post("/api/config/test-embeddings", json=body).json()  # noqa: E731
    assert said({"base_url": "ftp://x", "model": "m"})["detail"].startswith("The address must start with http")
    assert said({"base_url": "http://127.0.0.1:9/v1", "model": ""})["detail"].startswith("Enter the embedding model")
    assert said({"base_url": "http://127.0.0.1:9/v1", "model": "m"})["detail"].startswith("Cannot reach http://127.0.0.1:9/v1")
