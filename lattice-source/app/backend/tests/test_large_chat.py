"""Chat mode sized to the model (chat.Room): a large model (Qwen 3.5 27B, 32k+ context) is given
whole sections and long pages; a small one passages, as before. Also: the second part of a two-part
question searched with the first part's subject, single-dollar maths, the context window asked from
the server, and a large built-in model treated as a server model."""
import asyncio

import httpx

from conftest import ChatScript, write_markdown_kb
from oasis_engine import api as api_mod
from oasis_engine.chat import ChatAgent, Room, plain_math, room_for, subject_of
from oasis_engine.llm import LLMConfig
from oasis_engine.loaders import load_markdown, parse_markdown
from oasis_engine.search import SectionIndex

LONG = "Week | Tickets\n--- | ---\n" + "\n".join(f"W{n} | {1000 + n}" for n in range(1, 300))


def test_room_grows_with_the_context_window():
    small, local = room_for(8192, "remote"), room_for(8192, "local")
    assert not small.large and not local.large and local.max_requests < small.max_requests
    large = room_for(32_768, "remote")
    assert large.large and large.passage_chars >= 8_000 and large.read_chars >= 12_000
    huge = room_for(262_144, "remote")
    assert huge.read_chars > large.read_chars and huge.passage_chars <= 24_000
    assert room_for(0, "remote").large  # a server model whose window is not known: assumed large
    assert room_for(200_000, "local") == Room(max_requests=local.max_requests, wrap_up_at=local.wrap_up_at)


def _doc():
    md = ("# Support review\n\n## Ticket spike\n\nThe Q3 spike came from the R7 2.9.0 rollout: error E-217.\n\n"
          "## Customer feedback\n\nAgents were praised; customers asked how to fix it faster.\n\n"
          "## Weekly volume\n\n" + LONG + "\n\n# R7 guide\n\n## Firmware update failure\n\n"
          "E-217 means the image failed its checksum. Agents fix it with Retry download, then KA-0388.\n")
    return parse_markdown(md, "kb", "kb.md")


def test_large_models_get_the_best_sections_whole():
    doc = _doc()
    script = ChatScript([{"text": "Done."}])
    asyncio.run(ChatAgent(script.model).ask("What were the weekly ticket volumes?", [doc], SectionIndex([doc]),
                                            context_tokens=32_768))
    prompt = str(script.seen[0])
    assert "W299 | 1299" in prompt  # the whole table, not a 900-character passage
    script = ChatScript([{"text": "Done."}])
    asyncio.run(ChatAgent(script.model).ask("What were the weekly ticket volumes?", [doc], SectionIndex([doc]),
                                            tier="local"))
    assert "W299 | 1299" not in str(script.seen[0])


def test_second_part_is_searched_with_the_first_parts_subject():
    doc = _doc()
    hits = SectionIndex([doc]).search("What caused the ticket spike in Q3")
    assert "E-217" in subject_of([doc], hits, "What caused the ticket spike in Q3")
    script = ChatScript([{"text": "Done."}])
    asyncio.run(ChatAgent(script.model).ask("What caused the ticket spike in Q3 and how should agents fix it?",
                                            [doc], SectionIndex([doc]), tier="local"))
    assert "Firmware update failure" in str(script.seen[0])


def test_single_dollar_maths_loses_its_dollars():
    assert plain_math("Weeks late: $14 / 7 = 2$ full weeks") == "Weeks late: 14 / 7 = 2 full weeks"
    assert plain_math("$9,20,000 × 1.5% × 2 = 27,600$") == "9,20,000 × 1.5% × 2 = 27,600"
    assert plain_math("It costs $5 to $10 per user.") == "It costs $5 to $10 per user."


def test_context_window_is_asked_from_the_server(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        req = httpx.Request("GET", url)
        if url.endswith("/props"):
            return httpx.Response(200, json={"default_generation_settings": {"n_ctx": 65536}}, request=req)
        return httpx.Response(404, json={}, request=req)
    monkeypatch.setattr(api_mod.httpx, "get", fake_get)
    assert api_mod._served_context(LLMConfig(base_url="http://127.0.0.1:8080/v1")) == 65536

    def vllm(url, headers=None, timeout=None):
        req = httpx.Request("GET", url)
        if url.endswith("/models"):
            return httpx.Response(200, json={"data": [{"id": "qwen3.5-27b", "max_model_len": 40960}]}, request=req)
        return httpx.Response(404, text="no", request=req)
    monkeypatch.setattr(api_mod.httpx, "get", vllm)
    assert api_mod._served_context(LLMConfig(base_url="http://gpu.lan:8000/v1", model="qwen3.5-27b")) == 40960


def test_a_large_built_in_model_is_given_what_a_server_model_is(tmp_path, monkeypatch):
    from oasis_engine.api import Engine
    from oasis_engine.settings import Settings
    engine = Engine(Settings(data_dir=tmp_path, llm=LLMConfig()))
    for name, tier in (("Qwen3.5-27B-Q4_K_M", "remote"), ("Qwen3.5-4B-Q4_K_M", "local")):
        monkeypatch.setattr(engine, "_builtin_model_file_now", lambda cfg, n=name: n)
        assert engine.chat_tier() == tier


def test_markdown_kb_still_answers(tmp_path):
    docs = load_markdown(write_markdown_kb(tmp_path / "md"))
    script = ChatScript([{"text": "E17 means torque not reached [S1]."}])
    r = asyncio.run(ChatAgent(script.model).ask("E17?", docs, SectionIndex(docs), context_tokens=32_768))
    assert r.found and r.citations


def test_references_with_a_place_in_them_count():
    from oasis_engine.chat import normalize_refs
    assert normalize_refs("97% [S3, Part 2, Clause 2.1].") == "97% [S3]."
    assert normalize_refs("[S2: Delivery record] and [S2, S4]") == "[S2] and [S2] [S4]"


def test_headings_are_kept_though_they_repeat_earlier_words():
    from oasis_engine.loops import without_repeated_sentences
    text = ("1. **Firmware update failures (Error E-217):** the rollout caused failed updates.\n\n"
            "### Part 2: How should agents fix it?\n\n**For Firmware Update Failure (Error E-217):**\n"
            "* Click Retry download.")
    assert without_repeated_sentences(text) == text
    # a sentence said twice is still dropped
    assert without_repeated_sentences("Requests longer than 5 working days need HOD approval. "
                                      "Requests longer than 5 working days need HOD approval.") == \
        "Requests longer than 5 working days need HOD approval."


def test_a_section_that_names_another_brings_it_along():
    from oasis_engine.chat import referenced_sections
    md = ("# Manual\n\n## 5 Operation\n\n### 5.2 Spindle warm-up\n\nRun program O9000 at the start of every shift.\n\n"
          "## 9 Warranty\n\nSpindle bearings are covered for 12 months only if the warm-up rules in 5.2 have been "
          "followed. Torque 12 Nm in 3 days.\n")
    doc = parse_markdown(md, "m", "m.md")
    warranty = next(n for n, s in doc.sections.items() if s.title == "9 Warranty")
    assert [doc.sections[n].title for n in referenced_sections(doc, warranty)] == ["5.2 Spindle warm-up"]
    script = ChatScript([{"text": "Done."}])
    asyncio.run(ChatAgent(script.model).ask("Why was the bearing not covered by warranty?", [doc], SectionIndex([doc]),
                                            context_tokens=32_768))
    assert "O9000" in str(script.seen[0])


def test_worked_out_figures_are_not_flagged_as_missing():
    from oasis_engine.numbers import unverified_numbers
    answer = ("Penalty: 9,20,000 × 3% = 27,600 INR (1.5% × 2 weeks)\nPenalty: 7,80,000 × 1.5% = 11,700 INR\n"
              "Penalty: 6,50,000 × 1.5% = 9,750 INR\nTotal: 27,600 + 11,700 + 9,750 = 49,050 INR\nAlso 12,345 INR.")
    sources = "Order values INR 9,20,000, 7,80,000 and 6,50,000; 1.5% of the order value per full week."
    assert unverified_numbers(answer, sources, "How much penalty?") == ["12,345"]
    # a wrong sum is still flagged
    assert "2,76,000" in unverified_numbers("9,20,000 × 3% = 2,76,000", sources, "penalty?")


# ── general knowledge (26 Oct: "too fixated on the documents") ──

def _nimbus():
    md = ("# X2 guide\n\n## 5 Troubleshooting\n\nE2: Power stealing unstable. Install the PAK-2 adapter or connect a "
          "C wire. The C wire terminal is next to R; 24 VAC.\n\n## 6 Wiring\n\nConnect R, W, Y and the C wire.\n")
    return parse_markdown(md, "x2", "x2.md")


def _ask(question, reply):
    doc = _nimbus()
    return asyncio.run(ChatAgent(ChatScript([{"text": reply}]).model).ask(question, [doc], SectionIndex([doc]),
                                                                         context_tokens=32_768))


def test_asked_for_general_knowledge_it_is_answered_not_replaced():
    r = _ask("Apart from the documents, what is a C wire in general?",
             "A C wire (common wire) is the return leg of the 24 VAC transformer circuit; it gives a thermostat "
             "continuous power.")
    assert r.found and r.answer.startswith("A C wire (common wire)") and r.general and not r.citations


def test_the_hedge_before_a_general_explanation_goes():
    r = _ask("Apart from the documents, what is a C wire?",
             "The documents do not define a C wire.\n\nGeneral knowledge: A C wire is the common return wire of "
             "the 24 VAC circuit.")
    assert "do not define" not in r.answer and "**General knowledge:**" in r.answer


def test_a_general_definition_keeps_its_label_beside_document_facts():
    r = _ask("What is power stealing in thermostats generally, and why does the X2 show E2?",
             "General knowledge: Power stealing means a thermostat draws a small current through the call wire "
             "to power itself.\n\nE2 means power stealing unstable; install the PAK-2 adapter or connect a C "
             "wire [S1].")
    assert "**General knowledge:** Power stealing means" in r.answer and r.citations


def test_an_unlabelled_in_general_paragraph_is_labelled():
    r = _ask("How do 5 GHz and 2.4 GHz differ, and what does the guide say about E2?",
             "In general, 5 GHz is faster but has a shorter range.\n\nE2: power stealing unstable; install the PAK-2 adapter [S1].")
    assert r.answer.startswith("> **General knowledge:** In general, 5 GHz")
