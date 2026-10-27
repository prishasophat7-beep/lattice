"""Search quality: word forms, meaning-first merging, and the small reliability fixes."""
from oasis_engine.embeddings import fuse
from oasis_engine.numbers import unverified_numbers
from oasis_engine.search import Hit, SectionIndex, tokenize
from oasis_engine.loaders import load_markdown


def test_related_word_forms_share_a_stem():
    pairs = [("deliver", "delivery"), ("maintain", "maintenance"), ("level", "leveling"),
             ("plan", "planning"), ("assembly", "assemblies"), ("bolt", "bolts")]
    for a, b in pairs:
        assert tokenize(a) == tokenize(b), (a, b)
    assert tokenize("software") == ["software"]  # a stem is never cut into a non-word for display
    assert tokenize("E-217")[0] == "e-217"


class _Index:
    def __init__(self, code): self.code = code
    def names_code(self, question): return self.code


def _hits(*ids): return [Hit(0, i, 1.0) for i in ids]


def test_meaning_comes_first_unless_the_question_names_a_code():
    keyword, meaning = _hits("k1", "k2"), _hits("m1", "m2")
    assert [h.node_id for h in fuse("q", _Index(False), keyword, meaning)][:2] == ["m1", "m2"]
    assert [h.node_id for h in fuse("q", _Index(True), keyword, meaning)][:2] == ["k1", "m1"]


def test_a_code_few_sections_have_is_recognised(markdown_kb):
    index = SectionIndex(load_markdown(markdown_kb))
    assert index.names_code("What does error E17 mean?")
    assert not index.names_code("how do I reset a password")


def test_a_unit_named_in_a_table_title_verifies_a_bare_figure():
    table = "Downtime hours by cause\n| Line | Total |\n| C | 196 |"
    assert unverified_numbers("Line C had 196 hours of downtime.", table) == []
    assert unverified_numbers("Line C had 196 hours of downtime.", "Total 150 and 196 in cell") == ["196 hours"]


def test_regenerate_samples_only_the_step_that_writes_the_answer(markdown_kb):
    import asyncio
    from conftest import ScriptedModel
    from oasis_engine.llm import REGENERATING
    from oasis_engine.pipeline import Pipeline
    pipe = Pipeline(load_markdown(markdown_kb), ScriptedModel(select=[], answer=[]).model, output_mode="prompted")
    seen = {}
    REGENERATING.set(True)

    class Spy:
        async def __aenter__(self): raise RuntimeError("stop")
        async def __aexit__(self, *a): return False

    for name in ("selector", "answerer", "router"):
        agent = getattr(pipe, name)
        agent.iter = lambda *a, _n=name, **k: (seen.__setitem__(_n, k["model_settings"]), Spy())[1]
        try:
            asyncio.run(pipe._run_once(agent, "q", None))
        except RuntimeError:
            pass
    assert seen["selector"] is None and seen["router"] is None
    assert seen["answerer"] == {"temperature": 0.7}


def test_a_question_that_mentions_images_but_asks_how_to_proceed_is_no_picture_request():
    from oasis_engine.figures import picture_request
    stuck = picture_request("I am stuck while labelling images in SingleX solution, how to proceed?")
    assert stuck is not None and not stuck.only_pictures
    for request in ("show me the images", "any pics of the booth?", "diagrams for station 4"):
        assert picture_request(request).only_pictures, request


def test_a_model_that_writes_its_reply_as_text_is_not_shown_as_json():
    from oasis_engine.pipeline import _plain_draft
    call = '[{"name": "final_result", "parameters": {"found": true, "answer": "Add a folder.", "section_ids": []}}]'
    assert _plain_draft(call, ["A1"]).answer == "Add a folder."
    assert _plain_draft("Which step are you on?", ["A1"]).answer == "Which step are you on?"
    assert _plain_draft("{not json", ["A1"]) is None


def test_an_answer_the_model_could_not_write_is_an_error_not_a_not_covered(pageindex_kb):
    import asyncio
    import pytest
    from conftest import ScriptedModel
    from oasis_engine.loaders import load_pageindex
    from oasis_engine.pipeline import Pipeline, ProviderBusy
    docs = load_pageindex(pageindex_kb)
    script = ScriptedModel(select=[{"section_ids": ["A2"]}], answer=[{"bogus": 1}, {"bogus": 2}])
    pipe = Pipeline(docs, script.model, output_mode="prompted")
    with pytest.raises(ProviderBusy):
        asyncio.run(pipe.ask("What is the weather on Mars today?"))


def test_a_reasoning_model_is_sampled_and_gets_room_to_retry():
    from oasis_engine.config import Connection, effective_llm
    from oasis_engine.llm import LLMConfig
    conn = Connection(kind="remote", base_url="http://x/v1", model="qwen", reasoning=True)
    cfg = effective_llm(LLMConfig(), conn)
    assert cfg.reasoning and cfg.temperature == 0.6 and not cfg.thinking_off
    plain = effective_llm(LLMConfig(), Connection(kind="remote", base_url="http://x/v1", model="m"))
    assert plain.temperature == 0.0 and not plain.reasoning


def _doc_with(sections):
    from oasis_engine.models import Doc, Section
    doc = Doc(doc_id="d", name="manual.md", description="", kind="markdown", sections={})
    for i, (title, text) in enumerate(sections):
        nid = f"{i:04d}"
        doc.sections[nid] = Section(node_id=nid, title=title, summary="", depth=1, own_text=text)
    return doc


def test_a_picture_description_counts_for_less_than_the_manuals_text():
    # "What is the Frontier application?" found a screenshot of the app's title bar first (26 Oct 2026)
    doc = _doc_with([
        ("Introduction", "The Frontier application trains inspection models from labelled images."),
        ("Settings", "Change the paths here.\n\n[Figure: A screenshot of the Frontier application window, "
                     "Frontier application title bar, Frontier application menu]"),
    ])
    hits = SectionIndex([doc]).search("What is the Frontier application?")
    assert [doc.sections[h.node_id].title for h in hits][0] == "Introduction"
    # a picture still finds its section when nothing else does
    assert SectionIndex([doc]).search("title bar menu")[0].node_id == "0001"


def test_a_picture_description_left_as_plain_text_is_marked_as_a_picture():
    from oasis_engine.loaders import mark_loose_descriptions
    doc = _doc_with([("Setup", "Intro text.\n\nImage description: GLOBAL SCENE: A screenshot of the window. "
                               "KEY OBJECTS: a Create button.\n\n## Next\nmore")])
    mark_loose_descriptions([doc])
    text = doc.sections["0000"].own_text
    assert "GLOBAL SCENE" not in text and "[Figure: A screenshot of the window." in text
    assert text.startswith("Intro text.") and text.endswith("## Next\nmore")
