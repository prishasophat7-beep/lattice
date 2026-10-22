import asyncio

from conftest import ScriptedModel
from oasis_engine.loaders import load_pageindex
from oasis_engine.numbers import numbers_in, unverified_numbers
from oasis_engine.pipeline import Pipeline

# Page 10 of the Disney Q1 FY25 release, as PageIndex extracted it (tabs and all), shortened.
TAX_PAGE = ("Interest\texpense,\tnet\twas\tas\tfollows: ($\tin\tmillions) ...\n"
            "Income\tbefore\tincome\ttaxes $\t 3,660\t \t $\t 2,871\t \nIncome\ttax\texpense \t 1,016\t \t \t 720\t \n"
            "Effective\tincome\ttax\trate \t27.8\t% \t25.1\t%\t\nThe\tincrease ... Star\tIndia\tTransaction.")


def test_computed_figure_from_phase0_is_flagged():
    answer = "The effective income tax rate was approximately 27.75% ($1,016M divided by $3,660M)."
    assert unverified_numbers(answer, TAX_PAGE) == ["27.75%"]


def test_quoted_figures_pass_including_thousands_separators_and_trailing_zeros():
    answer = "The rate was 27.8%, up from 25.10%. Income tax expense was 1016 on income of $3,660 million."
    assert unverified_numbers(answer, TAX_PAGE) == []


def test_numbers_from_the_question_and_list_markers_are_allowed():
    source = "Set torque to 12 Nm for M6 bolts."
    answer = "1. Set torque to 12 Nm.\n2. Recheck after 500 cycles on station 4."
    assert unverified_numbers(answer, source, question="What torque for station 4?") == ["500 cycles"]


def test_codes_and_part_numbers_are_checked():
    source = "E17 means torque not reached. Replace bit T30."
    assert unverified_numbers("Error E17: replace bit T30.", source) == []
    assert unverified_numbers("Error E18: replace bit T30.", source) == ["E18"]  # the code, as written
    assert unverified_numbers("The R7 guide, section 4.3 and v2.9.1", "Skylink R7 router") == []


def test_number_with_scale_must_match_scale_in_source():
    # from the local test: "2" appears in the text, but never as "2 million"
    source = "Disney+ subscriptions increased 0.9 million; page 2; Q2 fiscal 2025; 2 percentage points."
    assert unverified_numbers("2 million.", source) == ["2 million"]
    assert unverified_numbers("Subscriptions rose 0.9 million.", source) == []


def test_table_figures_under_a_millions_header_pass():
    table = "($ in millions) Revenues $ 24,690 $ 23,549 5 %"
    assert unverified_numbers("Revenue was $24,690 million, up 5%.", table) == []
    assert unverified_numbers("Revenue grew 6%.", table) == ["6%"]


def test_bracketed_negative_percentages_are_not_bare_table_figures():
    # from the local test: page 9's table has "(2) %" under "($ in millions)", which must not
    # verify an answer of "2 million"
    table = "($ in millions) Consumer Products 1,337 1,359 (2) % $ 9,415 $ 9,132 3 %"
    assert unverified_numbers("2 million.", table) == ["2 million"]
    assert unverified_numbers("Revenue fell 2%.", table) == []


def test_units_survive_pdf_whitespace():
    assert unverified_numbers("The rate was 27.8%.", "Effective\ttax\trate \t27.8\t% \t25.1\t%") == []


def test_normalization():
    assert numbers_in("$1,016.50 and 0.5 and .5 and 007") == {"1016.5", "0.5", "7"}


def test_pipeline_reports_unverified_numbers(pageindex_kb):
    docs = load_pageindex(pageindex_kb)
    script = ScriptedModel(select=[{"section_ids": ["A2"]}],
                           answer=[{"found": True, "answer": "Set pressure to 185 bar.", "section_ids": ["A2"]}])
    r = asyncio.run(Pipeline(docs, script.model, output_mode="prompted").ask("What pressure at start-up?"))
    assert r.unverified_numbers == ["185 bar"]


def test_thousands_under_a_dollar_k_column_pass():
    # the web platform's spreadsheet export: "| Category | FY2026 Actual ($K) | FY2027 Budget ($K) |"
    table = "| Category | FY2026 Actual ($K) | FY2027 Budget ($K) |\n| Equipment | 142 | 186 | +31.0% |"
    assert unverified_numbers("It rose 31% from $142K to $186K.", table) == []
    assert unverified_numbers("It is $500K.", table) == ["500K"]


def test_a_date_before_an_initial_is_not_a_figure():
    notes = "| A. Patel | Deliver profile | 2026-10-10 |\n| M. Chen | Integrate profile | 2026-10-15 |"
    assert unverified_numbers("1. A. Patel (Due: 2026-10-10)\n2. M. Chen (Due: 2026-10-15)", notes) == []


def test_a_thousands_figure_written_out_in_full_passes():
    table = "| Item | Total ($K) |\n| Optical test bench | 92 |\n| Chart set | 3.5 |"
    assert unverified_numbers("The optical test bench costs $92,000.", table) == []
    assert unverified_numbers("A chart set costs $3,500.", table) == []
    assert unverified_numbers("It costs $93,000.", table) == ["93,000"]
    assert unverified_numbers("It costs $92,000.", "| Item | Total |\n| Bench | 92 |") == ["92,000"]


def test_one_step_of_arithmetic_on_checked_figures_is_not_unverified():
    source = "| Category | FY2026 ($K) | FY2027 ($K) |\n| Total | 697 | 770 |\n| Travel | 45 | 38 |"
    answer = "The budget rose from $770K from $697K, an increase of $73K. Cutting travel (38) by 10% saves 3.8, leaving 34.2."
    assert unverified_numbers(answer, source, "If we cut travel by another 10%?") == []
    quote = "The bench is $98,500 against $92,000 budgeted: $6,500 over, about 7.1% higher."
    assert unverified_numbers(quote, "Bench | 98,500\nOptical test bench | 1 | 92 | 92 ($K)", "", ["6.5"]) == []
    # a figure that no step from the answer's own figures gives is still flagged
    assert unverified_numbers("The total is 770 and travel is 38, so the bench costs 91.", source) == ["91"]


def test_steps_numbered_inside_a_paragraph_are_not_figures():
    from oasis_engine.numbers import unverified_numbers as unverified
    source = "Click the Train tab. Select the deep learning framework. Set Epochs to 80, then click Start Learning."
    # a large model numbered its steps in one paragraph; 1 to 3 are not figures from the manual
    assert unverified("1) Click the Train tab. 2) Select the framework. 3) Set Epochs to 80.", source) == []
    assert unverified("(1) Click Train; (2) set Epochs to 90.", source) == ["90"]  # a wrong figure still is
    assert unverified("Wait 5) minutes.", source) == ["5"]  # one alone is a figure
