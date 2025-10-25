"""Skill loading and execution tools."""

import re
import yaml
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from .base import BaseTool


class SkillRegistry:
    """Registry for discovering and managing skills."""

    def __init__(self, skills_dir: Path, logger):
        """Initialize skill registry.

        Args:
            skills_dir: Directory containing skill subdirectories
            logger: Logger instance
        """
        self.skills_dir = skills_dir
        self.logger = logger
        self.skills: Dict[str, Dict[str, Any]] = {}
        self.discover_skills()

    def _split_front_matter(self, text: str) -> Tuple[Optional[Dict[str, Any]], str]:
        """Extract YAML front matter from markdown if present.

        Args:
            text: Markdown text potentially with YAML front matter

        Returns:
            Tuple of (front_matter_dict, body_text)
        """
        t = text.lstrip()
        if not t.startswith('---'):
            return None, text
        head, sep, rest = t[3:].partition('\n---')
        if not sep:
            return None, text
        try:
            fm = yaml.safe_load(head) or {}
        except Exception:
            fm = None
        body = rest.lstrip('\n')
        return fm, body

    def discover_skills(self):
        """Discover and load all skills from skills directory."""
        self.skills = {}
        for skill_file in self.skills_dir.glob("*/SKILL.md"):
            try:
                name = skill_file.parent.name
                raw = skill_file.read_text(encoding="utf-8", errors="ignore")
                fm, body = self._split_front_matter(raw)
                self.skills[name] = {
                    "path": str(skill_file),
                    "frontmatter": fm or {},
                    "body": (body if fm else raw).strip()
                }
                self.logger.info(f'Discovered skill "{name}": {self.skills[name]["frontmatter"]}')
            except Exception as e:
                self.logger.warning(f"Failed to load skill {skill_file}: {e}")

        self.logger.info(f"Found {len(self.skills)} skills: {', '.join(sorted(self.skills.keys())) or '(none)'}")

    def resolve_skill(self, name: str) -> Optional[Dict[str, Any]]:
        """Resolve a skill by exact or case-insensitive name.

        Args:
            name: Skill name to resolve

        Returns:
            Skill dict or None if not found
        """
        if name in self.skills:
            return self.skills[name]
        lname = name.lower()
        for k, v in self.skills.items():
            if k.lower() == lname:
                return v
        return None

    def render_template(self, tpl: str, vars: Dict[str, Any]) -> str:
        """Simple {var} placeholder replacement.

        Args:
            tpl: Template string with {var} placeholders
            vars: Dict of variable values

        Returns:
            Rendered template
        """
        return re.sub(r"\{([a-zA-Z0-9_]+)\}", lambda m: str(vars.get(m.group(1), m.group(0))), tpl)


class LoadSkillTool(BaseTool):
    """Load skill documentation for the model to follow."""

    def __init__(self, logger, skill_registry: SkillRegistry):
        """Initialize load skill tool.

        Args:
            logger: Logger instance
            skill_registry: Skill registry instance
        """
        super().__init__(logger)
        self.skill_registry = skill_registry

    @property
    def name(self) -> str:
        return "load_skill"

    @property
    def description(self) -> str:
        return "Load a local skill (reads skills/<name>/SKILL.md, returns front-matter + body)."

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill folder name (e.g., 'journal-navigation')"}
            },
            "required": ["name"]
        }

    def execute(self, params: Dict[str, Any]) -> str:
        """Load skill documentation.

        Args:
            params: Dict with 'name' of skill

        Returns:
            Skill front-matter and body
        """
        name = params["name"]
        sk = self.skill_registry.resolve_skill(name)
        if not sk:
            available = ", ".join(sorted(self.skill_registry.skills.keys())) or "(none)"
            return f"❌ Skill not found: {name}\nAvailable: {available}"

        fm = yaml.safe_dump(sk["frontmatter"], sort_keys=False).strip()
        body = sk["body"]
        return f"---\n{fm}\n---\n\n{body}" if fm else body


class RunSkillTool(BaseTool):
    """Execute a skill's template command."""

    def __init__(self, logger, skill_registry: SkillRegistry, execute_shell_tool):
        """Initialize run skill tool.

        Args:
            logger: Logger instance
            skill_registry: Skill registry instance
            execute_shell_tool: ExecuteShellTool instance for running rendered commands
        """
        super().__init__(logger)
        self.skill_registry = skill_registry
        self.execute_shell_tool = execute_shell_tool

    @property
    def name(self) -> str:
        return "run_skill"

    @property
    def description(self) -> str:
        return "Render & execute a skill's template command using {var} substitution."

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Skill folder name"},
                "vars": {"type": "object", "description": "Variables for template placeholders"},
                "template": {"type": "string", "description": "Optional override of the skill's template/command"}
            },
            "required": ["name"]
        }

    def execute(self, params: Dict[str, Any]) -> str:
        """Execute skill template.

        Args:
            params: Dict with 'name', optional 'vars' and 'template'

        Returns:
            Result of executing rendered command
        """
        name = params["name"]
        vars = params.get("vars")
        template_override = params.get("template")

        sk = self.skill_registry.resolve_skill(name)
        if not sk:
            return f"❌ Skill not found: {name}"

        fm = sk["frontmatter"] or {}
        tpl = template_override or fm.get("template") or fm.get("command") or ""
        if not tpl:
            return "❌ Skill has no 'template' or 'command' in front-matter, and no override provided."

        rendered = self.skill_registry.render_template(tpl, vars or {})

        # Execute the rendered command using the shell tool
        return self.execute_shell_tool.execute({"command": rendered})
