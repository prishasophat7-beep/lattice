from oasis_engine.search import SectionIndex


def test_a_fact_far_into_a_long_section_is_found():
    # only a section's first 2,000 characters were searched: the commissioning sign-off 28,000
    # characters into a long section could never be found (large-document test, 16 Oct 2026)
    from oasis_engine.loaders import parse_markdown
    filler = " ".join(f"Check the coolant pump seal number {i} and record it in the log." for i in range(400))
    doc = parse_markdown(f"# m.md\n\n## 22.6 Pump\n{filler} The 72-hour dry run must pass before release to "
                         "production, signed on form HX9-COM-7.\n\n## 3. Safety\nWear safety glasses.\n",
                         "m", "m.md")
    ix = SectionIndex([doc])
    top = ix.search("When may the machine be released to production after the dry run?")[0]
    assert doc.sections[top.node_id].title == "22.6 Pump"
    assert {"production", "dry"} <= ix.matched_terms(0, top.node_id, "released to production dry run")


def test_a_heading_with_nothing_under_it_is_not_a_search_result():
    # the web export's document title becomes a section of its own beside the content ("## HX-900
    # Spare Parts Catalogue", "## Parts list"): it ranked first on its title, with nothing to read
    from oasis_engine.loaders import parse_markdown
    from oasis_engine import doctools as T
    doc = parse_markdown("# parts.xlsx\n\n## HX-900 Spare Parts Catalogue\n\n## Parts list\n| Part | Lead time (days) |\n"
                         "|---|---|\n| HX9-1 | 5 |\n| HX9-2 | 95 |\n", "p", "parts.xlsx")
    titles = [doc.sections[h.node_id].title for h in SectionIndex([doc]).search("HX-900 spare parts catalogue lead time")]
    assert "HX-900 Spare Parts Catalogue" not in titles and "Parts list" in titles
    note = "| a | b |\n|---|---|\n| 1 | 2 |\n| 3 | 4 |\n\n*Showing first 2 of 2,000 rows. Highest b 61.4.*"
    log = parse_markdown(f"# log.csv\n\n## Sheet: Log\n{note}\n", "l", "log.csv")
    nid = next(n for n, s in log.sections.items() if s.title == "Sheet: Log")
    assert T.table_highlights(log, nid, "What was the highest b?")[0][0].startswith("Only the first 2 of 2,000 rows")


def test_a_description_that_is_a_flattened_table_is_told_as_one():
    # start page: "Part number Description Price (INR) ... HX9 4002 chiller gasket 3,140 ..." on a card
    from oasis_engine.loaders import parse_markdown, table_description
    doc = parse_markdown("# parts.xlsx\n\n## HX-900 Spare Parts Catalogue\n\n## Parts list\n| Part number | Price (INR) |\n"
                         "|---|---|\n| HX9-1 | 5 |\n| HX9-2 | 9 |\n", "p", "parts.xlsx")
    doc.description = "Part number Price (INR) HX9 1 5 HX9 2 9"
    assert table_description(doc) == "HX-900 Spare Parts Catalogue: a table of 2 rows (Part number, Price (INR))."
    doc.description = "The spare parts for the HX-900."
    assert table_description(doc) is None


def test_a_search_snippet_starts_and_ends_on_whole_words():
    # final check: "...ch 38,437 96 59 Kirloskar HX9-4462 ..." (a part of "switch")
    from oasis_engine.loaders import parse_markdown
    from oasis_engine.search import SectionIndex
    rows = " ".join(f"| HX9-{4400 + i} | rotary table switch | {1000 + i} |" for i in range(40))
    d = parse_markdown(f"# parts.md\n\n## Parts list\n{rows}\n| HX9-4471 | way cover wiper strip | 2,350 |\n"
                       + " ".join(f"| HX9-{4500 + i} | coolant pump sensor | {2000 + i} |" for i in range(40)),
                       "p", "parts.md")
    index = SectionIndex([d])
    nid = next(n for n, s in d.sections.items() if s.title == "Parts list")
    text = " ".join(d.section_text(nid).split())
    snip = index.snippet(0, nid, "HX9-4471")
    assert "HX9-4471" in snip and snip.startswith("...") and snip.endswith("...")
    first, last = snip[3:].split()[0], snip[:-3].split()[-1]
    assert f" {first}" in f" {text}" and f"{last} " in f"{text} "  # whole words, not pieces
