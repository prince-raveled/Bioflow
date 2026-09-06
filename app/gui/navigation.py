"""The application's navigation structure: which pages exist, and their grouping.

Defined once because it was previously defined twice - the sidebar built its
tree from one list of names and the window keyed its pages by another. The two
agreed only by inspection, and a single character of drift made that sidebar
entry silently inert: the lookup missed, the guard swallowed it, and nothing
was logged. Both now read this.
"""

from __future__ import annotations

#: Section heading, then the pages under it. Headings are not selectable and
#: have no page of their own; every leaf must correspond to exactly one page.
NAVIGATION: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Setup", ("Setup & Resources",)),
    ("Analysis", ("Run workflow",)),
    ("Quality control", ("FastQC", "fastp", "MultiQC")),
    ("Host removal", ("Host Removal",)),
    ("Taxonomic profiling", ("MetaPhlAn",)),
    ("History", ("Run History",)),
)


def page_names() -> list[str]:
    """Every page the navigation offers, in the order it presents them."""
    return [name for _section, pages in NAVIGATION for name in pages]


def check_pages_match(available: list[str]) -> None:
    """Raise when the pages built do not match the pages offered.

    A mismatch is a programming error, and the symptom without this is a
    navigation entry that does nothing at all when clicked. Failing at startup
    names the problem instead.
    """
    expected, built = set(page_names()), set(available)
    if expected == built:
        return
    missing = ", ".join(sorted(expected - built)) or "none"
    extra = ", ".join(sorted(built - expected)) or "none"
    raise RuntimeError(
        f"Navigation and pages disagree. Listed but not built: {missing}. "
        f"Built but not listed: {extra}."
    )
