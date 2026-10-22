import asyncio

from conftest import ScriptedModel
from oasis_engine.loaders import parse_markdown
from oasis_engine.overview import overview, topic_of
from oasis_engine.pipeline import Pipeline
from oasis_engine.search import SectionIndex

POLICY = """# travel_expense_policy.pdf

## 7. Receipts
Original receipts are required for every expense above INR 500. Lost receipts: a self-declaration is accepted up
to INR 2,000 per trip; above that the expense is not reimbursed. Receipts must show the GST number of the supplier.

## 8. Hotels
| Grade | Limit |
|---|---|
| G2 | 6,000 |
"""


def test_a_question_naming_only_a_topic_gets_the_sections_overview():
    # final computer-use check: "receipts" was answered with the GST sentence alone
    ds = [parse_markdown(POLICY, "t", "travel_expense_policy.pdf")]
    r = asyncio.run(Pipeline(ds, ScriptedModel(select=[], answer=[]).model, output_mode="prompted").ask("receipts"))
    assert r.found and "INR 500" in r.answer and "INR 2,000" in r.answer and "GST" in r.answer
    assert [c.title for c in r.citations] == ["7. Receipts"]
    # ... and a question naming a document by its title gets the document's overview
    text, _, _ = overview("what is the travel expense policy?", SectionIndex(ds))
    assert "It covers: 7. Receipts; 8. Hotels." in text


def test_questions_about_something_in_particular_are_not_overviews():
    for q in ["What is the hotel limit for G2?", "Can I claim a lost receipt?", "How long are receipts kept?",
              "what is the limit?", "receipts for 2026", "HX9-4471"]:
        assert topic_of(q) is None, q
    for q in ["receipts", "what is the leave policy?", "tell me about lockout/tagout", "Warranty?"]:
        assert topic_of(q), q


def test_an_ask_back_option_is_answered_from_the_section_it_names():
    # final computer-use check: clicking "Leave Approval Procedure" gave one sentence, twice
    ds = [parse_markdown("# leave.pdf\n\n## Leave Approval Procedure\n- The employee applies on HRMS.\n"
                         "- The manager approves within 2 working days.\n- Requests longer than 5 working days also "
                         "require HOD approval.\n", "l", "leave.pdf")]
    text, _, _ = overview("What does “Leave Approval Procedure” say about approval?", SectionIndex(ds))
    assert "applies on HRMS" in text and "within 2 working days" in text and "HOD approval" in text


def test_a_sentence_that_adds_nothing_to_one_before_is_dropped_and_a_hindi_question_is_not_vague():
    from oasis_engine.loops import without_repeated_sentences
    from oasis_engine.vague import vague_clarification
    once = without_repeated_sentences("The document states that requests longer than 5 working days require HOD "
                                      "approval. Requests longer than 5 working days also require HOD approval.")
    assert once.count("HOD approval") == 1
    assert without_repeated_sentences("PL needs 7 days notice. CL needs 7 days notice.").count("notice") == 2
    ds = [parse_markdown(POLICY, "t", "travel_expense_policy.pdf")]
    assert vague_clarification("छुट्टी नीति क्या है?", SectionIndex(ds), None) is None
