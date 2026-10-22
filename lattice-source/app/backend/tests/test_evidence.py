"""The quotes shown under an answer, from the cited sections (oasis_engine.evidence)."""
from oasis_engine.evidence import _markdown_units, quotes
from oasis_engine.loaders import parse_markdown

MD = """# Leave

## Paternity

Paternity leave: 10 working days, to be availed within 3 months of the birth.

## Award leave

| Occasion | Award Leave |
|---|---|
| 10 years of service | 5 days |

## Projects

Enter a 2-6 letter Project Key (used as a task prefix, e.g. WEB-142). The key cannot be changed.

[Figure: Sprint burndown. A line chart that falls from 60 to 4 story points by day 10.]
"""


def doc():
    return parse_markdown(MD, "d", "policy.md")


def node(d, title):
    return next(s.node_id for s in d.sections.values() if s.title == title)


def test_a_sentence_goes_on_after_e_g():
    units = _markdown_units("Enter a key (used as a task prefix, e.g. WEB-142). It cannot be changed.")
    assert units == ["Enter a key (used as a task prefix, e.g. WEB-142).", "It cannot be changed."]


def test_a_shared_figure_alone_does_not_make_a_quote():
    d = doc()
    cited = [(d, node(d, "Paternity")), (d, node(d, "Award leave"))]
    found = quotes("10 días hábiles", "¿Cuántos días de permiso de paternidad?", cited)
    assert not any("years of service" in q.text for q in found), found


def test_a_picture_is_quoted_as_a_picture():
    d = doc()
    found = quotes("4 story points were left on day 10.", "story points left on day 10?", [(d, node(d, "Projects"))])
    assert found[0].text.startswith("Picture: Sprint burndown.") and "]" not in found[0].text
