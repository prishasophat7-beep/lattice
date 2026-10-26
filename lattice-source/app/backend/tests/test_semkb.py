"""sem-ai-kb knowledge bases (kbfolder.py): import, loading, the decision tree's questions back
(interview.py), and the knowledge base's own instructions reaching the model."""
import json
import zipfile

from fastapi.testclient import TestClient

from conftest import ChatScript, ScriptedModel
from oasis_engine import bundle
from oasis_engine.api import create_app
from oasis_engine.config import AppConfig, save_config
from oasis_engine.interview import Interview
from oasis_engine.kbfolder import guide_for_model, load_extras, load_semkb, notes_for_model
from oasis_engine.llm import LLMConfig
from oasis_engine.search import SectionIndex
from oasis_engine.settings import Settings

PNG = bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                    "1f15c4890000000d4944415478da63f8ffff3f0005fe02fea7d6a5a50000000049454e44ae426082")

SECTIONS = {
    "classification": [
        ("2 Image Classification", None, "Sorts each image of a part into classes such as OK and Scratch."),
        ("2.1 Training", [12, 12], "Click **Train** and set Epochs to 40."),
        ("2.2 Importing Images", [13, 13],
         "Images come from a folder or the camera.\n\n## 2.2.1 Option A: From a folder\n\nClick Import > Folder.\n\n"
         "## 2.2.2 Option B: From a camera\n\nClick Import > Camera.\n\n"
         "![screenshot p.13](../../images/page_013_img1.png)\n\nImage path: images/page_013_img1.png\n\n"
         "Image description: The Import dialog with two buttons, Folder and Camera, and a list of 3 cameras."),
    ],
    "anomaly": [
        ("3 Anomaly Detection", None, "Learns from good parts only."),
        ("3.1 Training", [20, 20], "Click **Train** and set the Patch Size to 32."),
        ("3.2 Importing Images", [21, 21], "Click Import > Folder and pick a folder of good parts."),
    ],
    "ocr": [
        ("4 Text Reading", None, "Reads printed characters."),
        ("4.1 Annotating Text", [30, 30],
         "Annotate by hand or assisted.\n\n## 4.1.1 Method 1: Manual annotation\n\nDraw a box around each line.\n\n"
         "## 4.1.2 Method 2: Assisted annotation\n\nClick **Propose** and correct the mistakes."),
        ("4.2 Training", [31, 31], "Click **Train** and set Epochs to 60."),
    ],
}

TREE = {
    "start": "task", "resolve_keys": ["task", "variant"],
    "nodes": {
        "task": {"ask": "What should the model do?", "options": [
            {"label": "Sort parts into classes", "set": {"task": "classification"}, "next": "source"},
            {"label": "Find defects from good parts", "set": {"task": "anomaly"}, "next": "source"},
            {"label": "Read printed text", "set": {"task": "ocr"}, "next": "annotation"},
            {"label": "Not sure", "next": "open"}]},
        "source": {"ask": "Where do the images come from?", "options": [
            {"label": "A folder", "set": {"variant": "folder"}, "next": "resolve"},
            {"label": "The line camera", "set": {"variant": "camera"}, "next": "resolve"}]},
        "annotation": {"ask": "How will you annotate the text?", "options": [
            {"label": "By hand", "set": {"variant": "manual"}, "next": "resolve"},
            {"label": "Assisted", "set": {"variant": "assisted"}, "next": "resolve"},
            {"label": "Not sure", "set": {"variant": "unknown"}, "next": "resolve"}]},
    },
    "resolve": {"classification:folder": "cls-folder", "classification:camera": "cls-camera",
                "anomaly:folder": "an", "anomaly:camera": "an",
                "ocr:manual": "ocr-manual", "ocr:assisted": "ocr-assisted", "ocr:unknown": "ocr"},
    "results": {
        "cls-folder": {"modules": ["classification"], "summary": "Classification from a folder",
                       "start_hint": "2.2 Importing Images, Option A: From a folder"},
        "cls-camera": {"modules": ["classification"], "summary": "Classification from the camera",
                       "start_hint": "2.2 Importing Images, Option B: From a camera"},
        "an": {"modules": ["anomaly"], "summary": "Anomaly detection", "start_hint": "3.2 Importing Images"},
        "ocr-manual": {"modules": ["ocr"], "summary": "Manual annotation", "start_hint": "4.1 Annotating Text, Method 1"},
        "ocr-assisted": {"modules": ["ocr"], "summary": "Assisted annotation", "start_hint": "4.1 Annotating Text, Method 2"},
        "ocr": {"modules": ["ocr"], "summary": "Text reading"},
    },
}

AGENTS = """# Vision Studio assistant

You are an interactive guide for **Vision Studio**.

## How to use the KB

- Read index.md first.

## Interview tone

- Friendly and brief.

> **User:** How do I train?
> **You:** What should the model do?

## Maintenance

- Rebuilt by build-kb.mjs.
"""

NEEDS_REVIEW = """# Needs review

Known gaps.

- **ocr/**: the manual has no Deployment section for Text Reading.
- **classification/02-training.md**: batch size is not documented.

<!-- auto-detected gaps: rewritten by tools/build-kb.mjs on every build -->

- **classification/03-importing-images.md**: picture page_013_img1.png named page 000

<!-- end of auto-detected gaps -->
"""


def write_semkb(root):
    """A small sem-ai-kb folder as kb-format/tools/build-kb.mjs writes it."""
    modules, sections = [], []
    titles = {"classification": "Image Classification", "anomaly": "Anomaly Detection", "ocr": "Text Reading"}
    for mod, secs in SECTIONS.items():
        (root / "modules" / mod).mkdir(parents=True)
        for n, (title, pages, body) in enumerate(secs, 1):
            slug = mod if n == 1 else title.split(" ", 1)[1].lower().replace(" ", "-")
            file = f"modules/{mod}/{n:02d}-{slug}.md"
            sid = f"{mod}/{n:02d}-{slug}"
            front = f"---\nid: {sid}\nmodule: {mod}\ntitle: {json.dumps(title)}\npages: {json.dumps(pages)}\n---\n\n"
            page_line = f"_Manual p. {pages[0]}_" if pages else "_Manual pages not recorded_"
            (root / file).write_text(f"{front}# {title}\n\n{page_line}\n\n{body}\n", encoding="utf-8")
            sections.append({"id": sid, "module": mod, "title": title, "pages": pages,
                             "images": ["images/page_013_img1.png"] if "page_013" in body else [],
                             "file": file, "text": body, "summary": f"Summary of {title}."})
        modules.append({"id": mod, "title": titles[mod], "pages": None, "source": "Vision_Studio_Guide.pdf"})
    (root / "images").mkdir()
    (root / "images" / "page_013_img1.png").write_bytes(PNG)
    (root / "interaction").mkdir()
    (root / "interaction" / "decision-tree.json").write_text(json.dumps(TREE), encoding="utf-8")
    (root / "AGENTS.md").write_text(AGENTS, encoding="utf-8")
    (root / "needs-review.md").write_text(NEEDS_REVIEW, encoding="utf-8")
    (root / "index.md").write_text("# Vision Studio knowledge base\n", encoding="utf-8")
    (root / "kb.json").write_text(json.dumps({"name": "Vision Studio", "description": "Test manual.",
                                              "modules": modules, "sections": sections}), encoding="utf-8")
    return root


def zip_kb(root, out, prefix=""):
    with zipfile.ZipFile(out, "w") as zf:
        for f in sorted(root.rglob("*")):
            if f.is_file():
                zf.write(f, prefix + f.relative_to(root).as_posix())
        zf.writestr(prefix + "mcp-server/server.mjs", "// not part of the knowledge base")
    return out


# ── loading ──

def test_each_source_file_is_one_document_with_modules_and_sections(tmp_path):
    docs = load_semkb(write_semkb(tmp_path / "kb"))
    assert len(docs) == 1
    doc = docs[0]
    assert doc.title == "Vision_Studio_Guide.pdf" and doc.meta["type"] == "pdf"
    # the file's name on top (as in the parser's Markdown), its modules under it
    roots = [s for s in doc.sections.values() if s.parent is None]
    assert [s.title for s in roots] == ["Vision_Studio_Guide.pdf"]
    tops = [doc.sections[c] for c in roots[0].children]
    # a module's first section with the module's heading is its intro, not a section of its own
    assert [s.title for s in tops] == ["2 Image Classification", "3 Anomaly Detection", "4 Text Reading"]
    assert tops[0].kb_ids == ["module:classification", "classification/01-classification"]
    assert doc.locator(tops[0].children[0]) == "2 Image Classification > 2.1 Training (p. 12)"
    training = next(s for s in doc.sections.values() if s.kb_ids == ["classification/02-training"])
    assert (training.start_page, training.end_page) == (12, 12)
    assert training.summary == "Summary of 2.1 Training." and not training.summary_is_fallback
    importing = next(s for s in doc.sections.values() if s.title == "2.2 Importing Images")
    # sub-headings of a section ("Option A") are its children, as in the manual
    assert [doc.sections[c].title for c in importing.children] == ["2.2.1 Option A: From a folder",
                                                                   "2.2.2 Option B: From a camera"]


def test_codes_in_module_titles_still_count_as_search_words(tmp_path):
    """ "Q2" in "Part 3: Delivery record Q2 2026": with modules as a document's top headings, the search
    read such codes as the document's name and dropped them from the question (Helix 4B test)."""
    root = write_semkb(tmp_path / "kb")
    index = json.loads((root / "kb.json").read_text())
    (root / "modules" / "records").mkdir()
    (root / "modules" / "records" / "01-records.md").write_text(
        "---\nid: records/01-records\nmodule: records\ntitle: \"Part 3: Delivery record Q2 2026\"\npages: null\n---\n\n"
        "# Part 3: Delivery record Q2 2026\n\n| PO | Days late |\n|---|---|\n| PO-26-0503 | 14 |\n", encoding="utf-8")
    index["modules"].append({"id": "records", "title": "Part 3: Delivery record Q2 2026", "pages": None,
                             "source": "Vision_Studio_Guide.pdf"})
    index["sections"].append({"id": "records/01-records", "module": "records", "title": "Part 3: Delivery record Q2 2026",
                              "pages": None, "images": [], "file": "modules/records/01-records.md", "text": ""})
    (root / "kb.json").write_text(json.dumps(index), encoding="utf-8")
    docs = load_semkb(root)
    hit = SectionIndex(docs).search("Which orders were late in Q2?")[0]
    assert docs[hit.doc_index].sections[hit.node_id].title == "Part 3: Delivery record Q2 2026"


def test_pictures_keep_their_whole_description(tmp_path):
    doc = load_semkb(write_semkb(tmp_path / "kb"))[0]
    camera = next(s for s in doc.sections.values() if s.title == "2.2.2 Option B: From a camera")
    assert camera.images == ["page_013_img1.png"]
    assert "a list of 3 cameras" in camera.image_notes["page_013_img1.png"]["description"]
    assert "[Figure: " in camera.own_text and "a list of 3 cameras" in camera.own_text
    assert "Image path" not in camera.own_text and "Image description" not in camera.own_text


def test_agents_and_needs_review_are_read_for_the_model(tmp_path):
    extras = load_extras(write_semkb(tmp_path / "kb"))
    assert "Interview tone" in extras.guide and "How to use the KB" not in extras.guide
    assert "Maintenance" not in extras.guide  # about the folder, not about answering
    assert extras.notes.splitlines() == [
        "- ocr/: the manual has no Deployment section for Text Reading.",
        "- classification/02-training.md: batch size is not documented."]
    assert guide_for_model("## Maintenance\nx\n## Grounding rules\ny") == "## Grounding rules\ny"
    assert notes_for_model("<!-- a -->\n- none found\n<!-- b -->") == ""
    assert "page 000" not in extras.notes  # build-detected gaps are for maintainers, not the model


# ── import ──

def test_zipped_folder_is_imported_with_only_its_own_files(tmp_path):
    root = write_semkb(tmp_path / "kb")
    for name, prefix in (("flat.zip", ""), ("folder.zip", "vision-studio/")):
        data = tmp_path / f"data-{name}"
        m = bundle.import_bundle(zip_kb(root, tmp_path / name, prefix), data)
        assert m.source_format == "semkb" and m.kb_version.startswith("vision-studio-")
        assert [d.title for d in m.documents] == ["Vision_Studio_Guide.pdf"]
        assert not any("mcp-server" in f for f in m.files)
        assert "content/interaction/decision-tree.json" in m.files and "content/images/page_013_img1.png" in m.files
        _, docs = bundle.load_active(data)
        assert docs[0].doc_id == m.documents[0].id
        assert bundle.import_bundle(tmp_path / name, data).kb_version == m.kb_version  # the same again


def test_lattice_bundles_with_a_kb_json_manifest_are_not_mistaken_for_one(tmp_path):
    from conftest import write_markdown_kb
    bundle.build_bundle(write_markdown_kb(tmp_path / "md"), "markdown", "v1", tmp_path / "kb.zip")
    assert bundle.import_bundle(tmp_path / "kb.zip", tmp_path / "data").source_format == "markdown"


# ── questions back from the decision tree ──

def interview(tmp_path):
    root = write_semkb(tmp_path / "kb")
    docs = load_semkb(root)
    return Interview(docs, load_extras(root)), SectionIndex(docs), docs


def test_same_step_in_several_parts_asks_the_trees_question(tmp_path):
    iv, index, docs = interview(tmp_path)
    c = iv.clarification("How do I train?", index)
    assert c.prompt == "What should the model do?"
    assert [o.label for o in c.options] == ["Sort parts into classes", "Find defects from good parts", "Read printed text"]
    assert [o.detail for o in c.options] == ["Image Classification › 2.1 Training", "Anomaly Detection › 3.1 Training",
                                             "Text Reading › 4.2 Training"]
    # each option is read from its section straight away
    assert all(o.section in docs[0].sections and o.question == "How do I train?" for o in c.options)


def test_one_section_done_in_several_ways_asks_which_way(tmp_path):
    iv, index, _ = interview(tmp_path)
    c = iv.clarification("How do I annotate text?", index)
    assert c.prompt == "How will you annotate the text?"
    assert [o.label for o in c.options] == ["By hand", "Assisted"]  # "Not sure" is the app's "Something else..."
    assert [o.question for o in c.options] == ["How do I annotate text (Method 1)?", "How do I annotate text (Method 2)?"]
    assert len({o.section for o in c.options}) == 1


def test_a_clear_question_is_not_asked_back(tmp_path):
    iv, index, _ = interview(tmp_path)
    assert iv.clarification("How do I train an anomaly model with a patch size?", index) is None
    assert iv.clarification("What does Propose do?", index) is None
    # the question names the option itself: followed without asking
    assert iv.clarification("How do I annotate text with assisted annotation?", index) is None


def test_no_tree_no_question(tmp_path):
    root = write_semkb(tmp_path / "kb")
    (root / "interaction" / "decision-tree.json").write_text("{not json", encoding="utf-8")
    docs = load_semkb(root)
    assert Interview(docs, load_extras(root)).clarification("How do I train?", SectionIndex(docs)) is None


# ── through the API ──

def client(tmp_path, model, profile=None):
    data = tmp_path / "data"
    if profile:
        save_config(data, AppConfig(profile=profile))
    c = TestClient(create_app(Settings(data_dir=data, llm=LLMConfig(output_mode="prompted")), model=model))
    zip_kb(write_semkb(tmp_path / "kb"), tmp_path / "kb.zip")
    assert c.post("/api/kb/import", json={"path": str(tmp_path / "kb.zip")}).status_code == 200
    return c


def test_api_manuals_asks_back_from_the_tree_then_reads_the_picked_section(tmp_path):
    script = ScriptedModel(select=[{"section_ids": ["A1"]}],
                           answer=[{"found": True, "answer": "Set the Patch Size to 32.", "section_ids": ["A1"]}])
    c = client(tmp_path, script.model)
    assert c.get("/api/kb").json()["source_format"] == "semkb"
    first = c.post("/api/ask", json={"question": "How do I train?", "conversation_id": "c1"}).json()
    assert not first["found"] and first["clarify"]["prompt"] == "What should the model do?"
    anomaly = next(o for o in first["clarify"]["options"] if o["label"] == "Find defects from good parts")
    second = c.post("/api/ask", json={"question": anomaly["question"], "conversation_id": "c1",
                                      "doc_ids": anomaly["doc_ids"], "section": anomaly["section"],
                                      "replaces": first["history_id"]}).json()
    assert second["found"] and second["clarify"] is None
    assert second["citations"][0]["title"] == "3.1 Training"
    # the known gaps go with the answer prompt
    assert any("KNOWN GAPS" in p and "batch size is not documented" in p for _, p in script.prompts)


def test_api_chat_gives_the_model_the_knowledge_bases_instructions(tmp_path):
    script = ChatScript([{"text": "Click Train and set Epochs to 60. (Text Reading › 4.2 Training)"}])
    c = client(tmp_path, script.model, profile="office")
    r = c.post("/api/ask", json={"question": "How do I train a text reading model?", "mode": "chat",
                                 "conversation_id": "c2"}).json()
    assert r["found"]
    sent = str(script.seen[0])
    assert "Instructions that come with this knowledge base" in sent and "Interview tone" in sent
    assert "batch size is not documented" in sent
    assert "How to use the KB" not in sent


def test_api_chat_asks_back_from_the_tree_on_a_first_question(tmp_path):
    c = client(tmp_path, ChatScript([]).model, profile="office")
    r = c.post("/api/ask", json={"question": "How do I annotate text?", "mode": "chat", "conversation_id": "c3"}).json()
    assert r["clarify"]["prompt"] == "How will you annotate the text?"
    assert [o["label"] for o in r["clarify"]["options"]] == ["By hand", "Assisted"]
