"""Low-level helpers shared by every code path that reads or writes note files."""

import hashlib
import os
import tempfile
from pathlib import Path


def content_hash(text: str) -> str:
    """SHA-256 of the text as UTF-8; the identity every writer and reader agrees on."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def atomic_write(path: Path, content: str) -> None:
    """Write content atomically: temp file in the same dir, then os.replace().

    A crash or a Syncthing read mid-write can never see a partially written note —
    readers observe either the old or the new file, never a truncated one. The temp
    name is dot-prefixed so watchers and file listings ignore it.
    """
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        # Best-effort cleanup; os.replace already consumed tmp_name on success.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
