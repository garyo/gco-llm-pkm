"""Task views in query blocks, completion stamps, and checking items off through copies."""

import io
import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from pkm_bridge.queries import (
    Corpus,
    QuerySyntaxError,
    TaskError,
    check_task,
    locate_task,
    parse_query,
    preview,
    refresh_text,
    results_hash,
    set_task_state,
)
from pkm_bridge.queries.__main__ import main as cli_main
from pkm_bridge.queries.notes import parse_note
from pkm_bridge.queries.syntax import And, Not, Or, Term

TODAY = date(2026, 10, 13)


@pytest.fixture
def notes(tmp_path: Path) -> Path:
    files = {
        "journals/2026-10-12.md": (
            "## Heartbeat (07:00)\n- [ ] machine task {ticktick:abc}\n\n"
            "# Pelorus Nav\n- [ ] Fix track export\n  - [ ] check GPX\n"
            "- [x] Ship 1.2 ✅ 2026-10-12\n\n## Android\n- [ ] Play Store listing\n\n"
            "# Today\n- [ ] Buy milk #errand\n- [ ] Fix track export\n- [x] Call Mario\n"
        ),
        "journals/2026-09-01.md": (
            "# Today\n- [ ] Renew passport #travel\n- [x] Old plan item\n"
            "- [x] Finally done ✅ 2026-10-11\n"
            "- [x] Parent done\n  - [ ] child still open\n"
        ),
        "journals/2025-06-01.md": "# Today\n- [ ] Ancient item\n",
        "pages/project.md": '---\ntitle: "Project"\n---\n\n# Plan\n- [ ] Page task\n',
    }
    for rel, text in files.items():
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return tmp_path


def run(notes: Path, query: str, page: str = "pages/todo.md") -> str:
    lines, _ = preview(query, page, Corpus(notes), TODAY)
    return "\n".join(lines)


def fill(notes: Path, body: str, page: str = "pages/todo.md") -> str:
    text = refresh_text(body, page, Corpus(notes), TODAY).text
    (notes / page).parent.mkdir(parents=True, exist_ok=True)
    (notes / page).write_text(text)
    return text


def line_of(text: str, needle: str, nth: int = 0) -> int:
    return [i for i, line in enumerate(text.split("\n")) if needle in line][nth]


# --------------------------------------------------------------------------- syntax


def test_not_and_keywords():
    q = parse_query("Travel AND NOT diary OR notes")
    assert isinstance(q.expr, And)
    travel, rest = q.expr.args
    assert isinstance(travel, Term) and isinstance(rest, Or)
    assert isinstance(rest.args[0], Not) and rest.args[0].arg.text == "diary"
    assert parse_query("NOT (a OR b)").expr == Not(parse_query("a OR b").expr)
    assert [t.text for t in parse_query("not and").expr.args] == ["not", "and"]


@pytest.mark.parametrize(
    "source, message",
    [
        ("AND x", "AND needs"),
        ("x AND", "AND needs"),
        ("x AND OR y", "AND needs"),
        ("NOT", "NOT needs"),
        ("x NOT", "NOT needs"),
        ("Egypt show:list", "show:list works with is:"),
        ("is:maybe", "is:"),
        ("x sort:random", "sort:"),
        ("x before:soon", "before:"),
        ("limit:5", "empty query"),
    ],
)
def test_syntax_errors(source, message):
    with pytest.raises(QuerySyntaxError, match=re.escape(message)):
        parse_query(source)


def test_task_options_and_relative_dates():
    q = parse_query("is:todo after:30d before:7d sort:oldest show:list")
    assert (q.task, q.sort, q.show, q.expr) == ("todo", "oldest", "list", None)
    assert q.date_range(TODAY) == (TODAY - timedelta(days=30), TODAY - timedelta(days=7))
    assert parse_query("x after:2026-01-01 last:7d").date_range(TODAY)[0] == date(2026, 10, 6)


# --------------------------------------------------------------------------- task lines


@pytest.mark.parametrize(
    "line, checked, result",
    [
        ("- [ ] Book flights", True, "- [x] Book flights ✅ 2026-10-13"),
        ("- [x] Book flights ✅ 2026-10-01", False, "- [ ] Book flights"),
        ("- [X] Book flights ✅ 2026-10-01", True, "- [x] Book flights ✅ 2026-10-13"),
        ("  * [ ] nested   ", True, "  * [x] nested ✅ 2026-10-13"),
        ("12. [ ] ordered", True, "12. [x] ordered ✅ 2026-10-13"),
        ("- [ ] open, trailing space ", False, "- [ ] open, trailing space "),
        ("- [x] tab\t✅ 2026-10-01", False, "- [ ] tab"),
        ("- [ ] see [x](../pages/a.md)", True, "- [x] see [x](../pages/a.md) ✅ 2026-10-13"),
    ],
)
def test_set_task_state(line, checked, result):
    assert set_task_state(line, checked, TODAY) == result


def test_set_task_state_refuses_other_lines():
    with pytest.raises(ValueError):
        set_task_state("- plain item", True, TODAY)


def test_task_parsing():
    note = parse_note(
        "journals/2026-10-12.md",
        "# P\n- [ ] parent #nav\n  - [x] child ✅ 2026-10-10\n\n    more\n- [ ] second\n"
        "next line\n\n```\n- [ ] in code\n```\n- [x] bad stamp ✅ 2026-13-45\n- [ ] \n",
    )
    tasks = note.sections[0].tasks
    assert [(t.first, t.end, t.done, t.key) for t in tasks] == [
        (1, 5, False, "parent #nav"),
        (2, 5, True, "child"),
        (5, 6, False, "second"),
        (11, 12, True, "bad stamp"),
    ]
    assert tasks[0].tags == {"nav"} and tasks[1].stamp == date(2026, 10, 10)
    assert tasks[3].stamp is None


def test_results_hash_ignores_ticks_and_stamps():
    open_lines = ["## [x](y)", "- [ ] a", "  - [ ] b · [2026-10-12 P](z)"]
    ticked = ["## [x](y)", "- [x] a ✅ 2026-10-13", "  - [X] b ✅ 2026-10-13 · [2026-10-12 P](z)"]
    assert results_hash(open_lines) == results_hash(ticked)
    assert results_hash(open_lines) != results_hash(["## [x](y)", "- [ ] a, edited"])


# --------------------------------------------------------------------------- task views


def headings(output: str) -> list[str]:
    return [line for line in output.split("\n") if line.startswith("## ")]


def test_todo_view_groups_open_items_by_section(notes):
    out = run(notes, "is:todo")
    assert headings(out) == [
        "## [2026-10-12 Mon · Pelorus Nav](../journals/2026-10-12.md#pelorus-nav)",
        "## [2026-10-12 Mon · Pelorus Nav › Android](../journals/2026-10-12.md#android)",
        "## [2026-10-12 Mon · Today](../journals/2026-10-12.md#today)",
        "## [2026-09-01 Tue · Today](../journals/2026-09-01.md#today)",
        "## [2025-06-01 Sun · Today](../journals/2025-06-01.md#today)",
    ]
    assert "- [ ] Fix track export\n  - [ ] check GPX\n\n## " in out
    assert "machine task" not in out and "Ship 1.2" not in out and "Call Mario" not in out
    assert "- [ ] child still open" in out and "Parent done" not in out
    assert "Page task" not in out


def test_heading_match_brings_the_whole_subtree(notes):
    out = run(notes, "Pelorus is:todo")
    assert headings(out) == [
        "## [2026-10-12 Mon · Pelorus Nav](../journals/2026-10-12.md#pelorus-nav)"
    ]
    assert "- [ ] Play Store listing" in out and "Buy milk" not in out


def test_tags_and_scope(notes):
    assert "Buy milk #errand" in run(notes, "is:todo #errand")
    assert "Fix track" not in run(notes, "is:todo #errand")
    assert "Page task" in run(notes, "in:all is:todo")


def test_dates_use_completion_stamps_for_done_items(notes):
    out = run(notes, "is:done last:7d show:list")
    assert "Finally done" in out and "Ship 1.2" in out
    assert "Old plan item" not in out and "Call Mario" in out  # unstamped: journal date
    assert "Ancient" not in run(notes, "is:todo last:30d") and "Ancient" in run(
        notes, "is:todo before:90d"
    )


def test_list_layout_sorting_and_limit(notes):
    out = run(notes, "is:todo show:list sort:oldest limit:3")
    items = [line for line in out.split("\n") if line.startswith("- [")]
    assert items == [
        "- [ ] Ancient item · [2025-06-01 Today](../journals/2025-06-01.md#today)",
        "- [ ] Renew passport #travel · [2026-09-01 Today](../journals/2026-09-01.md#today)",
        "- [ ] child still open · [2026-09-01 Today](../journals/2026-09-01.md#today)",
    ]
    assert "*4 more items not shown.*" in out


def test_titles_and_empty_task_views(notes):
    titles = run(notes, "Pelorus is:todo show:titles")
    assert titles.split("\n")[1].startswith("- [2026-10-12 Mon · Pelorus Nav](")
    assert run(notes, "nosuchthing is:todo") == "\n*No matching items.*\n"


# --------------------------------------------------------------------------- refresh and hashes


def test_ticked_copies_are_not_hand_edits_and_old_hashes_still_count(notes):
    text = fill(notes, "# All\n<!-- query: is:todo -->\n")
    ticked = text.replace("- [ ] Buy milk #errand", "- [x] Buy milk #errand ✅ 2026-10-13")
    assert not refresh_text(ticked, "pages/todo.md", Corpus(notes), TODAY).blocks[0].hand_edited

    full = fill(notes, "# All\n<!-- query: Pelorus -->\n", page="pages/full.md")
    lines = full.split("\n")
    first, last = [i for i, x in enumerate(lines) if "query" in x]
    import hashlib

    raw = hashlib.sha256("\n".join(lines[first + 1 : last]).encode()).hexdigest()[:8]
    lines[last] = f"<!-- /query {raw} -->"
    result = refresh_text("\n".join(lines), "pages/full.md", Corpus(notes), TODAY)
    assert not result.blocks[0].hand_edited and result.text == full


# --------------------------------------------------------------------------- checking off


def source(notes: Path, rel: str = "journals/2026-10-12.md") -> str:
    return (notes / rel).read_text()


def test_check_a_grouped_copy(notes):
    page = fill(notes, "# All\n<!-- query: is:todo -->\n")
    line = line_of(page, "check GPX")
    assert locate_task(page, "pages/todo.md", line).source_path == "journals/2026-10-12.md"
    change = check_task(page, "pages/todo.md", line, source(notes), True, TODAY)
    assert change.source_text.split("\n")[change.source_line] == "  - [x] check GPX ✅ 2026-10-13"
    assert change.page_text.split("\n")[line] == "  - [x] check GPX ✅ 2026-10-13"
    assert change.item == "check GPX"


def test_check_a_list_copy_keeps_its_source_link(notes):
    page = fill(notes, "# All\n<!-- query: is:todo show:list -->\n")
    line = line_of(page, "Buy milk")
    change = check_task(page, "pages/todo.md", line, source(notes), True, TODAY)
    assert change.page_text.split("\n")[line] == (
        "- [x] Buy milk #errand ✅ 2026-10-13 · [2026-10-12 Today](../journals/2026-10-12.md#today)"
    )
    assert "- [x] Buy milk #errand ✅ 2026-10-13" in change.source_text


def test_same_text_in_two_sections_maps_to_its_own(notes):
    page = fill(notes, "# All\n<!-- query: is:todo -->\n")
    under_today = line_of(page, "Fix track export", nth=1)
    change = check_task(page, "pages/todo.md", under_today, source(notes), True, TODAY)
    lines = change.source_text.split("\n")
    assert lines[change.source_line - 1] == "- [ ] Buy milk #errand"
    assert lines[change.source_line] == "- [x] Fix track export ✅ 2026-10-13"


def test_repeats_in_a_section_map_in_order_and_partial_copies_are_refused(tmp_path):
    (tmp_path / "journals").mkdir()
    (tmp_path / "journals/2026-10-12.md").write_text(
        "# Chores\n- [ ] Water plants\n- [ ] Trash\n- [ ] Water plants\n"
    )
    page = fill(tmp_path, "# All\n<!-- query: is:todo -->\n")
    second = line_of(page, "Water plants", nth=1)
    src = (tmp_path / "journals/2026-10-12.md").read_text()
    assert check_task(page, "pages/todo.md", second, src, True, TODAY).source_line == 3

    partial = fill(tmp_path, "# All\n<!-- query: text:water is:todo limit:1 -->\n", "pages/w.md")
    with pytest.raises(TaskError, match="several items"):
        check_task(partial, "pages/w.md", line_of(partial, "Water plants"), src, True, TODAY)


def test_uncheck_removes_stamps(notes):
    page = fill(notes, "# Done\n<!-- query: Pelorus is:done -->\n")
    line = line_of(page, "Ship 1.2")
    change = check_task(page, "pages/todo.md", line, source(notes), False, TODAY)
    assert change.page_text.split("\n")[line] == "- [ ] Ship 1.2"
    assert "- [ ] Ship 1.2\n" in change.source_text


def test_a_stale_copy_of_a_done_item_keeps_its_completion_date(notes):
    page = fill(notes, "# All\n<!-- query: is:todo -->\n")
    line = line_of(page, "Buy milk")
    done_earlier = source(notes).replace(
        "- [ ] Buy milk #errand", "- [x] Buy milk #errand ✅ 2026-10-01"
    )
    change = check_task(page, "pages/todo.md", line, done_earlier, True, TODAY)
    assert change.source_text == done_earlier
    assert change.page_text.split("\n")[line] == "- [x] Buy milk #errand ✅ 2026-10-01"


def test_non_ascii_digits_are_not_task_markers_or_stamps():
    assert set_task_state("- [x] item ✅ ٢٠٢٦-١٠-٠١", False, TODAY).endswith("✅ ٢٠٢٦-١٠-٠١")
    with pytest.raises(ValueError):
        set_task_state("١. [ ] item", True, TODAY)


def test_relative_links_in_copies_still_match(tmp_path):
    (tmp_path / "journals").mkdir()
    (tmp_path / "journals/2026-10-12.md").write_text("# P\n- [ ] read [spec](../pages/spec.md)\n")
    page = fill(tmp_path, "# All\n<!-- query: is:todo -->\n", page="todo.md")
    assert "- [ ] read [spec](pages/spec.md)" in page
    src = (tmp_path / "journals/2026-10-12.md").read_text()
    change = check_task(page, "todo.md", line_of(page, "read [spec]"), src, True, TODAY)
    assert "- [x] read [spec](../pages/spec.md) ✅ 2026-10-13" in change.source_text


@pytest.mark.parametrize(
    "needle, message", [("# All", "isn't in a query block"), ("## [", "isn't a task")]
)
def test_lines_that_are_not_copied_tasks(notes, needle, message):
    page = fill(notes, "# All\n<!-- query: is:todo -->\n")
    with pytest.raises(TaskError, match=message):
        locate_task(page, "pages/todo.md", line_of(page, needle))


def test_stale_copies_are_refused(notes):
    page = fill(notes, "# All\n<!-- query: is:todo -->\n")
    edited = source(notes).replace("Buy milk", "Buy oat milk")
    with pytest.raises(TaskError, match="out of date"):
        check_task(page, "pages/todo.md", line_of(page, "Buy milk"), edited, True, TODAY)


def test_page_sources_with_spaces(tmp_path):
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages/Long Now.md").write_text("# Plan\n- [ ] Book the hall\n")
    page = fill(tmp_path, "# All\n<!-- query: in:pages is:todo -->\n", page="todo.md")
    line = line_of(page, "Book the hall")
    assert locate_task(page, "todo.md", line).source_path == "pages/Long Now.md"


# --------------------------------------------------------------------------- CLI


def run_cli(args, stdin: str, monkeypatch, capsys) -> dict:
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(stdin.encode())))
    assert cli_main(args) == 0
    return json.loads(capsys.readouterr().out)


def test_cli_locate_and_check(notes, monkeypatch, capsys):
    page = fill(notes, "# All\n<!-- query: is:todo -->\n")
    n = str(line_of(page, "Buy milk") + 1)
    common = ["--root", str(notes), "--path", "pages/todo.md", "--line", n]
    located = run_cli(["locate-task", *common], page, monkeypatch, capsys)
    assert located == {"source_path": "journals/2026-10-12.md"}

    request = json.dumps({"page": page, "source": None})
    checked = run_cli(
        ["check-task", *common, "--check", "--today", "2026-10-13"], request, monkeypatch, capsys
    )
    assert (
        checked["source_path"] == "journals/2026-10-12.md" and checked["item"] == "Buy milk #errand"
    )
    assert checked["source"].split("\n")[checked["source_line"] - 1].endswith("✅ 2026-10-13")

    stale = json.dumps({"page": page, "source": "# Today\n"})
    assert "error" in run_cli(["check-task", *common, "--uncheck"], stale, monkeypatch, capsys)
