"""Open file in editor tool."""

from pathlib import Path
from typing import Dict, Any
from .base import BaseTool


class OpenFileTool(BaseTool):
    """Open a file in the web editor interface."""

    def __init__(self, logger, org_dir: Path, logseq_dir: Path | None = None):
        """Initialize open file tool.

        Args:
            logger: Logger instance
            org_dir: Primary org-mode directory
            logseq_dir: Optional Logseq directory
        """
        super().__init__(logger)
        self.org_dir = org_dir
        self.logseq_dir = logseq_dir

    @property
    def name(self) -> str:
        return "open_file"

    @property
    def description(self) -> str:
        return """Open a file in the web editor interface. Use this when the user asks to edit a file.

Accepts either:
- Absolute path (e.g., /path/to/org-agenda/file.org)
- Relative path from org or logseq directory (e.g., journals/2024-01-15.org)

The file will be opened in the editor tab of the web interface."""

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "filepath": {
                    "type": "string",
                    "description": "Path to the file to open (absolute or relative)"
                }
            },
            "required": ["filepath"]
        }

    def execute(self, params: Dict[str, Any]) -> str:
        """Open a file in the editor.

        Args:
            params: Dict with 'filepath'

        Returns:
            Success message or error
        """
        filepath = params.get("filepath", "").strip()
        if not filepath:
            return "❌ Error: No filepath provided"

        try:
            # Convert to Path object
            file_path = Path(filepath)

            # Determine if this is an absolute or relative path
            if file_path.is_absolute():
                # Try to resolve against our known directories
                resolved_path = file_path.resolve()

                # Check if it's in org_dir
                if self.org_dir and str(resolved_path).startswith(str(self.org_dir.resolve())):
                    relative_path = resolved_path.relative_to(self.org_dir.resolve())
                    formatted_path = f"org:{relative_path}"
                    base_dir = self.org_dir
                # Check if it's in logseq_dir
                elif self.logseq_dir and str(resolved_path).startswith(str(self.logseq_dir.resolve())):
                    relative_path = resolved_path.relative_to(self.logseq_dir.resolve())
                    formatted_path = f"logseq:{relative_path}"
                    base_dir = self.logseq_dir
                else:
                    return f"❌ Error: File '{filepath}' is not within allowed directories (org or logseq)"

                full_path = resolved_path
            else:
                # Relative path - try org_dir first, then logseq_dir
                org_path = self.org_dir / file_path if self.org_dir else None
                logseq_path = self.logseq_dir / file_path if self.logseq_dir else None

                if org_path and org_path.exists():
                    formatted_path = f"org:{file_path}"
                    full_path = org_path.resolve()
                    base_dir = self.org_dir
                elif logseq_path and logseq_path.exists():
                    formatted_path = f"logseq:{file_path}"
                    full_path = logseq_path.resolve()
                    base_dir = self.logseq_dir
                else:
                    return f"❌ Error: File '{filepath}' not found in org or logseq directories"

            # Verify file exists
            if not full_path.exists():
                return f"❌ Error: File '{filepath}' does not exist"

            if not full_path.is_file():
                return f"❌ Error: '{filepath}' is not a file"

            # Security check: ensure path is actually within allowed directory
            try:
                full_path.relative_to(base_dir.resolve())
            except ValueError:
                return f"❌ Error: Security violation - path traversal detected"

            # Broadcast event to frontend via SSE
            from pkm_bridge.events import event_manager
            event_manager.broadcast('open_file', {
                'path': formatted_path,
                'absolute_path': str(full_path)
            })

            self.logger.info(f"Opening file in editor: {formatted_path}")

            return f"✅ Opening '{filepath}' in editor tab..."

        except Exception as e:
            error_msg = f"❌ Error opening file: {str(e)}"
            self.logger.error(f"{error_msg} (filepath: {filepath})")
            return error_msg
