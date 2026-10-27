"""Answer-generation audit before the demo (27 Oct 2026): what each flow got wrong, as found by reading it."""
import asyncio

import pytest

from conftest import ScriptedModel
from oasis_engine import pipeline as p
from oasis_engine.chat import _without_tool_talk
from oasis_engine.loaders import load_markdown, load_pageindex
from oasis_engine.pipeline import CUT_OFF_NOTE, DraftAnswer, ModelRanAway, Pipeline
from oasis_engine.replies import parse_answer

STEPS = ("To bring the press back up after a stop, work through these steps in order:\n"
         "1. Check the pressure gauge reads between 160 and 200 bar before anything else.\n"
         "2. Open the bleed valve slowly and let trapped air escape from the upper circuit.\n"
         "3. Inspect the hose couplings for oil weeping, cracked sleeves or loose clamps.\n"
         "4. Confirm the emergency stop is released and the guard door switch is closed.\n"
         "5. Start the pump at low speed and listen for cavitation noise near the reservoir")


def test_an_answer_line_starting_with_sections_is_not_taken_for_its_sources():
    out = parse_answer("The machine has three modes.\nSections: Setup, Run and Maintenance are in chapter 4.\n"
                       "In Run mode, press START.", DraftAnswer)
    assert out.answer.endswith("press START.") and out.section_ids == []
    assert parse_answer("Press START.\nSections: A6, A12", DraftAnswer).section_ids == ["A6", "A12"]
    assert parse_answer("Press START.\nSOURCES: A6 (Run mode)", DraftAnswer).answer == "Press START."


@pytest.mark.parametrize("cls, base", [(p.SectionChoice, p.SELECT_INSTRUCTIONS),
                                       (p.FollowupChoice, p.FOLLOWUP_SELECT_INSTRUCTIONS),
                                       (p.DraftAnswer, p.ANSWER_INSTRUCTIONS)])
def test_plain_text_instructions_name_no_structured_fields(cls, base):
    for old, _new in p._TEXT_WORDING[cls.__name__]:
        assert old in base  # the wording still matches the instructions
    text = p._instructions(base, cls, "text")
    for field in ("section_ids", "found to", "worked_out", "standalone_question", "empty list"):
        assert field not in text
    assert p._instructions(base, cls, "native") == base


def test_a_long_answer_cut_off_at_its_cap_is_shown_marked_cut_off(tmp_path):
    (tmp_path / "press.md").write_text("# Press manual\n\n## Restart after a stop\n\n" + STEPS + ".\n", encoding="utf-8")
    docs = load_markdown(tmp_path)
    restart = next(nid for nid, s in docs[0].sections.items() if s.title == "Restart after a stop")
    script = ScriptedModel(select=[f"A{int(restart)}"], answer=[{"_text": STEPS, "_finish": "length"}])
    pipe = Pipeline(docs, script.model, output_mode="text", fast_path=False)
    r = asyncio.run(pipe.ask("How do I restart the press after a stop?"))
    assert "Open the bleed valve" in r.answer and CUT_OFF_NOTE in r.answer
    assert [step for step, _ in script.prompts] == ["select", "answer"]


def test_a_short_reply_cut_off_at_its_cap_is_still_a_runaway(pageindex_kb):
    docs = load_pageindex(pageindex_kb)
    script = ScriptedModel(select=["A2"], answer=[{"_text": "pressure pressure pressure", "_finish": "length"}])
    pipe = Pipeline(docs, script.model, output_mode="text", fast_path=False)
    with pytest.raises(ModelRanAway):
        asyncio.run(pipe.ask("Start-up pressure?"))


def test_chat_keeps_answer_lines_that_say_calculate():
    answer = "The penalty is 1.5% per week [1].\n\nTo calculate it, multiply the order value by 1.5%.\n" \
             "I will call search_manuals next."
    assert _without_tool_talk(answer) == ("The penalty is 1.5% per week [1].\n\n"
                                          "To calculate it, multiply the order value by 1.5%.")
    assert "calculate(" not in _without_tool_talk("A.\nUsing calculate(2 + 2) gives 4.")


def test_chat_mode_summaries_use_the_chat_model_not_the_manuals_one(tmp_path):
    # office setup: Manuals mode on the 4B on this computer, Chat mode on a server model. A Chat
    # "summarize the manual" was sorted and written by the 4B, for minutes, with the server idle
    from fastapi.testclient import TestClient
    from conftest import write_markdown_kb
    from oasis_engine import bundle
    from oasis_engine.api import create_app
    from oasis_engine.config import AppConfig, save_config
    from oasis_engine.llm import LLMConfig
    from oasis_engine.settings import Settings
    data = tmp_path / "data"
    bundle.build_bundle(write_markdown_kb(tmp_path / "md"), "markdown", "v1", tmp_path / "kb.zip",
                        documents=[{"id": "torque-tool.md", "title": "DT-450 manual"}])
    save_config(data, AppConfig(profile="office"))
    local = ScriptedModel(select=[], answer=[])  # Manuals mode's model: must not be called
    server = ScriptedModel(select=[], answer=[], route=[{"kind": "summary", "topic": ""}],
                           summary=[{"overview": "The DT-450 torque tool manual.", "points": ["Error E17"]}])
    client = TestClient(create_app(Settings(data_dir=data, llm=LLMConfig(output_mode="prompted")), model=local.model))
    assert client.post("/api/kb/import", json={"path": str(tmp_path / "kb.zip")}).status_code == 200
    engine = client.app.state.engine
    engine.chat_model = server.model
    r = client.post("/api/ask", json={"question": "Summarize the manual", "mode": "chat"}).json()
    assert r["summary"] and "DT-450 torque tool manual" in r["answer"]
    assert local.prompts == [] and [s for s, _ in server.prompts] == ["route", "summary"]
    assert engine.chat_pipeline.index is engine.pipeline.index  # the same documents, searched once


def test_the_about_section_of_what_is_a_product_is_not_called_the_users_pick():
    # given as "The user picked [S1] ...", the 4B answered "Based on the section you selected ..."
    from oasis_engine.chat import ChatAgent, ChatDeps
    from oasis_engine.search import SectionIndex
    from test_what_is_product import _library
    docs = _library()
    deps = ChatDeps(docs=docs, index=SectionIndex(docs), allowed=None)
    prompt = asyncio.run(ChatAgent._with_presearch("What is Frontier application", deps, introduction=(1, "0001")))
    assert "user picked" not in prompt and "own introduction" in prompt


def test_a_long_decline_with_offers_is_a_decline_not_general_knowledge(tmp_path):
    from conftest import ChatScript
    from fastapi.testclient import TestClient
    from conftest import write_markdown_kb
    from oasis_engine import bundle
    from oasis_engine.api import create_app
    from oasis_engine.config import AppConfig, save_config
    from oasis_engine.llm import LLMConfig
    from oasis_engine.settings import Settings
    data = tmp_path / "data"
    bundle.build_bundle(write_markdown_kb(tmp_path / "md"), "markdown", "v1", tmp_path / "kb.zip")
    save_config(data, AppConfig(profile="office"))
    decline = ("Sorry, I can only help with questions about your documents and work topics. The documents here "
               "cover the DT-450 torque tool, its error codes and calibration, but not writing Python scripts.\n\n"
               "I can help you with:\n- Error codes such as E17 and what to do about them.\n- Calibration "
               "intervals and the steps the manual gives.\n- Anything else the installed manuals describe.\n\n"
               "Would you like me to look up one of these?")
    client = TestClient(create_app(Settings(data_dir=data, llm=LLMConfig(output_mode="prompted")),
                                   model=ChatScript([{"text": decline}]).model))
    assert client.post("/api/kb/import", json={"path": str(tmp_path / "kb.zip")}).status_code == 200
    r = client.post("/api/ask", json={"question": "Write a python script to rename files", "mode": "chat"}).json()
    assert len(decline) > 400 and r["answer"].startswith("Sorry") and not r["general"] and not r["found"]
