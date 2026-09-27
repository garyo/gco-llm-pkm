"""Configuration management for PKM Bridge Server."""

import os
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from pkm_bridge.history_manager import TIME_NOTE_PREFIX

DEFAULT_EDITOR_BASE_URL = "https://pkm.oberbrunner.com/editor"

# A prompt template may open with an HTML comment for maintainers; it is not sent.
_MAINTAINER_COMMENT = re.compile(r"\A\s*<!--.*?-->", re.DOTALL)

# Dangerous command patterns (blacklist) blocked before running shell commands
# or saving skills. Real security comes from Docker isolation, limited filesystem
# access, and git backups — this list just stops accidents and obvious disasters.
# Exposed at module level so non-Config call sites (e.g. the retrospective's
# auto-proposed skills) can reuse the same defaults instead of disabling validation.
DEFAULT_DANGEROUS_PATTERNS = [
    # Destructive operations from root
    r"rm\s+(-[rf]+\s+)?/",
    r"rm\s+-[rf]*\s+\*\s*$",
    # Recursive/force rm of a relative directory, e.g. "rm -rf journals/"
    # (requires the recursive flag and a trailing slash so plain
    # "rm -f note.org" single-file deletes are not blocked).
    r"\brm\s+-\w*r\w*\s+\S+/",
    # find ... -delete
    r"\bfind\b.*-delete\b",
    # truncate to zero bytes
    r"\btruncate\s+-s\s?0\b",
    # dd writing to a file/device
    r"\bdd\b.*\bof=",
    # secure-erase / filesystem creation tools
    r"\bshred\b",
    r"\bmkfs\b",
    # Fork bombs
    r":\(\)\s*\{.*\:",
    r"fork\s*\(",
    # Download and execute
    r"curl.*\|.*\b(sh|bash)\b",
    r"wget.*\|.*\b(sh|bash)\b",
    # Network sockets (if network access is not needed)
    r"/dev/(tcp|udp)/",
    # Kernel/system tampering
    r"/proc/sys/",
    r"/sys/class/",
    # Package managers (prevent Claude from installing things)
    r"\b(apt|yum|dnf|pacman|brew)\s+install",
    r"\bpip\s+install",
    r"\bnpm\s+install\s+-g",
]


class Config:
    """Configuration manager for PKM Bridge Server.

    Loads settings from environment variables and provides validation.
    """

    def __init__(self, env_file: Optional[str] = None):
        """Initialize configuration from environment variables.

        Args:
            env_file: Optional path to .env file. If None, prefers .env.local, then .env
        """
        if env_file:
            load_dotenv(env_file)
        else:
            # Prefer .env.local (local dev) over .env (Docker/production)
            if Path(".env.local").exists():
                load_dotenv(".env.local")
            else:
                load_dotenv(".env")

        # API Configuration
        self.anthropic_api_key = os.getenv("ANTHROPIC_API_KEY")
        if not self.anthropic_api_key:
            raise ValueError("ANTHROPIC_API_KEY environment variable must be set")

        # Model Selection
        self.model = os.getenv("MODEL", "claude-haiku-4-5")

        # Directory Paths
        self.org_dir = Path(os.getenv("ORG_DIR", "~/Documents/org-agenda")).expanduser()
        if not self.org_dir.exists():
            raise ValueError(f"ORG_DIR does not exist: {self.org_dir}")

        logseq_dir_str = os.getenv("LOGSEQ_DIR", "~/Logseq Notes")
        self.logseq_dir = Path(logseq_dir_str).expanduser()
        if os.getenv("LOGSEQ_DIR") and not self.logseq_dir.exists():
            self.logseq_dir = None  # Explicitly set, but doesn't exist
        elif not self.logseq_dir.exists():
            self.logseq_dir = None  # Default path doesn't exist

        # Server Configuration
        self.port = int(os.getenv("PORT", "8000"))
        self.host = os.getenv("HOST", "127.0.0.1")
        self.debug = os.getenv("DEBUG", "true").lower() == "true"

        # Logging Configuration
        self.log_level = os.getenv("LOG_LEVEL", "INFO").upper()

        # Timezone Configuration
        timezone_str = os.getenv("TIMEZONE", "America/New_York")
        try:
            self.timezone = ZoneInfo(timezone_str)
        except Exception as e:
            print(f"Warning: Invalid timezone '{timezone_str}', using system default. Error: {e}")
            self.timezone = None  # Use system default

        # Security - Dangerous command patterns (blacklist), see module constant.
        self.dangerous_patterns = list(DEFAULT_DANGEROUS_PATTERNS)

        # Authentication Configuration
        self.auth_enabled = os.getenv("AUTH_ENABLED", "true").lower() == "true"
        self.jwt_secret = os.getenv("JWT_SECRET", "")
        self.password_hash = os.getenv("PASSWORD_HASH", "")
        self.token_expiry_hours = int(os.getenv("TOKEN_EXPIRY_HOURS", "168"))

        # Validate auth config if enabled
        if self.auth_enabled:
            if not self.jwt_secret or self.jwt_secret == "change-this-to-a-random-secret-key":
                raise ValueError(
                    "JWT_SECRET must be set to a secure random value. "
                    'Generate one with: python3 -c "import secrets; print(secrets.token_hex(32))"'
                )
            if not self.password_hash:
                raise ValueError(
                    "PASSWORD_HASH must be set. "
                    "Generate one with: python3 -c "
                    "\"import hashlib; print(hashlib.sha256(b'your-password').hexdigest())\""
                )

        # System Prompt
        self.system_prompt_file = Path(__file__).parent / "system_prompt.txt"
        if not self.system_prompt_file.exists():
            raise ValueError(f"System prompt file not found: {self.system_prompt_file}")

    def render_prompt(self, filename: str) -> str:
        """Load a prompt template from config/ and fill in its placeholders.

        The template's leading HTML comment is for maintainers and is dropped.
        Placeholders are filled with str.replace rather than str.format, so
        literal braces such as {ticktick:ID} are written singly.
        """
        text = (Path(__file__).parent / filename).read_text(encoding="utf-8")
        text = _MAINTAINER_COMMENT.sub("", text, count=1)
        placeholders = {
            "{ORG_DIR}": str(self.org_dir),
            "{LOGSEQ_DIR}": str(self.logseq_dir or "(not configured)"),
            "{EDITOR_BASE_URL}": os.getenv("EDITOR_BASE_URL", DEFAULT_EDITOR_BASE_URL),
        }
        for placeholder, value in placeholders.items():
            text = text.replace(placeholder, value)
        return text.strip()

    def get_user_context(self, user_context: Optional[str] = None) -> Optional[str]:
        """The given user context, else config/user_context.txt if present."""
        if user_context is None:
            user_context_file = Path(__file__).parent / "user_context.txt"
            if user_context_file.exists():
                user_context = user_context_file.read_text(encoding="utf-8")
        return user_context

    def get_system_prompt(self, user_context: Optional[str] = None) -> str:
        """The web app's full system prompt as one string, e.g. for session records."""
        return "".join(block["text"] for block in self.get_system_prompt_blocks(user_context))

    def get_system_prompt_blocks(
        self,
        user_context: Optional[str] = None,
        learned_rules: Optional[List] = None,
    ) -> list:
        """Get system prompt as structured blocks optimized for prompt caching.

        Every block is stable across requests, so the prompt cache for the
        conversation history after it survives from turn to turn. The current
        date/time goes in the user message instead (see current_time_note).

        Structure:
        - Block 1: Base instructions (cached - most stable)
        - Block 2: User context (changes occasionally)
        - Block 3: Learned rules (changes at most daily)
        The first and last blocks carry cache breakpoints.

        Args:
            user_context: Optional user context string. If None, will try to load from file.
            learned_rules: Optional list of LearnedRule objects to inject into the prompt.

        Returns:
            List of dicts with 'type', 'text', and optionally 'cache_control' keys.
        """
        template = self.render_prompt(self.system_prompt_file.name)
        user_context = self.get_user_context(user_context)

        blocks = []

        # Block 1: Static base instructions (cached - most stable)
        blocks.append({"type": "text", "text": template, "cache_control": {"type": "ephemeral"}})

        # Block 2: User context (cached - changes occasionally)
        if user_context:
            blocks.append(
                {
                    "type": "text",
                    "text": f"\n\n# USER CONTEXT\n\n{user_context.strip()}",
                    "cache_control": {"type": "ephemeral"},
                }
            )

        # Block 3: Learned patterns (cached - changes at most daily)
        rules_text = self.get_learned_patterns_block(learned_rules)
        if rules_text:
            blocks.append(
                {"type": "text", "text": rules_text, "cache_control": {"type": "ephemeral"}}
            )

        # Two system breakpoints (base + the last block) leave two for the
        # messages; the API allows four in all.
        for block in blocks[1:-1]:
            block.pop("cache_control", None)

        return blocks

    def current_time_note(self, user_timezone: Optional[str] = None) -> str:
        """The date/time note that opens each user message.

        It lives in the user turn rather than the system prompt so the system
        prompt stays byte-stable: any change there invalidates the prompt cache
        for the entire conversation history that follows it. Minute granularity,
        in the client's timezone if valid, else the configured one.
        """
        tz = self.timezone
        if user_timezone:
            try:
                tz = ZoneInfo(user_timezone)
            except Exception:
                pass
        now = datetime.now(tz) if tz else datetime.now()
        return f"{TIME_NOTE_PREFIX}{now.strftime('%A, %B %-d, %Y, %H:%M %Z (%Y-%m-%dT%H:%M%z)')}]"

    def get_learned_patterns_block(self, learned_rules=None) -> str:
        """Return the learned-patterns block injected into the system prompt.

        Precedence:
        1. The SI agent's curated `.pkm/learned-patterns.md`, injected verbatim.
           This is rewritten each run and kept within budget, so it is the block
           that actually reaches the live model.
        2. Fallback: `_format_learned_rules(learned_rules)` over the raw DB rules,
           so injection keeps working before the first curated run (or if the
           file is deleted).

        Shared by all three injection sites (custom app, MCP tools, MCP resources).
        """
        curated = self._read_curated_patterns()
        if curated:
            return "\n\n" + curated
        if learned_rules:
            return self._format_learned_rules(learned_rules)
        return ""

    def _read_curated_patterns(self) -> str:
        """Read `.pkm/learned-patterns.md`, or '' if absent/empty/unreadable."""
        path = self.org_dir / ".pkm" / "learned-patterns.md"
        try:
            if path.exists():
                return path.read_text(encoding="utf-8").strip()
        except OSError:
            pass
        return ""

    @staticmethod
    def _format_learned_rules(rules) -> str:
        """Format learned rules into a markdown block for the system prompt.

        Budget: max ~1500 tokens (~6KB).
        """
        # Exclude prompt_amendment rules (review-only, not auto-injected)
        EXCLUDED_TYPES = {"prompt_amendment"}

        sections = {
            "retrieval": [],
            "vocabulary": [],
            "preference": [],
            "embedding_gap": [],
            "tool_strategy": [],
            "approved_amendment": [],
            "general": [],
        }

        for rule in rules:
            rule_type = getattr(rule, "rule_type", "general")
            rule_text = getattr(rule, "rule_text", "")
            if rule_type in EXCLUDED_TYPES:
                continue
            if rule_type in sections:
                sections[rule_type].append(f"- {rule_text}")
            else:
                sections["general"].append(f"- {rule_text}")

        parts = [
            "\n\n# LEARNED PATTERNS\n"
            "Based on analysis of past sessions, these patterns improve retrieval:\n"
        ]

        section_headers = {
            "retrieval": "## Retrieval Hints",
            "vocabulary": "## Vocabulary",
            "preference": "## Preferences",
            "embedding_gap": "## Embedding Gaps",
            "tool_strategy": "## Tool Strategy",
            "approved_amendment": "## Approved Amendments",
            "general": "## General Insights",
        }

        for key, header in section_headers.items():
            if sections[key]:
                parts.append(f"\n{header}")
                parts.extend(sections[key])

        text = "\n".join(parts)

        # Budget cap: ~6KB / ~1500 tokens
        if len(text) > 6000:
            text = text[:6000] + "\n... (truncated)"

        return text

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"Config(model={self.model}, org_dir={self.org_dir}, "
            f"logseq_dir={self.logseq_dir}, host={self.host}:{self.port})"
        )
