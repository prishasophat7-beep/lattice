"""Vague first questions are asked back; answers worked out from the documents are labelled."""
import asyncio

from conftest import ChatScript, ScriptedModel
from oasis_engine.chat import ChatAgent
from oasis_engine.loaders import load_pageindex, parse_markdown
from oasis_engine.pipeline import Pipeline
from oasis_engine.search import SectionIndex
from oasis_engine.vague import vague_clarification

HANDBOOK = """# handbook.docx

## Uploads
Files can be up to 25 MB. The upload limit applies to each file.

## Invitations
Invitation links expire after 7 days. There is a limit of 50 members per project.

## Leave
Privilege leave: 18 days a year. Up to 45 days can be carried forward.
"""


def index():
    return SectionIndex([parse_markdown(HANDBOOK, "h", "handbook.docx")])


def test_a_vague_word_offers_the_sections_that_use_it():
    c = vague_clarification("What is the limit?", index())
    assert c is not None and [o.label for o in c.options] == ["Uploads", "Invitations"] or \
        sorted(o.label for o in c.options) == ["Invitations", "Uploads"]
    assert all(o.question == f"What does “{o.label}” say about limit?" for o in c.options)
    assert "25 MB" in " ".join(o.detail for o in c.options)


def test_clear_questions_are_not_asked_back():
    ix = index()
    for q in ("How many days of privilege leave?", "What is the upload limit?", "What is PL?", "invitations"):
        assert vague_clarification(q, ix) is None, q


def test_a_question_with_no_topic_offers_the_documents():
    c = vague_clarification("tell me about it", index())
    assert c is not None and c.options[0].question == "What does handbook.docx cover?"
    # "How long?" offers the sections that give durations, when there are some (see the test below)
    assert vague_clarification("How long?", index()) is not None


def test_a_vague_first_question_is_asked_back_but_not_a_follow_up(pageindex_kb, tmp_path):
    from fastapi.testclient import TestClient
    from oasis_engine import bundle
    from oasis_engine.api import create_app
    from oasis_engine.llm import LLMConfig
    from oasis_engine.settings import Settings
    root = tmp_path / "md"
    root.mkdir()
    (root / "handbook.md").write_text(HANDBOOK, encoding="utf-8")
    bundle.build_bundle(root, "markdown", "v1", tmp_path / "kb.zip")
    script = ScriptedModel(select=[{"section_ids": ["A1"]}] * 2,
                           answer=[{"found": True, "answer": "Files can be up to 25 MB.", "section_ids": ["A1"]}] * 2)
    client = TestClient(create_app(Settings(data_dir=tmp_path / "data", llm=LLMConfig(output_mode="prompted")),
                                   model=script.model))
    assert client.post("/api/kb/import", json={"path": str(tmp_path / "kb.zip")}).status_code == 200
    r = client.post("/api/ask", json={"question": "What is the limit?", "conversation_id": "c1"}).json()
    assert r["clarify"] and all(o["question"] for o in r["clarify"]["options"]) and not r["found"]
    client.post("/api/ask", json={"question": "How big can uploads be?", "conversation_id": "c2"})
    r = client.post("/api/ask", json={"question": "What is the limit?", "conversation_id": "c2"}).json()
    assert not r.get("clarify")  # a follow-up: "the limit" of what was just discussed


def test_a_worked_out_manuals_answer_is_labelled(pageindex_kb):
    docs = load_pageindex(pageindex_kb)
    script = ScriptedModel(select=[{"standalone_question": "Can I start at 200 bar?", "section_ids": ["A2"]}],
                           answer=[{"found": True, "worked_out": True, "section_ids": ["A2"],
                                    "answer": "No: start-up pressure is set to 180 bar."}])
    r = asyncio.run(Pipeline(docs, script.model, output_mode="prompted").ask("Can I start at 200 bar?"))
    assert r.found and r.worked_out


def test_a_worked_out_chat_answer_is_labelled(tmp_path):
    from conftest import write_markdown_kb
    from oasis_engine.loaders import load_markdown
    docs = load_markdown(write_markdown_kb(tmp_path / "md"))
    script = ChatScript([{"tool": "search_manuals", "args": {"query": "E17"}},
                         {"text": "**Worked out from the documents:** no, E17 means torque not reached [S1]."}])
    r = asyncio.run(ChatAgent(script.model).ask("Is E17 a sensor fault?", docs, SectionIndex(docs)))
    assert r.found and r.worked_out and r.answer.startswith("no, E17")


def test_a_topicless_how_long_offers_the_sections_that_give_durations():
    # final computer-use check: "How long?" offered the list of documents
    from oasis_engine.loaders import parse_markdown
    from oasis_engine.search import SectionIndex
    from oasis_engine.vague import vague_clarification
    ds = [parse_markdown("# leave.md\n\n## Maternity\n26 weeks of paid leave.\n\n## Holidays\nThe list of holidays.\n\n"
                         "## Marriage\n5 working days, once.\n", "l", "leave.md"),
          parse_markdown("# it.md\n\n## Incidents\nReport a lost laptop within 2 hours.\n", "i", "it.md")]
    c = vague_clarification("How long?", SectionIndex(ds), None)
    assert c is not None and "durations" in c.prompt
    assert {o.label for o in c.options} == {"Maternity", "Marriage", "Incidents"}


def test_a_kind_option_snippet_starts_and_ends_on_whole_words():
    # final check: "...ve Employee of the Year 5 days", "available once during employment, to b"
    from oasis_engine.loaders import parse_markdown
    from oasis_engine.search import SectionIndex
    from oasis_engine.vague import vague_clarification
    ds = [parse_markdown("# leave.md\n\n## Award\nAwards are given for the Employee of the Year who receives "
                         "5 days of extra leave, available once during employment, to be used within the year.\n\n"
                         "## Marriage\n5 working days, once.\n", "l", "leave.md")]
    c = vague_clarification("How long?", SectionIndex(ds), None)
    detail = next(o.detail for o in c.options if o.label == "Award").split(": ...", 1)[1]
    assert detail.startswith("Employee of the Year") or detail.split()[0] in "Awards are given for the".split()
    assert detail.rstrip(".").split()[-1] in "Awards are given for the Employee of the Year who receives 5 days of " \
        "extra leave, available once during employment, to be used within the year".split()
