"""File editor functionality for PKM notes."""

from pathlib import Path
from typing import List, Dict
import logging


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

    def list_files(self) -> List[Dict[str, str]]:
        """List all .org and .md files in allowed directories.

        Returns:
            List of dicts with file info: {path, name, dir, modified}
        """
        files = []
        
        # Scan org directory for .org files
        if self.org_dir and self.org_dir.exists():
            for file_path in self.org_dir.rglob("*.org"):
                if file_path.is_file():
                    rel_path = file_path.relative_to(self.org_dir)
                    files.append({
                        'path': str(rel_path),
                        'full_path': f"org:{rel_path}",  # Prefix to indicate dir
                        'name': file_path.name,
                        'dir': 'org',
                        'modified': file_path.stat().st_mtime
                    })
        
        # Scan Logseq directory for .md files
        if self.logseq_dir and self.logseq_dir.exists():
            for file_path in self.logseq_dir.rglob("*.md"):
                if file_path.is_file():
                    rel_path = file_path.relative_to(self.logseq_dir)
                    files.append({
                        'path': str(rel_path),
                        'full_path': f"logseq:{rel_path}",  # Prefix to indicate dir
                        'name': file_path.name,
                        'dir': 'logseq',
                        'modified': file_path.stat().st_mtime
                    })
        
        # Sort by modified time, newest first
        files.sort(key=lambda f: f['modified'], reverse=True)
        
        return files

    def read_file(self, filepath: str) -> Dict[str, any]:
        """Read file content.

        Args:
            filepath: Path in format "org:path/to/file.org" or "logseq:path/to/file.md"

        Returns:
            Dict with content, path, modified timestamp

        Raises:
            ValueError: If file path is invalid or file doesn't exist
        """
        # Parse prefix
        if ':' in filepath:
            dir_type, rel_path = filepath.split(':', 1)
            if dir_type == 'org':
                base_dir = self.org_dir
            elif dir_type == 'logseq':
                base_dir = self.logseq_dir
            else:
                raise ValueError(f"Unknown directory type: {dir_type}")
            
            full_path = (base_dir / rel_path).resolve()
        else:
            # Legacy: try to validate without prefix
            full_path = self.validate_path(filepath)
        
        if not full_path.exists():
            raise ValueError(f"File not found: {filepath}")
        
        if not full_path.is_file():
            raise ValueError(f"Not a file: {filepath}")
        
        content = full_path.read_text(encoding='utf-8')
        
        return {
            'content': content,
            'path': filepath,
            'modified': full_path.stat().st_mtime,
            'size': full_path.stat().st_size
        }

    def write_file(self, filepath: str, content: str) -> Dict[str, any]:
        """Write file content.

        Args:
            filepath: Path in format "org:path/to/file.org" or "logseq:path/to/file.md"
            content: File content to write

        Returns:
            Dict with status and modified timestamp

        Raises:
            ValueError: If file path is invalid
        """
        # Parse prefix
        if ':' in filepath:
            dir_type, rel_path = filepath.split(':', 1)
            if dir_type == 'org':
                base_dir = self.org_dir
            elif dir_type == 'logseq':
                base_dir = self.logseq_dir
            else:
                raise ValueError(f"Unknown directory type: {dir_type}")
            
            full_path = (base_dir / rel_path).resolve()
        else:
            # Legacy: try to validate without prefix
            full_path = self.validate_path(filepath)
        
        # Ensure parent directory exists
        full_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Write file
        full_path.write_text(content, encoding='utf-8')
        
        self.logger.info(f"Saved file: {filepath} ({len(content)} bytes)")
        
        return {
            'status': 'saved',
            'path': filepath,
            'modified': full_path.stat().st_mtime,
            'size': len(content)
        }
