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
    ("Who approves hotel bookings over INR 30,000?", False),
    ("Can I book a flight less than 7 days before travel without manager approval?", False),
    ("Do I need approval to book a hotel room?", False), ("Can you book a cab to the airport for me?", True),
    ("Please reserve a hotel room in Pune", True), ("book a flight to Delhi tomorrow", True)])
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


THERMOSTAT = """# X2 guide

## 4 Using Your Thermostat
Turn the outer ring to change the target temperature. Press the display to open the menu; the three buttons
at the bottom open the mode, fan and schedule settings.

## 4.2 Schedules
With Adaptive Recovery turned on, X2 learns how long your home takes to heat and starts early so that the
target temperature is reached at the scheduled time, not after it.

## 5 Operation
Run the spindle warm-up program before the first job of the day.
"""


def _ask_docs(docs, q, answer, sections):
    order = list(dict.fromkeys(di for di, _ in sections))  # excerpt letters go by the order documents are shown
    ids = [f"{chr(65 + order.index(di))}{int(next(n for n, s in docs[di].sections.items() if s.title == t))}"
           for di, t in sections]
    script = ScriptedModel(select=[{"standalone_question": q, "section_ids": ids}],
                           answer=[{"found": True, "answer": answer, "section_ids": []}])
    return asyncio.run(Pipeline(docs, script.model, output_mode="prompted", fast_path=False).ask(q))


def test_a_right_yes_is_not_replaced_by_a_rule_sharing_one_word_and_a_name():
    from oasis_engine.loaders import parse_markdown
    docs = [parse_markdown(THERMOSTAT, "x2", "x2_guide.md")]
    q = "Can I change the temperature and access settings using the X2's ring and display?"
    r = _ask_docs(docs, q, "Yes. Turn the outer ring to change the target temperature and press the display to "
                           "open the menu.", [(0, "4 Using Your Thermostat"), (0, "4.2 Schedules")])
    assert r.answer.startswith("Yes. Turn the outer ring") and "Adaptive Recovery" not in r.answer


def test_a_rule_on_part_of_the_question_does_not_stand_for_a_not_found():
    from oasis_engine.loaders import parse_markdown
    docs = [parse_markdown(THERMOSTAT, "x2", "x2_guide.md")]
    q = "Do I need training to run programs on the machine?"
    r = _ask_docs(docs, q, "The manual does not state whether training is required.", [(0, "5 Operation")])
    assert "warm-up" not in r.answer


WARRANTY = """# Warranty

## 2 Warranty Period
| Product | Warranty period |
| --- | --- |
| R7 and R9 routers | 3 years |
| M3 mesh nodes | 2 years |
"""
R7_GUIDE = """# R7 router troubleshooting guide

## 1 Before you start
Collect the router serial number and firmware version.
"""


def test_an_answer_about_a_product_from_another_document_naming_it_is_kept():
    from oasis_engine.loaders import parse_markdown
    docs = [parse_markdown(R7_GUIDE, "r7", "r7_router_troubleshooting_guide.md"),
            parse_markdown(WARRANTY, "w", "skylink_limited_warranty.md")]
    q = "How does the warranty period for R7 routers compare to that of M3 mesh nodes?"
    r = _ask_docs(docs, q, "R7 routers have a 3-year warranty, while M3 mesh nodes have a 2-year warranty: "
                           "1 year longer.", [(1, "2 Warranty Period")])
    assert r.found and r.answer.startswith("R7 routers have a 3-year warranty")


@pytest.mark.parametrize("line, sentence", [
    ("R7 and R9 routers minus M3 mesh nodes in Warranty period: 1 year (50% of M3 mesh nodes)",
     "The warranty period for R7 and R9 routers is 1 year longer than for M3 mesh nodes (50% longer)."),
    ("March minus January in Revenue: -3,000 (-2.5% of January)",
     "The revenue for March is 3,000 lower than for January (2.5% lower)."),
    ("Highest Revenue: March (291,400)", "March has the highest revenue (291,400)."),
    ("Total Downtime hours over the 5 rows: 406", "The total downtime hours over the 5 rows is 406."),
    ("Increase from January to March in Revenue: 12,400 (4.4%)", "The revenue rose by 12,400 from January to March (4.4%)."),
    ("Highest column total: Line B (271)", "Line B has the highest total (271)."),
    ("Leave Type: Privilege Leave (PL) · Carry Forward: Up to 45 days",
     "For Privilege Leave (PL), the carry forward is up to 45 days."),
    ("Part: HX9-4194 · Supplier: Bosch Rexroth", "For HX9-4194, the supplier is Bosch Rexroth."),
    ("Contractors may not drive Helix forklifts.", "Contractors may not drive Helix forklifts."),
])
def test_facts_read_out_in_code_are_shown_as_sentences(line, sentence):
    from oasis_engine.wording import as_sentence
    assert as_sentence(line) == sentence


def test_a_compared_difference_keeps_the_cells_unit():
    from oasis_engine.doctools import _shared_unit
    assert _shared_unit(["3 years", "2 years"], 1) == " year"
    assert _shared_unit(["12 days", "45 days"], -33) == " days"
    assert _shared_unit(["291,400", "120,000"], 171_400) == ""


def test_notes_under_an_answer_become_badges_and_other_italics_stay():
    from oasis_engine.chat import UNSOURCED_NOTE
    from oasis_engine.wording import lift_notes
    text, notes = lift_notes("Open the menu.\n\n" + UNSOURCED_NOTE + "\n\n*(The answer was cut off.)*")
    assert text == "Open the menu." and [n["kind"] for n in notes] == ["unsourced", "cut_off"]
    assert notes[1]["text"] == "The answer was cut off."
    text, notes = lift_notes("A.\n\n*Note: the document includes only part of this table. It says: mean 169.6 ms.*")
    assert notes == [{"kind": "partial_table", "text": "The document includes only part of this table. It says: mean 169.6 ms."}]
    assert lift_notes("Answer.\n\n*Hope this helps!*") == ("Answer.\n\n*Hope this helps!*", [])


def test_formulas_written_in_latex_are_shown_as_plain_text():
    from oasis_engine.wording import plain_math
    assert plain_math(r"OEE = \frac{Output × Target cycle time}{Available time}") == \
        "OEE = (Output × Target cycle time) / (Available time)"
    assert plain_math(r"Downtime = $288 \times (1 - 0.802) \approx 57$ h") == "Downtime = 288 × (1 - 0.802) ≈ 57 h"
    assert plain_math(r"**Total:** \(27{,}600 + 11{,}700 = 39{,}300\)") == "**Total:** 27,600 + 11,700 = 39,300"
    for kept in ("It costs $5 and $10.", r"Use `a\times b` in code", r"Path C:\new\folder stays"):
        assert plain_math(kept) == kept


def test_a_summary_point_keeps_the_bold_it_starts_with():
    draft = p.SummaryDraft(overview="The Q3 review.", points=["**12,480 tickets resolved**, CSAT **91.3%**",
                                                               "- **Backlog** grew 19%", "* Top reasons: no internet"])
    assert draft.text() == ("The Q3 review.\n\n- **12,480 tickets resolved**, CSAT **91.3%**\n- **Backlog** grew 19%\n"
                            "- Top reasons: no internet")


def test_a_figure_flagged_in_an_earlier_answer_does_not_check_out_a_later_one():
    from oasis_engine.chat import without_figures
    earlier = "| April | C | 52.3 | 378 |\nDowntime ≈ 378 h; OEE 52.3 %."
    assert "378" not in without_figures(earlier, {"378", "S5"}) and "52.3" in without_figures(earlier, {"378"})
    assert without_figures("3,780 parts", {"378"}) == "3,780 parts"


def test_an_answer_does_not_repeat_the_question_or_the_label_of_worked_out_tables():
    from oasis_engine.chat import without_echoes
    q = "Which line improved the most over the quarter?"
    answer = (f"**{q}**\n\nLine B improved the most [1].\n\n*Worked out from the tables (exact):* [1] OEE change "
              "per line, Jan → Jun.")
    assert without_echoes(answer, q) == "Line B improved the most [1]."
    assert without_echoes("Line B.", "Line B.") == "Line B."  # an answer that is only that line stays


@pytest.mark.parametrize("said, shown", [
    ("NO: The policy states a maximum of 45 days.", "No. The policy states a maximum of 45 days."),
    ("**YES:** you can.", "Yes. You can."),
    ("No - only 45 days.", "No. Only 45 days."),
    ("Nothing is said about it.", "Nothing is said about it."),
    ("No. Contractors may not drive.", "No. Contractors may not drive."),
    ("YES. Pin 7 is RLY_OB.", "Yes. Pin 7 is RLY_OB."),
    ("NO.5 pump is fitted.", "NO.5 pump is fitted."),
])
def test_a_shouted_yes_or_no_reads_as_a_sentence(said, shown):
    from oasis_engine.wording import tidy_verdict
    assert tidy_verdict(said) == shown


SUPPLY = """# Supply agreement

## Part 3: Delivery record Q2 2026
Orders delivered by Arvind Metals in Q2 2026: PO-1 on time, PO-2 late, PO-3 on time, PO-4 late.
"""
TARGETS = """# Quality targets

## 2.1 Delivery
The quarterly on-time delivery target for every supplier is 97%.
"""


def test_narrowing_sources_to_the_answers_figures_does_not_drop_the_one_naming_its_subject():
    from oasis_engine.loaders import parse_markdown
    docs = [parse_markdown(SUPPLY, "s", "supply_agreement.md"), parse_markdown(TARGETS, "t", "quality_targets.md")]
    r = _ask_docs(docs, "Did Arvind Metals meet its on-time delivery target in Q2 2026?",
                  "No. Arvind Metals delivered 2 of 4 orders on time, below the 97% target.",
                  [(0, "Part 3: Delivery record Q2 2026"), (1, "2.1 Delivery")])
    assert r.found and r.answer.startswith("No. Arvind Metals")
    assert {c.title for c in r.citations} == {"Part 3: Delivery record Q2 2026", "2.1 Delivery"}


def test_a_rate_limit_is_waited_out_longer_than_other_failures(monkeypatch):
    from pydantic_ai import ModelHTTPError
    from oasis_engine import llm
    waits = []

    async def sleep(s):
        waits.append(s)
    monkeypatch.setattr(llm.asyncio, "sleep", sleep)
    model = llm.RetryingModel(ScriptedModel(select=[], answer=[]).model, delay_s=2.0)
    asyncio.run(model._pause(1, ModelHTTPError(429, "m", {"title": "Too Many Requests"})))
    asyncio.run(model._pause(1, ModelHTTPError(502, "m", {"title": "Bad Gateway"})))
    assert waits == [5.0, 2.0]


def test_an_acronym_question_is_about_the_acronym_not_the_word_stand():
    from oasis_engine.loaders import parse_markdown
    from oasis_engine.search import SectionIndex
    from oasis_engine.support import made_up
    docs = [parse_markdown("# Leave policy\n\n## Annexure\n| Abbreviation | Meaning |\n|---|---|\n| PL | Privilege Leave |\n"
                           "| HOD | Head of Department |\n", "l", "leave_policy.md")]
    index = SectionIndex(docs)
    annex = docs[0].section_text(next(n for n, s in docs[0].sections.items() if s.title == "Annexure"), 2000)
    assert not made_up("What does PL stand for?", "PL stands for **Privilege Leave**.", annex, index, annex)
    assert not made_up("What is the full form of HOD?", "HOD is Head of Department.", annex, index, annex)
    assert made_up("How do I reset my password?", "Open Settings and click Reset password.", annex, index)


def test_most_in_a_phrase_that_ranks_nothing_does_not_rank_a_table():
    from oasis_engine.loaders import parse_markdown
    doc = parse_markdown("# Handbook\n\n## Benefits\n| Benefit | Details |\n|---|---|\n| Learning budget | 1,500 |\n"
                         "| Wellness | 300 |\n\n## Organization\nUp-to-date team information, including managers and office "
                         "locations, is always in the People directory in Workday.\n", "h", "handbook.md")
    q = "What is the most up-to-date way to find manager and office location details?"
    r = _ask_docs([doc], q, "The People directory in Workday has up-to-date managers and office locations.",
                  [(0, "Organization"), (0, "Benefits")])
    assert "Learning budget" not in r.answer and "Workday" in r.answer


def test_a_number_the_document_writes_as_a_word_checks_out():
    from oasis_engine.numbers import unverified_numbers
    src = "40 employees received the email. Six people clicked the link; one entered their password."
    assert unverified_numbers("6 people clicked and 1 entered it.", src) == []
    assert unverified_numbers("7 people clicked.", src) == ["7"]


def test_table_cells_with_dates_and_indian_grouping_are_read_right():
    from oasis_engine.doctools import number
    from oasis_engine.wording import as_sentence
    assert number("27 May 2026") is None and number("2026-05-27") is None and number("May 12, 2026") is None
    assert number("8,40,000") == 840000 and number("Rs. 3,60,000") == 360000 and number("1,870") == 1870
    assert as_sentence("Highest Order value (INR): PO-26-0503 (920,000)") == \
        "PO-26-0503 has the highest order value (920,000 INR)."
    assert as_sentence("Lowest OEE (%): Line C (58)") == "Line C has the lowest OEE (58%)."


def test_when_the_model_picks_no_section_the_searchs_best_few_are_read(tmp_path):
    # "How long do project invitations remain valid?": Nemotron picked no section and only the search's top
    # one (Reports, on projects) was read, so "Invitations expire after 7 days" was never seen (28 Oct 2026)
    (tmp_path / "manual.md").write_text(
        "# Reports\n\nProject reports show how long each project task took, project by project, and how long "
        "project sprints remain open.\n\n# Adding Team Members\n\nInvite people by email and pick a role. "
        "Invitations expire after 7 days.\n\n# Billing\n\nInvoices are sent monthly.\n",
        encoding="utf-8")
    docs = load_markdown(tmp_path)
    script = ScriptedModel(select=["NONE"], answer=["Invitations expire after 7 days.\nSOURCES: A2"])
    asyncio.run(Pipeline(docs, script.model, output_mode="text", fast_path=False).ask(
        "How long do project invitations remain valid?"))
    shown = next(prompt for step, prompt in script.prompts if step == "answer")
    assert "Invitations expire after 7 days" in shown
