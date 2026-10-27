"""The plain-text reply format: what models actually write, read without a structured reply."""
import asyncio

from conftest import ScriptedModel
from oasis_engine.loaders import load_pageindex
from oasis_engine.pipeline import DraftAnswer, FollowupChoice, Pipeline, Route, SectionChoice, SummaryDraft
from oasis_engine.replies import parse_answer, parse_choice, parse_followup, parse_route, parse_summary


def test_section_ids_in_every_shape_models_write_them():
    for reply, ids in [
        ("A6, A12", ["A6", "A12"]),
        ("SECTIONS: [A6] [A12]", ["A6", "A12"]),
        ("<think>The table of contents has A3 for errors, but A6 is about torque.</think>\nA6", ["A6"]),
        ("The best match is A6 (Torque settings), then A12.", ["A6", "A12"]),
        ("- A6\n- A12\n", ["A6", "A12"]),
        ("```\nA6, B-INTRO\n```", ["A6", "B-INTRO"]),
        ("NONE", []),
        ('[{"name": "final_result", "parameters": {"section_ids": ["A6"]}}]', ["A6"]),  # Nemotron, 22 Oct
        ('{"section_ids": ["A12"]}', ["A12"]),
    ]:
        assert parse_choice(reply, SectionChoice).section_ids == ids, reply


def test_an_answer_with_its_sources_line():
    a = parse_answer("Set the torque to 12 Nm.\nThen check it twice.\n\nSOURCES: A6, A12", DraftAnswer)
    assert a.found and a.answer == "Set the torque to 12 Nm.\nThen check it twice." and a.section_ids == ["A6", "A12"]
    a = parse_answer("<think>\nThe excerpt A6 says 12 Nm.\n</think>\nSet 12 Nm.\nSources: [A6]", DraftAnswer)
    assert a.answer == "Set 12 Nm." and a.section_ids == ["A6"]
    a = parse_answer("Set 12 Nm.", DraftAnswer)  # no sources line: cited by the pipeline from what it read
    assert a.found and a.section_ids == []


def test_not_found_and_worked_out():
    a = parse_answer("NOT FOUND: The documents do not cover the weather.", DraftAnswer)
    assert not a.found and a.answer == "The documents do not cover the weather."
    a = parse_answer("WORKED OUT: Yes, 45 days is within the 60-day limit.\nSOURCES: A4", DraftAnswer)
    assert a.found and a.worked_out and a.answer.startswith("Yes, 45 days") and a.section_ids == ["A4"]


def test_a_half_written_sources_line_is_not_shown_while_streaming():
    assert parse_answer("Set 12 Nm.\nSOUR", DraftAnswer).answer == "Set 12 Nm."
    assert parse_answer("Set 12 Nm.\nSo", DraftAnswer).answer == "Set 12 Nm."


def test_followup_route_and_summary():
    f = parse_followup("QUESTION: What torque do M10 bolts need?\nSECTIONS: A6", FollowupChoice)
    assert f.standalone_question == "What torque do M10 bolts need?" and f.section_ids == ["A6"]
    f = parse_followup("What torque do M10 bolts need?\nA6, A7", FollowupChoice)
    assert f.standalone_question == "What torque do M10 bolts need?" and f.section_ids == ["A6", "A7"]
    assert parse_route("PICTURES wiring diagram", Route).model_dump() == {"kind": "pictures", "topic": "wiring diagram"}
    assert parse_route("Summary: the HX-500 manual", Route).model_dump() == {"kind": "summary", "topic": "the HX-500 manual"}
    assert parse_route("I think this is a normal question.", Route).kind == "question"
    s = parse_summary("Covers the HX-500 press.\n- Installation\n- Start-up\n2. Error codes", SummaryDraft)
    assert s.overview == "Covers the HX-500 press." and s.points == ["Installation", "Start-up", "Error codes"]


def test_a_question_answered_end_to_end_by_a_model_that_writes_only_text(pageindex_kb):
    docs = load_pageindex(pageindex_kb)
    script = ScriptedModel(select=["<think>Start-up is where pressure is set.</think>\nA2"],
                           answer=["Set the pressure to 180 bar.\nSOURCES: A2"])
    pipe = Pipeline(docs, script.model, output_mode="text", fast_path=False)
    r = asyncio.run(pipe.ask("What pressure is set at start-up?"))
    assert r.found and "180 bar" in r.answer and [c.title for c in r.citations] == ["Start-up"]
    # each step was told how to lay out its reply
    assert "SOURCES:" in str(pipe.answerer._instructions) and "NONE" in str(pipe.selector._instructions)


def test_worked_out_on_a_later_line_is_a_label_not_text():
    # the 4B put it last, with its reasoning after it (final check, 27 Oct 2026)
    a = parse_answer("1. Click More.\n2. Click Auto Set.\n\nWORKED OUT: Under More, Auto Set splits the data.\n"
                     "SOURCES: A2", DraftAnswer)
    assert a.worked_out and "WORKED OUT" not in a.answer and a.answer.endswith("Auto Set splits the data.")
    assert not parse_answer("The torque was worked out by the engineer.", DraftAnswer).worked_out
