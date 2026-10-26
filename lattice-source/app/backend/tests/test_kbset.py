"""Knowledge bases of document folders (kb-format/tools/convert_kb.py): a KB Manager export is
converted with the real converter, zipped and imported; then loading, the joined decision tree
(which document, then that document's own questions) and routing hints are checked."""
import importlib.util
import io
import json
import zipfile
from pathlib import Path

import pytest

from oasis_engine import bundle
from oasis_engine.interview import Interview
from oasis_engine.kbfolder import load_extras, merged_tree
from oasis_engine.search import SectionIndex

CONVERTER = Path(__file__).resolve().parents[4] / "kb-format" / "tools" / "convert_kb.py"
pytestmark = pytest.mark.skipif(not CONVERTER.is_file(), reason="kb-format/tools/convert_kb.py not in this checkout")

PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d4944415478da63f8ffff3f0005fe02fea7d6a5a50000000049454e44ae426082")

GUIDE = """# R7_guide.pdf

# 1 Introduction

This guide is for Tier 1 agents handling R7 router tickets.

# 2 Firmware

## 2.1 Updating firmware

Updates are done in the Admin Console.

### 2.1.1 Method 1: Automatic update

Click **Check now** and wait.

### 2.1.2 Method 2: Manual upload

Download the image from KA-0388 and click **Upload file manually**.

## 2.2 Error E-217

E-217 means the image failed its checksum. Click **Retry download**.

![Figure](images/figure_000.png)

*Figure 1: Admin Console showing error E-217*

# 3 Escalation

## 3.1 When to escalate

Escalate E-217 after a manual upload fails to Tier 2 Firmware.

## 3.2 Response times

Tier 2 answers within 4 business hours.
"""

REVIEW = """# Q3_review.docx

# 1 Executive Summary

Ticket volume rose 18 % in Q3 after the R7 2.9.0 rollout, mostly error E-217.

# 2 Ticket Volume

Week 34 peaked at 1,260 tickets.

# 3 Q4 Priorities

Cut the backlog below 300 tickets.
"""

WARRANTY = """# Warranty.pdf

# 1 Warranty Period

The R7 is covered for 2 years, or 5 years with Care+.

# 2 Updating firmware

Customers must keep firmware at 2.9.1 or later.
"""


def _bundle(name: str, md: str, images: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(f"{name}.md", md)
        for n, data in images.items():
            z.writestr(f"images/{n}", data)
    return buf.getvalue()


def _records(md: str) -> list[dict]:
    out = []
    for line in md.splitlines():
        if line.startswith("#") and not line.lstrip("#").strip().endswith((".pdf", ".docx")):
            title = line.lstrip("#").strip()
            num, _, rest = title.partition(" ")
            out.append({"title": rest, "section_number": num, "start_page": 1, "end_page": 1,
                        "summary": f"About {rest}.", "type": "section", "level": 1})
    return out


def make_export(path: Path) -> Path:
    docs = [("R7_guide.pdf", GUIDE, {"figure_000.png": PNG}), ("Q3_review.docx", REVIEW, {}),
            ("Warranty.pdf", WARRANTY, {})]
    index = {"kb_name": "Skylink Support", "kb_description": "Router support documents.", "documents": []}
    with zipfile.ZipFile(path, "w") as z:
        for name, md, images in docs:
            member = f"files/{name}/{name}.bundle.zip"
            z.writestr(member, _bundle(name, md, images))
            index["documents"].append({"filename": name, "file_type": name.rsplit(".", 1)[1], "page_count": 3,
                                       "doc_summary": f"The {name} document.", "files": {"bundle": member},
                                       "sections": _records(md)})
        z.writestr("page_index.json", json.dumps(index))
    return path


@pytest.fixture(scope="module")
def converted(tmp_path_factory):
    spec = importlib.util.spec_from_file_location("convert_kb", CONVERTER)
    conv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(conv)
    tmp = tmp_path_factory.mktemp("kbset")
    out = tmp / "kb"
    conv.build(make_export(tmp / "export.zip"), out)
    zipped = tmp / "kb.zip"
    with zipfile.ZipFile(zipped, "w") as z:
        for f in out.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(out).as_posix())
    return out, zipped


def _load(converted, tmp_path):
    _, zipped = converted
    manifest = bundle.import_bundle(zipped, tmp_path)
    content = bundle.version_dir(tmp_path, manifest.kb_version) / bundle.CONTENT
    _, docs = bundle.load_version(bundle.version_dir(tmp_path, manifest.kb_version))
    return manifest, docs, load_extras(content), content


def test_document_folders_import_as_one_document_each(converted, tmp_path):
    manifest, docs, _, content = _load(converted, tmp_path)
    assert manifest.source_format == "semkb"
    assert [d.title for d in docs] == ["R7_guide.pdf", "Q3_review.docx", "Warranty.pdf"]
    assert [e.title for e in manifest.documents] == ["R7_guide.pdf", "Q3_review.docx", "Warranty.pdf"]
    guide = docs[0]
    titles = [s.title for s in guide.sections.values()]
    assert "2.2 Error E-217" in titles and "3.2 Response times" in titles
    assert not (content / "r7-guide" / "validate_kb.py").exists()  # scripts are not imported


def test_pictures_and_their_descriptions_come_from_the_document_folder(converted, tmp_path):
    _, docs, _, _ = _load(converted, tmp_path)
    sec = next(s for s in docs[0].sections.values() if s.title == "2.2 Error E-217")
    assert sec.images and sec.images[0].startswith("page_001_img")
    assert "Admin Console showing error E-217" in sec.own_text
    assert docs[0].assets_dir.name == "r7-guide"


def test_codes_and_related_documents_help_pick_the_document(converted, tmp_path):
    _, docs, _, _ = _load(converted, tmp_path)
    assert "E-217" in docs[0].meta["codes"]
    assert "Q3 review" in docs[0].meta["related"]


def test_one_documents_question_follows_its_own_tree(converted, tmp_path):
    _, docs, extras, _ = _load(converted, tmp_path)
    assert extras.tree["start"] == "kb::document"
    iv, index = Interview(docs, extras), SectionIndex(docs)
    # "Updating firmware" is a section of the router guide and of the warranty: which document first
    c = iv.clarification("How do I do updating firmware?", index)
    assert c.prompt == "Which document is your question about?"
    assert [o.label for o in c.options] == ["R7 guide", "Warranty"]
    # in the router guide only, its 2.1 describes two methods: which one
    c = iv.clarification("How is firmware installed in the Admin Console?", index)
    assert c is not None and "2 ways" in c.prompt
    assert [o.label for o in c.options] == ["Method 1: Automatic update", "Method 2: Manual upload"]
    assert c.options[1].question.endswith("(Method 2: Manual upload)?")


def test_a_clear_question_is_not_asked_back(converted, tmp_path):
    _, docs, extras, _ = _load(converted, tmp_path)
    assert Interview(docs, extras).clarification("How fast does Tier 2 respond?", SectionIndex(docs)) is None


def test_guide_and_notes_for_several_documents(converted, tmp_path):
    _, _, extras, _ = _load(converted, tmp_path)
    assert "Skylink Support" in extras.guide and "Interview tone" in extras.guide
    assert "How to find things" not in extras.guide and "Maintenance" not in extras.guide
    assert "R7 guide" not in extras.guide.split("\n", 1)[0]
    assert extras.notes == ""  # only the converter's auto-detected gaps: not for the model


def test_merged_tree_prefixes_ids_and_keeps_resolve_tables():
    tree = {"start": "t", "resolve_keys": ["task"], "nodes": {"t": {"ask": "Which?", "options": [
        {"label": "A", "set": {"task": "a"}, "next": "resolve"}, {"label": "B", "next": "result:b"}]}},
        "resolve": {"a": "ra"}, "results": {"ra": {"modules": ["m1"]}, "b": {"modules": ["m2"], "start_hint": "m2/01-x"}}}
    merged = merged_tree([("doc", "Doc", tree, ["m1", "m2"]), ("other", "Other", None, ["x"])])
    assert merged["start"] == "kb::document"
    assert merged["nodes"]["doc::t"]["options"][0]["next"] == "resolve:doc"
    assert merged["resolves"]["doc"]["table"] == {"a": "doc::ra"}
    assert merged["results"]["doc::b"]["start_section"] == "doc/m2/01-x"
    assert merged["results"]["other::all"]["modules"] == ["other/x"]


# ── through the API ──

def _client(tmp_path, zipped, model, profile=None):
    from fastapi.testclient import TestClient

    from oasis_engine.api import create_app
    from oasis_engine.config import AppConfig, save_config
    from oasis_engine.llm import LLMConfig
    from oasis_engine.settings import Settings
    data = tmp_path / "data"
    if profile:
        save_config(data, AppConfig(profile=profile))
    c = TestClient(create_app(Settings(data_dir=data, llm=LLMConfig(output_mode="prompted")), model=model))
    assert c.post("/api/kb/import", json={"path": str(zipped)}).status_code == 200
    return c


def test_api_manuals_asks_which_document_then_answers_from_it(converted, tmp_path):
    from conftest import ScriptedModel
    script = ScriptedModel(select=[{"section_ids": ["A1"]}],
                           answer=[{"found": True, "answer": "Keep firmware at 2.9.1 or later.", "section_ids": ["A1"]}])
    c = _client(tmp_path, converted[1], script.model)
    first = c.post("/api/ask", json={"question": "How do I do updating firmware?", "conversation_id": "k1"}).json()
    assert first["clarify"]["prompt"] == "Which document is your question about?"
    warranty = next(o for o in first["clarify"]["options"] if o["label"] == "Warranty")
    second = c.post("/api/ask", json={"question": warranty["question"], "conversation_id": "k1",
                                      "doc_ids": warranty["doc_ids"], "section": warranty["section"],
                                      "replaces": first["history_id"]}).json()
    assert second["found"] and second["citations"][0]["doc_name"] == "Warranty.pdf"


def test_api_chat_lists_documents_with_codes_and_the_shared_guide(converted, tmp_path):
    from conftest import ChatScript
    script = ChatScript([{"text": "Tier 2 answers within 4 business hours."}])
    c = _client(tmp_path, converted[1], script.model, profile="office")
    r = c.post("/api/ask", json={"question": "How fast does Tier 2 respond?", "mode": "chat", "conversation_id": "k2"}).json()
    assert r["found"]
    sent = str(script.seen[0])
    assert "codes: " in sent and "E-217" in sent and "related: " in sent
    assert "Instructions that come with this knowledge base" in sent and "Skylink Support" in sent
    assert "Tier 2 answers within 4 business hours" in sent  # the best section, whole


def test_folders_made_by_another_tool_import_too(converted, tmp_path):
    """"multi doc test/": the parser's own page_index.json (no "folder" entries), one folder from
    convert_kb.py and one with a one-manual sem-ai-kb kb.json whose sections leave out "file",
    lower-case agents.md, extra references/ and validate_kb.py. Before, this was taken for a KB
    Manager export: "The export has no document with text"."""
    out, _ = converted
    kb = tmp_path / "multi doc test"
    import shutil
    shutil.copytree(out / "r7-guide", kb / "r7-guide")
    other = kb / "warranty-terms"
    (other / "modules" / "terms").mkdir(parents=True)
    (other / "modules" / "terms" / "01-period.md").write_text(
        '---\nid: terms/01-period\nmodule: terms\ntitle: "1 Period"\npages: [1, 1]\n---\n\n# 1 Period\n\n'
        "The R7 is covered for 2 years.\n", encoding="utf-8")
    (other / "kb.json").write_text(json.dumps({"name": "Warranty terms", "modules": [{"id": "terms", "title": "Terms"}],
                                               "sections": [{"id": "terms/01-period", "module": "terms", "title": "1 Period"}]}),
                                   encoding="utf-8")
    (other / "agents.md").write_text("# Warranty assistant\n\n## Interview tone\n\n- Brief.\n", encoding="utf-8")
    (kb / "page_index.json").write_text(json.dumps({"kb_name": "multi doc test", "documents": [
        {"filename": "R7_guide.pdf", "sections": []}]}), encoding="utf-8")
    (kb / "README.md").write_text("# multi doc test\n", encoding="utf-8")
    zipped = tmp_path / "multi.zip"
    with zipfile.ZipFile(zipped, "w") as z:
        for f in kb.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(tmp_path).as_posix())  # zipped with its top folder
    data = tmp_path / "data"
    manifest = bundle.import_bundle(zipped, data)
    _, docs = bundle.load_version(bundle.version_dir(data, manifest.kb_version))
    assert sorted(d.title for d in docs) == ["R7_guide.pdf", "Warranty terms"]
    warranty = next(d for d in docs if d.title == "Warranty terms")
    assert any("covered for 2 years" in s.own_text for s in warranty.sections.values())
    extras = load_extras(bundle.version_dir(data, manifest.kb_version) / bundle.CONTENT)
    assert extras.name == "multi doc test" and extras.tree["start"] == "kb::document"


def test_the_parsers_page_index_at_the_top_is_not_taken_for_an_export(converted, tmp_path):
    """The internal bundle (26 Oct): page_index.json is the parser's own (file_id, bundle_zip
    "files/....bundle.zip", no "folder"), the folders are named "datavault-pro-usermanual" and
    "b-acd-e1sem-ai-frontier70-usermanual-1", and the zip has no top folder. Before: "The export has
    no document with text", as page_index.json at the top made it look like a KB Manager export."""
    out, _ = converted
    import shutil
    kb = tmp_path / "kb"
    names = {"b-acd-e1sem-ai-frontier70-usermanual-1": "[B-ACD-E1]SEM_AI_Frontier7.0_UserManual (1).docx",
             "datavault-pro-usermanual": "DataVault_Pro_UserManual.docx"}
    for folder in names:
        shutil.copytree(out / "r7-guide", kb / folder)
        data = json.loads((kb / folder / "kb.json").read_text(encoding="utf-8"))
        (kb / folder / "kb.json").write_text(json.dumps({"name": folder, "modules": data["modules"],
                                                         "sections": data["sections"]}), encoding="utf-8")
    (kb / "page_index.json").write_text(json.dumps({"kb_id": 21, "kb_name": "DataVault_Pro_UserManual", "documents": [
        {"file_id": 357 + i, "filename": fn, "bundle_zip": f"files/{fn}.bundle.zip", "doc_summary": f"About {fn}.",
         "sections": []} for i, fn in enumerate(names.values())]}), encoding="utf-8")
    (kb / "README.md").write_text("# multi-doc-test\n", encoding="utf-8")
    zipped = tmp_path / "kb.zip"
    with zipfile.ZipFile(zipped, "w") as z:
        for f in kb.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(kb).as_posix())
    manifest = bundle.import_bundle(zipped, tmp_path / "data")
    assert [d.title for d in manifest.documents] == list(names.values())  # page_index.json's order and names
    assert manifest.documents[0].description == f"About {list(names.values())[0]}."
