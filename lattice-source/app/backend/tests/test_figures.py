"""Cited PDF pages that hold a figure are shown with the answer."""
from fastapi.testclient import TestClient

from conftest import ScriptedModel, make_pdf, write_pageindex_kb
from oasis_engine import bundle
from oasis_engine.api import create_app
from oasis_engine.figures import has_figure, render_page
from oasis_engine.llm import LLMConfig
from oasis_engine.settings import Settings


def test_only_pages_with_a_picture_count(tmp_path):
    pdf = make_pdf(tmp_path / "m.pdf", ["Safety", "Start-up", "Shutdown"], figure_pages={2})
    assert [has_figure(pdf, p) for p in (1, 2, 3, 9)] == [False, True, False, False]
    png = render_page(pdf, 2, tmp_path / "cache")
    assert png.read_bytes()[:4] == b"\x89PNG" and render_page(pdf, 2, tmp_path / "cache") == png


def test_answer_lists_cited_figure_pages_and_serves_them(tmp_path):
    content = write_pageindex_kb(tmp_path / "pi")
    originals = tmp_path / "originals"
    originals.mkdir()
    make_pdf(originals / "press-manual.pdf", ["Safety", "Start-up", "Shutdown"], figure_pages={2})
    bundle.build_bundle(content, "pageindex", "v1", tmp_path / "kb.zip", originals_dir=originals)
    script = ScriptedModel(select=[{"section_ids": ["A2"]}],
                           answer=[{"found": True, "answer": "Set pressure to 180 bar.", "section_ids": ["A2"]}])
    client = TestClient(create_app(Settings(data_dir=tmp_path / "data", llm=LLMConfig(output_mode="prompted")),
                                   model=script.model))
    assert client.post("/api/kb/import", json={"path": str(tmp_path / "kb.zip")}).status_code == 200
    r = client.post("/api/ask", json={"question": "What pressure at start-up?"}).json()
    assert r["figures"] == [{"citation": 0, "page": 2, "image": None, "caption": None, "description": None}]
    image = client.get("/api/docs/pi-test/pages/2/image")
    assert image.status_code == 200 and image.headers["content-type"] == "image/png"
    assert client.get("/api/docs/pi-test/pages/7/image").status_code == 404
    assert client.get("/api/history").json()[0]["figures"] == [{"citation": 0, "page": 2, "image": None, "caption": None, "description": None}]


def test_figure_crop_is_the_picture_not_the_page(tmp_path):
    from PIL import Image
    pdf = make_pdf(tmp_path / "m.pdf", ["Safety", "Start-up", "Shutdown"], figure_pages={2})
    whole = Image.open(render_page(pdf, 2, tmp_path / "cache"))
    figure = Image.open(render_page(pdf, 2, tmp_path / "cache", crop=True))
    # the test picture is 400 x 300 points on a 612 x 792 page
    assert whole.size[0] == 1600 and abs(figure.size[0] / figure.size[1] - 400 / 300) < 0.1
    assert Image.open(render_page(pdf, 1, tmp_path / "cache", crop=True)).size == \
        Image.open(render_page(pdf, 1, tmp_path / "cache")).size  # no figure: the whole page


def test_a_follow_up_does_not_repeat_the_pictures_already_shown(tmp_path):
    content = write_pageindex_kb(tmp_path / "pi")
    originals = tmp_path / "originals"
    originals.mkdir()
    make_pdf(originals / "press-manual.pdf", ["Safety", "Start-up", "Shutdown"], figure_pages={2})
    bundle.build_bundle(content, "pageindex", "v1", tmp_path / "kb.zip", originals_dir=originals)
    step = {"found": True, "answer": "Set pressure to 180 bar.", "section_ids": ["A2"]}
    script = ScriptedModel(select=[{"section_ids": ["A2"]},
                                   {"standalone_question": "What pressure at start-up?", "section_ids": ["A2"]},
                                   {"section_ids": ["A2"]}], answer=[step] * 3)
    client = TestClient(create_app(Settings(data_dir=tmp_path / "data", llm=LLMConfig(output_mode="prompted")),
                                   model=script.model))
    assert client.post("/api/kb/import", json={"path": str(tmp_path / "kb.zip")}).status_code == 200
    ask = {"question": "What pressure at start-up?", "conversation_id": "c1"}
    assert client.post("/api/ask", json=ask).json()["figures"]
    again = client.post("/api/ask", json={**ask, "question": "And the start-up pressure again?"}).json()
    assert again["found"] and again["figures"] == []
    # another conversation shows it again
    assert client.post("/api/ask", json={**ask, "conversation_id": "c2"}).json()["figures"]
