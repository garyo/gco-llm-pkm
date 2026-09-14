"""Tests for the content-aware file watcher behind SSE file events."""

import os
import queue
import time

import pytest

from pkm_bridge.events import FileWatcher, SSEEventManager
from pkm_bridge.fileio import atomic_write, content_hash

SETTLE = 2.0  # generous: observer thread + settle timer


@pytest.fixture
def watched(tmp_path):
    root = tmp_path / "org"
    root.mkdir()
    (root / "note.org").write_text("v1\n")

    manager = SSEEventManager()
    client = manager.add_client()
    watcher = FileWatcher(manager, {"org": root})
    watcher.start()
    # Let the observer arm, then drop anything it replays about the setup writes
    # (macOS FSEvents reports recent history on start).
    time.sleep(0.5)
    while next_event(client, timeout=0.2):
        pass
    yield root, client
    watcher.stop()


def next_event(client: queue.Queue, timeout: float = SETTLE):
    try:
        return client.get(timeout=timeout)
    except queue.Empty:
        return None


def test_in_place_write_emits_file_changed(watched):
    root, client = watched
    (root / "note.org").write_text("v2\n")

    event = next_event(client)
    assert event is not None and event["type"] == "file_changed"
    data = event["data"]
    assert data["file"] == "org:note.org"
    assert data["hash"] == content_hash("v2\n")
    assert isinstance(data["mtime"], float)


def test_atomic_rename_emits_file_changed(watched):
    root, client = watched
    atomic_write(root / "note.org", "v3\n")

    event = next_event(client)
    assert event is not None and event["type"] == "file_changed"
    assert event["data"]["hash"] == content_hash("v3\n")


def test_move_in_from_outside_emits_file_changed(watched, tmp_path):
    root, client = watched
    outside = tmp_path / "incoming.org"
    outside.write_text("delivered\n")
    os.rename(outside, root / "incoming.org")

    event = next_event(client)
    assert event is not None and event["type"] == "file_changed"
    assert event["data"]["file"] == "org:incoming.org"


def test_identical_rewrite_is_silent(watched):
    root, client = watched
    (root / "note.org").write_text("v2\n")
    assert next_event(client)["data"]["hash"] == content_hash("v2\n")

    (root / "note.org").write_text("v2\n")
    assert next_event(client, timeout=1.0) is None


def test_delete_emits_file_deleted(watched):
    root, client = watched
    (root / "note.org").unlink()

    event = next_event(client)
    assert event is not None and event["type"] == "file_deleted"
    assert event["data"]["file"] == "org:note.org"


def test_temp_files_are_ignored(watched):
    root, client = watched
    (root / ".note.org.abc.tmp").write_text("partial")
    (root / "note.org~").write_text("backup")

    assert next_event(client, timeout=1.0) is None
