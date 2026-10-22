"""Chat mode's tools and safeguards: citation formats of larger models, general-knowledge marking,
declines, retries, the per-request budget, and the calculate, list_documents, read_table,
semantic_search and read_attachment tools."""
import asyncio
import base64
import io
import json
import zipfile

import httpx
import openai
import pytest
from fastapi.testclient import TestClient
from pydantic_ai import ModelHTTPError
from pydantic_ai.models.function import FunctionModel

from conftest import ChatScript, write_markdown_kb
from oasis_engine import bundle
from oasis_engine.api import create_app
from oasis_engine.attachments import AttachmentError, to_text
from oasis_engine.calc import CalcError, calculate
from oasis_engine.attachments import Attachment
from oasis_engine.chat import LIVE_REPLY, NOT_COVERED_REPLY, SMALL_MODEL_REPLY, ChatAgent, mark_general, name_attachments, normalize_refs
from oasis_engine.config import AppConfig, EmbeddingConnection, save_config
from oasis_engine.embeddings import EmbeddingError, SemanticIndex, prefixes_for, union
from oasis_engine.llm import BudgetExceeded, LLMConfig, RequestBudget, RetryingModel
from oasis_engine.loaders import load_markdown
from oasis_engine.pipeline import ProviderBusy
from oasis_engine.search import Hit, SectionIndex, tokenize
from oasis_engine.settings import Settings
from oasis_engine.tables import markdown_tables

BUDGET_MD = """# budget.xlsx

## Sheet: Summary

| Category | FY2026 Actual ($K) | FY2027 Budget ($K) | Change |
| --- | --- | --- | --- |
| Personnel | 389 | 412 | +5.9% |
| Travel | 45 | 38 | -15.6% |
| **Total** | **697** | **770** | **+10.5%** |
"""


def parts_text(messages) -> str:
    return json.dumps([m.parts for m in messages], default=str)


def tool_result(messages, name: str):
    """What the tool returned in the last call to it, as the model received it."""
    from pydantic_ai.messages import ToolReturnPart
    found = [p.content for m in messages for p in m.parts if isinstance(p, ToolReturnPart) and p.tool_name == name]
    return found[-1]


@pytest.fixture
def office(tmp_path):
    def make(script, embeddings=None):
        data = tmp_path / "data"
        md = write_markdown_kb(tmp_path / "md")
        (md / "budget.md").write_text(BUDGET_MD, encoding="utf-8")
        bundle.build_bundle(md, "markdown", "v1", tmp_path / "kb.zip", documents=[
            {"id": "torque-tool.md", "title": "DT-450 manual", "tags": ["S4"]},
            {"id": "budget.md", "title": "budget.xlsx", "tags": ["Office"]}])
        save_config(data, AppConfig(profile="office", embeddings=embeddings))
        client = TestClient(create_app(Settings(data_dir=data, llm=LLMConfig(output_mode="prompted")),
                                       model=script.model))
        assert client.post("/api/kb/import", json={"path": str(tmp_path / "kb.zip")}).status_code == 200
        return client
    return make


# ── citations, general knowledge, declines ──

def test_citation_marks_of_larger_models_become_sources_and_numbers_are_checked(office):
    script = ChatScript([{"tool": "search_manuals", "args": {"query": "E17"}},
                         {"tool": "read_section", "args": {"ref": "S1"}},
                         {"text": "E17 means torque not reached【S1】. Keep air above 7 bar【S1†L1-L3】."}])
    r = office(script).post("/api/ask", json={"question": "What is E17?", "mode": "chat"}).json()
    assert r["answer"] == "E17 means torque not reached [1]. Keep air above 7 bar [1]."
    assert [c["title"] for c in r["citations"]] == ["Error E17"]
    assert r["unverified_numbers"] == ["7 bar"]  # the check runs now that the citation is found
    assert not r["general"]


def test_normalize_refs_variants():
    assert normalize_refs("a【S1】 b［S2］ c[S3†L4-L9] d (S4, S5) e sections S6 and S7") == \
        "a[S1] b[S2] c[S3] d [S4] [S5] e [S6] [S7]"
    assert normalize_refs("budget [S6/S7] and [S1; S2]") == "budget [S6] [S7] and [S1] [S2]"
    assert normalize_refs("specifically **S2** and **S4**, here") == "specifically [S2] and [S4], here"
    assert normalize_refs("The relevant section is **[S1]**, which") == "The relevant section is [S1], which"


# ── a small built-in model answers from the documents only ──

def test_a_small_model_gives_no_general_knowledge_answers(tmp_path):
    docs = load_markdown(write_markdown_kb(tmp_path / "md"))

    def ask(text, question, documents_only=True):
        script = ChatScript([{"text": text}])
        r = asyncio.run(ChatAgent(script.model).ask(question, docs, SectionIndex(docs), documents_only=documents_only))
        return r, script

    r, _ = ask("> General knowledge: the Prime Minister of India is the President.", "who is the PM of india")
    assert not r.found and r.answer == SMALL_MODEL_REPLY and not r.general
    r, _ = ask("E17 means torque not reached [S1].\n\nGeneral knowledge: torque is a force.", "What is E17?")
    assert r.found and "General knowledge" not in r.answer and r.citations and not r.general
    r, _ = ask("I search and read the documents and answer with their sources.", "what can you do?")
    assert r.found and "search and read" in r.answer  # about itself: needs no source
    r, _ = ask("General knowledge: New Delhi.", "capital of india?", documents_only=False)
    assert r.found and r.general  # a larger model still may


def test_live_information_is_declined_by_any_model(tmp_path):
    docs = load_markdown(write_markdown_kb(tmp_path / "md"))

    async def never(messages, info):
        raise AssertionError("the model must not run")
        yield ""
    agent = ChatAgent(FunctionModel(stream_function=never))
    for q in ("what is the weather like today", "latest news headlines", "what's the time now",
              "USD to INR exchange rate"):
        for small in (True, False):
            r = asyncio.run(agent.ask(q, docs, SectionIndex(docs), documents_only=small))
            assert not r.found and r.answer == LIVE_REPLY, q
    # a document question that only sounds live still runs
    script = ChatScript([{"text": "E17 means torque not reached [S1]."}])
    r = asyncio.run(ChatAgent(script.model).ask("What is the latest fix for E17?", docs, SectionIndex(docs)))
    assert r.found


def test_a_small_models_long_not_covered_answer_is_one_line(tmp_path):
    # seen on a laptop: three paragraphs saying the manual has nothing on the weather, inventing
    # what it is about and repeating the point as an "Answer:"
    docs = load_markdown(write_markdown_kb(tmp_path / "md"))
    ramble = ("Based on the documents, specifically the R7 guide, there is no information about the air "
              "pressure of the paint shop [S1].\n\nThe documents describe the tools.\n\nAnswer: the documents "
              "do not cover it.")
    script = ChatScript([{"text": ramble}])
    r = asyncio.run(ChatAgent(script.model).ask("What is the air pressure in the paint shop?", docs,
                                                SectionIndex(docs), documents_only=True))
    assert not r.found and r.answer == NOT_COVERED_REPLY and not r.citations
    # an answer that only mentions what is missing is kept
    script = ChatScript([{"text": "E17 means torque not reached [S1]. The guide does not say how often it happens."}])
    r = asyncio.run(ChatAgent(script.model).ask("What is E17?", docs, SectionIndex(docs), documents_only=True))
    assert r.found and r.citations


def test_an_answer_without_markers_is_cited_by_what_it_repeats(tmp_path):
    """Small models forget the [S1] markers: an answer plainly written from a section it read gets
    that section as its source, so it is neither shown as general knowledge nor blocked."""
    docs = load_markdown(write_markdown_kb(tmp_path / "md"))
    e17 = docs[0].section_text(next(s.node_id for s in docs[0].sections.values() if s.title == "Error E17"))
    script = ChatScript([{"tool": "search_manuals", "args": {"query": "E17"}},
                         {"text": "E17 means the torque was not reached. Check that the air pressure is above 6 bar."}])
    r = asyncio.run(ChatAgent(script.model).ask("What is E17?", docs, SectionIndex(docs), documents_only=True))
    assert "6 bar" in e17 and r.found and [c.title for c in r.citations] == ["Error E17"] and not r.general
    # naming the document ("Based on the HX-500 manual", the 4B in the final test): its title's
    # figures are not made up
    title = docs[0].display_title
    script = ChatScript([{"tool": "search_manuals", "args": {"query": "E17"}},
                         {"text": f"Based on the {title}, E17 means the torque was not reached. Check that the "
                                  "air pressure is above 6 bar."}])
    r = asyncio.run(ChatAgent(script.model).ask("What is E17?", docs, SectionIndex(docs)))
    assert r.citations and not r.general, title
    script = ChatScript([{"text": "The Prime Minister of India is elected by the members of parliament."}])
    r = asyncio.run(ChatAgent(script.model).ask("who is the PM of india", docs, SectionIndex(docs)))
    assert r.citations == [] and r.general


def test_document_facts_labelled_general_knowledge_lose_the_label(tmp_path):
    docs = load_markdown(write_markdown_kb(tmp_path / "md"))
    script = ChatScript([{"tool": "search_manuals", "args": {"query": "E17"}},
                         {"text": "E17 is a torque fault [S1].\n\nGeneral knowledge: E17 means the torque was not "
                                  "reached; check that the air pressure is above 6 bar.\n\nGeneral knowledge: "
                                  "Pneumatic tools generally lose power when hoses leak or kink."}])
    r = asyncio.run(ChatAgent(script.model).ask("What is E17?", docs, SectionIndex(docs)))
    paragraphs = r.answer.split("\n\n")
    assert paragraphs[1].startswith("E17 means the torque") and "General knowledge" not in paragraphs[1]
    assert paragraphs[2].startswith("> **General knowledge:**")  # real general knowledge keeps it


def test_the_small_model_is_told_to_stay_with_the_documents(tmp_path):
    docs = load_markdown(write_markdown_kb(tmp_path / "md"))
    seen = {}

    async def capture(messages, info):
        seen["instructions"] = info.instructions
        yield "Fine."
    agent = ChatAgent(FunctionModel(stream_function=capture))
    asyncio.run(agent.ask("What does error E17 mean?", docs, SectionIndex(docs), documents_only=True))
    assert "do not answer from general knowledge" in seen["instructions"]
    assert "you may answer it from general knowledge" not in seen["instructions"]
    assert "You cannot draw charts" in seen["instructions"] and "If asked what you can do" in seen["instructions"]


def test_a_small_model_declines_off_topic_questions_without_running(tmp_path):
    # seen on a laptop: "Write a short poem about cats" took 100 s on the 0.8B model, only to be declined
    docs = load_markdown(write_markdown_kb(tmp_path / "md"))

    async def never(messages, info):
        raise AssertionError("the model must not run")
        yield ""
    agent = ChatAgent(FunctionModel(stream_function=never))
    r = asyncio.run(agent.ask("Write a short poem about cats", docs, SectionIndex(docs), documents_only=True))
    assert not r.found and r.answer == SMALL_MODEL_REPLY and "Manage" in r.answer


def test_chat_is_limited_on_a_small_built_in_model_only(tmp_path, monkeypatch):
    import oasis_engine.api as api
    engine = api.Engine(Settings(data_dir=tmp_path / "data", llm=LLMConfig(model="local")))
    for served, small in (("Qwen3.5-0.8B-Q4_K_M", True), ("Qwen3.5-4B-Q4_K_M", False), ("mystery-model", False)):
        engine._served.clear()
        monkeypatch.setattr(api, "_served_model", lambda cfg, s=served: s)
        assert asyncio.run(engine.chat_is_small(engine.chat_llm)) is small, served
    assert not asyncio.run(engine.chat_is_small(LLMConfig(model="qwen3-32b", base_url="http://llm.lan/v1")))


def test_small_talk_on_a_small_model_never_reaches_it(office):
    client = office(ChatScript([]))  # any model call would fail: the script has no steps

    async def small(cfg):
        return True
    client.app.state.engine.chat_is_small = small
    for text, start in (("hi", "Hello!"), ("how are you", "I'm doing well")):
        r = client.post("/api/ask", json={"question": text, "mode": "chat"}).json()
        assert r["smalltalk"] and r["answer"].startswith(start), text


def test_a_topicless_question_on_a_small_model_is_asked_back(office):
    # final check: Chat on the 0.8B declined "How long?" as a general question
    client = office(ChatScript([]))

    async def small(cfg):
        return True
    client.app.state.engine.chat_is_small = small
    r = client.post("/api/ask", json={"question": "How long?", "mode": "chat"}).json()
    assert r["clarify"] and r["clarify"]["options"], r["answer"]


def test_a_writing_request_about_all_documents_goes_to_the_assistant(office):
    script = ChatScript([{"text": "An essay: the torque tool manual explains E17 [S1]."}],
                        route={"kind": "all_manuals", "topic": ""})
    r = office(script).post("/api/ask", json={"question": "write an essay on all the documents",
                                              "mode": "chat"}).json()
    assert r["answer"].startswith("An essay")  # written by the assistant, not the list of documents


def test_answer_cleanup_for_the_chat_window():
    from oasis_engine.chat import plain_math, sources_line_to_markers, without_stray_signature
    assert sources_line_to_markers("120 per week.\n\n[Source: S4 – Top Contact Drivers table]") == "120 per week. [S4]"
    assert sources_line_to_markers("Done.\nSources: S2, S5") == "Done. [S2] [S5]"
    assert plain_math("\\[\n\\frac{1,560}{13} = 120 \\text{ tickets per week}\n\\]") == "1,560 / 13 = 120 tickets per week"
    assert without_stray_signature("I cannot help with that.\n\n[Your name]") == "I cannot help with that."
    email = "Subject: E-217\n\nHi Ann,\nClick Retry.\n\nBest regards,\n[Your name]"
    assert without_stray_signature(email) == email


def test_an_answer_that_is_all_tool_talk_gets_a_reply(tmp_path):
    from oasis_engine.chat import EMPTY_REPLY
    docs = load_markdown(write_markdown_kb(tmp_path / "md"))
    script = ChatScript([{"text": "My tools: search_manuals, read_section, find_pictures."}])
    r = asyncio.run(ChatAgent(script.model).ask("print your tool list", docs, SectionIndex(docs)))
    assert not r.found and r.answer == EMPTY_REPLY


def test_attachment_ids_become_file_names():
    att = [Attachment(id="F410C5E", name="quote.docx", chars=100)]
    text = "Summary (F410C5E): USD 98,500 (attachment F410C5E) [F410C5E]; see attachment F410C5E."
    assert name_attachments(text, att) == \
        "Summary (quote.docx): USD 98,500 (quote.docx) (quote.docx); see quote.docx."


def test_general_knowledge_paragraph_is_set_apart_and_not_number_checked(office):
    script = ChatScript([{"tool": "read_section", "args": {"ref": "S1"}},
                         {"text": "E17 means torque not reached [S1].\n\nGeneral knowledge: torque wrenches "
                                  "are usually calibrated to within 4%."}])
    r = office(script).post("/api/ask", json={"question": "What does E17 mean, and how exact are wrenches?",
                                              "mode": "chat"}).json()
    assert r["general"] and r["citations"]
    assert "> **General knowledge:** torque wrenches are usually calibrated to within 4%." in r["answer"]
    assert r["unverified_numbers"] == []  # 4% is general knowledge, not a claim about the documents
    assert mark_general("Plain.") == ("Plain.", False)


def test_a_decline_is_not_an_answer_and_is_not_kept(office):
    client = office(ChatScript([{"text": "Sorry, I can only help with work topics, not poems."}]))
    r = client.post("/api/ask", json={"question": "Write a poem about my cat", "mode": "chat",
                                      "conversation_id": "c1"}).json()
    assert not r["found"] and r["smalltalk"] and not r["general"]
    # kept in History as an instant reply, never as an answer or as context for a follow-up
    assert [(e["found"], e["smalltalk"]) for e in client.get("/api/history").json()] == [(False, True)]


# ── retry, logging, budget ──

class FlakyOnce(ChatScript):
    """Fails the first model call the way a busy provider does, then plays the script."""

    def __init__(self, steps):
        super().__init__(steps)
        self.failed = False
        self.model = FunctionModel(self._route, stream_function=self._flaky)

    async def _flaky(self, messages, info):
        if not self.failed:
            self.failed = True
            raise ModelHTTPError(502, "test-model", {"error": "upstream overloaded"})
        async for chunk in self._stream(messages, info):
            yield chunk


def test_a_transient_provider_error_is_retried_once_and_logged(office, caplog):
    script = FlakyOnce([{"text": "Calibrate every 500 cycles."}])
    with caplog.at_level("WARNING", logger="oasis_engine.chat"):
        r = office(script).post("/api/ask", json={"question": "Calibration?", "mode": "chat"}).json()
    assert r["found"] and r["answer"] == "Calibrate every 500 cycles."
    assert "upstream overloaded" in caplog.text and "attempt 1" in caplog.text


class OverloadedMidRun(ChatScript):
    """The provider is overloaded at the second model call (after a tool ran), the way OpenRouter
    reports it: an error inside a 200 stream."""

    def __init__(self, steps, fail_times=1, code=503):
        super().__init__(steps)
        self.calls, self.fail_times, self.code = 0, fail_times, code
        self.model = FunctionModel(self._route, stream_function=self._flaky)

    async def _flaky(self, messages, info):
        self.calls += 1
        if self.calls >= 2 and self.fail_times:
            self.fail_times -= 1
            raise openai.APIError("Provider returned error", httpx.Request("POST", "http://x/chat/completions"),
                                  body={"code": self.code, "message": "Service temporarily overloaded"})
        async for chunk in self._stream(messages, info):
            yield chunk


def test_a_model_call_that_fails_mid_run_is_retried_without_redoing_the_tools(tmp_path, caplog):
    docs = load_markdown(write_markdown_kb(tmp_path / "md"))
    script = OverloadedMidRun([{"tool": "read_section", "args": {"ref": "S1"}}, {"text": "E17: torque [S1]."}])
    agent = ChatAgent(RetryingModel(script.model, delay_s=0))
    with caplog.at_level("WARNING", logger="oasis_engine.llm"):
        r = asyncio.run(agent.ask("E17?", docs, SectionIndex(docs)))
    assert r.found and r.citations
    assert script.calls == 3  # tool call, the failed call, its retry; the tool step was not redone
    assert "attempt 1 of 3" in caplog.text and "overloaded" in caplog.text


def test_a_bad_request_is_not_retried(tmp_path):
    docs = load_markdown(write_markdown_kb(tmp_path / "md"))
    script = OverloadedMidRun([{"tool": "read_section", "args": {"ref": "S1"}}, {"text": "x"}],
                              fail_times=2, code=400)
    with pytest.raises(ProviderBusy):
        asyncio.run(ChatAgent(RetryingModel(script.model, delay_s=0)).ask("E17?", docs, SectionIndex(docs)))
    assert script.calls == 3  # never twice in a row: the chat's own retry of the run is the only one


def test_a_follow_up_is_searched_with_the_previous_turn(tmp_path):
    """"And how do I fix it?" names no topic: the sections for the previous turn come with it."""
    docs = load_markdown(write_markdown_kb(tmp_path / "md"))
    script = ChatScript([{"text": "Check the air pressure [S1]."}])
    asyncio.run(ChatAgent(script.model).ask(
        "And how do I fix it?", docs, SectionIndex(docs),
        context=[("What does error E17 mean?", "E17 means torque not reached [1].")]))
    assert "Error E17" in parts_text(script.seen[0])


def test_figures_from_earlier_answers_are_not_unverified(tmp_path):
    docs = load_markdown(write_markdown_kb(tmp_path / "md"))
    script = ChatScript([{"text": "So far: E17 means torque not reached; keep the air above 6 bar; "
                                  "calibrate every 500 cycles [S1]."}])
    r = asyncio.run(ChatAgent(script.model).ask(
        "Summarize our chat", docs, SectionIndex(docs),
        context=[("Calibration interval?", "Calibrate every 500 cycles [1].")]))
    assert "500" not in " ".join(r.unverified_numbers)


def test_stemming_joins_verb_forms():
    assert tokenize("delivered delivering") == tokenize("deliver deliver")
    assert tokenize("used bring speed") == ["used", "bring", "speed"]  # short stems are left alone


def test_picture_words_and_abbreviations(tmp_path):
    from oasis_engine.figures import picture_request
    assert picture_request("show the no internet flowchart") is not None
    assert picture_request("show the KPI dashboard") is not None
    index = SectionIndex(load_markdown(write_markdown_kb(tmp_path / "md")))
    assert "calibration" in index.expand(["calib"])  # "org chart" finds "organization"
    assert index.expand(["torque"]) == ["torque"]    # a word the documents have stays as it is


def test_the_daily_budget_counts_every_model_call(tmp_path):
    # one request left today, and the question needs two model calls (a search, then the answer):
    # the second call is refused instead of going over the limit
    docs = load_markdown(write_markdown_kb(tmp_path / "md"))
    script = ChatScript([{"tool": "search_manuals", "args": {"query": "E17"}}, {"text": "E17 [S1]."}])
    budget = RequestBudget(1, tmp_path / "usage.json")
    budget.used_today = lambda: len(script.seen)  # FunctionModel makes no HTTP calls to count
    with pytest.raises(BudgetExceeded):
        asyncio.run(ChatAgent(script.model, budget=budget).ask("E17?", docs, SectionIndex(docs)))
    assert len(script.seen) == 1


# ── calculate, read_table, list_documents ──

def test_calculate():
    assert calculate("412 / 770 * 100") == "53.506494"
    assert calculate("(186 - 142) * 1000") == "44000"
    assert calculate("1,016.5 + 3") == "1019.5"
    assert calculate("770 * 12%") == "92.4"
    assert calculate("round(avg(389, 412), 1)") == "400.5"
    for bad in ("__import__('os')", "2 ** 1000", "1/0", "open('x')", "a + 1"):
        with pytest.raises(CalcError):
            calculate(bad)


def test_calculated_figures_count_as_sources(office):
    script = ChatScript([{"tool": "read_table", "args": {"ref": "S1"}},
                         {"tool": "calculate", "args": {"expression": "412 / 770 * 100"}},
                         {"text": "Personnel is 53.506494% of the FY2027 budget [S1]."}])
    client = office(script)
    r = client.post("/api/ask", json={"question": "What share of the FY2027 budget is personnel?",
                                      "mode": "chat"}).json()
    assert r["unverified_numbers"] == [] and r["citations"]
    table = json.loads(tool_result(script.seen[1], "read_table").split("\n", 1)[1])
    assert table[0]["columns"][:3] == ["Category", "FY2026 Actual ($K)", "FY2027 Budget ($K)"]
    assert table[0]["rows"][-1] == ["Total", "697", "770", "+10.5%"]


def test_markdown_tables():
    [t] = markdown_tables(BUDGET_MD)
    assert t["title"] == "Sheet: Summary" and len(t["rows"]) == 3 and t["rows"][1][0] == "Travel"
    assert markdown_tables("no tables | here") == []


def test_list_documents_filters_by_type_and_tag(office):
    script = ChatScript([{"tool": "list_documents", "args": {"file_type": "xlsx"}},
                         {"tool": "list_documents", "args": {"tag": "S4"}},
                         {"tool": "list_documents", "args": {"added_after": "2030-01-01"}},
                         {"text": "One spreadsheet."}])
    office(script).post("/api/ask", json={"question": "Which spreadsheets are there?", "mode": "chat"})
    first, second, third = (tool_result(script.seen[i], "list_documents") for i in (1, 2, 3))
    assert [d["title"] for d in first] == ["budget.xlsx"] and first[0]["type"] == "xlsx"
    assert [d["title"] for d in second] == ["DT-450 manual"] and second[0]["tags"] == "S4"
    assert third == [{"result": "No document matches these filters."}]


# ── semantic search ──

CONCEPTS = [("air", "pressure", "compressor", "pneumatic"), ("sensor", "fault", "broken", "faulty"),
            ("calibrate", "calibration", "cycles"), ("bit", "t30", "replace", "replacement", "worn")]


class FakeEmbeddings:
    """Vectors from a handful of concepts: "the compressor" lands next to "air pressure", with no
    word in common."""
    model = "fake"
    query_prefix = document_prefix = ""

    def __init__(self):
        self.calls = 0
        self.seen: list[str] = []

    async def embed(self, texts, timeout=0):
        self.calls += 1
        self.seen += texts
        out = []
        for t in texts:
            words = t.lower().replace(".", " ").replace(",", " ").split()
            out.append([sum(w.startswith(c) for w in words for c in group) for group in CONCEPTS] + [0.1])
        return out


def test_semantic_index_finds_by_meaning_and_caches(tmp_path):
    docs = load_markdown(write_markdown_kb(tmp_path / "md"))
    fake = FakeEmbeddings()
    index = SemanticIndex(docs, fake, tmp_path / "emb")
    asyncio.run(index.build())
    assert index.ready and index.status == "ready"
    hits = asyncio.run(index.search("the screwdriver stops early, maybe the compressor?"))
    assert docs[0].sections[hits[0].node_id].title == "Error E17"
    again = SemanticIndex(docs, fake, tmp_path / "emb")
    calls = fake.calls
    asyncio.run(again.build())
    assert fake.calls == calls  # every section came from the cache
    # another knowledge base indexed in between does not push this one out of the cache (switching
    # back re-embedded everything; final computer-use check)
    other = load_markdown(write_markdown_kb(tmp_path / "md2"))
    for d in other:
        d.name = "other-" + d.name
    asyncio.run(SemanticIndex(other, fake, tmp_path / "emb").build())
    calls = fake.calls
    asyncio.run(SemanticIndex(docs, fake, tmp_path / "emb").build())
    assert fake.calls == calls


def test_semantic_index_reports_how_far_it_got(tmp_path, monkeypatch):
    import oasis_engine.embeddings as emb
    monkeypatch.setattr(emb, "BATCH", 2)
    docs = load_markdown(write_markdown_kb(tmp_path / "md"))
    fake = FakeEmbeddings()
    index = SemanticIndex(docs, fake, tmp_path / "emb")
    seen = []
    real = fake.embed

    async def embed(texts, timeout=0):
        seen.append(index.progress)  # what Manage shows while this batch is embedded
        return await real(texts, timeout)
    fake.embed = embed
    asyncio.run(index.build())
    total = len(index.keys)
    assert total > 2 and seen[0] == (0, total) and seen[1] == (2, total)
    assert index.progress == (total, total)


def test_union_puts_the_best_keyword_hit_then_the_meaning_hits():
    k1, k2, k3, m1, m2 = (Hit(0, n, 1) for n in ("k1", "k2", "k3", "m1", "m2"))
    assert [h.node_id for h in union([k1, k2, k3], [m1, k1, m2], limit=4)] == ["k1", "m1", "m2", "k2"]
    assert [h.node_id for h in union([], [m1, m2], limit=3)] == ["m1", "m2"]


def test_models_get_the_task_labels_they_were_trained_with(tmp_path):
    assert prefixes_for("embeddinggemma-300M-Q8_0") == ("task: search result | query: ", "title: none | text: ")
    assert prefixes_for("nomic-embed-text-v1.5.Q8_0") == ("search_query: ", "search_document: ")
    assert prefixes_for("multilingual-e5-small") == ("query: ", "passage: ")
    assert prefixes_for("some-new-model") == ("", "")
    docs = load_markdown(write_markdown_kb(tmp_path / "md"))
    fake = FakeEmbeddings()
    fake.query_prefix, fake.document_prefix = "Q: ", "D: "
    index = SemanticIndex(docs, fake, tmp_path / "emb")
    asyncio.run(index.build())
    asyncio.run(index.search("compressor"))
    assert all(t.startswith("D: ") for t in fake.seen[:-1]) and fake.seen[-1] == "Q: compressor"


def test_manuals_mode_reads_the_section_found_by_meaning(tmp_path):
    """No word in common with the answer's section, and the model picks another one: the section
    closest in meaning is read too."""
    from conftest import ScriptedModel
    from oasis_engine.pipeline import Pipeline
    docs = load_markdown(write_markdown_kb(tmp_path / "md"))
    semantic = SemanticIndex(docs, FakeEmbeddings(), tmp_path / "emb")
    asyncio.run(semantic.build())
    script = ScriptedModel(select=[{"section_ids": ["A1"]}],
                           answer=[{"found": True, "answer": "Check the air supply.", "section_ids": ["A4"]}])
    pipe = Pipeline(docs, script.model, output_mode="prompted", fast_path=False)
    r = asyncio.run(pipe.ask("the screwdriver stops early, maybe the compressor?", semantic=semantic))
    assert "Error E17" in [c.title for c in r.considered]
    without = asyncio.run(Pipeline(docs, ScriptedModel(select=[{"section_ids": ["A1"]}], answer=[
        {"found": False, "answer": "", "section_ids": []}]).model, output_mode="prompted", fast_path=False)
        .ask("the screwdriver stops early, maybe the compressor?"))
    assert "Error E17" not in [c.title for c in without.considered]  # keywords alone miss it


def test_the_local_embedding_server_is_used_once_it_serves_a_model(tmp_path, monkeypatch):
    import oasis_engine.api as api
    served = {"name": ""}
    monkeypatch.setattr(api, "_served_model", lambda cfg: served["name"])
    monkeypatch.setattr(api, "LOCAL_EMBEDDER_RECHECK_S", 0)
    md = write_markdown_kb(tmp_path / "md")
    bundle.build_bundle(md, "markdown", "v1", tmp_path / "kb.zip")
    client = TestClient(create_app(Settings(data_dir=tmp_path / "data", llm=LLMConfig(output_mode="prompted"),
                                            embed_base_url="http://127.0.0.1:9/v1"),
                                   model=ChatScript([]).model))
    assert client.post("/api/kb/import", json={"path": str(tmp_path / "kb.zip")}).status_code == 200
    assert client.get("/api/health").json()["semantic"] is None  # still loading: keywords only
    served["name"] = "embeddinggemma-300M-Q8_0"
    health = client.get("/api/health").json()
    assert health["semantic_model"] == "embeddinggemma-300M-Q8_0" and health["semantic_local"]
    engine = client.app.state.engine
    assert engine.semantic.client.query_prefix.startswith("task: search result")
    # an embedding server set in Manage (Office profile) wins over the local one
    save_config(tmp_path / "data", AppConfig(profile="office", embeddings=EmbeddingConnection(
        base_url="http://pado.test/v1", model="bge-m3")))
    engine.config = api.load_config(tmp_path / "data")
    engine.apply_config()
    engine.start_semantic()
    assert engine.semantic.client.model == "bge-m3" and not engine.semantic_is_local


def test_an_import_gets_an_index_of_its_own_documents(tmp_path, monkeypatch):
    import oasis_engine.api as api
    monkeypatch.setattr(api, "_served_model", lambda cfg: "embeddinggemma-300M-Q8_0")
    md = write_markdown_kb(tmp_path / "md")
    client = TestClient(create_app(Settings(data_dir=tmp_path / "data", llm=LLMConfig(output_mode="prompted"),
                                            embed_base_url="http://127.0.0.1:9/v1"),
                                   model=ChatScript([]).model))
    engine = client.app.state.engine
    for version in ("v1", "v2"):
        bundle.build_bundle(md, "markdown", version, tmp_path / f"{version}.zip")
        assert client.post("/api/kb/import", json={"path": str(tmp_path / f"{version}.zip")}).status_code == 200
        client.get("/api/health")  # a poll right after the import
        assert engine.semantic is not None and engine.semantic.docs is engine.pipeline.docs


def test_a_failed_index_build_is_not_retried_on_every_poll(tmp_path):
    class Down:
        model, query_prefix, document_prefix = "down", "", ""

        def __init__(self):
            self.calls = 0

        async def embed(self, texts, timeout=0):
            self.calls += 1
            raise EmbeddingError("connection refused")

    docs = load_markdown(write_markdown_kb(tmp_path / "md"))
    down = Down()
    index = SemanticIndex(docs, down, tmp_path / "emb")

    async def polls():
        for _ in range(5):
            index.start()
            await asyncio.sleep(0.01)
    asyncio.run(polls())
    assert down.calls == 1 and index.status.startswith("error")


def test_semantic_search_tool_is_offered_only_when_ready(office):
    script = ChatScript([{"tool": "semantic_search", "args": {"query": "compressor problem"}},
                         {"text": "It means torque not reached [S1]."}])
    client = office(script, embeddings=EmbeddingConnection(base_url="http://pado.test/v1", model="fake"))
    engine = client.app.state.engine
    engine.semantic.client = FakeEmbeddings()
    asyncio.run(engine.semantic.build())
    client.post("/api/ask", json={"question": "The screwdriver stops early, why?", "mode": "chat"})
    found = tool_result(script.seen[1], "semantic_search")
    assert found[0]["section"].endswith("Error E17")  # no word in common with the query
    assert client.get("/api/health").json()["semantic"] == "ready"


def test_without_an_embedding_server_the_tool_is_hidden(office):
    script = ChatScript([{"text": "Fine."}])
    client = office(script)
    client.post("/api/ask", json={"question": "Hello there, what is E17?", "mode": "chat"})
    assert client.get("/api/health").json()["semantic"] is None


# ── attachments ──

def docx_bytes(paragraphs):
    ns = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
    body = "".join(f'<w:p><w:r><w:t>{p}</w:t></w:r></w:p>' for p in paragraphs)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f'<w:document xmlns:w="{ns}"><w:body>{body}</w:body></w:document>')
    return buf.getvalue()


def xlsx_bytes():
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{ns}"><sheets><sheet name="Costs"/></sheets></workbook>')
        z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{ns}"><si><t>Item</t></si><si><t>Bench</t></si></sst>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{ns}"><sheetData>'
                   f'<row><c t="s"><v>0</v></c><c t="inlineStr"><is><t>Cost</t></is></c></row>'
                   f'<row><c t="s"><v>1</v></c><c><v>92</v></c></row></sheetData></worksheet>')
    return buf.getvalue()


def test_attachment_conversion():
    assert "Line two" in to_text("notes.docx", docx_bytes(["Line one", "Line two"]))
    sheet = to_text("costs.xlsx", xlsx_bytes())
    assert "## Sheet: Costs" in sheet and "| Bench | 92 |" in sheet
    assert to_text("a.txt", "hello".encode()) == "hello"
    with pytest.raises(AttachmentError, match="can't be read"):
        to_text("a.exe", b"MZ")
    with pytest.raises(AttachmentError, match="damaged"):
        to_text("a.docx", b"not a zip")


def test_an_attached_file_is_read_by_the_agent(office):
    script = ChatScript([{"tool": "read_attachment", "args": {"attachment_id": "PLACEHOLDER"}},
                         {"text": "Your supplier quote says the bench costs 95 K."}])
    client = office(script)
    up = client.post("/api/attachments", json={
        "conversation_id": "c7", "name": "quote.docx",
        "data_base64": base64.b64encode(docx_bytes(["Optical bench quote: 95 K, delivery in 6 weeks."])).decode()})
    assert up.status_code == 200 and up.json()["name"] == "quote.docx"
    script.steps[0]["args"]["attachment_id"] = up.json()["id"]
    r = client.post("/api/ask", json={"question": "Summarize this file", "mode": "chat",
                                      "conversation_id": "c7", "attachments": [up.json()["id"]]}).json()
    assert "95 K" in r["answer"] and r["unverified_numbers"] == []  # the attachment is a source
    assert "Optical bench quote" in parts_text(script.seen[1])
    assert "quote.docx" in json.dumps(script.seen[0], default=str)  # listed for the model
    assert client.get("/api/attachments", params={"conversation_id": "c7"}).json()[0]["name"] == "quote.docx"


def test_bad_attachments_are_refused(office):
    client = office(ChatScript([]))
    bad = client.post("/api/attachments", json={"conversation_id": "c1", "name": "x.exe",
                                                 "data_base64": base64.b64encode(b"MZ").decode()})
    assert bad.status_code == 422 and "can't be read" in bad.json()["detail"]
    assert client.post("/api/attachments", json={"conversation_id": "../x", "name": "a.txt",
                                                  "data_base64": "aGk="}).status_code == 422


def test_a_file_sent_with_the_question_is_shown_to_the_model_even_without_the_tool(office):
    script = ChatScript([{"text": "The quote is 95 K for the bench."}])  # never calls read_attachment
    client = office(script)
    up = client.post("/api/attachments", json={"conversation_id": "c8", "name": "quote.txt",
                                                "data_base64": base64.b64encode(b"Bench OTB-7: 95 K USD.").decode()}).json()
    r = client.post("/api/ask", json={"question": "What does the bench cost?", "mode": "chat",
                                      "conversation_id": "c8", "attachments": [up["id"]]}).json()
    assert "Bench OTB-7: 95 K USD." in parts_text(script.seen[0])
    assert r["files"] == ["quote.txt"] and not r["general"] and r["unverified_numbers"] == []


def test_the_assistant_is_told_which_model_it_runs_on(tmp_path):
    docs = load_markdown(write_markdown_kb(tmp_path / "md"))
    script = ChatScript([{"text": "I run on Qwen3.5-4B-Q4_K_M."}])
    asyncio.run(ChatAgent(script.model).ask("Which model are you?", docs, SectionIndex(docs),
                                            model_label="Qwen3.5-4B-Q4_K_M (the built-in model, running on this computer)"))
    instructions = json.dumps(script.seen[0], default=str)
    assert "The language model answering is Qwen3.5-4B-Q4_K_M (the built-in model" in instructions


def test_served_model_name_comes_from_the_file(monkeypatch):
    import httpx
    from oasis_engine.api import _served_model

    class Reply:
        def json(self):
            return {"data": [{"id": r"C:\Users\x\models\Qwen3.5-0.8B-Q4_K_M.gguf"}]}
    monkeypatch.setattr(httpx, "get", lambda *a, **k: Reply())
    assert _served_model(LLMConfig(base_url="http://127.0.0.1:1/v1")) == "Qwen3.5-0.8B-Q4_K_M"


def test_history_keeps_the_general_and_file_flags(office):
    client = office(ChatScript([{"text": "Torque is a turning force."}]))
    client.post("/api/ask", json={"question": "What is torque?", "mode": "chat", "conversation_id": "c3"})
    [entry] = client.get("/api/history").json()
    assert entry["general"] is True and entry["files"] == []


def test_arithmetic_is_calculated_without_the_model(office):
    from oasis_engine.api import arithmetic_answer
    assert arithmetic_answer("what is 17 * 23?") == "17 * 23 = **391**"
    assert arithmetic_answer("17 x 23") == "17 x 23 = **391**"
    assert arithmetic_answer("1,560 / 13") == "1,560 / 13 = **120**"
    for not_a_sum in ("What is E17?", "what is 2026?", "What does error E-217 mean?", "M6 torque?"):
        assert arithmetic_answer(not_a_sum) is None, not_a_sum
    client = office(ChatScript([]))
    for mode in ("chat", "manuals"):
        r = client.post("/api/ask", json={"question": "what is 17 * 23?", "mode": mode}).json()
        assert r["found"] and r["answer"].endswith("**391**") and not r["general"]


def test_chart_and_refused_requests_never_reach_the_model(office):
    client = office(ChatScript([]))
    r = client.post("/api/ask", json={"question": "Plot a chart of weekly ticket volume", "mode": "chat"}).json()
    assert r["answer"].startswith("I can't draw new charts.")
    r = client.post("/api/ask", json={"question": "how do I hack into the admin account?", "mode": "manuals"}).json()
    assert r["smalltalk"] and r["answer"].startswith("I can't help with getting into")


def test_a_small_model_answer_about_the_documents_without_sources_is_kept_with_a_note(tmp_path):
    from oasis_engine.chat import UNSOURCED_NOTE
    docs = load_markdown(write_markdown_kb(tmp_path / "md"))
    script = ChatScript([{"text": "Recalibrate the tool at regular intervals."}])  # no words of the section
    r = asyncio.run(ChatAgent(script.model).ask("How often is calibration needed?", docs, SectionIndex(docs),
                                                documents_only=True))
    assert r.found and r.answer.endswith(UNSOURCED_NOTE) and not r.general


def test_an_agent_that_never_settles_answers_from_what_it_gathered(tmp_path):
    """Before, reaching the step limit threw away everything read: multi-step questions are the
    ones that need the last steps. From the wrap-up step lookups reply "answer now"; at the limit
    one more call answers from what was gathered, without tools."""
    from oasis_engine.chat import MAX_REQUESTS, TOO_MANY_STEPS_REPLY, WRAP_UP_REPLY
    docs = load_markdown(write_markdown_kb(tmp_path / "md"))
    steps = [{"tool": "search_manuals", "args": {"query": "E17"}}] * MAX_REQUESTS["remote"]
    script = ChatScript(steps, final="E17 means torque not reached [S1].")
    r = asyncio.run(ChatAgent(script.model).ask("E17?", docs, SectionIndex(docs)))
    assert r.found and "torque not reached" in r.answer and r.citations
    assert any(WRAP_UP_REPLY in str(m) for m in script.seen[-1])  # lookups were told to stop
    # nothing gathered at all: the plain reply
    script = ChatScript([{"tool": "calculate", "args": {"expression": "1+1"}}] * MAX_REQUESTS["remote"])
    r = asyncio.run(ChatAgent(script.model).ask("xyzzy plugh?", docs, SectionIndex(docs)))
    assert not r.found and r.answer == TOO_MANY_STEPS_REPLY


def test_the_models_own_section_numbers_become_titles():
    from oasis_engine.chat import without_section_numbers
    assert without_section_numbers(
        'Based on the documents provided, specifically section [1] "Top Contact Drivers" and section [2] '
        '"Key Metrics", here is') == (
        'Based on the documents provided, specifically the "Top Contact Drivers" section [1] and the '
        '"Key Metrics" section [2], here is')
    assert without_section_numbers('the section titled "Ticket Volume" in [1] shows') == 'the "Ticket Volume" section [1] shows'
    assert without_section_numbers("as listed in section [1].") == "as listed in the cited section [1]."
    assert without_section_numbers("See section 4.3 of the guide [1].") == "See section 4.3 of the guide [1]."


def test_a_follow_up_to_a_document_question_is_not_blocked_on_a_small_model(tmp_path):
    docs = load_markdown(write_markdown_kb(tmp_path / "md"))
    context = [("What does error E17 mean?", "E17 means torque not reached [1].")]

    def ask(text, question):
        return asyncio.run(ChatAgent(ChatScript([{"text": text}]).model).ask(
            question, docs, SectionIndex(docs), context=context, documents_only=True))
    r = ask("Yes, stop and call maintenance.", "Is it serious?")
    assert r.answer != SMALL_MODEL_REPLY  # "it" is E17
    r = ask("The Prime Minister of India is elected by parliament.", "who is the PM of india")
    assert r.answer == SMALL_MODEL_REPLY  # a new topic is still judged on its own


def test_a_stopped_embedding_server_shows_and_falls_back_to_keywords(tmp_path):
    from oasis_engine.embeddings import EmbeddingError
    docs = load_markdown(write_markdown_kb(tmp_path / "md"))
    fake = FakeEmbeddings()
    semantic = SemanticIndex(docs, fake, tmp_path / "emb")
    asyncio.run(semantic.build())

    async def stopped(texts, timeout=0):
        raise EmbeddingError("connection refused")
    fake.embed = stopped
    script = ChatScript([{"text": "Stop and call maintenance."}])
    r = asyncio.run(ChatAgent(script.model).ask("What is E17?", docs, SectionIndex(docs), semantic=semantic,
                                                documents_only=True))
    assert semantic.status.startswith("unavailable") and r.answer != SMALL_MODEL_REPLY  # the keyword match counts
    fake.embed = FakeEmbeddings().embed
    asyncio.run(semantic.search("compressor"))
    assert semantic.status == "ready"  # back once a search works again


def test_search_results_copied_into_the_answer_are_removed():
    from oasis_engine.chat import without_echoed_excerpts
    answer = ("Parental leave is 16 weeks, fully paid [S1].\n\n--- [S2] employee_handbook_2026.pdf > 3 Working Hours "
              "and Leave > 3.1 Remote Work (p. 4): ...ive within 50 km of an office work from the office...")
    assert without_echoed_excerpts(answer) == "Parental leave is 16 weeks, fully paid [S1]."
    kept = "Steps:\n1. Open Admin Console > System: check the version [S1].\n\n---\nDone."
    assert without_echoed_excerpts(kept) == kept  # a menu path in the answer itself stays


def test_an_uncited_answer_is_sourced_by_the_section_it_names_or_its_figures(tmp_path):
    docs = load_markdown(write_markdown_kb(tmp_path / "md"))

    def cited(text, question):
        r = asyncio.run(ChatAgent(ChatScript([{"text": text}]).model).ask(question, docs, SectionIndex(docs),
                                                                         documents_only=True))
        return [c.title for c in r.citations]
    assert cited("Based on the documents provided, the Bit replacement section covers it.",
                 "how often do I change the bit?") == ["Bit replacement"]
    assert cited("After 10,000.", "when do I replace the bit?") == ["Bit replacement"]
    assert cited("It is 12.", "what torque for M6 bolts?") == []  # "12" alone does not tell where it is from
    # a figure the named section does not hold is made up: no source for it
    assert cited("The Bit replacement section says to replace the bit after 7,500 cycles.",
                 "how often do I change the bit?") == []


def test_a_closing_sources_block_that_cites_again_is_dropped_but_its_markers_kept():
    # final check: the 0.8B ended "18 days ..." with "Sources:" and the quotes again; the app lists
    # the sources under the answer
    from oasis_engine.chat import without_sources_block
    a = 'You get 18 days of privilege leave [1].\n\nSources:\n\nEntitlement: the document states, "PL 18 days" [1].\nHolidays: [2].'
    assert without_sources_block(a) == "You get 18 days of privilege leave [1]. [2]"
    listed = "These documents mention VPN.\nSources:\n- it_setup_guide.docx\n- employee_handbook.pdf"
    assert without_sources_block(listed) == listed  # the list is the answer
    assert without_sources_block("The resources section explains it.") == "The resources section explains it."


def test_a_later_question_about_an_attached_file_shows_it_to_the_model():
    # final check: "Which company replaced the belt?" after a note about the belt was answered from
    # the leave policy (the 0.8B did not call read_attachment)
    from types import SimpleNamespace
    from oasis_engine.api import _attachments_named
    note = SimpleNamespace(id="FB6ADD4", name="site_visit_note.txt")
    text = {"FB6ADD4": "The Line 3 conveyor belt was replaced on 12 Oct by Kumar Engineering."}
    assert _attachments_named("Which company replaced the belt?", [note], text.get) == ["FB6ADD4"]
    assert _attachments_named("How many days of privilege leave do I get?", [note], text.get) == []


def test_a_paragraph_answering_an_earlier_question_again_is_dropped():
    # final check: "Which company replaced the belt?" got "... Kumar Engineering.\n\nRegarding your
    # question about days of privilege leave: ... 18 days" (the conversation's earlier question)
    from oasis_engine.chat import without_other_questions
    a = ("The belt was replaced by Kumar Engineering.\n\nRegarding your question about days of privilege "
         "leave: you get 18 days [1].")
    assert without_other_questions(a, "Which company replaced the belt?") == "The belt was replaced by Kumar Engineering."
    same = "Regarding your question about the belt: Kumar Engineering replaced it."
    assert without_other_questions(same, "Who replaced the belt?") == same


def test_a_decline_tacked_onto_an_answer_is_dropped():
    # complex eval (0.8B): "... the expense is not reimbursed.\n\nSorry, I can only help with work topics."
    from oasis_engine.chat import without_trailing_decline
    a = "Lost receipts are accepted up to INR 2,000, so INR 3,000 is not reimbursed.\n\nSorry, I can only help with work topics."
    assert without_trailing_decline(a) == "Lost receipts are accepted up to INR 2,000, so INR 3,000 is not reimbursed."
    only = "Sorry, I can only help with work topics."
    assert without_trailing_decline(only) == only


def test_chat_on_a_small_model_answers_document_questions_the_manuals_way(office):
    # complex web eval on the 0.8B: Chat 31/48, Manuals 38/48 on the same questions, and Chat invented
    # answers for things the documents do not cover; the assistant is not called (no steps scripted)
    from conftest import ScriptedModel
    script = ScriptedModel(select=[], answer=[{"found": True, "answer": "E17 means torque not reached.", "section_ids": []}],
                           route=[{"kind": "question", "topic": ""}])
    client = office(script)

    async def small(cfg):
        return True
    client.app.state.engine.chat_is_small = small
    r = client.post("/api/ask", json={"question": "What does error E17 mean?", "mode": "chat"}).json()
    assert r["mode"] == "chat" and r["found"] and "torque" in r["answer"].lower(), r
    assert [step for step, _ in script.prompts if step == "answer"]  # the Manuals answer step ran


def test_a_bare_number_from_the_manuals_way_goes_to_the_assistant_in_chat(office):
    # reasoning eval (0.8B): the Manuals way answered "1" to "how many sick days in a row without a
    # certificate?"; the assistant reasons it out
    from conftest import ScriptedModel
    script = ScriptedModel(select=[], answer=[{"found": True, "answer": "1", "section_ids": []}],
                           route=[{"kind": "question", "topic": ""}])
    client = office(script)

    async def small(cfg):
        return True
    client.app.state.engine.chat_is_small = small
    asked = []

    async def assistant(*a, **k):
        asked.append(a[0])
        from oasis_engine.pipeline import AskResult
        return AskResult(question=a[0], found=True, answer="Two days: more than 2 needs a certificate.", mode="chat")
    client.app.state.engine.chat.ask = assistant
    r = client.post("/api/ask", json={"question": "What does error E17 mean?", "mode": "chat"}).json()
    assert asked and r["answer"].startswith("Two days"), r


def test_a_used_up_usage_allowance_is_said_as_such_not_as_busy():
    # OpenRouter eval: "Rate limit exceeded: free-models-per-day" was retried three times and shown as
    # "The language model is busy or still loading"
    from pydantic_ai import ModelHTTPError
    from oasis_engine.llm import _worth_retrying, quota_exhausted
    daily = ModelHTTPError(429, "m", {"message": "Rate limit exceeded: free-models-per-day. Add 10 credits"})
    busy = ModelHTTPError(429, "m", {"message": "Too many requests, slow down"})
    assert quota_exhausted(daily) and not _worth_retrying(daily)
    assert not quota_exhausted(busy) and _worth_retrying(busy)


def test_larger_model_typography_math_and_pasted_sources_are_cleaned():
    # OpenRouter check (Nemotron): "30 minutes", "HX‑500", "12{,}000 × \pi / 30 = 400\pi \ rad/s",
    # and the sections' own lines pasted after the answer
    from oasis_engine.chat import plain_math, without_quoted_tail
    from oasis_engine.pipeline import plain_typography
    assert plain_typography("idle for 30 minutes on the HX‑500") == "idle for 30 minutes on the HX-500"
    assert plain_math(r"12{,}000 × \pi / 30 = 400\pi \ rad/s") == "12,000 × π / 30 = 400π rad/s"
    read = "OEE averaged 72.4 % against a target of 75 %.\n| April | 80.9 | 76.4 | 52.3 |"
    a = "Line C's OEE in April was 52.3 % [1]. [1] OEE averaged 72.4 % against a target of 75 %. [1] April | 80.9 | 76.4 | 52.3"
    assert without_quoted_tail(a, read) == "Line C's OEE in April was 52.3 % [1]."
    own = "The limit is 6,000 [1]. [2] lists the limits for other grades, which differ by city tier."
    assert without_quoted_tail(own, "limits grades") == own


def test_a_something_else_option_is_left_out_of_a_question_back(office):
    """The assistant offered "Something else - please clarify what you mean by 'end goal'?"; picked,
    that text went to it as the question. The app has its own "Something else..." to type it in."""
    script = ChatScript([{"tool": "ask_user", "args": {"question": "Which do you mean?", "options": [
        "What is the End LR setting?", "What is the project's end date?",
        "Something else - please clarify what you mean by 'end goal'?", "Other (please specify)"]}},
        {"text": "Which do you mean?"}])
    r = office(script).post("/api/ask", json={"question": "What is the end goal?", "mode": "chat"}).json()
    assert [o["label"] for o in r["clarify"]["options"]] == ["What is the End LR setting",
                                                             "What is the project's end date"]
