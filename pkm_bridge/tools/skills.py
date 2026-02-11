"""Skill management and working memory tools.

Provides tools for Claude to save, list, and use reusable procedures (skills),
and to maintain per-session working memory (note_to_self).
"""

import re
import stat
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import yaml

from .base import BaseTool

# Regex for valid skill names
SKILL_NAME_RE = re.compile(r'^[a-z0-9][a-z0-9-]{0,48}[a-z0-9]$')

# YAML frontmatter delimiters
SHELL_FM_START = '# ---'
SHELL_FM_END = '# ---'
MD_FM_DELIM = '---'


def _get_skills_dir(org_dir: Path) -> Path:
    """Get or create the skills directory under .pkm/skills/.

    Falls back to .pkm-skills/ if .pkm/ doesn't exist yet (pre-migration).
    """
    pkm_skills_dir = org_dir / '.pkm' / 'skills'
    if pkm_skills_dir.exists():
        return pkm_skills_dir
    # Create .pkm/skills/ if .pkm/ dir exists
    pkm_dir = org_dir / '.pkm'
    if pkm_dir.exists():
        pkm_skills_dir.mkdir(exist_ok=True)
        return pkm_skills_dir
    # Fallback: create .pkm/skills/ (new install)
    pkm_dir.mkdir(exist_ok=True)
    pkm_skills_dir.mkdir(exist_ok=True)
    return pkm_skills_dir


def _parse_shell_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from a shell script (# --- delimited)."""
    lines = content.split('\n')
    if not lines or lines[0].strip() != SHELL_FM_START:
        return {}, content

    fm_lines = []
    body_start = 1
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == SHELL_FM_END:
            body_start = i + 1
            break
        # Strip leading '# ' from frontmatter lines
        stripped = line
        if stripped.startswith('# '):
            stripped = stripped[2:]
        elif stripped.startswith('#'):
            stripped = stripped[1:]
        fm_lines.append(stripped)

    try:
        metadata = yaml.safe_load('\n'.join(fm_lines)) or {}
    except yaml.YAMLError:
        metadata = {}

    body = '\n'.join(lines[body_start:])
    return metadata, body


def _parse_md_frontmatter(content: str) -> tuple[dict, str]:
    """Parse YAML frontmatter from a markdown file (--- delimited)."""
    lines = content.split('\n')
    if not lines or lines[0].strip() != MD_FM_DELIM:
        return {}, content

    fm_lines = []
    body_start = 1
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == MD_FM_DELIM:
            body_start = i + 1
            break
        fm_lines.append(line)

    try:
        metadata = yaml.safe_load('\n'.join(fm_lines)) or {}
    except yaml.YAMLError:
        metadata = {}

    body = '\n'.join(lines[body_start:])
    return metadata, body


def _build_shell_frontmatter(metadata: dict) -> str:
    """Build shell script frontmatter block."""
    yaml_str = yaml.dump(metadata, default_flow_style=False, sort_keys=False).strip()
    fm_lines = [f'# {line}' for line in yaml_str.split('\n')]
    return SHELL_FM_START + '\n' + '\n'.join(fm_lines) + '\n' + SHELL_FM_END


def _build_md_frontmatter(metadata: dict) -> str:
    """Build markdown frontmatter block."""
    yaml_str = yaml.dump(metadata, default_flow_style=False, sort_keys=False).strip()
    return MD_FM_DELIM + '\n' + yaml_str + '\n' + MD_FM_DELIM


def _parse_skill_file(filepath: Path) -> Optional[dict]:
    """Parse a skill file and return its metadata + content."""
    try:
        content = filepath.read_text(encoding='utf-8')
    except (OSError, IOError):
        return None

    ext = filepath.suffix
    if ext == '.sh':
        metadata, body = _parse_shell_frontmatter(content)
    elif ext == '.md':
        metadata, body = _parse_md_frontmatter(content)
    else:
        return None

    metadata['_file'] = filepath.name
    metadata['_type'] = 'shell' if ext == '.sh' else 'recipe'
    metadata['_body'] = body.strip()
    return metadata


class SaveSkillTool(BaseTool):
    """Save a discovered pattern as a reusable skill."""

    def __init__(self, logger, org_dir: Path, dangerous_patterns: list[str]):
        super().__init__(logger)
        self.org_dir = org_dir
        self.dangerous_patterns = dangerous_patterns

    @property
    def name(self) -> str:
        return "save_skill"

    @property
    def description(self) -> str:
        return (
            "Save a discovered multi-step procedure or script as a reusable skill. "
            "Shell skills (.sh) can be executed directly. Recipe skills (.md) describe "
            "procedures to follow. Skills are stored in .pkm/skills/ and persist across sessions."
        )

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Kebab-case name for the skill (e.g., 'weekly-review', 'search-music-notes'). Max 50 chars, [a-z0-9-]."
                },
                "skill_type": {
                    "type": "string",
                    "enum": ["shell", "recipe"],
                    "description": "Type: 'shell' for executable .sh scripts, 'recipe' for .md procedure descriptions."
                },
                "description": {
                    "type": "string",
                    "description": "Brief description of what the skill does."
                },
                "content": {
                    "type": "string",
                    "description": "The skill content: shell script code or markdown procedure steps."
                },
                "trigger": {
                    "type": "string",
                    "description": "When to use this skill (e.g., 'user asks for weekly review')."
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags for categorization."
                }
            },
            "required": ["skill_name", "skill_type", "description", "content"]
        }

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        skill_name = params['skill_name']
        skill_type = params['skill_type']
        description = params['description']
        content = params['content']
        trigger = params.get('trigger', '')
        tags = params.get('tags', [])

        # Validate name
        if not SKILL_NAME_RE.match(skill_name):
            return "Error: skill_name must be 2-50 chars, kebab-case [a-z0-9-]."

        # For shell skills, check dangerous patterns
        if skill_type == 'shell':
            from .shell import validate_command
            is_valid, error = validate_command(content, self.dangerous_patterns)
            if not is_valid:
                return f"Error: Shell content blocked by safety check: {error}"

        skills_dir = _get_skills_dir(self.org_dir)
        ext = '.sh' if skill_type == 'shell' else '.md'
        filepath = skills_dir / f'{skill_name}{ext}'

        # Check if updating existing skill
        existing_metadata = {}
        if filepath.exists():
            parsed = _parse_skill_file(filepath)
            if parsed:
                existing_metadata = parsed

        now = datetime.utcnow().isoformat(timespec='seconds') + 'Z'
        metadata = {
            'name': skill_name,
            'description': description,
            'trigger': trigger,
            'tags': tags,
            'created': existing_metadata.get('created', now),
            'last_used': existing_metadata.get('last_used', now),
            'use_count': existing_metadata.get('use_count', 0),
        }

        # Build file content
        if skill_type == 'shell':
            fm = _build_shell_frontmatter(metadata)
            file_content = fm + '\n#!/bin/bash\nset -euo pipefail\n\n' + content.strip() + '\n'
        else:
            fm = _build_md_frontmatter(metadata)
            file_content = fm + '\n\n' + content.strip() + '\n'

        filepath.write_text(file_content, encoding='utf-8')

        # Make shell scripts executable
        if skill_type == 'shell':
            filepath.chmod(filepath.stat().st_mode | stat.S_IRUSR | stat.S_IXUSR)

        action = 'Updated' if existing_metadata else 'Created'
        self.logger.info(f"Skill {action.lower()}: {skill_name} ({skill_type})")
        return f"{action} skill '{skill_name}' ({skill_type}) at .pkm/skills/{skill_name}{ext}"


class ListSkillsTool(BaseTool):
    """List available skills with metadata."""

    def __init__(self, logger, org_dir: Path):
        super().__init__(logger)
        self.org_dir = org_dir

    @property
    def name(self) -> str:
        return "list_skills"

    @property
    def description(self) -> str:
        return (
            "List available saved skills with their descriptions, tags, and usage stats. "
            "Use before starting a multi-step task to check for existing skills."
        )

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "tag": {
                    "type": "string",
                    "description": "Optional tag filter."
                },
                "search": {
                    "type": "string",
                    "description": "Optional search text to match in name/description."
                }
            }
        }

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        tag_filter = params.get('tag', '')
        search_text = params.get('search', '').lower()

        skills_dir = _get_skills_dir(self.org_dir)
        skills = []

        for filepath in sorted(skills_dir.iterdir()):
            if filepath.suffix not in ('.sh', '.md'):
                continue
            parsed = _parse_skill_file(filepath)
            if not parsed:
                continue

            # Apply filters
            if tag_filter and tag_filter not in parsed.get('tags', []):
                continue
            if search_text:
                name = parsed.get('name', '').lower()
                desc = parsed.get('description', '').lower()
                if search_text not in name and search_text not in desc:
                    continue

            skills.append(parsed)

        if not skills:
            return "No skills found." + (" Try without filters." if tag_filter or search_text else "")

        lines = [f"Found {len(skills)} skill(s):\n"]
        for s in skills:
            stype = s.get('_type', 'unknown')
            name = s.get('name', s.get('_file', '?'))
            desc = s.get('description', '')
            use_count = s.get('use_count', 0)
            last_used = s.get('last_used', 'never')
            tags = ', '.join(s.get('tags', []))
            lines.append(
                f"- **{name}** ({stype}) — {desc}\n"
                f"  Uses: {use_count} | Last used: {last_used}"
                + (f" | Tags: {tags}" if tags else "")
            )

        return '\n'.join(lines)


class UseSkillTool(BaseTool):
    """Load a skill's content and bump its usage counter."""

    def __init__(self, logger, org_dir: Path):
        super().__init__(logger)
        self.org_dir = org_dir

    @property
    def name(self) -> str:
        return "use_skill"

    @property
    def description(self) -> str:
        return (
            "Load a saved skill's content for execution or following. "
            "For shell skills, you can pass the content to execute_shell. "
            "For recipe skills, follow the described procedure steps. "
            "Automatically tracks usage count."
        )

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Name of the skill to load."
                }
            },
            "required": ["skill_name"]
        }

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        skill_name = params['skill_name']
        skills_dir = _get_skills_dir(self.org_dir)

        # Try both extensions
        filepath = None
        for ext in ('.sh', '.md'):
            candidate = skills_dir / f'{skill_name}{ext}'
            if candidate.exists():
                filepath = candidate
                break

        if not filepath:
            return f"Skill '{skill_name}' not found. Use list_skills to see available skills."

        parsed = _parse_skill_file(filepath)
        if not parsed:
            return f"Error reading skill '{skill_name}'."

        # Bump use_count and last_used
        now = datetime.utcnow().isoformat(timespec='seconds') + 'Z'
        content = filepath.read_text(encoding='utf-8')

        if filepath.suffix == '.sh':
            metadata, body = _parse_shell_frontmatter(content)
            metadata['use_count'] = metadata.get('use_count', 0) + 1
            metadata['last_used'] = now
            fm = _build_shell_frontmatter(metadata)
            # Reconstruct: frontmatter + body (body includes #!/bin/bash etc.)
            updated = fm + '\n' + body.lstrip('\n')
        else:
            metadata, body = _parse_md_frontmatter(content)
            metadata['use_count'] = metadata.get('use_count', 0) + 1
            metadata['last_used'] = now
            fm = _build_md_frontmatter(metadata)
            updated = fm + '\n\n' + body.lstrip('\n')

        filepath.write_text(updated, encoding='utf-8')

        stype = parsed.get('_type', 'unknown')
        body_content = parsed.get('_body', '')
        desc = parsed.get('description', '')

        self.logger.info(f"Skill loaded: {skill_name} (use_count now {metadata['use_count']})")
        return (
            f"**Skill: {skill_name}** ({stype})\n"
            f"{desc}\n\n"
            f"```\n{body_content}\n```"
        )


class NoteToSelfTool(BaseTool):
    """Save a note to per-session working memory."""

    def __init__(self, logger):
        super().__init__(logger)

    @property
    def name(self) -> str:
        return "note_to_self"

    @property
    def description(self) -> str:
        return (
            "Save a note to your per-session working memory. Notes persist across queries "
            "within the same session and survive history truncation (they're in the system prompt). "
            "Use for: user preferences discovered mid-session, effective strategies, "
            "corrections to remember, or any context you want to retain."
        )

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "note": {
                    "type": "string",
                    "description": "The note to save."
                },
                "category": {
                    "type": "string",
                    "enum": ["user_preference", "discovery", "strategy", "correction", "other"],
                    "description": "Category for the note."
                }
            },
            "required": ["note"]
        }

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        note = params['note']
        category = params.get('category', 'other')
        session_id = context.get('session_id', 'default') if context else 'default'

        try:
            from ..database import get_db
            from ..db_repository import SessionNoteRepository

            db = get_db()
            try:
                SessionNoteRepository.create(
                    db=db,
                    session_id=session_id,
                    note=note,
                    category=category,
                )
            finally:
                db.close()

            self.logger.info(f"Session note saved [{category}]: {note[:80]}")
            return f"Noted [{category}]: {note}"
        except Exception as e:
            self.logger.warning(f"Failed to save session note: {e}")
            return f"Note acknowledged (storage failed: {e}): {note}"
