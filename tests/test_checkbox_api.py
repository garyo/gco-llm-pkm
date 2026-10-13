"""Tests for /api/checkbox/toggle: ticking items off in notes, through query results to the
notes they came from, and in TickTick."""

import logging
from datetime import date

import pytest

from pkm_bridge import ntfy
from pkm_bridge.checkbox_api import handle_toggle
from pkm_bridge.file_editor import ConflictError, FileEditor
from pkm_bridge.queries.refresher import QueryRefresher

LOG = logging.getLogger("test")
TODAY = date(2026, 10, 13)
STAMP = "✅ 2026-10-13"
PAGE = "org:pages/todo.md"

JOURNAL = (
    "# Pelorus Nav\n"
    "- [ ] Fix track export\n"
    "- [ ] Ship 1.3 {ticktick:tt42}\n"
    "\n"
    "# Today\n"
    "- [ ] Buy milk #errand\n"
    "- [x] Call Mario ✅ 2026-10-11\n"
)
MILK_TICKED = JOURNAL.replace("- [ ] Buy milk #errand", f"- [x] Buy milk #errand {STAMP}")
CHORES = (
    "# Chores\n"
    "- [ ] Water plants\n"
    "- [x] Take out trash ✅ 2026-10-01\n"
    "- Mow lawn\n"
    "- TODO Call the bank\n"
)
NOTES = {
    "journals/2026-10-12.md": JOURNAL,
    "pages/todo.md": (
        "# Open\n<!-- query: is:todo -->\n\n# Checklist\n<!-- query: is:todo show:list -->\n"
    ),
    "pages/chores.md": CHORES,
}


class FakeTickTick:
    def __init__(self):
        self.completed: list[str] = []

    def complete_task(self, task_id: str) -> None:
        self.completed.append(task_id)


@pytest.fixture
def org(tmp_path):
    org = tmp_path / "org"
    for rel, text in NOTES.items():
        (org / rel).parent.mkdir(parents=True, exist_ok=True)
        (org / rel).write_text(text)
    return org


@pytest.fixture
def refresher(org, monkeypatch):
    """A refresher whose today is TODAY, with the query blocks in pages/todo.md filled in."""
    refresher = QueryRefresher(FileEditor(LOG, str(org), ""), None)
    monkeypatch.setattr(refresher, "today", lambda: TODAY)
    refresher.refresh_all()
    return refresher


@pytest.fixture(autouse=True)
def no_pushes(monkeypatch):
    monkeypatch.setattr(ntfy, "send", lambda message, **kwargs: None)


def toggle(refresher, line, item_text="", checked=True, path=PAGE, ticktick=None):
    """A file toggle request for 1-based `line` of `path`."""
    body = {
        "type": "file",
        "path": path,
        "item_text": item_text,
        "line_hint": line,
        "checked": checked,
    }
    return handle_toggle(refresher, lambda: ticktick, body)


def line_of(file, needle, nth=0) -> int:
    """1-based number of the nth line of `file` containing `needle`."""
    return [i for i, line in enumerate(file.read_text().split("\n"), 1) if needle in line][nth]


class TestThroughCopies:
    @pytest.mark.parametrize(
        "nth, copy",
        [
            (0, f"- [x] Buy milk #errand {STAMP}"),
            (
                1,
                f"- [x] Buy milk #errand {STAMP} · "
                "[2026-10-12 Today](../journals/2026-10-12.md#today)",
            ),
        ],
        ids=["grouped", "show:list"],
    )
    def test_ticking_a_copy_ticks_the_item_it_came_from(self, org, refresher, nth, copy):
        page = org / "pages/todo.md"
        line = line_of(page, "Buy milk", nth)

        body, status = toggle(refresher, line, "Buy milk")

        assert status == 200
        assert body == {
            "status": "ok",
            "source": "org:journals/2026-10-12.md",
            "source_line": 6,
            "checked": True,
        }
        assert (org / "journals/2026-10-12.md").read_text() == MILK_TICKED
        assert page.read_text().split("\n")[line - 1] == copy

        # The next pass drops the item from the views; the flipped copy isn't a hand edit.
        blocks = refresher.refresh_note(PAGE)["blocks"]
        assert not any(block["hand_edited"] for block in blocks)
        assert "Buy milk" not in page.read_text()

    @pytest.mark.parametrize(
        "at, text",
        [
            ("Buy milk", ""),  # the line alone
            ("# Open", "buy MILK"),  # the text near a rough line, as from the chat
            ("Fix track export", "Buy milk"),  # not the other copy on that line
        ],
    )
    def test_the_copy_is_found_by_its_line_or_its_text(self, org, refresher, at, text):
        body, status = toggle(refresher, line_of(org / "pages/todo.md", at), text)
        assert status == 200 and body["source_line"] == 6
        assert (org / "journals/2026-10-12.md").read_text() == MILK_TICKED

    def test_unticking_a_copy_reopens_the_item(self, org, refresher):
        page = org / "pages/todo.md"
        line = line_of(page, "Buy milk", nth=1)
        untouched = page.read_text().split("\n")[line - 1]
        toggle(refresher, line, "Buy milk")

        # As the web editor sends it: the copy's text after its checkbox, stamp and all.
        text = page.read_text().split("\n")[line - 1].removeprefix("- [x] ")
        body, status = toggle(refresher, line, text, checked=False)

        assert status == 200 and body["checked"] is False
        assert (org / "journals/2026-10-12.md").read_text() == JOURNAL
        assert page.read_text().split("\n")[line - 1] == untouched

    def test_a_stale_copy_is_refused(self, org, refresher):
        journal, page = org / "journals/2026-10-12.md", org / "pages/todo.md"
        journal.write_text(JOURNAL.replace("Buy milk", "Buy oat milk"))
        before = page.read_text()

        body, status = toggle(refresher, line_of(page, "Buy milk"), "Buy milk")

        assert status == 409 and "out of date" in body["error"]
        assert journal.read_text() == JOURNAL.replace("Buy milk", "Buy oat milk")
        assert page.read_text() == before

    def test_a_failed_page_write_still_reports_the_tick(self, org, refresher, monkeypatch):
        write = refresher.editor.write_file

        def conflicting_on_the_page(path, content, **kwargs):
            if path == PAGE:
                raise ConflictError("changed on the same lines", reason="overlap")
            return write(path, content, **kwargs)

        monkeypatch.setattr(refresher.editor, "write_file", conflicting_on_the_page)
        page = org / "pages/todo.md"
        before = page.read_text()

        body, status = toggle(refresher, line_of(page, "Buy milk"), "Buy milk")

        assert status == 200 and body["status"] == "ok"
        assert (org / "journals/2026-10-12.md").read_text() == MILK_TICKED
        assert page.read_text() == before

    @pytest.mark.parametrize(
        "edit, result",
        [
            # Elsewhere in the note: merged with the tick.
            (lambda text: text + "- [ ] New item\n", MILK_TICKED + "- [ ] New item\n"),
            # On the item's own line, keeping its text: ticked again on the new text.
            (
                lambda text: text.replace("#errand\n", "#errand https://shop.example\n"),
                MILK_TICKED.replace("#errand ✅", "#errand https://shop.example ✅"),
            ),
        ],
        ids=["merged", "redone"],
    )
    def test_a_source_changed_meanwhile(self, org, refresher, monkeypatch, edit, result):
        journal = org / "journals/2026-10-12.md"
        write = refresher.editor.write_file

        def racing_write(path, content, **kwargs):
            # Emacs saves the journal between the server's read and its write.
            journal.write_text(edit(journal.read_text()))
            monkeypatch.setattr(refresher.editor, "write_file", write)
            return write(path, content, **kwargs)

        monkeypatch.setattr(refresher.editor, "write_file", racing_write)

        body, status = toggle(refresher, line_of(org / "pages/todo.md", "Buy milk"), "Buy milk")

        assert status == 200 and body["status"] == "ok"
        assert journal.read_text() == result


class TestTickTick:
    def test_a_copied_item_is_completed_there_and_cannot_be_reopened(self, org, refresher):
        tasks = FakeTickTick()
        journal = org / "journals/2026-10-12.md"
        line = line_of(org / "pages/todo.md", "Ship 1.3")

        assert toggle(refresher, line, "Ship 1.3", ticktick=tasks)[1] == 200
        assert tasks.completed == ["tt42"]
        ticked = journal.read_text()
        assert f"- [x] Ship 1.3 {{ticktick:tt42}} {STAMP}\n" in ticked

        body, status = toggle(refresher, line, "Ship 1.3", checked=False, ticktick=tasks)
        assert status == 400 and "not supported" in body["error"]
        assert journal.read_text() == ticked

    def test_nothing_is_ticked_while_ticktick_is_not_connected(self, org, refresher):
        body, status = toggle(refresher, line_of(org / "pages/todo.md", "Ship 1.3"), "Ship 1.3")
        assert (status, body) == (503, {"error": "TickTick not connected"})
        assert (org / "journals/2026-10-12.md").read_text() == JOURNAL

    def test_tasks_by_id(self, refresher):
        tasks = FakeTickTick()
        request = {"type": "ticktick", "task_id": "a1", "checked": True}
        assert handle_toggle(refresher, lambda: tasks, request) == ({"status": "ok"}, 200)
        assert tasks.completed == ["a1"]
        assert handle_toggle(refresher, lambda: tasks, {**request, "checked": False})[1] == 400
        assert handle_toggle(refresher, lambda: None, request)[1] == 503
        assert handle_toggle(refresher, lambda: tasks, {"type": "ticktick"})[1] == 400


class TestItemsOfTheNoteItself:
    @pytest.mark.parametrize(
        "item, text, checked, result",
        [
            ("- [ ] Water plants", "Water plants", True, f"- [x] Water plants {STAMP}"),
            ("- [x] Take out trash ✅ 2026-10-01", "take out trash", False, "- [ ] Take out trash"),
            ("- Mow lawn", "Mow lawn", True, f"- [x] Mow lawn {STAMP}"),
            ("- TODO Call the bank", "Call the bank", True, "- DONE Call the bank"),
        ],
        ids=["check", "uncheck", "plain item", "TODO"],
    )
    def test_toggled_in_place(self, org, refresher, item, text, checked, result):
        chores = org / "pages/chores.md"
        body, status = toggle(
            refresher, line_of(chores, item), text, checked, path="org:pages/chores.md"
        )
        assert (status, body) == (200, {"status": "ok"})
        assert chores.read_text() == CHORES.replace(item, result)
