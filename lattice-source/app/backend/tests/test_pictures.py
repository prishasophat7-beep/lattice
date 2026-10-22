"""Requests for pictures and summaries: sorted by the model (keywords only decide whether to ask
it), answered with the right tool in both modes, with typos, follow-ups and fallbacks."""
import pytest
from fastapi.testclient import TestClient

from conftest import ChatScript, ScriptedModel, make_pdf, write_pageindex_kb
from oasis_engine import bundle
from oasis_engine.api import create_app
from oasis_engine.config import AppConfig, save_config
from oasis_engine.figures import picture_request
from oasis_engine.llm import LLMConfig
from oasis_engine.pipeline import summary_request
from oasis_engine.settings import Settings

PICTURES = {"kind": "pictures", "topic": ""}
QUESTION = {"kind": "question", "topic": ""}


@pytest.mark.parametrize("question", [
    "shoe me some images from the manual", "Show me the diagrams", "any pics of the start-up?",
    "diagarm for the shutdown", "What does the start-up diagram show?", "give me a summary of this doc",
    "sumary please", "What is this manual about?", "which manuals do you have?",
    "How do I reset the error summary?", "What does the outline of the booth look like?"])
def test_possible_special_requests_are_sent_to_the_model(question):
    from oasis_engine.api import _ABOUT_MANUALS
    assert picture_request(question) or summary_request(question) or _ABOUT_MANUALS.search(question)


@pytest.mark.parametrize("question", ["What torque for M6 bolts?", "How do I start the press?",
                                      "What does E17 mean?"])
def test_ordinary_questions_skip_the_extra_model_call(question):
    from oasis_engine.api import _ABOUT_MANUALS
    assert not (picture_request(question) or summary_request(question) or _ABOUT_MANUALS.search(question))


def make(tmp_path, script, originals=True, profile="line"):
    content = write_pageindex_kb(tmp_path / "pi")
    folder = None
    if originals:
        folder = tmp_path / "originals"
        folder.mkdir()
        make_pdf(folder / "press-manual.pdf", ["Safety", "Start-up", "Shutdown"], figure_pages={2})
    bundle.build_bundle(content, "pageindex", "v1", tmp_path / "kb.zip", originals_dir=folder)
    data = tmp_path / "data"
    save_config(data, AppConfig(profile=profile))
    client = TestClient(create_app(Settings(data_dir=data, llm=LLMConfig(output_mode="prompted")),
                                   model=script.model))
    assert client.post("/api/kb/import", json={"path": str(tmp_path / "kb.zip")}).status_code == 200
    return client


def ask(client, question, **kw):
    r = client.post("/api/ask", json={"question": question, **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_picture_request_lists_the_pages_with_pictures(tmp_path):
    client = make(tmp_path, ScriptedModel(select=[], answer=[], route=[PICTURES]))  # one routing call only
    r = ask(client, "shoe me some images from the manual")
    assert r["found"] and r["pictures_only"] and r["figures"] == [{"citation": 0, "page": 2, "image": None, "caption": None, "description": None}]
    assert r["citations"][0]["title"] == "Start-up"


def test_topic_without_pictures_says_so_and_lists_the_others(tmp_path):
    client = make(tmp_path, ScriptedModel(select=[], answer=[], route=[{"kind": "pictures", "topic": "shutdown"}]))
    r = ask(client, "show me a diagram of the shutdown")
    assert r["answer"].startswith('No section about "shutdown" has a picture')
    assert [f["page"] for f in r["figures"]] == [2]


def test_follow_up_takes_the_topic_of_the_question_before(tmp_path):
    script = ScriptedModel(select=[{"section_ids": ["A2"]}], route=[PICTURES],
                           answer=[{"found": True, "answer": "Set pressure to 180 bar.", "section_ids": ["A2"]}])
    client = make(tmp_path, script)
    ask(client, "What pressure at start-up?", conversation_id="c1")
    r = ask(client, "shoe me some images", conversation_id="c1")  # no topic of its own
    assert r["pictures_only"] and r["answer"].startswith('Pictures in the documents about "pressure start-up"')
    assert r["standalone_question"] is None  # not glued onto the earlier question


def test_word_like_summary_in_a_real_question_gets_a_normal_answer(tmp_path):
    script = ScriptedModel(route=[QUESTION], select=[{"section_ids": ["A1"]}],
                           answer=[{"found": True, "answer": "Apply lockout first.", "section_ids": ["A1"]}])
    client = make(tmp_path, script)
    r = ask(client, "How do I reset the error summary?")
    assert r["answer"] == "Apply lockout first." and not r["summary"] and not r["pictures_only"]


def test_question_about_a_picture_gets_an_answer_and_the_picture(tmp_path):
    script = ScriptedModel(route=[QUESTION], select=[{"section_ids": ["A1"]}],
                           answer=[{"found": True, "answer": "Apply lockout first.", "section_ids": ["A1"]}])
    client = make(tmp_path, script)
    r = ask(client, "What does the start-up diagram show?")
    assert not r["pictures_only"] and r["answer"] == "Apply lockout first."
    page2 = [f for f in r["figures"] if f["page"] == 2]
    assert page2 and r["citations"][page2[0]["citation"]]["title"] == "Start-up"


def test_summary_covers_the_whole_manual(tmp_path):
    script = ScriptedModel(select=[], answer=[], route=[{"kind": "summary", "topic": ""}],
                           summary=[{"overview": "Hydraulic press manual.",
                                     "points": ["Lockout", "- Start-up at 180 bar"]}])
    client = make(tmp_path, script)
    r = ask(client, "give me a summary of this doc")
    assert r["summary"] and r["found"] and r["unverified_numbers"] == []
    assert r["answer"] == "Hydraulic press manual.\n\n- Lockout\n- Start-up at 180 bar"
    assert [c["title"] for c in r["citations"]] == ["Safety", "Operation"]  # the chapters
    prompt = script.prompts[-1][1]
    assert "Start-up" in prompt and "Shutdown" in prompt  # the whole table of contents, not one section


def test_overview_of_all_manuals_needs_no_model_beyond_routing(tmp_path):
    client = make(tmp_path, ScriptedModel(select=[], answer=[], route=[{"kind": "all_manuals", "topic": ""}]))
    r = ask(client, "which manuals do you have?")
    assert r["summary"] and "1 document" in r["answer"] and "Hydraulic press manual." in r["answer"]


def test_keyword_rules_decide_when_the_model_is_unreachable(tmp_path):
    script = ScriptedModel(select=[], answer=[], route=[{"_finish": "length", "kind": "question"}])
    client = make(tmp_path, script)
    r = ask(client, "shoe me some images from the manual")
    assert r["pictures_only"] and [f["page"] for f in r["figures"]] == [2]


def test_without_original_pdfs_the_answer_explains_why(tmp_path):
    client = make(tmp_path, ScriptedModel(select=[], answer=[], route=[PICTURES]), originals=False)
    r = ask(client, "show me the pictures")
    assert not r["found"] and "original PDF files" in r["answer"]


def test_chat_agent_shows_pictures_with_its_tool(tmp_path):
    script = ChatScript([{"tool": "find_pictures", "args": {"topic": "start-up"}},
                         {"text": "Here is the start-up picture."}])
    client = make(tmp_path, script, profile="office")
    r = ask(client, "Show me the diagrams of the start-up", mode="chat")
    assert r["mode"] == "chat" and [f["page"] for f in r["figures"]] == [2]
    assert r["citations"][r["figures"][0]["citation"]]["title"] == "Start-up"


def test_chat_agent_summarizes_from_the_outline_and_cites_it(tmp_path):
    script = ChatScript([{"tool": "manual_outline", "args": {"manual": "press"}},
                         {"text": "The manual covers lockout [S1] and start-up at 180 bar [S3]."}])
    client = make(tmp_path, script, profile="office")
    r = ask(client, "Summarize the press manual", mode="chat")
    assert [c["title"] for c in r["citations"]] == ["Safety", "Start-up"] and r["unverified_numbers"] == []


def test_chat_agent_can_ask_which_manual(tmp_path):
    script = ChatScript([{"tool": "ask_which_manual",
                          "args": {"question": "Which press?", "manuals": ["press-manual", "other"]}},
                         {"text": "Which press?"}])
    client = make(tmp_path, script, profile="office")
    r = ask(client, "Summarize it", mode="chat")
    assert not r["found"] and r["clarify"]["prompt"] == "Which press?" and r["clarify"]["options"]


@pytest.mark.parametrize("text, fixed", [("diagarm for the shutdown", "diagram for the shutdown"),
                                         ("sumary please", "summary please"),
                                         ("What torque for the bolts?", "What torque for the bolts?")])
def test_typos_in_picture_and_summary_words_are_fixed_before_routing(text, fixed):
    from oasis_engine.figures import fix_typos
    assert fix_typos(text) == fixed


def test_chat_mode_picture_request_is_answered_directly(tmp_path):
    script = ChatScript([], route={"kind": "pictures", "topic": "start-up"})  # the agent is never called
    client = make(tmp_path, script, profile="office")
    r = ask(client, "show me the diagrams of the start-up", mode="chat")
    assert r["mode"] == "chat" and r["pictures_only"] and [f["page"] for f in r["figures"]] == [2]
