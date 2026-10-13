"""Tests for the query-block refresher: passes, merge-safe writes, debounce and the API."""

import logging
import time

import pytest

from pkm_bridge import ntfy
from pkm_bridge.events import SSEEventManager
from pkm_bridge.file_editor import ConflictError, FileEditor
from pkm_bridge.job_lock import job_lock
from pkm_bridge.queries import refresher as refresher_module
from pkm_bridge.queries.refresher import (
    JOB,
    QueryRefresher,
    auto_refresh_enabled,
    handle_preview,
    handle_refresh,
)

LOG = logging.getLogger("test")

NOTES = {
    "journals/2026-10-01.md": "# Travel Egypt\n- Booked flights\n",
    "journals/2026-10-02.md": "# Misc\n- Read about #Egypt tombs\n- groceries\n",
    "journals/2026-10-03.md": "# Music\n- rehearsed\n",
    "pages/egypt.md": "# Egypt Trip\n\nPlans for 2027.\n\n## Journal\n\n<!-- query: Egypt -->\n",
    "pages/music.md": "# Music\n\n<!-- query: Music -->\n<!-- /query -->\n",
    "pages/plain.md": "# Plain\n\nNo queries here.\n",
}
TRAVEL_ENTRY = "[2026-10-01 Thu · Travel Egypt](../journals/2026-10-01.md#travel-egypt)"


@pytest.fixture
def org(tmp_path):
    org = tmp_path / "org"
    (org / "journals").mkdir(parents=True)
    (org / "pages").mkdir()
    for rel, text in NOTES.items():
        (org / rel).write_text(text)
    return org


@pytest.fixture
def editor(org):
    return FileEditor(LOG, str(org), "")


@pytest.fixture
def refresher(editor):
    return QueryRefresher(editor, None)


@pytest.fixture
def writes(editor, monkeypatch):
    """Paths written through the editor, in order."""
    paths = []
    write = editor.write_file

    def recording_write(path, content, **kwargs):
        paths.append(path)
        return write(path, content, **kwargs)

    monkeypatch.setattr(editor, "write_file", recording_write)
    return paths


@pytest.fixture(autouse=True)
def pushes(monkeypatch):
    """ntfy pushes as (message, kwargs); nothing leaves the machine."""
    sent = []
    monkeypatch.setattr(ntfy, "send", lambda message, **kwargs: sent.append((message, kwargs)))
    return sent


def wait_for(condition, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


class TestPasses:
    def test_only_changed_pages_are_written(self, org, refresher, writes):
        pages = refresher.refresh_all()
        assert [(p["path"], p["status"]) for p in pages] == [
            ("org:pages/egypt.md", "saved"),
            ("org:pages/music.md", "saved"),
        ]
        egypt = (org / "pages/egypt.md").read_text()
        assert f"### {TRAVEL_ENTRY}\n- Booked flights" in egypt
        assert "- Read about #Egypt tombs" in egypt

        writes.clear()
        assert {p["status"] for p in refresher.refresh_all()} == {"unchanged"}
        assert writes == []

        (org / "journals/2026-10-04.md").write_text("# Music\n- new song\n")
        refresher.refresh_all()
        assert writes == ["org:pages/music.md"]
        assert "- new song" in (org / "pages/music.md").read_text()

    def test_a_concurrent_edit_to_the_rest_of_the_page_survives(
        self, org, editor, refresher, monkeypatch
    ):
        page = org / "pages/egypt.md"
        write = editor.write_file

        def racing_write(path, content, **kwargs):
            # Emacs saves the page between the refresher's read and its write.
            page.write_text(page.read_text().replace("Plans for 2027.", "Plans for 2027: Luxor."))
            monkeypatch.setattr(editor, "write_file", write)
            return write(path, content, **kwargs)

        monkeypatch.setattr(editor, "write_file", racing_write)
        assert refresher.refresh_note("org:pages/egypt.md")["status"] == "merged"
        text = page.read_text()
        assert "Plans for 2027: Luxor." in text
        assert "- Booked flights" in text

    def test_one_bad_note_does_not_stop_the_pass(self, org, refresher, monkeypatch, caplog):
        refresh_text = refresher_module.refresh_text

        def failing_on_egypt(text, rel, corpus, today):
            if rel == "pages/egypt.md":
                raise RuntimeError("refreshed query blocks would not read back as written")
            return refresh_text(text, rel, corpus, today)

        monkeypatch.setattr(refresher_module, "refresh_text", failing_on_egypt)
        with caplog.at_level(logging.INFO, logger=refresher_module.__name__):
            pages = refresher.refresh_all()

        assert [(p["path"], p["status"]) for p in pages] == [
            ("org:pages/egypt.md", "error"),
            ("org:pages/music.md", "saved"),
        ]
        assert "would not read back" in pages[0]["error"]
        assert "- rehearsed" in (org / "pages/music.md").read_text()
        assert "1 written, 0 unchanged, 1 errors" in caplog.text

    def test_hand_edits_are_regenerated_and_pushed(self, org, refresher, pushes):
        refresher.refresh_all()
        page = org / "pages/egypt.md"
        page.write_text(page.read_text().replace("- Booked flights", "- Booked flights (aisle)"))

        result = refresher.refresh_note("org:pages/egypt.md")

        assert result["status"] == "saved" and result["blocks"][0]["hand_edited"] is True
        assert "(aisle)" not in page.read_text()
        [(message, kwargs)] = pushes
        assert "pages/egypt.md" in message and "version store for 7 days" in message
        assert kwargs["title"] == "Query results regenerated"

    def test_hand_edit_notice_is_logged_when_it_cannot_be_pushed(
        self, org, refresher, monkeypatch, caplog
    ):
        def unconfigured(message, **kwargs):
            raise ntfy.NtfyError("No ntfy topic configured.")

        monkeypatch.setattr(ntfy, "send", unconfigured)
        refresher.refresh_all()
        page = org / "pages/music.md"
        page.write_text(page.read_text().replace("- rehearsed", "- rehearsed twice"))

        assert refresher.refresh_note("org:pages/music.md")["status"] == "saved"
        assert "pages/music.md" in caplog.text and "No ntfy topic configured" in caplog.text


class TestAutomaticPasses:
    def test_a_pass_waits_until_changes_settle(self, refresher):
        assert refresher.due_in(0) is None
        refresher.note_change(100)
        assert refresher.due_in(100) == 20
        refresher.note_change(110)  # still typing: the wait starts over
        assert refresher.due_in(110) == 20
        assert refresher.due_in(130) == 0

    def test_continuous_changes_wait_at_most_max_wait(self, refresher):
        for t in range(100, 211, 10):
            refresher.note_change(t)
        assert refresher.due_in(210) == 10  # 120 s after the first change

    def test_only_org_markdown_changes_count(self, refresher):
        refresher.on_event("file_changed", {"file": "logseq:Personal/pages/x.md"})
        refresher.on_event("file_changed", {"file": "org:notes.org"})
        refresher.on_event("file_changed", {"path": "/elsewhere/x.md", "file": None})
        refresher.on_event("open_file", {"path": "org:pages/x.md"})
        assert refresher.due_in(time.monotonic()) is None

        refresher.on_event("file_deleted", {"file": "org:journals/2026-10-01.md"})
        assert refresher.due_in(time.monotonic()) is not None

    def test_worker_runs_one_pass_per_burst_of_changes(self, editor, monkeypatch):
        refresher = QueryRefresher(editor, None, settle=0.2, max_wait=5)
        passes = []
        monkeypatch.setattr(refresher, "refresh_all", lambda: passes.append(time.monotonic()))
        events = SSEEventManager()
        refresher.start(events)
        assert wait_for(lambda: len(passes) == 1)  # the startup pass

        burst = time.monotonic()
        for _ in range(5):
            events.broadcast("file_changed", {"file": "org:journals/2026-10-01.md"})
        assert wait_for(lambda: len(passes) == 2)
        assert passes[1] >= burst + 0.2
        time.sleep(0.4)
        assert len(passes) == 2

    def test_worker_runs_again_after_a_pass_that_was_already_running(self, editor, monkeypatch):
        refresher = QueryRefresher(editor, None, settle=0.05, max_wait=5)
        passes = []
        monkeypatch.setattr(refresher, "refresh_all", lambda: passes.append(1))
        with job_lock(JOB):  # the nightly pass, say
            refresher.start(SSEEventManager())
            time.sleep(0.3)
            assert passes == []
        assert wait_for(lambda: passes == [1])

    @pytest.mark.parametrize(
        "setting, debug, enabled",
        [
            (None, False, True),
            (None, True, False),
            ("on", True, True),
            ("off", False, False),
            (" OFF ", False, False),
            ("yes", True, False),
        ],
    )
    def test_enabled_unless_debug_or_overridden(self, monkeypatch, setting, debug, enabled):
        if setting is None:
            monkeypatch.delenv("QUERY_REFRESH", raising=False)
        else:
            monkeypatch.setenv("QUERY_REFRESH", setting)
        assert auto_refresh_enabled(debug) is enabled


class TestApi:
    def test_refresh_one_note(self, refresher):
        body, status = handle_refresh(refresher, {"path": "org:pages/egypt.md"})
        assert status == 200
        assert body == {
            "pages": [
                {
                    "path": "org:pages/egypt.md",
                    "status": "saved",
                    "blocks": [
                        {
                            "query": "Egypt",
                            "entries": 2,
                            "error": None,
                            "changed": True,
                            "hand_edited": False,
                        }
                    ],
                }
            ]
        }

    def test_refresh_without_a_path_refreshes_every_page(self, refresher):
        body, status = handle_refresh(refresher, None)
        assert status == 200
        assert [p["path"] for p in body["pages"]] == ["org:pages/egypt.md", "org:pages/music.md"]

    def test_paths_are_normalized(self, refresher):
        body, status = handle_refresh(refresher, {"path": "org:journals/../pages/egypt.md"})
        assert status == 200 and body["pages"][0]["path"] == "org:pages/egypt.md"

    @pytest.mark.parametrize(
        "path",
        ["pages/egypt.md", "logseq:pages/egypt.md", "org:../escape.md", "org:pages/nope.md", "", 7],
    )
    def test_bad_paths_are_400(self, refresher, path):
        body, status = handle_refresh(refresher, {"path": path})
        assert status == 400 and body["error"]

    def test_conflict_after_retries_is_409(self, editor, refresher, monkeypatch):
        def always_conflicting(path, content, **kwargs):
            raise ConflictError("changed on the same lines", reason="overlap")

        monkeypatch.setattr(editor, "write_file", always_conflicting)
        body, status = handle_refresh(refresher, {"path": "org:pages/egypt.md"})
        assert status == 409 and body["reason"] == "overlap"

    def test_other_failures_are_500_with_a_message(self, refresher, monkeypatch):
        def broken(*args):
            raise RuntimeError("refreshed query blocks would not read back as written")

        monkeypatch.setattr(refresher_module, "refresh_text", broken)
        body, status = handle_refresh(refresher, {"path": "org:pages/egypt.md"})
        assert status == 500 and "would not read back" in body["error"]

    def test_preview_writes_nothing(self, org, refresher):
        body, status = handle_preview(refresher, {"query": "Egypt", "path": "org:pages/new.md"})
        assert status == 200 and body["entries"] == 2 and body["error"] is None
        assert f"## {TRAVEL_ENTRY}\n- Booked flights" in body["markdown"]
        assert not (org / "pages/new.md").exists()

    def test_preview_reports_query_errors(self, refresher):
        body, status = handle_preview(refresher, {"query": "(", "path": "org:pages/egypt.md"})
        assert status == 200 and body["error"] and "Query error" in body["markdown"]

    @pytest.mark.parametrize(
        "body",
        [
            None,
            {"query": "Egypt"},
            {"path": "org:pages/egypt.md"},
            {"query": "Egypt", "path": "org:../escape.md"},
        ],
    )
    def test_preview_bad_requests_are_400(self, refresher, body):
        assert handle_preview(refresher, body)[1] == 400
