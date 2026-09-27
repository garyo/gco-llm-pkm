"""Tests for the embedding scanner's handling of partial scans."""

import logging
import os
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from pkm_bridge.database import Document, DocumentChunk
from pkm_bridge.embeddings.embedding_service import reconcile_deleted_files, scan_note_files

logger = logging.getLogger("test")


@pytest.fixture
def db():
    # Enough of the schema for reconciliation; SQLite ignores the vector type
    engine = create_engine("sqlite://")
    Document.__table__.create(engine)
    DocumentChunk.__table__.create(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _doc(path: str) -> Document:
    return Document(file_path=path, file_type="md", file_hash="h")


@pytest.mark.skipif(os.geteuid() == 0, reason="root can read unreadable dirs")
def test_partial_rg_failure_keeps_files_but_marks_dir_incomplete(tmp_path: Path):
    good = tmp_path / "notes"
    (good / "pages").mkdir(parents=True)
    (good / "pages" / "a.md").write_text("a")
    locked = good / "locked"
    locked.mkdir()
    (locked / "b.md").write_text("b")
    other = tmp_path / "other"
    other.mkdir()
    (other / "c.md").write_text("c")

    locked.chmod(0)
    try:
        files, complete = scan_note_files([good, other], logger)
    finally:
        locked.chmod(0o755)

    assert good / "pages" / "a.md" in files
    assert other / "c.md" in files
    assert complete == [other]


def test_empty_or_missing_dir_is_not_complete(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    files, complete = scan_note_files([empty, tmp_path / "missing"], logger)
    assert files == [] and complete == []


def test_reconcile_only_within_scanned_dirs(db):
    db.add_all(
        [
            _doc("/org/kept.md"),
            _doc("/org/deleted.md"),
            _doc("/org-archive/x.md"),  # shares a name prefix with /org
            _doc("/logseq/unscanned.md"),
            _doc("gmail://123"),
        ]
    )
    db.commit()

    removed = reconcile_deleted_files(
        [Path("/org/kept.md")], db, logger, scanned_dirs=[Path("/org")]
    )

    assert removed == 1
    remaining = {d.file_path for d in db.query(Document).all()}
    assert remaining == {"/org/kept.md", "/org-archive/x.md", "/logseq/unscanned.md", "gmail://123"}


def test_reconcile_nothing_when_no_dir_scanned_completely(db):
    db.add(_doc("/org/a.md"))
    db.commit()
    assert reconcile_deleted_files([], db, logger, scanned_dirs=[]) == 0
    assert db.query(Document).count() == 1
