"""Tests for the IVFFlat -> HNSW vector index upgrade (SQL mocked)."""

from unittest.mock import MagicMock, patch

from pkm_bridge.database import (
    VECTOR_INDEX,
    _upgrade_vector_index,
    start_vector_index_upgrade,
)


def _engine(conn: MagicMock) -> MagicMock:
    engine = MagicMock()
    for ctx in (engine.connect.return_value, engine.begin.return_value):
        ctx.__enter__ = MagicMock(return_value=conn)
        ctx.__exit__ = MagicMock(return_value=False)
    return engine


def _conn(got_lock: bool, indexes: list[str]) -> MagicMock:
    """A connection answering the advisory-lock and pg_indexes queries."""
    conn = MagicMock()

    def execute(sql, params=None):
        result = MagicMock()
        result.scalar.return_value = got_lock
        result.scalars.return_value = list(indexes)
        return result

    conn.execute.side_effect = execute
    return conn


def _sql(conn: MagicMock) -> list[str]:
    return [str(c.args[0]) for c in conn.execute.call_args_list]


def test_no_thread_when_already_upgraded():
    engine = _engine(_conn(True, [VECTOR_INDEX, "idx_chunks_content_fts"]))
    with patch("pkm_bridge.database.threading.Thread") as thread:
        start_vector_index_upgrade(engine)
    thread.assert_not_called()


def test_upgrade_starts_in_background():
    engine = _engine(_conn(True, ["idx_embedding_cosine"]))
    with patch("pkm_bridge.database.threading.Thread") as thread:
        start_vector_index_upgrade(engine)
    assert thread.call_args.kwargs["target"] is _upgrade_vector_index
    thread.return_value.start.assert_called_once()


def test_builds_hnsw_then_drops_ivfflat():
    conn = _conn(True, ["idx_embedding_cosine"])
    _upgrade_vector_index(_engine(conn))
    sql = _sql(conn)
    create = next(i for i, s in enumerate(sql) if "CREATE INDEX" in s)
    drop = next(i for i, s in enumerate(sql) if "DROP INDEX" in s)
    assert "USING hnsw (embedding vector_cosine_ops)" in sql[create]
    assert "idx_embedding_cosine" in sql[drop]
    assert create < drop


def test_skips_when_another_process_holds_the_lock():
    conn = _conn(False, ["idx_embedding_cosine"])
    _upgrade_vector_index(_engine(conn))
    assert not any("CREATE INDEX" in s or "DROP INDEX" in s for s in _sql(conn))


def test_failure_is_logged_not_raised(capsys):
    conn = MagicMock()
    conn.execute.side_effect = RuntimeError("out of memory")
    _upgrade_vector_index(_engine(conn))
    assert "vector index upgrade failed" in capsys.readouterr().out


def test_schema_upgrade_does_not_start_the_index_build():
    """Short-lived processes (migration scripts) run init_db too; they must not start it."""
    from pkm_bridge.database import _upgrade_schema

    inspector = MagicMock()
    inspector.get_table_names.return_value = ["document_chunks"]
    with (
        patch("pkm_bridge.database.inspect", return_value=inspector),
        patch("pkm_bridge.database.threading.Thread") as thread,
    ):
        _upgrade_schema(_engine(_conn(True, ["idx_embedding_cosine"])))
    thread.assert_not_called()
