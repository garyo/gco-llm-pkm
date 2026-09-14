"""File editor functionality for PKM notes."""

import fcntl
import hashlib
import logging
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, List

from .fileio import atomic_write, content_hash
from .merge import three_way_merge
from .version_store import version_store

# Cap for read_file to keep large notes from blowing the LLM token budget.
# Editor/checkbox callers opt out (max_chars=None) since they need full content.
READ_FILE_CHAR_CAP = 200_000


class ConflictError(Exception):
    """A save was rejected because the file changed underneath the caller.

    reason: 'stale' (legacy mtime check), 'overlap' (both sides edited the same
    lines; `merged` carries the conflict-marked text), or 'base_unknown' (the
    version the edits were made against is no longer available to merge with).
    """

    def __init__(
        self,
        message: str,
        reason: str = "stale",
        theirs: Dict[str, Any] | None = None,
        merged: str | None = None,
    ):
        super().__init__(message)
        self.reason = reason
        self.theirs = theirs
        self.merged = merged

    def to_response(self) -> Dict[str, Any]:
        body: Dict[str, Any] = {"error": "conflict", "reason": self.reason, "message": str(self)}
        if self.theirs is not None:
            body["theirs"] = self.theirs
        if self.merged is not None:
            body["merged"] = self.merged
        return body


# Per-path serialisation of check-merge-write. The thread lock covers this
# process; the flock covers the MCP server, which writes the same files from a
# sibling process. Lock files live outside the synced note tree.
_LOCK_DIR = Path(tempfile.gettempdir()) / "pkm-locks"
_thread_locks: Dict[str, threading.Lock] = {}
_thread_locks_guard = threading.Lock()


@contextmanager
def _path_lock(full_path: Path) -> Generator[None, None, None]:
    key = str(full_path)
    with _thread_locks_guard:
        lock = _thread_locks.setdefault(key, threading.Lock())
    with lock:
        _LOCK_DIR.mkdir(parents=True, exist_ok=True)
        lock_file = _LOCK_DIR / (hashlib.sha1(key.encode()).hexdigest() + ".lock")
        with open(lock_file, "w") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)


class FileEditor:
    """Handle file reading, writing, and listing for the editor."""

    def __init__(self, logger: logging.Logger, org_dir: str, logseq_dir: str):
        """Initialize file editor.

        Args:
            logger: Logger instance
            org_dir: Path to org-mode directory
            logseq_dir: Path to Logseq directory
        """
        self.logger = logger
        self.org_dir = Path(org_dir) if org_dir else None
        self.logseq_dir = Path(logseq_dir) if logseq_dir else None
        self.allowed_dirs = [d for d in [self.org_dir, self.logseq_dir] if d]

    def validate_path(self, filepath: str) -> Path:
        """Validate file path is within allowed directories.

        Args:
            filepath: Relative path to file

        Returns:
            Resolved Path object

        Raises:
            ValueError: If path is outside allowed directories or doesn't exist
        """
        # Try to resolve relative to each allowed directory
        for allowed_dir in self.allowed_dirs:
            try:
                full_path = (allowed_dir / filepath).resolve()

                # Check it's actually within the allowed directory
                if full_path.is_relative_to(allowed_dir):
                    return full_path
            except (ValueError, RuntimeError):
                continue

        raise ValueError(f"Path '{filepath}' is not within allowed directories")

    def _resolve_prefixed_path(self, filepath: str) -> Path:
        """Resolve a prefixed ('org:'/'logseq:') or legacy path with containment check.

        Args:
            filepath: Path in format "org:path/to/file.org", "logseq:path/to/file.md",
                      or a legacy prefix-less relative path.

        Returns:
            Resolved Path, guaranteed to be inside the target base directory.

        Raises:
            ValueError: On unknown prefix, unconfigured directory, or path traversal
                        (resolved path escaping the base directory).
        """
        if ":" not in filepath:
            # Legacy: validate_path already enforces containment.
            return self.validate_path(filepath)

        dir_type, rel_path = filepath.split(":", 1)
        if dir_type == "org":
            base_dir = self.org_dir
        elif dir_type == "logseq":
            base_dir = self.logseq_dir
        else:
            raise ValueError(f"Unknown directory type: {dir_type}")

        if base_dir is None:
            raise ValueError(f"Directory type '{dir_type}' is not configured")

        base_dir = base_dir.resolve()
        full_path = (base_dir / rel_path).resolve()
        if not full_path.is_relative_to(base_dir):
            raise ValueError(f"Path '{filepath}' escapes the '{dir_type}' directory")
        return full_path

    def _resolve_with_fallback(self, filepath: str) -> tuple[Path, str]:
        """Resolve a prefixed path, trying pages/ and toplevel variants.

        Pages don't live consistently at the directory root vs pages/ (some
        get created at toplevel), so when the exact path doesn't exist, try in
        order: pages/ inserted before (or removed from before) the basename,
        then the bare basename at the prefix root, then pages/<basename>.
        The first existing candidate wins; if none exist, the original
        resolution is returned (so writes create the file where requested).

        Returns:
            (resolved absolute Path, canonical prefixed path)
        """
        full_path = self._resolve_prefixed_path(filepath)
        if full_path.exists() or ":" not in filepath:
            return full_path, filepath

        dir_type, rel_path = filepath.split(":", 1)
        parts = rel_path.split("/")
        name = parts[-1]
        parent = parts[:-1]

        candidates = []
        if parent and parent[-1] == "pages":
            candidates.append("/".join(parent[:-1] + [name]))
        else:
            candidates.append("/".join(parent + ["pages", name]))
        candidates.append(name)
        candidates.append(f"pages/{name}")

        seen = {rel_path}
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            alt_filepath = f"{dir_type}:{candidate}"
            try:
                alt_path = self._resolve_prefixed_path(alt_filepath)
            except ValueError:
                continue
            if alt_path.exists():
                self.logger.info(f"Resolved {filepath} -> {alt_filepath} (pages/ fallback)")
                return alt_path, alt_filepath

        return full_path, filepath

    def list_files(self) -> List[Dict[str, str]]:
        """List all .org and .md files in allowed directories.

        Returns:
            List of dicts with file info: {path, name, dir, type, modified}
            where type is 'journal', 'page', or 'other'
        """
        files = []

        # Scan the notes directory. Both suffixes are listed so the corpus
        # stays browsable while it is being converted from org to markdown,
        # and afterwards without further change.
        if self.org_dir and self.org_dir.exists():
            note_files = (p for pattern in ("*.org", "*.md") for p in self.org_dir.rglob(pattern))
            for file_path in note_files:
                if file_path.is_file():
                    rel_path = file_path.relative_to(self.org_dir)

                    # Skip dotfiles (hidden files, Syncthing temp files, etc.)
                    if any(part.startswith(".") for part in rel_path.parts):
                        continue

                    parts_lower = [part.lower() for part in rel_path.parts]

                    # Classify: journals, pages (default), or other (bak)
                    if "bak" in parts_lower:
                        file_type = "other"
                    elif "journals" in parts_lower:
                        file_type = "journal"
                    elif "assets" in parts_lower:
                        file_type = "other"
                    else:
                        file_type = "page"

                    files.append(
                        {
                            "path": str(rel_path),
                            "full_path": f"org:{rel_path}",
                            "name": file_path.name,
                            "dir": "org",
                            "type": file_type,
                            "modified": file_path.stat().st_mtime,
                        }
                    )

        # Scan Logseq directory for .md files
        if self.logseq_dir and self.logseq_dir.exists():
            for file_path in self.logseq_dir.rglob("*.md"):
                if file_path.is_file():
                    rel_path = file_path.relative_to(self.logseq_dir)

                    # Skip dotfiles (hidden files, Syncthing temp files, etc.)
                    if any(part.startswith(".") for part in rel_path.parts):
                        continue

                    parts_lower = [part.lower() for part in rel_path.parts]

                    # Classify: journals (*/journals/), pages (*/pages/), or other
                    if "bak" in parts_lower:
                        file_type = "other"
                    elif "version-files" in parts_lower:
                        file_type = "other"
                    elif "sync-conflict" in str(rel_path):
                        file_type = "other"
                    elif "journals" in parts_lower:
                        file_type = "journal"
                    elif "pages" in parts_lower:
                        file_type = "page"
                    else:
                        file_type = "other"

                    files.append(
                        {
                            "path": str(rel_path),
                            "full_path": f"logseq:{rel_path}",
                            "name": file_path.name,
                            "dir": "logseq",
                            "type": file_type,
                            "modified": file_path.stat().st_mtime,
                        }
                    )

        return files

    def read_file(
        self,
        filepath: str,
        offset: int = 0,
        max_chars: int | None = READ_FILE_CHAR_CAP,
    ) -> Dict[str, Any]:
        """Read file content.

        Args:
            filepath: Path in format "org:path/to/file.org" or "logseq:path/to/file.md"
            offset: Character offset to start reading from (for paging large files).
            max_chars: Cap on returned characters; a truncation note is appended when the
                       content is longer. Pass None to return the full file (editor path).

        Returns:
            Dict with content, path (canonical — may differ from the request
            when the pages/-fallback found the file elsewhere), modified
            timestamp, size, and 'truncated' flag.

        Raises:
            ValueError: If file path is invalid or file doesn't exist
        """
        full_path, filepath = self._resolve_with_fallback(filepath)

        if not full_path.exists():
            raise ValueError(f"File not found: {filepath}")

        if not full_path.is_file():
            raise ValueError(f"Not a file: {filepath}")

        full_content = full_path.read_text(encoding="utf-8")
        digest = version_store.put(filepath, full_content)
        total_chars = len(full_content)

        content = full_content[offset:] if offset else full_content
        truncated = False
        if max_chars is not None and len(content) > max_chars:
            next_offset = offset + max_chars
            content = content[:max_chars]
            truncated = True
            content += (
                f"\n\n[... truncated at {max_chars} of {total_chars} chars. "
                f"Read more with offset={next_offset}.]"
            )

        stat = full_path.stat()
        return {
            "content": content,
            "path": filepath,
            "hash": digest,
            "modified": stat.st_mtime,
            "size": stat.st_size,
            "truncated": truncated,
        }

    def write_file(
        self,
        filepath: str,
        content: str,
        create_only: bool = False,
        expected_mtime: float | None = None,
        base_hash: str | None = None,
    ) -> Dict[str, Any]:
        """Write file content, merging with concurrent changes when a base is given.

        Args:
            filepath: Path in format "org:path/to/file.org" or "logseq:path/to/file.md"
            content: File content to write
            create_only: If True, only create the file if it doesn't exist (atomic check)
            expected_mtime: Legacy optimistic-concurrency token: reject if the file's mtime
                            is newer. Ignored when base_hash is given.
            base_hash: Hash of the content these edits were made against. If the file has
                       changed since, the two sets of changes are three-way merged and
                       the merged text is written (status 'merged', with `content`).

        Returns:
            Dict with status ('saved' | 'merged' | 'unchanged' | 'exists'), path, hash,
            modified, size, and `content` when merged.

        Raises:
            ValueError: If file path is invalid
            ConflictError: The file changed and the edits could not be reconciled
                           (caller should return 409)
        """
        # Fallback keeps edits targeting the existing file (wherever it lives)
        # instead of creating a duplicate at the requested location.
        full_path, filepath = self._resolve_with_fallback(filepath)
        full_path.parent.mkdir(parents=True, exist_ok=True)

        if create_only:
            return self._create_exclusive(full_path, filepath, content)

        with _path_lock(full_path):
            current = full_path.read_text(encoding="utf-8") if full_path.exists() else None
            status = "saved"
            if base_hash is not None and current is not None:
                content, status = self._reconcile(filepath, full_path, base_hash, content, current)
            elif expected_mtime is not None and current is not None:
                actual_mtime = full_path.stat().st_mtime
                if actual_mtime > expected_mtime:
                    raise ConflictError(
                        f"File modified on disk (expected mtime {expected_mtime}, "
                        f"actual {actual_mtime})",
                        theirs=self._file_data(filepath, full_path, current),
                    )

            if status == "unchanged":
                return {"status": status, **self._file_data(filepath, full_path, content)}

            if current is not None:
                version_store.put(filepath, current)
            atomic_write(full_path, content)
            version_store.put(filepath, content)

        self.logger.info(f"{status.capitalize()} file: {filepath} ({len(content)} bytes)")
        result = {"status": status, **self._file_data(filepath, full_path, content)}
        if status == "merged":
            result["content"] = content
        return result

    def merge_preview(self, filepath: str, content: str, base_hash: str) -> Dict[str, Any]:
        """What write_file would write for these edits, without writing.

        `hash`/`modified` describe the on-disk version the result is now based on.
        """
        full_path, filepath = self._resolve_with_fallback(filepath)
        if not full_path.exists():
            raise ValueError(f"File not found: {filepath}")
        with _path_lock(full_path):
            current = full_path.read_text(encoding="utf-8")
            merged, status = self._reconcile(filepath, full_path, base_hash, content, current)
            disk = self._file_data(filepath, full_path, current)
            return {"status": status, **disk, "content": merged}

    def _reconcile(
        self, filepath: str, full_path: Path, base_hash: str, mine: str, current: str
    ) -> tuple[str, str]:
        """Combine `mine` (edited against base_hash) with `current` (on disk).

        Returns (text, status). Raises ConflictError when they cannot be combined.
        """
        if content_hash(current) == base_hash:
            return mine, "saved"
        if mine == current:
            return mine, "unchanged"

        theirs = self._file_data(filepath, full_path, current)
        base = version_store.get(base_hash)
        if base is None:
            raise ConflictError(
                "File modified on disk and the base version is no longer available",
                reason="base_unknown",
                theirs=theirs,
            )
        result = three_way_merge(base, mine, current)
        if not result.clean:
            raise ConflictError(
                "File modified on disk on the same lines you edited",
                reason="overlap",
                theirs=theirs,
                merged=result.merged,
            )
        return result.merged, "merged"

    def _create_exclusive(self, full_path: Path, filepath: str, content: str) -> Dict[str, Any]:
        try:
            with open(full_path, "x", encoding="utf-8") as f:
                f.write(content)
        except FileExistsError:
            self.logger.info(f"File already exists (create_only): {filepath}")
            existing = full_path.read_text(encoding="utf-8")
            return {"status": "exists", **self._file_data(filepath, full_path, existing)}
        version_store.put(filepath, content)
        self.logger.info(f"Created file: {filepath} ({len(content)} bytes)")
        return {"status": "saved", **self._file_data(filepath, full_path, content)}

    @staticmethod
    def _file_data(filepath: str, full_path: Path, content: str) -> Dict[str, Any]:
        stat = full_path.stat()
        return {
            "path": filepath,
            "content": content,
            "hash": content_hash(content),
            "modified": stat.st_mtime,
            "size": stat.st_size,
        }
