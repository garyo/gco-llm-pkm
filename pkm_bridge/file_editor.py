"""File editor functionality for PKM notes."""

import logging
import os
import tempfile
from pathlib import Path
from typing import Dict, List

# Cap for read_file to keep large notes from blowing the LLM token budget.
# Editor/checkbox callers opt out (max_chars=None) since they need full content.
READ_FILE_CHAR_CAP = 200_000


class ConflictError(Exception):
    """Raised when a save is rejected due to a stale mtime (optimistic concurrency)."""


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

        # Scan org directory for .org files
        if self.org_dir and self.org_dir.exists():
            for file_path in self.org_dir.rglob("*.org"):
                if file_path.is_file():
                    rel_path = file_path.relative_to(self.org_dir)

                    # Skip dotfiles (hidden files, Syncthing temp files, etc.)
                    if any(part.startswith('.') for part in rel_path.parts):
                        continue

                    parts_lower = [part.lower() for part in rel_path.parts]

                    # Classify: journals, pages (default), or other (bak)
                    if 'bak' in parts_lower:
                        file_type = 'other'
                    elif 'journals' in parts_lower:
                        file_type = 'journal'
                    elif 'assets' in parts_lower:
                        file_type = 'other'
                    else:
                        file_type = 'page'

                    files.append({
                        'path': str(rel_path),
                        'full_path': f"org:{rel_path}",
                        'name': file_path.name,
                        'dir': 'org',
                        'type': file_type,
                        'modified': file_path.stat().st_mtime
                    })

        # Scan Logseq directory for .md files
        if self.logseq_dir and self.logseq_dir.exists():
            for file_path in self.logseq_dir.rglob("*.md"):
                if file_path.is_file():
                    rel_path = file_path.relative_to(self.logseq_dir)

                    # Skip dotfiles (hidden files, Syncthing temp files, etc.)
                    if any(part.startswith('.') for part in rel_path.parts):
                        continue

                    parts_lower = [part.lower() for part in rel_path.parts]

                    # Classify: journals (*/journals/), pages (*/pages/), or other
                    if 'bak' in parts_lower:
                        file_type = 'other'
                    elif 'version-files' in parts_lower:
                        file_type = 'other'
                    elif 'sync-conflict' in str(rel_path):
                        file_type = 'other'
                    elif 'journals' in parts_lower:
                        file_type = 'journal'
                    elif 'pages' in parts_lower:
                        file_type = 'page'
                    else:
                        file_type = 'other'

                    files.append({
                        'path': str(rel_path),
                        'full_path': f"logseq:{rel_path}",
                        'name': file_path.name,
                        'dir': 'logseq',
                        'type': file_type,
                        'modified': file_path.stat().st_mtime
                    })

        return files

    def read_file(
        self,
        filepath: str,
        offset: int = 0,
        max_chars: int | None = READ_FILE_CHAR_CAP,
    ) -> Dict[str, any]:
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

        return {
            'content': content,
            'path': filepath,
            'modified': full_path.stat().st_mtime,
            'size': full_path.stat().st_size,
            'truncated': truncated,
        }

    def write_file(
        self,
        filepath: str,
        content: str,
        create_only: bool = False,
        expected_mtime: float | None = None,
    ) -> Dict[str, any]:
        """Write file content.

        Args:
            filepath: Path in format "org:path/to/file.org" or "logseq:path/to/file.md"
            content: File content to write
            create_only: If True, only create the file if it doesn't exist (atomic check)
            expected_mtime: If set, reject the write if the file's current mtime is newer
                            (optimistic concurrency control to prevent silent overwrites).

        Returns:
            Dict with status and modified timestamp.
            Status is 'saved' for new/updated files, 'exists' if create_only and file exists.

        Raises:
            ValueError: If file path is invalid
            FileExistsError: (via create_only)
            ConflictError: If expected_mtime is stale (caller should return 409)
        """
        # Fallback keeps edits targeting the existing file (wherever it lives)
        # instead of creating a duplicate at the requested location.
        full_path, filepath = self._resolve_with_fallback(filepath)

        # Ensure parent directory exists
        full_path.parent.mkdir(parents=True, exist_ok=True)

        # Optimistic concurrency check: reject if file changed since client loaded it
        if expected_mtime is not None and full_path.exists():
            actual_mtime = full_path.stat().st_mtime
            if actual_mtime > expected_mtime:
                raise ConflictError(
                    f"File modified on disk (expected mtime {expected_mtime}, "
                    f"actual {actual_mtime})"
                )

        # Write file
        if create_only:
            # Use exclusive create mode - atomically fails if file exists
            try:
                with open(full_path, 'x', encoding='utf-8') as f:
                    f.write(content)
                self.logger.info(f"Created file: {filepath} ({len(content)} bytes)")
                return {
                    'status': 'saved',
                    'path': filepath,
                    'modified': full_path.stat().st_mtime,
                    'size': len(content)
                }
            except FileExistsError:
                self.logger.info(f"File already exists (create_only): {filepath}")
                return {
                    'status': 'exists',
                    'path': filepath,
                    'modified': full_path.stat().st_mtime,
                    'size': full_path.stat().st_size
                }
        else:
            self._atomic_write(full_path, content)
            self.logger.info(f"Saved file: {filepath} ({len(content)} bytes)")
            return {
                'status': 'saved',
                'path': filepath,
                'modified': full_path.stat().st_mtime,
                'size': len(content)
            }

    @staticmethod
    def _atomic_write(full_path: Path, content: str) -> None:
        """Write content atomically: temp file in the same dir, then os.replace().

        A crash or a Syncthing read mid-write can never see a partially written note —
        readers observe either the old or the new file, never a truncated one.
        """
        directory = full_path.parent
        fd, tmp_name = tempfile.mkstemp(
            dir=directory, prefix=f".{full_path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, full_path)
        except BaseException:
            # Best-effort cleanup; os.replace already consumed tmp_name on success.
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
