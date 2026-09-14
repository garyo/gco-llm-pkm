"""Tests for base-hash reconciliation in FileEditor.write_file."""

import logging
import threading

import pytest

from pkm_bridge.file_editor import ConflictError, FileEditor
from pkm_bridge.fileio import content_hash

BASE = "* Monday\n- coffee\n- email\n"


@pytest.fixture
def editor(tmp_path):
    org = tmp_path / "org"
    org.mkdir()
    (org / "note.org").write_text(BASE)
    return FileEditor(logging.getLogger("test"), str(org), "")


def disk(editor, path="org:note.org"):
    return editor.read_file(path, max_chars=None)


def test_read_returns_content_hash(editor):
    assert disk(editor)["hash"] == content_hash(BASE)


def test_write_with_current_base_is_a_plain_save(editor):
    base = disk(editor)["hash"]
    result = editor.write_file("org:note.org", BASE + "- gym\n", base_hash=base)
    assert result["status"] == "saved"
    assert result["hash"] == content_hash(BASE + "- gym\n")
    assert "content" not in result or result["content"] == BASE + "- gym\n"
    assert disk(editor)["content"] == BASE + "- gym\n"


def test_stale_base_with_disjoint_edits_merges(editor, tmp_path):
    base = disk(editor)["hash"]
    # Emacs appends a line behind the editor's back.
    (tmp_path / "org" / "note.org").write_text(BASE + "- from emacs\n")

    result = editor.write_file(
        "org:note.org", "* Monday (rainy)\n- coffee\n- email\n", base_hash=base
    )
    assert result["status"] == "merged"
    assert result["content"] == "* Monday (rainy)\n- coffee\n- email\n- from emacs\n"
    assert disk(editor)["content"] == result["content"]
    assert result["hash"] == content_hash(result["content"])


def test_overlapping_edits_raise_with_theirs_and_marked_text(editor, tmp_path):
    base = disk(editor)["hash"]
    (tmp_path / "org" / "note.org").write_text("* Monday\n- coffee (decaf)\n- email\n")

    with pytest.raises(ConflictError) as info:
        editor.write_file("org:note.org", "* Monday\n- coffee (large)\n- email\n", base_hash=base)
    err = info.value
    assert err.reason == "overlap"
    assert err.theirs["content"] == "* Monday\n- coffee (decaf)\n- email\n"
    assert "<<<<<<< mine" in err.merged
    body = err.to_response()
    assert body["error"] == "conflict" and body["reason"] == "overlap"
    # Nothing was written.
    assert disk(editor)["content"] == "* Monday\n- coffee (decaf)\n- email\n"


def test_unknown_base_raises_base_unknown(editor, tmp_path):
    (tmp_path / "org" / "note.org").write_text(BASE + "- changed\n")
    with pytest.raises(ConflictError) as info:
        editor.write_file("org:note.org", BASE + "- mine\n", base_hash="0" * 64)
    assert info.value.reason == "base_unknown"
    assert info.value.theirs["hash"] == content_hash(BASE + "- changed\n")


def test_identical_content_with_stale_base_is_unchanged(editor, tmp_path):
    (tmp_path / "org" / "note.org").write_text(BASE + "- same\n")
    result = editor.write_file("org:note.org", BASE + "- same\n", base_hash="0" * 64)
    assert result["status"] == "unchanged"


def test_legacy_expected_mtime_still_rejects_newer_files(editor, tmp_path):
    with pytest.raises(ConflictError) as info:
        editor.write_file("org:note.org", "x\n", expected_mtime=0.0)
    assert info.value.reason == "stale"
    assert info.value.theirs["content"] == BASE


def test_merge_preview_does_not_write(editor, tmp_path):
    base = disk(editor)["hash"]
    (tmp_path / "org" / "note.org").write_text(BASE + "- from emacs\n")
    preview = editor.merge_preview("org:note.org", "* Monday (rainy)\n- coffee\n- email\n", base)
    assert preview["status"] == "merged"
    assert preview["content"] == "* Monday (rainy)\n- coffee\n- email\n- from emacs\n"
    assert preview["hash"] == content_hash(BASE + "- from emacs\n")
    assert disk(editor)["content"] == BASE + "- from emacs\n"


def test_concurrent_writers_serialise_and_both_survive(editor):
    base = disk(editor)["hash"]
    errors = []

    def write(text):
        try:
            editor.write_file("org:note.org", text, base_hash=base)
        except Exception as e:  # pragma: no cover - reported via assertion below
            errors.append(e)

    a = threading.Thread(target=write, args=("* Monday (A)\n- coffee\n- email\n",))
    b = threading.Thread(target=write, args=(BASE + "- appended by B\n",))
    a.start()
    b.start()
    a.join()
    b.join()

    assert not errors
    assert disk(editor)["content"] == "* Monday (A)\n- coffee\n- email\n- appended by B\n"
