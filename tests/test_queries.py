"""The query-block engine: syntax, matching, rendering and refreshing a note."""

import io
import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from pkm_bridge.queries import (
    Corpus,
    QuerySyntaxError,
    parse_query,
    preview,
    query_block_ranges,
    refresh_text,
    results_hash,
    strip_machine_blocks,
)
from pkm_bridge.queries.__main__ import main as cli_main
from pkm_bridge.queries.notes import heading_slug
from pkm_bridge.queries.syntax import And, Not, Or, Term

TODAY = date(2026, 10, 13)


def journal(day: str, body: str) -> str:
    return f'---\ntitle: "{day}"\nid: X\ndate: {day}\n---\n\n{body}'


@pytest.fixture
def notes(tmp_path: Path) -> Path:
    files = {
        "journals/2026-10-13.md": journal(
            "2026-10-13",
            "## Heartbeat (07:00)\n- [ ] Book Egypt flights #egypt\n\n"
            "# Travel Egypt\nFlights look cheaper.\n\n## Hotels\n- Jolie Ville\n\n"
            "## Heartbeat (13:00)\n- machine text #egypt\n\n"
            "# Today\n- dentist\n- passport photos #egypt\n- groceries\n\n"
            "# Diary\nTalked about Egypt over dinner.\n",
        ),
        "journals/2026-10-01.md": journal(
            "2026-10-01",
            "Quick note before any heading #egypt\n\n"
            "# Music\nPlayed [Nardis](../pages/standards.md).\n![](../assets/a.jpg)\n\n"
            "# Music\nSecond set. See [above](#music).\n",
        ),
        "journals/2026-06-01.md": journal(
            "2026-06-01",
            "# [OpenFX](../pages/openfx.md) meeting\nNotes [here](../pages/openfx.md#tsc).\n\n"
            "# Travel\n- #egypt visa needed\n- Paris hotel\n",
        ),
        "pages/openfx.md": '---\ntitle: "OpenFX"\n---\n\n# OpenFX\n\n## Egypt mirror\nText.\n',
        "egypt.md": '---\ntitle: "Egypt Trip"\n---\n\n# Overview\nPyramids.\n',
    }
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return tmp_path


def run(notes: Path, query: str, page: str = "egypt.md") -> str:
    lines, _ = preview(query, page, Corpus(notes), TODAY)
    return "\n".join(lines)


def headings(output: str) -> list[str]:
    return [line for line in output.split("\n") if line.startswith("## ")]


# --------------------------------------------------------------------------- syntax


def test_or_binds_tighter_than_and():
    q = parse_query("Travel Egypt OR eclipse")
    assert isinstance(q.expr, And)
    first, second = q.expr.args
    assert isinstance(first, Term) and first.text == "Travel"
    assert isinstance(second, Or) and [t.text for t in second.args] == ["Egypt", "eclipse"]


def test_negation_groups_fields_and_phrases():
    q = parse_query('-(a b) text:"Long  Now" heading:#Music #Tag')
    assert isinstance(q.expr, And)
    neg, phrase, heading, tag = q.expr.args
    assert isinstance(neg, Not) and isinstance(neg.arg, And)
    assert (phrase.text, phrase.scope) == ("Long Now", "text")
    assert (heading.text, heading.scope) == ("Music", "heading")
    assert (tag.text, tag.scope) == ("Tag", "topic")


def test_options_are_taken_out_wherever_they_appear():
    q = parse_query("in:all Egypt limit:5 last:2w show:titles after:2026-01-01 before:2026-09-01")
    assert (q.scope, q.limit, q.show) == ("all", 5, "titles")
    assert q.last == timedelta(days=14)
    assert q.date_range(TODAY) == (date(2026, 9, 29), date(2026, 9, 1))
    assert isinstance(q.expr, Term)


def test_unknown_fields_are_plain_words():
    q = parse_query("re:vision")
    assert isinstance(q.expr, Term) and q.expr.text == "re:vision"


@pytest.mark.parametrize(
    "source, message",
    [
        ("", "empty query"),
        ("limit:5", "empty query"),
        ("(Egypt", "missing ')'"),
        ("Egypt)", "unbalanced ')'"),
        ("Egypt OR", "OR needs"),
        ("OR Egypt", "OR needs"),
        ("Egypt limit:0", "limit:"),
        ("Egypt last:week", "last:"),
        ("Egypt after:yesterday", "after:"),
        ("Egypt in:logseq", "in:"),
    ],
)
def test_syntax_errors(source, message):
    with pytest.raises(QuerySyntaxError, match=re.escape(message)):
        parse_query(source)


def test_options_alone_need_a_date_or_scope():
    assert parse_query("last:7d").expr is None
    assert parse_query("in:pages").expr is None


# --------------------------------------------------------------------------- matching


def test_heading_match_brings_the_whole_section_without_machine_parts(notes):
    out = run(notes, "Egypt")
    section = out.split("## [2026-10-13 Tue · Travel Egypt]")[1].split("\n## ")[0]
    assert "Flights look cheaper." in section
    assert "### Hotels" in section and "- Jolie Ville" in section
    assert "machine text" not in out and "Book Egypt flights" not in out


def test_tags_match_single_blocks_but_plain_mentions_do_not(notes):
    out = run(notes, "Egypt")
    assert (
        "## [2026-10-13 Tue · Today](journals/2026-10-13.md#today)\n- passport photos #egypt\n"
        in out
    )
    assert "dentist" not in out and "groceries" not in out
    assert "over dinner" not in out


def test_text_prefix_matches_plain_mentions(notes):
    assert "Talked about Egypt over dinner." in run(notes, "text:Egypt")


def test_generic_headings_do_not_match_bare_words(notes):
    assert "*No matching entries.*" in run(notes, "today")
    assert "- dentist" in run(notes, "heading:today")


def test_heading_path_and_tags_combine(notes):
    out = run(notes, "Travel Egypt")
    assert "Travel Egypt" in out
    assert "- #egypt visa needed" in out and "Paris hotel" not in out


def test_negation(notes):
    assert headings(run(notes, "Egypt -Travel")) == [
        "## [2026-10-13 Tue · Today](journals/2026-10-13.md#today)",
        "## [2026-10-01 Thu](journals/2026-10-01.md)",
    ]


def test_text_before_the_first_heading_is_a_section(notes):
    out = run(notes, "Egypt")
    assert (
        "## [2026-10-01 Thu](journals/2026-10-01.md)\nQuick note before any heading #egypt" in out
    )


def test_scope_dates_and_self_exclusion(notes):
    assert "Egypt mirror" in run(notes, "in:pages Egypt")
    assert "Egypt mirror" not in run(notes, "in:pages Egypt", page="pages/openfx.md")
    assert "Overview" in run(notes, "in:pages heading:Overview", page="pages/openfx.md")
    recent = headings(run(notes, "Egypt last:5d"))
    assert recent and all("2026-10-13" in h for h in recent)
    assert headings(run(notes, "Egypt before:2026-10-01")) == [
        "## [2026-06-01 Mon · Travel](journals/2026-06-01.md#travel)"
    ]
    assert "Egypt mirror" not in run(notes, "in:all Egypt after:2026-01-01")


def test_limit_titles_and_empty(notes):
    out = run(notes, "Egypt limit:1")
    assert len(headings(out)) == 1 and "*3 more entries not shown.*" in out
    titles = run(notes, "Egypt show:titles")
    assert titles.split("\n")[1] == (
        "- [2026-10-13 Tue · Travel Egypt](journals/2026-10-13.md#travel-egypt)"
    )
    assert "Flights" not in titles
    assert run(notes, "nothinglikethis") == "\n*No matching entries.*\n"
    assert "*Query error: missing ')'*" in run(notes, "(Egypt")


# --------------------------------------------------------------------------- rendering


def test_links_are_rebased_to_the_page_folder(notes):
    out = run(notes, "Music", page="pages/music.md")
    assert "Played [Nardis](standards.md)." in out
    assert "![](../assets/a.jpg)" in out
    assert "See [above](../journals/2026-10-01.md#music)." in out
    assert "(journals/" not in out.replace("(../journals/", "")


def test_repeated_headings_get_github_suffixes(notes):
    assert headings(run(notes, "Music")) == [
        "## [2026-10-01 Thu · Music](journals/2026-10-01.md#music)",
        "## [2026-10-01 Thu · Music](journals/2026-10-01.md#music-1)",
    ]


def test_repeat_numbering_counts_headings_inside_query_blocks(tmp_path):
    (tmp_path / "journals").mkdir()
    (tmp_path / "journals/2026-10-13.md").write_text(
        "# Log\n<!-- query: x -->\n## Log\n<!-- /query 00000000 -->\n# Log\n- #egypt\n"
    )
    assert headings(run(tmp_path, "Egypt")) == [
        "## [2026-10-13 Tue · Log](journals/2026-10-13.md#log-2)"
    ]


def test_links_in_source_headings_are_flattened(notes):
    assert "## [2026-06-01 Mon · OpenFX meeting](journals/2026-06-01.md#openfx-meeting)" in run(
        notes, "OpenFX"
    )


def test_titles_drop_html_but_anchors_keep_the_emacs_rule(tmp_path):
    (tmp_path / "journals").mkdir()
    (tmp_path / "journals/2026-10-13.md").write_text(
        '# <span class="done DONE">DONE</span> PKM:  calendar\nx\n'
    )
    assert headings(run(tmp_path, "PKM")) == [
        "## [2026-10-13 Tue · DONE PKM: calendar]"
        "(journals/2026-10-13.md#span-classdone-donedonespan-pkm--calendar)"
    ]


def test_titles_escape_brackets(tmp_path):
    (tmp_path / "journals").mkdir()
    (tmp_path / "journals/2026-10-13.md").write_text("# Fix [bug] in Egypt code\nx\n")
    assert "## [2026-10-13 Tue · Fix \\[bug\\] in Egypt code](" in run(tmp_path, "Egypt")


@pytest.mark.parametrize(
    "heading, slug",
    [
        ("Travel/Wedding planning", "travelwedding-planning"),
        ("Eclipse Trip 2027: camera thoughts", "eclipse-trip-2027-camera-thoughts"),
        ("[OpenFX](../pages/openfx.md) TSC meeting", "openfx-tsc-meeting"),
        ("  Two  spaces ", "two--spaces"),
        ("Budget !", "budget-"),
        ("Pelorus Nav ^pn", "pelorus-nav-pn"),
    ],
)
def test_heading_slug(heading, slug):
    assert heading_slug(heading) == slug


# --------------------------------------------------------------------------- refreshing


def page_with(*blocks: str) -> str:
    return '---\ntitle: "Egypt Trip"\n---\n\n# Overview\nPyramids.\n\n# Journal\n\n' + "".join(
        blocks
    )


def test_refresh_fills_a_block_and_is_idempotent(notes):
    corpus = Corpus(notes)
    first = refresh_text(
        page_with("<!-- query: Egypt -->\n<!-- /query -->\n"), "egypt.md", corpus, TODAY
    )
    assert first.changed and first.blocks[0].entries == 4 and first.blocks[0].changed
    assert first.text.startswith(
        page_with("<!-- query: Egypt -->\n\n## [2026-10-13 Tue · Travel Egypt]")
    )
    second = refresh_text(first.text, "egypt.md", corpus, TODAY)
    assert not second.changed and second.text == first.text
    assert not second.blocks[0].changed and not second.blocks[0].hand_edited


def test_closing_line_carries_the_results_hash(notes):
    text = refresh_text(page_with("<!-- query: Egypt -->\n"), "egypt.md", Corpus(notes), TODAY).text
    lines = text.split("\n")
    ((first, last),) = query_block_ranges(text)
    assert lines[last] == f"<!-- /query {results_hash(lines[first + 1 : last])} -->"


def test_unclosed_block_keeps_the_text_after_it(notes):
    text = page_with("<!-- query: Egypt limit:1 -->\nMy own notes stay put.\n")
    result = refresh_text(text, "egypt.md", Corpus(notes), TODAY).text
    assert result.endswith("-->\nMy own notes stay put.\n")
    assert len(query_block_ranges(result)) == 1


def test_hand_edits_are_reported_and_replaced(notes):
    corpus = Corpus(notes)
    filled = refresh_text(page_with("<!-- query: Egypt -->\n"), "egypt.md", corpus, TODAY).text
    edited = filled.replace("Flights look cheaper.", "Flights look MUCH cheaper.")
    result = refresh_text(edited, "egypt.md", corpus, TODAY)
    assert result.blocks[0].hand_edited and result.text == filled


def test_unhashed_block_with_text_is_left_alone(notes):
    text = page_with("<!-- query: Egypt -->\nSomething I wrote.\n<!-- /query -->\n")
    result = refresh_text(text, "egypt.md", Corpus(notes), TODAY)
    assert not result.changed and "closing line has no hash" in result.blocks[0].error


def test_entry_level_follows_the_heading_above_not_earlier_results(notes):
    text = page_with(
        "## Egypt entries\n\n<!-- query: Egypt limit:1 -->\n\n<!-- query: Music limit:1 -->\n"
    )
    result = refresh_text(text, "egypt.md", Corpus(notes), TODAY).text
    assert "\n### [2026-10-13 Tue · Travel Egypt]" in result
    assert "\n#### Hotels" in result
    assert "\n### [2026-10-01 Thu · Music]" in result


def test_markers_in_code_fences_are_ignored(notes):
    text = page_with("```\n<!-- query: Egypt -->\n```\n")
    assert refresh_text(text, "egypt.md", Corpus(notes), TODAY).changed is False


def test_sources_never_contribute_markers_or_open_fences(tmp_path):
    (tmp_path / "journals").mkdir()
    (tmp_path / "journals/2026-10-13.md").write_text(
        "# Egypt notes\n<!-- query: Music -->\n- copy\n<!-- /query 00000000 -->\n"
        "stray <!-- /query --> below:\n<!-- /query -->\n```\nunclosed code\n"
    )
    corpus = Corpus(tmp_path)
    first = refresh_text("<!-- query: Egypt -->\n", "page.md", corpus, TODAY).text
    assert "- copy" not in first and "<!-- query: Music -->" not in first
    assert len(query_block_ranges(first)) == 1 and "<!-- /query -->" not in first.split("\n")
    assert "stray <!-- /query --> below:" in first
    assert "unclosed code\n```\n" in first
    assert refresh_text(first, "page.md", corpus, TODAY).text == first


def test_query_blocks_in_other_notes_are_never_matched(tmp_path):
    (tmp_path / "journals").mkdir()
    (tmp_path / "journals/2026-10-13.md").write_text("# Log\n- #egypt real entry\n")
    corpus = Corpus(tmp_path)
    page_a = refresh_text("# A\n<!-- query: Egypt -->\n", "a.md", corpus, TODAY).text
    (tmp_path / "a.md").write_text(page_a)
    page_b = refresh_text("# B\n<!-- query: in:all Egypt -->\n", "b.md", corpus, TODAY).text
    assert page_b.count("real entry") == 1 and "[A ›" not in page_b


# --------------------------------------------------------------------------- helpers


def test_ranges_and_stripping_keep_line_numbers():
    text = (
        "# P\n<!-- backlinks -->\n- link\n<!-- /backlinks -->\nkeep\n"
        "<!-- query: x -->\ncopy\n<!-- /query 12345678 -->\nkeep too\n<!-- query: y -->\n"
    )
    assert query_block_ranges(text) == [(5, 7), (9, 9)]
    stripped = strip_machine_blocks(text)
    assert stripped.count("\n") == text.count("\n")
    assert [line for line in stripped.split("\n") if line] == ["# P", "keep", "keep too"]


# --------------------------------------------------------------------------- CLI


def test_cli_refresh_text_json(notes, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(b"# J\n<!-- query: Egypt -->\n")))
    code = cli_main(
        [
            "refresh-text",
            "--root",
            str(notes),
            "--path",
            "egypt.md",
            "--json",
            "--today",
            "2026-10-13",
        ]
    )
    report = json.loads(capsys.readouterr().out)
    assert code == 0 and report["changed"] and report["blocks"][0]["entries"] == 4
    assert "Travel Egypt" in report["text"]


def test_cli_refresh_rewrites_notes_with_queries(notes, capsys):
    (notes / "pages/topic.md").write_text("# T\n<!-- query: Music -->\n")
    assert cli_main(["refresh", "--root", str(notes), "--today", "2026-10-13"]) == 0
    assert "pages/topic.md: written (2)" in capsys.readouterr().out
    assert "Second set." in (notes / "pages/topic.md").read_text()
    assert cli_main(["preview", "--root", str(notes), "--path", "x.md", "(oops"]) == 1
