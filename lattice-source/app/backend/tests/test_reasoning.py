"""Reasoning over documents: reading tools, table queries, facts, the plan for multi-part questions,
the evidence ledger, the step budget and the follow-up state (see docs/agent-harness-proposal.md)."""
import asyncio

from conftest import ChatScript, ScriptedModel
from oasis_engine import doctools as T
from oasis_engine.chat import ChatAgent
from oasis_engine.loaders import parse_markdown
from oasis_engine.pipeline import Pipeline
from oasis_engine.plan import question_parts
from oasis_engine.search import SectionIndex

REPORT = """# report.xlsx

## Sheet: Q1 Summary

| Month | Revenue | Expenses | Margin % |
| --- | --- | --- | --- |
| January | 248,500 | 181,200 | 27.1 |
| February | 263,900 | 186,750 | 29.2 |
| March | 291,400 | 198,300 | 31.9 |
| Q1 Total | 803,800 | 566,250 | 29.6 |
"""

POLICY = """# policy.docx

## Leave
Leave rules for all staff.

### Privilege leave
Employees get 18 days a year. Up to 45 days can be carried forward. Leave must be applied for at
least 7 days in advance.

### Sick leave
12 days a year. More than 2 consecutive days requires a medical certificate.

## Software
Python packages used by the API: fastapi, sqlalchemy and httpx. New software needs IT approval.
"""


def docs():
    return [parse_markdown(REPORT, "r", "report.xlsx"), parse_markdown(POLICY, "p", "policy.docx")]


def node(doc, title):
    return next(n for n, s in doc.sections.items() if s.title == title)


# ── the tools ──

def test_read_gives_a_sections_own_text_and_lists_its_subsections():
    policy = docs()[1]
    view = T.read_view(policy, node(policy, "Leave"), ref_for=lambda n: f"S{n}")
    own = view.split("Subsections")[0]
    assert "Leave rules for all staff." in own and "45 days" not in own  # not the whole subtree
    assert "Privilege leave" in view and "Sick leave" in view and "[S" in view
    around = T.read_view(policy, node(policy, "Leave"), around="medical certificate")
    assert "medical certificate" in around


def test_query_table_filters_sorts_and_adds_up_in_code():
    report = docs()[0]
    nid = node(report, "Sheet: Q1 Summary")
    text, _ = T.query_table(report, nid, sort="Margin %", descending=True, columns=["Month", "Margin %"],
                            where=[["Month", "!=", "Q1 Total"]])
    rows = text.splitlines()
    assert rows[2] == "March | 31.9" and rows[-1] == "January | 27.1"
    text, computed = T.query_table(report, nid, aggregate={"sum": "Revenue"}, where=[["Month", "!=", "Q1 Total"]])
    assert "sum(Revenue) over 3 rows = 803800" in text and computed == ["803800"]
    text, _ = T.query_table(report, nid, where=[["Revenue", ">", "260,000"], ["Month", "~", "ar"]])
    assert "February" in text and "March" in text and "January" not in text
    text, _ = T.query_table(report, nid, where=[["Colour", "=", "red"]])
    assert "no column 'Colour'" in text


def test_numbers_in_cells():
    assert T.number("1,870") == 1870 and T.number("+31.0%") == 31.0 and T.number("(2)") == -2
    assert T.number("18-27 days") == 18 and T.number("n/a") is None


def test_find_text_and_find_facts():
    ix = SectionIndex(docs())
    [(_, nid, line)] = T.find_text(ix, "45 days")
    assert ix.docs[1].sections[nid].title == "Privilege leave" and "45 days" in line
    assert T.find_text(ix, "5 days") == []  # whole words: not "45 days"
    facts = T.find_facts(ix, "privilege leave carry forward", kind="limit")
    assert any("Up to 45 days" in f[2] for f in facts)


def test_doc_map_marks_tables():
    report = docs()[0]
    assert "▦ table: Month | Revenue | Expenses | Margin % (4 rows)" in T.doc_map(report, lambda n: f"S{n}")


def test_quotes_are_checked_against_the_section():
    policy = docs()[1]
    nid = node(policy, "Privilege leave")
    assert T.verify_quote(policy, nid, "Up to 45 days can be carried forward")[0]
    assert T.verify_quote(policy, nid, "up to 45 days can be carried  forward.")[0]  # spacing, case
    ok, nearest = T.verify_quote(policy, nid, "Up to 60 days can be carried forward to next year")
    assert not ok and "45 days" in nearest


def test_rankings_and_differences_are_worked_out_from_the_table():
    report = docs()[0]
    nid = node(report, "Sheet: Q1 Summary")
    lines, computed = T.table_highlights(report, nid, "Which month had the best margin?")
    assert "Highest Margin %: March (31.9)" in lines and not any(l.startswith("Total") for l in lines)  # % do not add up
    lines, computed = T.table_highlights(report, nid, "Compare March vs January revenue")
    assert "March minus January in Revenue: 42,900 (17.26% of January)" in lines and "42900" in computed
    assert T.table_highlights(report, nid, "What was the revenue in March?") == ([], [])


def test_a_long_section_is_excerpted_where_the_question_is():
    long = "# m.docx\n\n## Big\n" + "Filler text about nothing. " * 600 + "The torque for M8 is 25 Nm. " + \
           "More filler. " * 200
    doc = parse_markdown(long, "m", "m.docx")
    text = T.excerpt(doc, node(doc, "Big"), "torque for M8", 3000)
    assert "25 Nm" in text and len(text) < 3500


def test_multi_part_questions_are_split():
    assert question_parts("Which Python packages does the API use and which leave needs HR approval?") == [
        "Which Python packages does the API use", "which leave needs HR approval?"]
    assert question_parts("What is the cost and benefit of X?") == []
    assert question_parts("What is E17 and how do I fix it?") == []


# ── Chat ──

def chat(steps, question, final=None, **kw):
    ds = docs()
    script = ChatScript(steps, final=final)
    r = asyncio.run(ChatAgent(script.model).ask(question, ds, SectionIndex(ds), **kw))
    return r, script


def test_a_table_comparison_is_computed_and_its_figures_check_out():
    r, _ = chat([{"tool": "search_manuals", "args": {"query": "margin"}},
                 {"tool": "query_table", "args": {"ref": "S1", "sort": "Margin %", "descending": True,
                                                  "where": [["Month", "!=", "Q1 Total"]]}},
                 {"text": "March had the best margin, 31.9% [S1]."}], "Which month had the best margin?")
    assert r.found and "March" in r.answer and r.unverified_numbers == []


def test_checked_notes_become_the_evidence_and_failed_ones_do_not():
    r, script = chat([{"tool": "search_manuals", "args": {"query": "carry forward"}},
                      {"tool": "note", "args": {"claim": "45 days carry forward", "ref": "S1",
                                                "quote": "Up to 45 days can be carried forward"}},
                      {"tool": "note", "args": {"claim": "60 days", "ref": "S1", "quote": "60 days may be kept"}},
                      {"text": "Worked out from the documents: no - at most 45 days carry forward, so 50 cannot [S1]."}],
                     "Can I carry 50 days into next year?")
    assert r.found and r.worked_out and r.answer.startswith("no")
    assert [e.text for e in r.evidence] == ["Up to 45 days can be carried forward"]
    told = str(script.seen[-1])
    assert "Checked. Cite it as [S1]" in told and "Not found in S1" in told


def test_both_parts_of_a_question_are_searched():
    _, script = chat([{"text": "fastapi, sqlalchemy and httpx [S1]; leave needs approval [S2]."}],
                     "Which Python packages does the API use and which leave needs HR approval?")
    first = str(script.seen[0])
    assert "Software" in first and "Privilege leave" in first and "This question has 2 parts" in first


def test_asking_back_only_on_a_first_question():
    steps = [{"tool": "ask_user", "args": {"question": "Which limit?", "options": [
        "What is the carry-forward limit?", "What is the sick leave limit?"]}}, {"text": "Which limit?"}]
    r, _ = chat(steps, "What is the limit?")
    assert r.clarify and [o.question for o in r.clarify.options] == [
        "What is the carry-forward limit?", "What is the sick leave limit?"]
    # a follow-up: the tool is not offered, so the model's call fails and it answers instead
    r, script = chat([{"text": "45 days [S1]."}], "and the limit?",
                     context=[("How much leave carries forward?", "Up to 45 days [1].")])
    assert not r.clarify


def test_a_follow_up_keeps_the_earlier_sources():
    ds = docs()
    nid = node(ds[1], "Privilege leave")
    _, script = chat([{"text": "Yes [S1]."}], "Is it paid?", context=[("How many days?", "18 days [1].")],
                     earlier=[(1, nid)])
    told = str(script.seen[0])
    assert "Sections the earlier answers in this conversation came from:" in told
    assert "policy.docx > Leave > Privilege leave" in told.split("Sections the earlier answers")[1]


def test_a_model_on_this_computer_gets_passages_up_front():
    _, script = chat([{"text": "12 days [S1]."}], "How many sick days?", tier="local")
    first = str(script.seen[0])
    assert "medical certificate" in first  # the passage itself, not only a snippet


# ── Manuals ──

def test_manuals_gives_the_worked_out_ranking_to_the_model():
    ds = docs()
    script = ScriptedModel(select=[{"standalone_question": "Which month had the best margin?", "section_ids": ["A1"]}],
                           answer=[{"found": True, "answer": "March, at 31.9%.", "section_ids": ["A1"]}])
    r = asyncio.run(Pipeline(ds, script.model, output_mode="prompted").ask("Which month had the best margin?"))
    answer_prompt = script.prompts[-1][1]
    assert "WORKED OUT FROM THE TABLES" in answer_prompt and "Highest Margin %: March (31.9)" in answer_prompt
    assert r.found and r.unverified_numbers == []


def test_manuals_reads_a_section_for_each_part():
    ds = docs()
    q = "Which Python packages does the API use and which leave needs a medical certificate?"
    script = ScriptedModel(select=[{"standalone_question": q, "section_ids": []}],
                           answer=[{"found": True, "answer": "fastapi; sick leave over 2 days.", "section_ids": []}])
    asyncio.run(Pipeline(ds, script.model, output_mode="prompted").ask(q))
    answer_prompt = script.prompts[-1][1]
    assert "Software" in answer_prompt and "Sick leave" in answer_prompt and "This question has 2 parts" in answer_prompt


def test_a_bare_no_gets_the_rule_it_rests_on():
    ds = docs()
    script = ScriptedModel(select=[{"standalone_question": "Can I carry 50 days forward?", "section_ids": ["A2"]}],
                           answer=[{"found": False, "answer": "No", "section_ids": []}])
    r = asyncio.run(Pipeline(ds, script.model, output_mode="prompted").ask("Can I carry 50 days forward?"))
    assert r.found and r.answer.startswith("No. ") and "45 days" in r.answer


def test_a_multi_part_question_the_model_gives_up_on_is_answered_part_by_part():
    ds = docs()
    q = "Which Python packages does the API use and which leave needs a medical certificate?"
    script = ScriptedModel(select=[{"standalone_question": q, "section_ids": []}],
                           answer=[{"found": False, "answer": "The documents don't cover this.", "section_ids": []},
                                   {"found": True, "answer": "fastapi, sqlalchemy and httpx.", "section_ids": []},
                                   {"found": True, "answer": "Sick leave of more than 2 consecutive days.", "section_ids": []}])
    r = asyncio.run(Pipeline(ds, script.model, output_mode="prompted").ask(q))
    assert r.found and r.answer.startswith("1. Which Python packages does the API use: fastapi")
    assert "2. which leave needs a medical certificate: Sick leave" in r.answer
    assert {c.title for c in r.citations} >= {"Software", "Sick leave"}


# ── seen in the reasoning eval of 14 Oct 2026 (0.8B, Manuals) ──
def test_a_statement_before_the_question_is_not_a_part_of_its_own():
    assert question_parts("I am 30 years old. Do I get the paid medical checkup leave?") == []
    assert question_parts("I was sick for 3 days in a row. Do I need a medical certificate?") == []


def test_a_follow_up_that_swaps_the_subject_is_rewritten_in_code():
    from oasis_engine.plan import follow_up
    assert follow_up(["What was the revenue in January?"], "And in March?") == "What was the revenue in March?"
    both = follow_up(["What was the revenue in January?", "And in March?"], "What is the increase between the two?")
    assert "January and March" in both and "revenue" in both
    assert follow_up(["What is E17?"], "And how do I fix it?") is None


def test_the_increase_between_two_rows_is_worked_out():
    ds = docs()
    lines, _ = T.table_highlights(ds[0], node(ds[0], "Sheet: Q1 Summary"),
                                  "What is the increase in revenue between January and March?")
    assert "Increase from January to March in Revenue: 42,900 (17.26%)" in lines


def test_the_one_statement_that_answers_is_used_when_the_model_gives_up():
    ds = docs()

    def ask(question, section):
        script = ScriptedModel(select=[{"standalone_question": question, "section_ids": [section]}],
                               answer=[{"found": False, "answer": "The documents don't cover this.", "section_ids": []}])
        return asyncio.run(Pipeline(ds, script.model, output_mode="prompted", fast_path=False).ask(question))
    r = ask("By how much did revenue grow from January to February?", "A1")
    assert r.found and r.worked_out and "15,400" in r.answer
    r = ask("What was the revenue in April?", "A1")  # no section says April
    assert not r.found


def test_a_figure_from_the_wrong_column_is_replaced_by_the_cell_asked_for():
    ds = docs()
    q = "What was the revenue in March?"
    script = ScriptedModel(select=[{"standalone_question": q, "section_ids": ["A1"]}],
                           answer=[{"found": True, "answer": "198,300", "section_ids": ["A1"]}])
    r = asyncio.run(Pipeline(ds, script.model, output_mode="prompted", fast_path=False).ask(q))
    assert r.found and "291,400" in r.answer and "198,300" not in r.answer
    assert "Month: March · Revenue: 291,400" in script.prompts[-1][1]


def test_a_comparison_is_not_answered_by_one_row():
    ds = docs()
    q = "Which leave gives more days a year, privilege leave or sick leave?"
    script = ScriptedModel(select=[{"standalone_question": q, "section_ids": []}],
                           answer=[{"found": False, "answer": "The documents don't cover this.", "section_ids": []}])
    r = asyncio.run(Pipeline(ds, script.model, output_mode="prompted", fast_path=False).ask(q))
    assert not r.answer.startswith("From the documents")


def test_a_more_question_is_answered_with_the_highest_row():
    # seen: "which award gives more days, Employee of the Year or Quarterly Star Performer?" answered
    # with "Lowest Award Leave: Quarterly Star Performer (2)", the row naming more of its words
    from oasis_engine.pipeline import PromptIds
    ds = docs()
    p = Pipeline(ds, ScriptedModel(select=[], answer=[]).model, output_mode="prompted")
    shown = PromptIds(by_id={"A1": (0, node(ds[0], "Sheet: Q1 Summary"))}, letters={0: "A"}, picked=["A1"])
    worked = {"A1": ["Highest Revenue: March (291400)", "Lowest Revenue: January February start (248500)"]}
    assert p._stated("Which month had more revenue, January or March?", shown, worked)[1].startswith("Highest")
    assert p._stated("Which month had less revenue, January or March?", shown, worked)[1].startswith("Lowest")


AGREEMENT = """# agreement.md

## 1. Original terms

### 1.5 Payment terms
Payment within 60 days of the invoice date.

### 1.6 Late delivery penalty
1 % per full week of delay, up to 5 %.

## 2. Amendment 1

### 2.2 Payment terms (replaces 1.5)
Payment within 45 days of the invoice date.
"""


def test_a_clause_an_amendment_replaced_is_read_with_its_replacement():
    from oasis_engine.vague import vague_clarification
    ds = [parse_markdown(AGREEMENT, "a", "agreement.md")]
    index = SectionIndex(ds)
    old, new = node(ds[0], "1.5 Payment terms"), node(ds[0], "2.2 Payment terms (replaces 1.5)")
    assert index.replaced == {(0, old): (0, new)}
    # the topic alone: the overview of the clause in force (see overview.py), without the model
    r = asyncio.run(Pipeline(ds, ScriptedModel(select=[], answer=[]).model, output_mode="prompted")
                    .ask("What are the payment terms?"))
    assert "45 days" in r.answer and "60 days" not in r.answer
    q = "Within how many days of the invoice must we pay?"
    script = ScriptedModel(select=[{"standalone_question": q, "section_ids": [f"A{old}"]}],
                           answer=[{"found": True, "answer": "45 days.", "section_ids": []}])
    asyncio.run(Pipeline(ds, script.model, output_mode="prompted", fast_path=False).ask(q))
    prompt = script.prompts[-1][1]
    assert "REPLACED by" in prompt and "Payment within 45 days" in prompt
    # the replaced clause is not offered as a meaning of a vague question
    c = vague_clarification("What is the penalty?", index, None)
    assert c is None or all("1.5" not in o.label for o in c.options)


def test_a_code_the_section_does_not_name_gets_no_statement_answer():
    # seen: "the maximum spindle speed of the HX-700?" answered with the HX-500's
    from oasis_engine.pipeline import PromptIds
    spec = parse_markdown("# hx500.md\n\n## Specifications\n| Item | Value |\n|---|---|\n| Spindle speed | 50 - 12,000 rpm |\n"
                          "| Tool magazine | 24 tools |\n", "h", "hx500.md")
    p = Pipeline([spec], ScriptedModel(select=[], answer=[]).model, output_mode="prompted")
    shown = PromptIds(by_id={"A1": (0, node(spec, "Specifications"))}, letters={0: "A"}, picked=["A1"])
    assert p._stated("What is the maximum spindle speed of the HX-700?", shown, {}) is None
    assert "12,000" in p._stated("What is the maximum spindle speed?", shown, {})[1]


def test_the_rule_a_bare_no_rests_on_is_matched_by_word_stems():
    from oasis_engine.pipeline import PromptIds
    manual = parse_markdown("# m.md\n\n## Specifications\n| Item | Value |\n|---|---|\n| Max table load | 600 kg |\n\n"
                            "## Loading tools\nTools heavier than 7 kg or longer than 300 mm must not be loaded.\n", "m", "m.md")
    p = Pipeline([manual], ScriptedModel(select=[], answer=[]).model, output_mode="prompted")
    shown = PromptIds(by_id={"A1": (0, node(manual, "Specifications")), "A2": (0, node(manual, "Loading tools"))},
                      letters={0: "A"}, picked=["A1", "A2"])
    assert "must not be loaded" in p._rule_for("Can I load an 8 kg tool into the magazine?", shown)[1][2]
    # a bare "Yes" with that rule (final test): the rule alone, not "Yes. ... must not be loaded"
    q = "Can I load an 8 kg tool into the magazine?"
    script = ScriptedModel(select=[{"standalone_question": q, "section_ids": [f"A{node(manual, 'Loading tools')}"]}],
                           answer=[{"found": True, "answer": "Yes.", "section_ids": []}])
    r = asyncio.run(Pipeline([manual], script.model, output_mode="prompted", fast_path=False).ask(q))
    assert not r.answer.startswith("Yes") and "must not be loaded" in r.answer


def test_columns_are_ranked_by_their_totals():
    t = parse_markdown("# r.md\n\n## Sheet: Downtime hours\n| Cause | Line A | Line B | Line C |\n|---|---|---|---|\n"
                       "| Breakdowns | 42 | 58 | 196 |\n| Setup | 88 | 104 | 97 |\n", "r", "r.md")
    lines, _ = T.table_highlights(t, node(t, "Sheet: Downtime hours"), "Which line had the highest total downtime?")
    assert "Highest column total: Line C (293)" in lines


def test_a_ranking_over_a_partial_table_gives_the_documents_note():
    # seen: "the highest response time?" answered 133.0 ms from the 10 rows shown of 96
    csv = parse_markdown("# m.csv\n| t | response_time_ms |\n|---|---|\n| 00:00 | 107.3 |\n| 00:30 | 133.0 |\n\n"
                         "*Showing first 2 of 96 rows. Mean response time 169.6 ms; one spike at 13:30 (1840.6 ms).*",
                         "m", "m.csv")
    q = "What was the highest response time?"
    script = ScriptedModel(select=[{"standalone_question": q, "section_ids": []}],
                           answer=[{"found": True, "answer": "133.0 ms", "section_ids": []}])
    r = asyncio.run(Pipeline([csv], script.model, output_mode="prompted", fast_path=False).ask(q))
    assert r.found and "1840.6" in r.answer and "133.0" not in r.answer
    script = ScriptedModel(select=[{"standalone_question": "What is the mean response time?", "section_ids": []}],
                           answer=[{"found": True, "answer": "169.6 ms", "section_ids": []}])
    r = asyncio.run(Pipeline([csv], script.model, output_mode="prompted", fast_path=False).ask("What is the mean response time?"))
    assert r.answer == "169.6 ms"


ALARMS = """# HX-500 Operator Manual

## 1. About this manual
This manual covers the HX-500 machine. Operators must read chapter 2 before operating the HX-500.

## 6. Alarms
| Code | Meaning | Operator action |
|---|---|---|
| A301 | Spindle overload | Reduce the feed |
| A302 | Spindle over-temperature (above 70 °C) | Stop and let the spindle cool for 30 minutes |
"""


def test_a_rare_code_finds_its_section_and_an_answer_must_cite_it():
    # seen in the final test: "What does alarm A302 mean on the HX-500 ...?" found "About this manual"
    # first, and the 0.8B's made-up meaning, cited from it, was shown
    ds = [parse_markdown(ALARMS, "h", "hx500.md")]
    index = SectionIndex(ds)
    q = "What does alarm A302 mean on the HX-500 and what should the operator do?"
    ranked = [ds[0].sections[h.node_id].title for h in index.search(q)]
    assert ranked.index("6. Alarms") < ranked.index("1. About this manual")
    about, alarms = node(ds[0], "1. About this manual"), node(ds[0], "6. Alarms")
    script = ScriptedModel(select=[{"standalone_question": q, "section_ids": [f"A{about}", f"A{alarms}"]}],
                           answer=[{"found": True, "answer": "Alarm A302 indicates a No Machine condition.",
                                    "section_ids": [f"A{about}"]}])
    r = asyncio.run(Pipeline(ds, script.model, output_mode="prompted", fast_path=False).ask(q))
    assert "No Machine" not in r.answer


def test_a_short_answer_naming_a_row_is_not_an_echo():
    # seen: "Which lines met the scrap rate target in every month?" streamed "Line A", then the card
    # showed "not covered": "A" is not a search word, and "line" is in the question
    from oasis_engine.pipeline import _echoes
    assert not _echoes("Line A", "Which lines met the scrap rate target in every month?")
    assert _echoes("E-217", "E-217?")


SAFETY = """# safety.md

## 6. Forklifts
Only employees holding a valid Helix forklift license may drive forklifts. Speed limit 10 km/h in the
warehouse. Contractors may not drive Helix forklifts.

## 7. Leave
PL can be availed only after completing the probation period.

## 8. Receipts
Original receipts are required for every expense above INR 500. Lost receipts: a self-declaration is
accepted up to INR 2,000 per trip; above that the expense is not reimbursed.
"""


def test_a_yes_against_a_rule_that_says_no_shows_the_rule():
    ds = [parse_markdown(SAFETY, "s", "safety.md")]

    def ask(q, answer, section):
        script = ScriptedModel(select=[{"standalone_question": q, "section_ids": [f"A{node(ds[0], section)}"]}],
                               answer=[{"found": True, "answer": answer, "section_ids": []}])
        return asyncio.run(Pipeline(ds, script.model, output_mode="prompted", fast_path=False).ask(q))
    r = ask("Can a contractor drive one of our forklifts if they have a license from their own company?",
            "Yes, contractors may drive forklifts with their own license.", "6. Forklifts")
    assert not r.answer.startswith("Yes") and "Contractors may not drive" in r.answer
    # a condition with a "not" in it is not a no (the 0.8B in the large-document run)
    r = ask("Can a contractor drive one of our forklifts if they have a license from their own company?",
            "Yes, a contractor may drive a forklift if they have a license from their own company, provided they "
            "are not holding a Helix forklift license.", "6. Forklifts")
    assert not r.answer.startswith("Yes") and "Contractors may not drive" in r.answer
    r = ask("Can I take PL after my probation?", "Yes, after completing the probation period.", "7. Leave")
    assert r.answer.startswith("Yes")  # the rule says "only after", not no: the yes stays
    # a yes that goes on to say no (final test): the rule, not the contradiction
    r = ask("I lost a receipt for INR 3,000, can I still claim it?",
            "Yes, you can still claim it with a self-declaration. Above INR 2,000 it is not reimbursed.",
            "8. Receipts")
    assert not r.answer.startswith("Yes") and "not reimbursed" in r.answer


def test_a_code_no_excerpt_names_and_a_weight_for_a_price_are_not_answers():
    # seen: "the maximum spindle speed of the HX-700?" answered with the HX-500's, and "How much does
    # an HX-500 machine cost?" answered "5,800 kg" (its weight)
    spec = parse_markdown("# HX-500 Manual\n\n## 3. Specifications\n| Item | Value |\n|---|---|\n"
                          "| Spindle speed | 50 - 12,000 rpm |\n| Machine weight | 5,800 kg |\n", "h", "hx500.md")

    def ask(q, answer):
        script = ScriptedModel(select=[{"standalone_question": q, "section_ids": [f"A{node(spec, '3. Specifications')}"]}],
                               answer=[{"found": True, "answer": answer, "section_ids": []}])
        return asyncio.run(Pipeline([spec], script.model, output_mode="prompted", fast_path=False).ask(q))
    assert not ask("What is the maximum spindle speed of the HX-700?", "50 - 12,000 rpm").found
    assert not ask("How much does an HX-500 machine cost?", "5,800 kg").found
    assert not ask("How much does an HX-500 machine cost?", "Cost ($K): 5,800 kg").found  # a label's currency
    assert ask("What is the maximum spindle speed of the HX-500?", "50 - 12,000 rpm").found


def test_a_figure_in_the_question_picks_the_column_whose_range_it_falls_in():
    # seen in the final test: "I am a G3 manager flying to Frankfurt; the flight takes 9 hours" was
    # not covered: two columns say "hours", and "G3" was too short to name its row
    t = parse_markdown("# travel.md\n\n## 3. Travel class\n| Grade | Air (domestic) | Air (international, up to 6 hours) "
                       "| Air (international, over 6 hours) | Train |\n|---|---|---|---|---|\n"
                       "| G2 | Economy | Economy | Premium economy | AC 2-tier |\n"
                       "| G3 | Economy | Premium economy | Business | AC 2-tier |\n", "t", "travel.md")
    q = "I am a G3 manager flying to Frankfurt; the flight takes 9 hours. Which class can I book?"
    assert T.table_cell(t, node(t, "3. Travel class"), q)[1] == "Business"
    assert T.table_cell(t, node(t, "3. Travel class"), q.replace("9 hours", "5 hours"))[1] == "Premium economy"


TRAVEL = """# travel.md

## 6. Approvals
| Expense | Approval needed |
|---|---|
| Domestic trip | Manager |
| Single expense above INR 25,000 | Department head |
| Single expense above INR 1,00,000 | Plant manager and finance controller |

## 8. Not reimbursed
Alcohol, minibar, laundry for trips shorter than 4 nights, traffic fines and upgrades bought by the employee.
"""


def test_not_found_gives_the_cell_a_range_names_and_the_rule_a_yes_no_rests_on():
    # seen in the final test: "Who has to approve a single expense of INR 30,000?" and "Is laundry
    # reimbursed on a 3-night trip?" were not covered
    ds = [parse_markdown(TRAVEL, "t", "travel.md")]

    def ask(q, section):
        script = ScriptedModel(select=[{"standalone_question": q, "section_ids": [f"A{node(ds[0], section)}"]}],
                               answer=[{"found": False, "answer": "", "section_ids": []}])
        return asyncio.run(Pipeline(ds, script.model, output_mode="prompted", fast_path=False).ask(q))
    assert T.table_cell(ds[0], node(ds[0], "6. Approvals"), "Who approves an expense of INR 2,00,000?")[1] \
        == "Plant manager and finance controller"
    r = ask("Who has to approve a single expense of INR 30,000?", "6. Approvals")
    assert r.found and "Department head" in r.answer and "Plant manager" not in r.answer
    r = ask("Is laundry reimbursed on a 3-night trip?", "8. Not reimbursed")
    assert r.found and "laundry for trips shorter than 4 nights" in r.answer
    assert not ask("Is parking reimbursed at the airport?", "8. Not reimbursed").found


def test_the_highest_total_is_the_column_total_not_a_row():
    # seen in the final test: "Highest Line C: Breakdowns (196)" for the line with the highest total
    t = parse_markdown("# Plant performance H1 2026\n\n## Sheet: Downtime hours by cause\n| Cause | Line A | Line B | Line C |\n"
                       "|---|---|---|---|\n| Breakdowns | 42 | 58 | 196 |\n| Setup | 88 | 104 | 97 |\n| Holds | 9 | 14 | 31 |\n",
                       "r", "r.md")
    q = "Which line had the highest total downtime in H1, and how many hours was it?"
    script = ScriptedModel(select=[{"standalone_question": q, "section_ids": [f"A{node(t, 'Sheet: Downtime hours by cause')}"]}],
                           answer=[{"found": True, "answer": "Highest Line C: Breakdowns (196)", "section_ids": []}])
    r = asyncio.run(Pipeline([t], script.model, output_mode="prompted", fast_path=False).ask(q))
    assert "Line C has the highest total (324)" in r.answer


def test_a_city_names_its_tier_column_through_the_sections_list():
    # seen in the final test: "What about in Jaipur?" got the Tier 1 rate; Jaipur is in the Tier 2 list
    t = parse_markdown("# travel.md\n\n## 2. Grades\n| Grade | Examples |\n|---|---|\n| G4 | Senior managers |\n\n"
                       "## 4. Accommodation limits per night (INR)\n| Grade | Tier 1 city | Tier 2 city | Other |\n"
                       "|---|---|---|---|\n| G2 | 6,000 | 4,500 | 3,500 |\n| G4 | 12,000 | 8,000 | 6,000 |\n\n"
                       "Tier 1 cities: Mumbai, Pune, Kolkata. Tier 2 cities: Ahmedabad, Jaipur, Indore.\n", "t", "travel.md")
    acc = node(t, "4. Accommodation limits per night (INR)")
    assert T.table_cell(t, acc, "What is the hotel limit per night for a G4 in Jaipur?")[1] == "8,000"
    assert T.table_cell(t, acc, "What is the hotel limit per night for a G2 in Pune?")[1] == "6,000"
    assert T.table_cell(t, acc, "What is the hotel limit for G4 in a Tier 2 city?")[1] == "8,000"
    assert T.table_cell(t, acc, "What is the hotel limit per night for a G4 in Goa?") is None


def test_a_ranking_question_takes_what_was_worked_out_from_the_table_it_names():
    # seen in the final test: the production table's column total for "the highest total downtime",
    # and March for "the lowest production on Line C" (April)
    t = parse_markdown("# p.md\n\n## Sheet: Production by line\n| Month | Line A | Line B | Line C |\n|---|---|---|---|\n"
                       "| March | 3,910 | 3,550 | 3,140 |\n| April | 3,870 | 3,600 | 2,210 |\n\n"
                       "## Sheet: Downtime hours by cause\n| Cause | Line A | Line B | Line C |\n|---|---|---|---|\n"
                       "| Breakdowns | 42 | 58 | 196 |\n| Setup | 88 | 104 | 97 |\n", "p", "p.md")

    def ask(q, answer):
        ids = [f"A{node(t, 'Sheet: Production by line')}", f"A{node(t, 'Sheet: Downtime hours by cause')}"]
        script = ScriptedModel(select=[{"standalone_question": q, "section_ids": ids}],
                               answer=[{"found": True, "answer": answer, "section_ids": []}])
        return asyncio.run(Pipeline([t], script.model, output_mode="prompted", fast_path=False).ask(q))
    assert "Line C has the highest total (293)" in ask("Which line had the highest total downtime, and how many hours?", "Line A").answer
    assert "April" in ask("Which month had the lowest production on Line C?", "March").answer


def test_a_later_part_that_points_back_gets_the_code_an_earlier_part_found():
    # final eval: "What does alarm C-2087 mean, and what does the replacement part cost?" -> part 2
    # answered 10,480 (another row); the part number was only in part 1's answer
    ds = [parse_markdown("# manual.md\n\n## Alarms\nAlarm C-2087 (way cover jam): replace the cover wiper strip, "
                         "part HX9-4471.\n", "m", "manual.md"),
          parse_markdown("# parts.md\n\n## Parts list\n| Part number | Description | Price (INR) |\n|---|---|---|\n"
                         "| HX9-4471 | way cover wiper strip | 2,350 |\n| HX9-4480 | replacement cover | 10,480 |\n",
                         "p", "parts.md")]
    q = "What does alarm C-2087 mean, and what does the replacement part cost?"
    script = ScriptedModel(
        select=[{"standalone_question": q, "section_ids": [f"A{node(ds[0], 'Alarms')}", f"B{node(ds[1], 'Parts list')}"]}],
        answer=[{"found": False, "answer": "", "section_ids": []},
                {"found": True, "answer": "A way cover jam: replace the wiper strip, part HX9-4471.", "section_ids": []},
                {"found": True, "answer": "10,480", "section_ids": []}])
    r = asyncio.run(Pipeline(ds, script.model, output_mode="prompted", fast_path=False).ask(q))
    assert r.answer.startswith("1. ") and "2. what does the replacement part cost:" in r.answer
    assert "2,350" in r.answer.split("2. ")[1] and "10,480" not in r.answer


def test_a_bare_figure_from_a_cell_is_completed_to_the_whole_cell():
    # samples eval: "What is the 401(k) match?" answered "100%" (the cell: "100% match on the first 4%")
    t = parse_markdown("# hb.md\n\n## 4 Compensation\n| Benefit | Details |\n| --- | --- |\n"
                       "| 401(k) | 100% match on the first 4% of salary, vesting immediately |\n"
                       "| Wellness | USD 60 per month for gym |\n", "h", "hb.md")
    q = "What is the 401(k) match?"
    script = ScriptedModel(select=[{"standalone_question": q, "section_ids": [f"A{node(t, '4 Compensation')}"]}],
                           answer=[{"found": True, "answer": "100%", "section_ids": []}])
    r = asyncio.run(Pipeline([t], script.model, output_mode="prompted", fast_path=False).ask(q))
    assert r.answer == "100% match on the first 4% of salary, vesting immediately"


def test_a_bare_answer_to_one_half_of_an_and_question_gets_the_other_half():
    # samples eval: "How much flash and SRAM ...?" answered "786 KB"; "Which microcontroller ... and at
    # what clock speed?" answered "STM32U585"
    t = parse_markdown("# x2_spec.md\n\n## 2.1 Main Controller\nThe STM32U585 was selected for its TrustZone security. "
                       "It runs at 160 MHz with 2 MB of flash and 786 KB of SRAM.\n\n- Clock: 160 MHz from a 16 MHz crystal\n"
                       "- Watchdog timeout: 4 seconds\n", "s", "x2_spec.md")

    def ask(q, answer):
        script = ScriptedModel(select=[{"standalone_question": q, "section_ids": [f"A{node(t, '2.1 Main Controller')}"]}],
                               answer=[{"found": True, "answer": answer, "section_ids": [f"A{node(t, '2.1 Main Controller')}"]}])
        return asyncio.run(Pipeline([t], script.model, output_mode="prompted", fast_path=False).ask(q)).answer
    assert "2 MB of flash and 786 KB of SRAM" in ask("How much flash and SRAM does the main controller have?", "786 KB")
    got = ask("Which microcontroller does the X2 use and at what clock speed?", "STM32U585")
    assert got.startswith("STM32U585") and "160 MHz" in got
    assert ask("How much SRAM does the main controller have?", "786 KB") == "786 KB"  # one thing asked


def test_most_expensive_and_cheapest_rank_the_price_column():
    # final check: "Which HX-900 spare part is the most expensive?" answered a 36,105 INR part; the
    # question names no column, "expensive" means the price
    t = parse_markdown("# parts.md\n\n## Parts list\n| Part number | Description | Price (INR) | In stock |\n"
                       "|---|---|---|---|\n| HX9-4004 | cable | 36,105 | 11 |\n| HX9-4970 | spindle kit | 412,000 | 2 |\n"
                       "| HX9-5055 | washer | 150 | 900 |\n", "p", "parts.md")

    def ask(q, answer):
        script = ScriptedModel(select=[{"standalone_question": q, "section_ids": [f"A{node(t, 'Parts list')}"]}],
                               answer=[{"found": True, "answer": answer, "section_ids": []}])
        return asyncio.run(Pipeline([t], script.model, output_mode="prompted", fast_path=False).ask(q))
    assert "HX9-4970" in ask("Which spare part is the most expensive?", "HX9-4004").answer
    assert "HX9-5055" in ask("Which spare part is the cheapest?", "HX9-4004").answer


def test_a_name_the_cited_section_never_mentions_is_not_answered_from_it():
    # seen in the final test: "What was the OEE of the Chennai plant in H1 2026?" answered with a
    # figure from the Pune plant's report; Chennai is only in the travel policy
    ds = [parse_markdown("# plant.md\n\n## Summary\nPune OEE averaged 72.4 % in H1 2026.\n", "p", "plant.md"),
          parse_markdown("# travel.md\n\n## Cities\nTier 1 cities: Mumbai, Chennai, Pune.\n", "t", "travel.md")]
    q = "What was the OEE of the Chennai plant in H1 2026?"
    script = ScriptedModel(select=[{"standalone_question": q, "section_ids": [f"A{node(ds[0], 'Summary')}"]}],
                           answer=[{"found": True, "answer": "72.4 %", "section_ids": [f"A{node(ds[0], 'Summary')}"]}])
    assert not asyncio.run(Pipeline(ds, script.model, output_mode="prompted", fast_path=False).ask(q)).found
    q = "What was the OEE of the Pune plant in H1 2026?"
    script = ScriptedModel(select=[{"standalone_question": q, "section_ids": [f"A{node(ds[0], 'Summary')}"]}],
                           answer=[{"found": True, "answer": "72.4 %", "section_ids": [f"A{node(ds[0], 'Summary')}"]}])
    assert asyncio.run(Pipeline(ds, script.model, output_mode="prompted", fast_path=False).ask(q)).found


def test_a_name_used_in_other_documents_is_checked_against_the_cited_document():
    # Chennai is in three of six documents, never in the plant report the answer came from; the
    # company name only in a policy's title block still lets its sections answer
    ds = [parse_markdown("# plant.md\n\n## OEE\n| Month | Line C |\n|---|---|\n| January | 66.1 |\n", "p", "plant.md"),
          parse_markdown("# safety.md\n\n## Scope\nThe Pune and Chennai plants.\n", "s", "safety.md"),
          parse_markdown("# supply.md\n\n## Scope\nSupplies the plants in Pune and Chennai.\n", "u", "supply.md"),
          parse_markdown("# leave.md\n\n## Title\nAcme Technologies leave policy.\n\n## Privilege Leave\n18 days a year.\n",
                         "l", "leave.md")]

    def ask(d, section, q, answer):
        i = f"A{node(ds[d], section)}"
        script = ScriptedModel(select=[{"standalone_question": q, "section_ids": [i]}],
                               answer=[{"found": True, "answer": answer, "section_ids": [i]}])
        return asyncio.run(Pipeline(ds, script.model, output_mode="prompted", fast_path=False).ask(q))
    assert not ask(0, "OEE", "What was the OEE of the Chennai plant in January?", "66.1").found
    assert ask(3, "Privilege Leave", "How many days of privilege leave do Acme employees get?", "18 days").found


def test_a_big_table_is_read_whole_and_part_numbers_are_not_figures():
    # large-document test: a 500-row parts list; part numbers ("HX9-4002") were read as the number 9,
    # every row matched "hx9", and rows past 200 were never read
    rows = "".join(f"| HX9-{4000 + i} | seal {i} | {1000 + i:,} | {i % 90 + 1} |\n" for i in range(500))
    rows = rows.replace("| HX9-4450 | seal 450 | 1,450 | 1 |", "| HX9-4450 | wiper strip | 2,350 | 95 |")
    t = parse_markdown("# parts.md\n\n## Parts list\n| Part number | Description | Price (INR) | Lead time (days) |\n"
                       "|---|---|---|---|\n" + rows, "p", "parts.md")
    nid = node(t, "Parts list")
    assert T.table_cell(t, nid, "What is the price of part HX9-4450?") == ("Part number: HX9-4450 · Price (INR): 2,350", "2,350")
    assert T.table_cell(t, nid, "What is the price of part HX9-5600?") is None
    lines, _ = T.table_highlights(t, nid, "Which part has the longest lead time?")
    assert "Highest Lead time (days): HX9-4450 (95)" in lines
    assert T.number("HX9-4002") is None and T.number("Rs.500") == 500 and T.number("INR500") == 500


def test_an_answer_from_another_manual_than_the_one_named_is_not_an_answer():
    # large-document test: the HX-900's coupling torque answered from the HX-500 manual's torque table
    ds = [parse_markdown("# HX-500 Manual\n\n## 8. Torque values\n| Fastener | Torque |\n|---|---|\n"
                         "| Way cover screws | 10 Nm |\n", "a", "HX-500_manual.md"),
          parse_markdown("# HX-900 Service Manual\n\n## 1. About\nThe HX-900 service manual.\n", "b", "HX-900_manual.md")]
    q = "What torque are the way cover screws on the HX-900 tightened to?"
    i = f"A{node(ds[0], '8. Torque values')}"
    script = ScriptedModel(select=[{"standalone_question": q, "section_ids": [i]}],
                           answer=[{"found": True, "answer": "10 Nm", "section_ids": [i]}])
    assert not asyncio.run(Pipeline(ds, script.model, output_mode="prompted", fast_path=False).ask(q)).found


def test_a_long_table_excerpt_includes_the_rows_for_codes_other_excerpts_name():
    # large-document test: the alarm text names part HX9-4471; its price was in row 157 of a
    # 500-row parts list that the excerpt (passages around the question's words) never reached
    rows = "".join(f"| HX9-{4000 + i} | seal {i} | {1000 + i:,} |\n" for i in range(500))
    rows = rows.replace("| HX9-4471 | seal 471 | 1,471 |", "| HX9-4471 | wiper strip | 2,350 |")
    ds = [parse_markdown("# m.md\n\n## 30.3 Alarms\nAlarm C-2087 (way cover jam): replace the wiper strip, part "
                         "HX9-4471.\n", "m", "m.md"),
          parse_markdown("# p.md\n\n## Parts list\n| Part number | Description | Price (INR) |\n|---|---|---|\n" + rows,
                         "p", "p.md")]
    q = "What does alarm C-2087 mean, and what does the replacement part cost?"
    ids = [f"A{node(ds[0], '30.3 Alarms')}", f"B{node(ds[1], 'Parts list')}"]
    script = ScriptedModel(select=[{"standalone_question": q, "section_ids": ids}],
                           answer=[{"found": True, "answer": "A way cover jam; part HX9-4471 costs 2,350.",
                                    "section_ids": ids}])
    asyncio.run(Pipeline(ds, script.model, output_mode="prompted", fast_path=False).ask(q))
    assert "| HX9-4471 | wiper strip | 2,350 |" in script.prompts[-1][1]


def test_a_word_from_another_cell_is_replaced_and_a_yes_no_question_is_not_answered_with_a_cell():
    # large-document test: "Premium economy" for a 9-hour flight (the G3 row says Business over 6
    # hours); "Can I load an 8 kg tool?" got "Tool magazine · 24 tools" instead of the loading rule
    t = parse_markdown("# travel.md\n\n## 3. Travel class\n| Grade | Air (international, up to 6 hours) "
                       "| Air (international, over 6 hours) |\n|---|---|---|\n| G3 | Premium economy | Business |\n",
                       "t", "travel.md")
    q = "I am a G3 manager flying to Frankfurt; the flight takes 9 hours. Which class can I book?"
    i = f"A{node(t, '3. Travel class')}"
    script = ScriptedModel(select=[{"standalone_question": q, "section_ids": [i]}],
                           answer=[{"found": True, "answer": "Premium economy", "section_ids": [i]}])
    assert "Business" in asyncio.run(Pipeline([t], script.model, output_mode="prompted", fast_path=False).ask(q)).answer
    m = parse_markdown("# m.md\n\n## Specifications\n| Item | Value |\n|---|---|\n| Tool magazine | 24 tools |\n\n"
                       "## Loading tools\nTools heavier than 7 kg must not be loaded into the magazine.\n", "m", "m.md")
    q = "Can I load an 8 kg tool into the magazine?"
    ids = [f"A{node(m, 'Specifications')}", f"A{node(m, 'Loading tools')}"]
    script = ScriptedModel(select=[{"standalone_question": q, "section_ids": ids}],
                           answer=[{"found": False, "answer": "", "section_ids": []}])
    r = asyncio.run(Pipeline([m], script.model, output_mode="prompted", fast_path=False).ask(q))
    assert "24 tools" not in r.answer


def test_evidence_quotes_the_row_with_the_answers_part_number_and_bare_cells_get_their_unit():
    # large-document check: "HX9-4970 (95)" was backed by rows that only shared the 95 ("In stock:
    # 95"), the real row was past the 20,000 characters searched, and "2,350" had no unit
    rows = "".join(f"| HX9-{4000 + i} | seal {i} | {1000 + i:,} | {95 if i == 5 else i % 90} | {i % 90 + 1} |\n"
                   for i in range(500))
    rows = rows.replace("| HX9-4450 | seal 450 | 1,450 | 0 | 1 |", "| HX9-4450 | rebuild kit | 2,350 | 1 | 95 |")
    t = parse_markdown("# parts.md\n\n## Parts list\n| Part number | Description | Price (INR) | In stock | Lead time (days) |\n"
                       "|---|---|---|---|---|\n" + rows, "p", "parts.md")
    i = f"A{node(t, 'Parts list')}"

    def ask(q, answer):
        script = ScriptedModel(select=[{"standalone_question": q, "section_ids": [i]}],
                               answer=[{"found": True, "answer": answer, "section_ids": [i]}])
        return asyncio.run(Pipeline([t], script.model, output_mode="prompted", fast_path=False).ask(q))
    r = ask("Which part has the longest lead time?", "HX9-4450 (95)")
    assert r.evidence and all("HX9-4450" in e.text for e in r.evidence)
    assert ask("What is the price of part HX9-4450?", "2,350").answer == "2,350 INR"


def test_a_code_misspelled_by_one_letter_is_written_as_the_document_writes_it():
    # samples eval: "What is on pin 7?" answered "RYL_OB" (the table: RLY_OB)
    from oasis_engine.pipeline import _codes_as_written
    src = "| 6 | RLY_G | Out |\n| 7 | RLY_OB | Out | Heat pump reversing valve |\n| HX9-4472 | seal |"
    assert _codes_as_written("RYL_OB", src) == "RLY_OB"
    assert _codes_as_written("RLY_OB", src) == "RLY_OB"
    assert _codes_as_written("Part HX9-4471", src) == "Part HX9-4471"  # another part, not a typo


def test_a_question_written_back_with_the_language_line_is_asked_again():
    # samples eval: "EVT2 बिल्ड कब निर्धारित है?" answered with itself and "(Write the answer in Hindi.)"
    t = parse_markdown("# plan.md\n\n## Schedule\nThe EVT2 build is scheduled for October 20, 2026.\n", "p", "plan.md")
    q = "EVT2 बिल्ड कब निर्धारित है?"
    script = ScriptedModel(select=[{"standalone_question": q, "section_ids": [f"A{node(t, 'Schedule')}"]}],
                           answer=[{"found": True, "answer": q + " (Write the answer in Hindi.)", "section_ids": []},
                                   {"found": True, "answer": "October 20, 2026", "section_ids": []}])
    r = asyncio.run(Pipeline([t], script.model, output_mode="prompted", fast_path=False).ask(q))
    assert r.answer == "October 20, 2026"
    assert "Write the answer in Hindi" in script.prompts[-2][1] and "Write the answer in" not in script.prompts[-1][1]


def test_a_bare_no_is_not_backed_by_a_rule_about_something_else():
    # samples eval: "Does the R7 router support Wi-Fi 7?" -> "No. The documents state: Recommend the
    # 5 GHz network for devices within two rooms of the router." (Wi-Fi 7 is not in the documents)
    t = parse_markdown("# r7.md\n\n## Wi-Fi\nRecommend the 5 GHz network for devices within two rooms of the router. "
                       "Devices must not be placed behind metal cabinets.\n", "r", "r7.md")
    q = "Does the R7 router support Wi-Fi 7?"
    script = ScriptedModel(select=[{"standalone_question": q, "section_ids": [f"A{node(t, 'Wi-Fi')}"]}],
                           answer=[{"found": True, "answer": "No.", "section_ids": [f"A{node(t, 'Wi-Fi')}"]}])
    r = asyncio.run(Pipeline([t], script.model, output_mode="prompted", fast_path=False).ask(q))
    assert "Recommend the 5 GHz" not in r.answer and "metal" not in r.answer, r.answer


def test_an_answer_copied_from_the_instructions_typo_example_is_not_an_answer():
    # reasoning eval: "How do I reset my password in TaskFlow?" -> "The amber led meen"
    from oasis_engine.pipeline import _foreign_words
    assert _foreign_words("The amber led meen", "Settings > Account. How do I reset my password in TaskFlow?")
    assert not _foreign_words("The amber LED means a fault.", "The amber LED blinks on a fault. What does the amber led mean?")
