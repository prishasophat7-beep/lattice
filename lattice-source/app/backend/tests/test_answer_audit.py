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


def test_a_table_row_restating_the_sentence_above_is_kept():
    # "| Casual / Sick Leave (CL/SL) | 12 days |" under "employees get 12 days of Casual / Sick Leave":
    # the table was left with its header only (Nemotron 3.5 Lightning, 27 Oct 2026)
    from oasis_engine.loops import without_repeated_sentences
    answer = ("Employees get **12 days of Casual / Sick Leave (CL/SL)** per year.\n\n"
              "| Leave Type | Annual Entitlement |\n|---|---|\n| Casual / Sick Leave (CL/SL) | 12 days |")
    assert without_repeated_sentences(answer) == answer


def test_a_total_written_out_in_bold_is_checked_against_its_parts():
    from oasis_engine.numbers import unverified_numbers
    source = "PO-26-0503 9,20,000 14 days late. PO-26-0561 7,80,000. PO-26-0590 6,50,000. 1.5% per full week."
    answer = ("| PO-26-0503 | 2 × 1.5 % = **3.0 %** | 9,20,000 × 0.03 = **27,600** |\n"
              "| PO-26-0561 | 1 × 1.5 % = **1.5 %** | 7,80,000 × 0.015 = **11,700** |\n"
              "| PO-26-0590 | 1 × 1.5 % = **1.5 %** | 6,50,000 × 0.015 = **9,750** |\n"
              "- Sum of penalties: 27,600 + 11,700 + 9,750 = **49,050 INR**")
    assert unverified_numbers(answer, source) == []
    assert "99,999" in unverified_numbers("27,600 + 11,700 = **99,999**", source)


def test_the_introduction_comes_with_its_text():
    from oasis_engine.chat import ChatDeps, ChatAgent
    from oasis_engine.search import SectionIndex
    from test_what_is_product import _library
    docs = _library()
    deps = ChatDeps(docs=docs, index=SectionIndex(docs), allowed=None)
    prompt = asyncio.run(ChatAgent._with_presearch("What is Frontier application", deps, introduction=(1, "0001")))
    assert "UI based application designed to tackle CNN" in prompt


@pytest.mark.parametrize("question, declined", [
    ("Write a python script to rename all files in a folder", True), ("Book me a train ticket to Pune", True),
    ("generate a SQL query to list employees", True), ("How do I switch Frontier to Script mode?", False),
    ("What is the travel policy for booking flights?", False), ("How do I export the model as Python code?", False),
    ("Who approves hotel bookings over INR 30,000?", False)])
def test_code_and_bookings_are_declined_whatever_the_model(question, declined):
    from oasis_engine.pipeline import fixed_reply
    reply = fixed_reply(question)
    assert bool(reply) == declined and (not reply or reply.startswith("Sorry, I can only help"))


def test_sources_at_the_end_of_the_last_line_are_read_as_sources():
    out = parse_answer("(1) Caused by the 2.9.0 rollout. (2) Agents should collect WAN logs. SOURCES: A0, A3, B5", DraftAnswer)
    assert out.answer == "(1) Caused by the 2.9.0 rollout. (2) Agents should collect WAN logs." and out.section_ids == ["A0", "A3", "B5"]
    assert parse_answer("Check the sources: then press START.", DraftAnswer).answer == "Check the sources: then press START."


def test_a_how_or_why_question_is_not_answered_with_a_table_cell():
    assert p._ASKS_HOW_OR_WHY.search("What caused the ticket spike in Q3 and how should agents fix it?")
    assert not p._ASKS_HOW_OR_WHY.search("How many tickets were resolved in Q3?")
    assert not p._ASKS_HOW_OR_WHY.search("What is the price of part HX9-4471?")


def test_a_question_in_another_script_is_searched_in_english():
    from oasis_engine.chat import ChatAgent, ChatDeps
    from oasis_engine.search import SectionIndex
    from test_what_is_product import _library
    docs = _library()
    deps = ChatDeps(docs=docs, index=SectionIndex(docs), allowed=None)
    prompt = asyncio.run(ChatAgent._with_presearch("मुझे कितनी बीमारी की छुट्टी मिलती है?", deps))
    assert "The question is in Hindi" in prompt and "search them with English words" in prompt


def test_a_bare_figure_from_what_was_read_is_drawn_from_it():
    from oasis_engine.chat import drawn_from
    read = "Casual / Sick Leave (CL/SL) | 12 days | 1 day/month"
    assert drawn_from("12 days", read) and not drawn_from("42 days", read)


def test_a_count_worked_out_in_a_written_calculation_does_not_break_the_check():
    from oasis_engine.numbers import unverified_numbers
    source = "PO-26-0503 9,20,000 14 days late. 1.5% per full week."
    answer = "1.5 % × 2 = 3.0 % → 9,20,000 × 3.0 % = **27,600**"
    assert unverified_numbers(answer, source) == []
    assert unverified_numbers("3 × 4 = 12", source) == ["3", "4", "12"]


def test_references_written_with_a_dash_become_sources():
    from oasis_engine.chat import normalize_refs
    assert normalize_refs("tool [S5 – About].") == "tool [S5]."
    assert normalize_refs("wires (S4 - Wiring)") == "wires [S4]"
    assert normalize_refs("(see the 5 – 10 range)") == "(see the 5 – 10 range)"


def test_what_the_documents_lack_is_not_general_knowledge_and_a_closing_decline_goes():
    from oasis_engine.chat import _NOT_COVERED, without_trailing_decline
    answer = ("The files are a user manual and an HR policy, none of which list executive leadership.\n\n"
              "Sorry, I can only help with questions about your documents and work topics. I can assist with leave "
              "policies, project management tools, or the SEM AI software if those are relevant to your needs.")
    assert _NOT_COVERED.search(answer)
    assert without_trailing_decline(answer) == "The files are a user manual and an HR policy, none of which list executive leadership."


def test_a_draft_signed_twice_keeps_one_signature():
    from oasis_engine.chat import without_stray_signature
    assert without_stray_signature("Hi,\n\nPlease approve.\n\nThanks,\n\n[Your Name]\n\n*[Your Name]*") == \
        "Hi,\n\nPlease approve.\n\nThanks,\n\n[Your Name]"


def test_the_parts_of_a_hyphenated_word_are_stemmed_like_words():
    from oasis_engine.search import tokenize
    assert "deliver" in tokenize("late-delivery penalty") and "deliver" in tokenize("Late delivery penalty")
    assert {"e-217", "e", "217"} <= set(tokenize("E-217"))


def test_a_figure_that_does_not_check_out_is_not_called_missing_from_the_documents(tmp_path):
    (tmp_path / "agreement.md").write_text(
        "# Supplier agreement\n\n## Late delivery penalty\n\nArvind Metals pays 1.5% of the order value for each "
        "full week of late delivery.\n\n## Delivery record\n\n| PO | Order value | Days late |\n|---|---|---|\n"
        "| PO-1 | 9,20,000 | 14 |\n", encoding="utf-8")
    docs = load_markdown(tmp_path)
    ids = [f"A{int(n)}" for n, s in docs[0].sections.items() if s.depth > 0]
    script = ScriptedModel(select=[", ".join(ids)], answer=["13500 INR\nSOURCES: " + ids[0]])
    r = asyncio.run(Pipeline(docs, script.model, output_mode="text", fast_path=False).ask(
        "How much late-delivery penalty does Arvind Metals owe?"))
    assert not r.found and r.answer == p.NOT_CHECKED
    script = ScriptedModel(select=[ids[0]], answer=["$12 per user per month\nSOURCES: " + ids[0]])
    r = asyncio.run(Pipeline(docs, script.model, output_mode="text", fast_path=False).ask(
        "How much does a user licence cost per month?"))
    assert not r.found and r.answer == p.NOT_FOUND


def test_a_code_named_by_a_section_brings_its_lines_from_another_section(tmp_path):
    # "What does alarm C-2087 mean and what does the replacement part cost?": the alarm names part
    # HX9-4471, whose price is a row of the parts list (Nemotron never opened it, 27 Oct 2026)
    from oasis_engine.chat import ChatAgent, ChatDeps, room_for
    from oasis_engine.search import SectionIndex
    (tmp_path / "manual.md").write_text("# HX-900 manual\n\n## Alarms\n\nAlarm C-2087 (way cover jam): replace the "
                                        "cover wiper strip, part HX9-4471, if it is torn.\n", encoding="utf-8")
    (tmp_path / "parts.md").write_text("# Spare parts\n\n## Parts list\n\n| Part | Name | Price |\n|---|---|---|\n"
                                       "| HX9-4470 | way cover | 9,800 |\n| HX9-4471 | way cover wiper strip | 2,350 |\n",
                                       encoding="utf-8")
    docs = load_markdown(tmp_path)
    deps = ChatDeps(docs=docs, index=SectionIndex(docs), allowed=None, room=room_for(32768, "remote"))
    prompt = asyncio.run(ChatAgent._with_presearch("What does alarm C-2087 mean?", deps))
    assert "naming HX9-4471" in prompt and "2,350" in prompt and "9,800" not in prompt.split("naming HX9-4471")[1]


def test_drafting_requests_are_told_to_write_the_text():
    from oasis_engine.chat import _DRAFT_REQUEST
    assert _DRAFT_REQUEST.search("Write a reply to a customer whose R7 shows error E-217")
    assert _DRAFT_REQUEST.search("draft an email to my manager asking for leave")
    assert not _DRAFT_REQUEST.search("How do I write a test case?")


def test_the_same_step_under_two_titles_is_offered_in_all_its_parts(tmp_path):
    from oasis_engine.search import SectionIndex
    from oasis_engine.vague import vague_clarification
    body = "\n\n".join(f"## {part}\n\n### Creating a {part} solution\n\n#### {title}\n\nClick Add Folder and pick the "
                       f"images for {part}.\n" for part, title in [
                           ("Single X Classification", "Adding Data"), ("Multi X Classification", "Adding Data"),
                           ("Object Detection", "Adding data to dataset"), ("Segmentation", "Adding data to dataset")])
    (tmp_path / "frontier.md").write_text("# Frontier manual\n\n" + body, encoding="utf-8")
    docs = load_markdown(tmp_path)
    c = vague_clarification("How do I add data?", SectionIndex(docs), None)
    assert c is not None and [o.label for o in c.options][:2] == ["Single X Classification", "Multi X Classification"]
    assert {"Object Detection", "Segmentation"} <= {o.label for o in c.options}


def test_only_general_knowledge_is_shown_in_the_general_knowledge_box():
    from oasis_engine.chat import mark_general, without_other_quote_boxes
    answer = ("Steps 1-4 [1].\n\n> **What the UI shows** - The toolbar has an **Add Folder** icon.\n\n"
              "**General knowledge:** (brief)**\nResNet is a network with skip connections.")
    text, general = mark_general(without_other_quote_boxes(answer))
    assert "\n\n**What the UI shows** - The toolbar" in text and general
    assert "> **General knowledge:** ResNet is a network" in text and "(brief)" not in text


def test_a_line_repeating_the_last_answer_on_another_topic_goes():
    from oasis_engine.chat import without_last_answer_lines
    last = ("How many days of sick leave do employees get?",
            "Employees get **12 days of Casual / Sick Leave (CL/SL)** per year. [1]")
    answer = ("Frontier lists ResNet 14 and ResNet 18. [1]\n\n**Answer**\n"
              "Employees get **12 days of Casual / Sick Leave (CL/SL)** per year. [3]")
    assert without_last_answer_lines(answer, "What is ResNet and does Frontier support it?", last) == \
        "Frontier lists ResNet 14 and ResNet 18. [1]"
    same = "Employees get 12 days of Casual / Sick Leave (CL/SL) per year, accrued monthly."
    assert without_last_answer_lines(same, "And how is sick leave accrued?", last) == same


def test_a_fact_read_out_of_a_table_is_stated_not_worked_out():
    from oasis_engine.support import stated_as_is
    source = "Leave Type | Annual Entitlement | Carry Forward\nCasual / Sick Leave (CL/SL) | 12 days | Not permitted"
    assert stated_as_is("Employees get 12 days of Casual / Sick Leave (CL/SL) per year, as the entitlement table "
                        "in the leave policy lists.", source, "How many days of sick leave do employees get?")
    assert not stated_as_is("No: up to 12 days, so 15 days is not allowed.", source, "Can I take 15 days of sick leave?")


def test_a_reworded_line_of_the_last_answer_on_its_topic_goes():
    from oasis_engine.chat import without_last_answer_lines
    last = ("How many days of sick leave do employees get?",
            "Employees get **12 days of Casual / Sick Leave (CL/SL)** per year, accruing 1 day per month. [1]")
    answer = ("ResNet is listed under Models Supported. [1]\n\nEmployees get **12 days of casual/sick leave per year** "
              "(accruing 1 day per month) – as stated in [4].")
    assert without_last_answer_lines(answer, "What is ResNet and does Frontier support it?", last) == \
        "ResNet is listed under Models Supported. [1]"


def test_a_general_paragraph_with_the_documents_figures_keeps_the_label_for_its_general_part():
    from oasis_engine.chat import ChatDeps, mark_general, unlabel_document_paragraphs
    from oasis_engine.search import SectionIndex
    deps = ChatDeps(docs=[], index=SectionIndex([]), allowed=None)
    deps.worked.append("Downtime hours by cause: Line C | 196 | 97 | 22 | 60 | 31 | Line A total 215 | Line B total 271")
    deps.computed += ["406"]
    answer = ("> **General knowledge:** Total downtime is the sum of all time a line is not running. Line C's row "
              "totals are 196 + 97 + 22 + 60 + 31 = 406 hours, higher than Line A (215) and Line B (271).")
    text, _ = mark_general(unlabel_document_paragraphs(answer, deps))
    assert text.startswith("Line C's row totals are 196") and "> **General knowledge:** Total downtime is the sum" in text
