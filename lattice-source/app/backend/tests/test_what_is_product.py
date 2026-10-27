"""'What is <product>?' in a knowledge base of several manuals named by their files (office machine,
26 Oct 2026: "What is Frontier application" was answered from a screenshot's description)."""
from oasis_engine.models import Doc, Section
from oasis_engine.overview import overview
from oasis_engine.search import SectionIndex


def _manual(doc_id: str, file_name: str, about: str, other: list[tuple[str, str]]) -> Doc:
    sections = {"0000": Section(node_id="0000", title=file_name, summary="", depth=0, children=["0001"])}
    sections["0001"] = Section(node_id="0001", title="About", summary="", depth=1, parent="0000", own_text=about)
    for i, (title, text) in enumerate(other, start=2):
        nid = f"{i:04d}"
        sections[nid] = Section(node_id=nid, title=title, summary="", depth=1, parent="0000", own_text=text)
        sections["0000"].children.append(nid)
    return Doc(doc_id=doc_id, name=file_name, description="", kind="markdown", sections=sections, title=file_name)


def _library() -> list[Doc]:
    return [
        _manual("d1", "DataVault_Pro_UserManual.docx",
                "DataVault Pro is a backup application that stores copies of files on a network drive every night "
                "and keeps thirty days of versions for every file.",
                [("Restore", "Open the DataVault Pro application and pick a backup date to restore files.")]),
        _manual("d2", "[B-ACD-E1]SEM_AI_Frontier7.0_UserManual (1).docx",
                "SEM AI Frontier tool is a UI based application designed to tackle CNN based problems such as "
                "image classification, object detection and image segmentation.",
                [("Configuration", "Launch the SEM-AI Frontier application and log in.\n\n[Figure: A screenshot of "
                                   "the SEM-AI FRONTIER application window, Frontier application title bar]"),
                 ("Training", "Click Train to start training the Frontier model.")]),
    ]


def test_what_is_a_product_reads_its_manuals_own_about_section():
    docs = _library()
    index = SectionIndex(docs)
    for question in ("What is Frontier application", "What is the SEM-AI Frontier tool?", "Tell me about Frontier"):
        answer, di, nid = overview(question, index)
        assert (di, nid) == (1, "0001"), question
        assert "UI based application designed to tackle CNN" in answer and "screenshot" not in answer
    answer, di, nid = overview("What is DataVault Pro?", index)
    assert (di, nid) == (0, "0001") and "backup application" in answer


def test_a_topic_inside_a_manual_is_not_taken_for_the_manual():
    index = SectionIndex(_library())
    assert overview("What is Frontier training?", index) is None or overview("What is Frontier training?", index)[2] != "0001"
