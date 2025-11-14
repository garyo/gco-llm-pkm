"""Configuration management for PKM Bridge Server."""

import os
from pathlib import Path
from typing import Optional, Set
from dotenv import load_dotenv
from datetime import datetime
from zoneinfo import ZoneInfo

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
            if Path('.env.local').exists():
                load_dotenv('.env.local')
            else:
                load_dotenv('.env')

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

        # Security - Allowed Shell Commands
        allowed_commands_str = os.getenv(
            "ALLOWED_COMMANDS",
            "date,rg,ripgrep,grep,fd,find,cat,ls,emacs,git,sed,awk,head,tail,wc"
        )
        self.allowed_commands: Set[str] = set(allowed_commands_str.split(","))

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
                    "Generate one with: python3 -c \"import secrets; print(secrets.token_hex(32))\""
                )
            if not self.password_hash:
                raise ValueError(
                    "PASSWORD_HASH must be set. "
                    "Generate one with: python3 -c \"import hashlib; print(hashlib.sha256(b'your-password').hexdigest())\""
                )

        # System Prompt
        self.system_prompt_file = Path(__file__).parent / "system_prompt.txt"
        if not self.system_prompt_file.exists():
            raise ValueError(f"System prompt file not found: {self.system_prompt_file}")

    def get_system_prompt(self, user_context: Optional[str] = None, user_timezone: Optional[str] = None) -> str:
        """Load and render the system prompt template with configuration values.

        Loads system_prompt.txt and optionally user_context (from database or file).
        Personal information in user_context is kept separate for privacy.

        Args:
            user_context: Optional user context string. If None, will try to load from file.
            user_timezone: Optional timezone string from client (e.g., 'America/New_York').
                          If provided, uses client's timezone. Otherwise falls back to server config.

        Returns:
            Rendered system prompt string.
        """
        template = self.system_prompt_file.read_text(encoding="utf-8")

        # Use provided user context, or fall back to file
        if user_context is None:
            user_context_file = Path(__file__).parent / "user_context.txt"
            if user_context_file.exists():
                user_context = user_context_file.read_text(encoding="utf-8")

        # Insert user context if available
        if user_context:
            template = template.replace(
                "# USER CONTEXT loaded from user_context.txt (if present)",
                user_context
            )

        # Replace placeholders
        return template.format(
            ORG_DIR=self.org_dir,
            LOGSEQ_DIR=self.logseq_dir)

    def get_system_prompt_blocks(self, user_context: Optional[str] = None, user_timezone: Optional[str] = None) -> list:
        """Get system prompt as structured blocks optimized for prompt caching.

        Returns a list of blocks where static content comes first (cached),
        followed by dynamic content like dates (not cached).

        Structure:
        - Block 1: Base instructions (cached - most stable)
        - Block 2: User context (cached - changes occasionally)
        - Block 3: Today's date (NOT cached - changes daily)

        Args:
            user_context: Optional user context string. If None, will try to load from file.
            user_timezone: Optional timezone string from client (e.g., 'America/New_York').
                          If provided, uses client's timezone. Otherwise falls back to server config.

        Returns:
            List of dicts with 'type', 'text', and optionally 'cache_control' keys.
        """
        template = self.system_prompt_file.read_text(encoding="utf-8")

        # Use provided user context, or fall back to file
        if user_context is None:
            user_context_file = Path(__file__).parent / "user_context.txt"
            if user_context_file.exists():
                user_context = user_context_file.read_text(encoding="utf-8")

        # Replace static placeholders (paths don't change)
        template = template.replace("{ORG_DIR}", str(self.org_dir))
        template = template.replace("{LOGSEQ_DIR}", str(self.logseq_dir))

        # Remove user context placeholder from base template
        template = template.replace(
            "# USER CONTEXT loaded from user_context.txt (if present)\n\n",
            ""
        )

        blocks = []

        # Block 1: Static base instructions (cached - most stable)
        blocks.append({
            "type": "text",
            "text": template.strip(),
            "cache_control": {"type": "ephemeral"}
        })

        # Block 2: User context (cached - changes occasionally)
        if user_context:
            blocks.append({
                "type": "text",
                "text": f"\n\n# USER CONTEXT\n\n{user_context.strip()}",
                "cache_control": {"type": "ephemeral"}
            })

        # Block 3: Today's date (NOT cached - changes daily)
        # Get current time in user's timezone
        # Priority: user_timezone (from client) > self.timezone (from config) > system default
        timezone_to_use = None
        if user_timezone:
            try:
                timezone_to_use = ZoneInfo(user_timezone)
            except Exception as e:
                print(f"Warning: Invalid user timezone '{user_timezone}', falling back. Error: {e}")
                timezone_to_use = self.timezone
        else:
            timezone_to_use = self.timezone

        if timezone_to_use:
            now = datetime.now(timezone_to_use)
        else:
            now = datetime.now()

        timestring = now.strftime('%A, %B %d, %Y, %H:%M:%S %Z')
        blocks.append({
            "type": "text",
            "text": f"\n\nCurrent date/time is {now.isoformat()} or {timestring}."
        })

        return blocks

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"Config(model={self.model}, org_dir={self.org_dir}, "
            f"logseq_dir={self.logseq_dir}, host={self.host}:{self.port})"
        )
