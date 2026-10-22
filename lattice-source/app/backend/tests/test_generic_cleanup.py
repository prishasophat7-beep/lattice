"""General rules added while testing a large exported manual: none may depend on that manual's wording."""
from oasis_engine.loaders import _brief_description, _plain_description, parse_markdown, repair_mojibake, useful_summary
from oasis_engine.search import SectionIndex, without_contents


def test_garbled_utf8_is_repaired_and_clean_text_is_untouched():
    assert repair_mojibake("the userâ\u0080\u0099s guide") == "the user’s guide"
    assert repair_mojibake("cafÃ© â€“ open") == "café – open"
    for clean in ("Café au lait", "naïve", "Ångström", "plain ascii"):
        assert repair_mojibake(clean) == clean


def test_summaries_about_missing_content_are_dropped():
    assert useful_summary("This section contains no descriptive content.") == ""
    assert useful_summary("![Figure](images/a.png)") == ""
    mixed = "Explains pump priming. The excerpt has no further details."
    assert useful_summary(mixed) == "Explains pump priming."
    assert useful_summary(mixed, whole=True) == ""  # a document summary half about nothing is not kept
    assert useful_summary("Explains pump priming.", whole=True) == "Explains pump priming."


def test_any_all_caps_labels_are_removed_from_a_picture_description():
    text = "OVERVIEW: A settings dialog.\n- **VISIBLE TEXT**: \"Save\", \"Cancel\"\nLAYOUT: buttons at the bottom."
    plain = _plain_description(text)
    assert "OVERVIEW" not in plain and "VISIBLE TEXT" not in plain and "LAYOUT" not in plain
    assert "A settings dialog." in plain


def test_a_long_picture_description_is_shortened_to_its_first_part_and_shown_words():
    text = ("SCENE: A login window of an inspection program.\nDETAILS: " + "A grey toolbar with icons. " * 40
            + 'Labels read "Username", "Password", "Login,".')
    brief = _brief_description(text)
    assert len(brief) <= 400
    assert brief.startswith("A login window of an inspection program.")
    assert "Shows: Username, Password, Login" in brief and ",," not in brief
    assert _brief_description("A pump.") == "A pump."  # short ones in full


def test_a_table_of_contents_is_not_searched_but_ordinary_text_is():
    toc = "\n".join(f"Chapter {i} topic\t{i * 3}" for i in range(1, 15))
    assert without_contents(toc).strip() == ""
    prose = "Set the pressure to 4 bar.\nWait 10 minutes.\nTraining\t20\n"
    assert without_contents(prose) == prose


def _doc(md):
    return parse_markdown(md, "m", "m.md")


def test_words_written_apart_and_together_find_each_other():
    doc = _doc("# m.md\n\n## Grid setup\nOpen the Data Set editor and add rows.\n\n"
               "## Export\nThe dataset is exported as CSV.\n\n## Pumps\nPrime the pump.\n")
    ix = SectionIndex([doc])
    together = {doc.sections[h.node_id].title for h in ix.search("How do I edit a dataset?")[:2]}
    apart = {doc.sections[h.node_id].title for h in ix.search("How do I export the data set?")[:2]}
    assert "Grid setup" in together and "Export" in apart


def test_neighbouring_words_are_not_joined_unless_the_documents_join_them():
    doc = _doc("# m.md\n\n## Line C output\nLine C made 400 units in May.\n\n## Lines\nThe lines run two shifts.\n")
    ix = SectionIndex([doc])
    assert ix.query_spellings("What did Line C make?") == [ix.query_spellings("What did Line C make?")[0]]
    # nor is a plural a compound of the word and an "s" ("customer's")
    doc2 = _doc("# m.md\n\n## Customers\nThe customer's order. All customers.\n")
    assert SectionIndex([doc2]).query_spellings("customer order") == [["customer", "order"]]


def test_a_title_many_sections_share_is_searched_with_the_headings_above_it():
    md = "# m.md\n\n"
    for module in ("Welding", "Painting", "Cutting"):
        md += f"## {module}\nAbout {module.lower()}.\n\n### Training\nClick Train and wait.\n\n"
    doc = _doc(md)
    hits = SectionIndex([doc]).search("training for painting")
    trainings = [doc.path(h.node_id)[-2] for h in hits if doc.sections[h.node_id].title == "Training"]
    assert trainings[0] == "Painting"


def test_a_file_name_title_reads_as_people_would_write_it():
    from oasis_engine.models import readable_name
    assert readable_name("[B-ACD-E1]SEM_AI_Frontier7.0_UserManual (1).docx") == "SEM AI Frontier 7.0 User Manual"
    assert readable_name("InstallGuide - Copy.pdf") == "Install Guide"
    assert readable_name("(QA-12) notes_v3_2.docx") == "notes v3.2"
    # left as they are: an ordinary file name, a written title, a path that tells two files apart, a
    # name that is all code
    for kept in ("budget.xlsx", "employee_handbook_2026.pdf", "TaskFlow Pro 4.2 - User Manual",
                 "2025/notes.docx", "[A1].pdf"):
        assert readable_name(kept) == kept


def test_a_summary_cut_off_mid_word_ends_at_its_last_whole_sentence():
    from oasis_engine.loaders import whole_sentences
    cut = ("The tool is a UI based application for image classification and detection. "
           "The tool is designed for both profes")
    assert whole_sentences(cut) == "The tool is a UI based application for image classification and detection."
    one = "This section explains how to add data by selecting a parent folder containing all of the cla"
    assert whole_sentences(one).endswith("all of the ...")
    for kept in ("Hardware Requirements:", "Steps to install the tool.", "Short title"):
        assert whole_sentences(kept) == kept
