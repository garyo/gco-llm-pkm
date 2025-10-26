"""Configuration management for PKM Bridge Server."""

import os
from pathlib import Path
from typing import Optional, Set
from dotenv import load_dotenv


class Config:
    """Configuration manager for PKM Bridge Server.

    Loads settings from environment variables and provides validation.
    """

    def __init__(self, env_file: Optional[str] = None):
        """Initialize configuration from environment variables.

        Args:
            env_file: Optional path to .env file. If None, uses default .env in cwd.
        """
        if env_file:
            load_dotenv(env_file)
        else:
            load_dotenv()

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

        self.skills_dir = Path(os.getenv("SKILLS_DIR", "./skills")).expanduser()
        self.skills_dir.mkdir(exist_ok=True)

        # Server Configuration
        self.port = int(os.getenv("PORT", "8000"))
        self.host = os.getenv("HOST", "127.0.0.1")
        self.debug = os.getenv("DEBUG", "true").lower() == "true"

        # Logging Configuration
        self.log_level = os.getenv("LOG_LEVEL", "INFO").upper()

        # Security - Allowed Shell Commands
        allowed_commands_str = os.getenv(
            "ALLOWED_COMMANDS",
            "date,rg,ripgrep,grep,fd,find,cat,ls,emacs,git,sed,head,tail,wc"
        )
        self.allowed_commands: Set[str] = set(allowed_commands_str.split(","))

        # System Prompt
        self.system_prompt_file = Path(__file__).parent / "system_prompt.txt"
        if not self.system_prompt_file.exists():
            raise ValueError(f"System prompt file not found: {self.system_prompt_file}")

    def get_system_prompt(self) -> str:
        """Load and render the system prompt template with configuration values.

        Returns:
            Rendered system prompt string.
        """
        template = self.system_prompt_file.read_text(encoding="utf-8")

        # Replace placeholders
        return template.format(
            ORG_DIR=self.org_dir,
            LOGSEQ_DIR=self.logseq_dir
        )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"Config(model={self.model}, org_dir={self.org_dir}, "
            f"logseq_dir={self.logseq_dir}, host={self.host}:{self.port})"
        )
