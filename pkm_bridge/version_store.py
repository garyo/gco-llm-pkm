"""Recently seen file contents, keyed by hash, so a save can be merged against its base.

Two tiers: an in-process LRU for the common case, and the `file_versions` table
so that bases survive restarts and are visible to the other server process (the
MCP server). The DB tier is best-effort; without it a stale base degrades to a
`base_unknown` conflict, never to a silent overwrite.
"""

import logging
import threading
from collections import OrderedDict
from datetime import datetime, timedelta

from .fileio import content_hash

logger = logging.getLogger(__name__)

RETENTION = timedelta(days=7)
CLEANUP_EVERY_PUTS = 200


class VersionStore:
    def __init__(self, max_entries: int = 64, max_bytes: int = 16_000_000):
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._lru: OrderedDict[str, str] = OrderedDict()
        self._bytes = 0
        self._puts = 0
        self._lock = threading.Lock()

    def get(self, digest: str) -> str | None:
        with self._lock:
            if digest in self._lru:
                self._lru.move_to_end(digest)
                return self._lru[digest]
        content = self._db_get(digest)
        if content is not None:
            self._remember(digest, content)
        return content

    def put(self, path: str, content: str) -> str:
        """Record `content` for `path`; returns its hash."""
        digest = content_hash(content)
        if self._remember(digest, content):
            self._db_put(digest, path, content)
        return digest

    def _remember(self, digest: str, content: str) -> bool:
        """Insert into the LRU. Returns False if it was already present."""
        with self._lock:
            if digest in self._lru:
                self._lru.move_to_end(digest)
                return False
            self._lru[digest] = content
            self._bytes += len(content)
            while self._lru and (len(self._lru) > self.max_entries or self._bytes > self.max_bytes):
                _, evicted = self._lru.popitem(last=False)
                self._bytes -= len(evicted)
            return True

    # -- DB tier (best effort) ------------------------------------------------

    def _db_get(self, digest: str) -> str | None:
        try:
            from .database import FileVersion, get_db

            db = get_db()
            try:
                row = db.get(FileVersion, digest)
                return row.content if row else None
            finally:
                db.close()
        except Exception as e:
            logger.debug(f"Version store DB lookup skipped: {e}")
            return None

    def _db_put(self, digest: str, path: str, content: str) -> None:
        try:
            from sqlalchemy.dialects.postgresql import insert

            from .database import FileVersion, get_db

            db = get_db()
            try:
                stmt = insert(FileVersion).values(hash=digest, path=path, content=content)
                db.execute(stmt.on_conflict_do_nothing(index_elements=["hash"]))
                self._puts += 1
                if self._puts % CLEANUP_EVERY_PUTS == 0:
                    cutoff = datetime.utcnow() - RETENTION
                    db.query(FileVersion).filter(FileVersion.created_at < cutoff).delete()
                db.commit()
            finally:
                db.close()
        except Exception as e:
            logger.debug(f"Version store DB write skipped: {e}")


version_store = VersionStore()
