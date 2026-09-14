"""Tests for the save/merge endpoint handlers."""

import logging

import pytest

from pkm_bridge.file_editor import FileEditor
from pkm_bridge.save_api import handle_merge, handle_save

LOG = logging.getLogger("test")
BASE = "one\ntwo\n"


@pytest.fixture
def editor(tmp_path):
    org = tmp_path / "org"
    org.mkdir()
    (org / "n.org").write_text(BASE)
    return FileEditor(LOG, str(org), "")


def test_missing_content_is_400(editor):
    body, status = handle_save(editor, LOG, "org:n.org", {}, {})
    assert status == 400 and "content" in body["error"]


def test_plain_save_returns_hash_without_echoing_content(editor):
    base = editor.read_file("org:n.org")["hash"]
    body, status = handle_save(
        editor, LOG, "org:n.org", {"content": BASE + "three\n", "base_hash": base}, {}
    )
    assert status == 200 and body["status"] == "saved"
    assert "hash" in body and "content" not in body


def test_merged_save_echoes_merged_content(editor, tmp_path):
    base = editor.read_file("org:n.org")["hash"]
    (tmp_path / "org" / "n.org").write_text("zero\n" + BASE)
    body, status = handle_save(
        editor, LOG, "org:n.org", {"content": BASE + "three\n", "base_hash": base}, {}
    )
    assert status == 200 and body["status"] == "merged"
    assert body["content"] == "zero\none\ntwo\nthree\n"


def test_overlap_is_409_with_details(editor, tmp_path):
    base = editor.read_file("org:n.org")["hash"]
    (tmp_path / "org" / "n.org").write_text("one\nTWO\n")
    body, status = handle_save(
        editor, LOG, "org:n.org", {"content": "one\ntwo!\n", "base_hash": base}, {}
    )
    assert status == 409
    assert body["reason"] == "overlap" and body["theirs"]["content"] == "one\nTWO\n"
    assert "<<<<<<<" in body["merged"]


def test_create_only_reports_exists(editor):
    body, status = handle_save(editor, LOG, "org:n.org", {"content": "x"}, {"create_only": "true"})
    assert status == 200 and body["status"] == "exists"


def test_invalid_path_is_400(editor):
    body, status = handle_save(editor, LOG, "org:../escape.org", {"content": "x"}, {})
    assert status == 400


def test_merge_endpoint_validates_and_previews(editor, tmp_path):
    body, status = handle_merge(editor, LOG, "org:n.org", {"content": "x"})
    assert status == 400

    base = editor.read_file("org:n.org")["hash"]
    (tmp_path / "org" / "n.org").write_text("zero\n" + BASE)
    body, status = handle_merge(
        editor, LOG, "org:n.org", {"content": BASE + "three\n", "base_hash": base}
    )
    assert status == 200 and body["status"] == "merged"
    assert body["content"] == "zero\none\ntwo\nthree\n"
    assert (tmp_path / "org" / "n.org").read_text() == "zero\n" + BASE
