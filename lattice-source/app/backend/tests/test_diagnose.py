"""The --diagnose report: how the knowledge base is read and searched, without its text."""
from conftest import TORQUE_MANUAL, write_markdown_kb
from oasis_engine import bundle
from oasis_engine.diagnose import report


def test_the_report_names_sections_but_never_quotes_them(tmp_path):
    bundle.build_bundle(write_markdown_kb(tmp_path / "md"), "markdown", "v1", tmp_path / "kb.zip")
    bundle.import_bundle(tmp_path / "kb.zip", tmp_path / "data")
    text = report(["What does error E17 mean?"], tmp_path / "data")
    assert "knowledge base: v1" in text and "QUESTION: What does error E17 mean?" in text
    assert "Error E17" in text  # the section's title
    # no sentence of the manual's own text is in the report
    sentences = [s.strip() for s in TORQUE_MANUAL.replace("\n", " ").split(".") if len(s.strip()) > 30]
    assert sentences and not any(s in text for s in sentences)


def test_no_knowledge_base_is_said_plainly(tmp_path):
    assert "No knowledge base is active." in report(["anything"], tmp_path)
