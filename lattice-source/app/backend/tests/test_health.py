"""Checks on what was understood of a knowledge base, at every load (see health.py)."""
import json

from conftest import ScriptedModel
from test_semkb import write_semkb, zip_kb
from fastapi.testclient import TestClient

from oasis_engine.api import create_app
from oasis_engine.health import check
from oasis_engine.kbfolder import load_semkb
from oasis_engine.llm import LLMConfig
from oasis_engine.settings import Settings


def test_a_clean_knowledge_base_has_nothing_to_report(tmp_path):
    root = write_semkb(tmp_path / "kb")
    health = check(load_semkb(root), root)
    assert health.checks == [] and health.documents and all(d.loose_descriptions == 0 for d in health.documents)


def test_a_field_the_loader_does_not_read_is_reported(tmp_path):
    # the office converter added "image_descriptions"; the next one may add something else
    root = write_semkb(tmp_path / "kb")
    data = json.loads((root / "kb.json").read_text(encoding="utf-8"))
    data["tables"] = {"t1": "..."}
    data["sections"][0]["table_rows"] = []
    (root / "kb.json").write_text(json.dumps(data), encoding="utf-8")
    [c] = check(load_semkb(root), root).checks
    assert c.level == "warn" and "tables" in c.message and "table_rows" in c.message


def test_a_picture_description_left_in_the_text_is_reported(tmp_path):
    root = write_semkb(tmp_path / "kb")
    docs = load_semkb(root)
    section = next(s for s in docs[0].sections.values() if s.own_text.strip())
    section.own_text += "\n\n> - **GLOBAL SCENE**: A screenshot of the main window.\n>   It shows a toolbar."
    health = check(docs)
    assert health.documents[0].loose_descriptions == 1
    assert [c.level for c in health.checks] == ["bad"] and "picture descriptions" in health.checks[0].message


def test_import_and_manage_show_the_checks(tmp_path):
    root = write_semkb(tmp_path / "kb")
    data = json.loads((root / "kb.json").read_text(encoding="utf-8"))
    data["tables"] = {}
    (root / "kb.json").write_text(json.dumps(data), encoding="utf-8")
    zip_kb(root, tmp_path / "kb.zip")
    c = TestClient(create_app(Settings(data_dir=tmp_path / "data", llm=LLMConfig(output_mode="prompted")),
                              model=ScriptedModel(select=[], answer=[]).model))
    r = c.post("/api/kb/import", json={"path": str(tmp_path / "kb.zip")}).json()
    assert [x["level"] for x in r["checks"]] == ["warn"]
    kb = c.get("/api/kb").json()
    assert kb["health"]["checks"][0]["message"].startswith("kb.json: fields Lattice does not read (kb.json: tables)")
    assert kb["health"]["documents"][0]["sections"] > 0


def test_a_title_heading_over_one_table_is_not_reported():
    # HX-900_spare_parts.xlsx: its title heading and one table section ("1 of 2 sections have no text")
    from oasis_engine.models import Doc, Section
    sections = {"0000": Section(node_id="0000", title="HX-900_spare_parts.xlsx", summary="", depth=0, children=["0001"]),
                "0001": Section(node_id="0001", title="Sheet: Parts", summary="", depth=1, parent="0000",
                                own_text="| Part | Price |\n|---|---|\n| A | 5 |"),
                "0002": Section(node_id="0002", title="Notes", summary="", depth=1, parent="0000")}
    doc = Doc(doc_id="d", name="x.xlsx", description="", kind="markdown", sections=sections)
    assert check([doc]).checks == []
