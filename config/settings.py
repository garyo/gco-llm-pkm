"""Configuration management for PKM Bridge Server."""

import os
from pathlib import Path
from typing import Optional, Set
from dotenv import load_dotenv
from datetime import datetime

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

        # Security - Allowed Shell Commands
        allowed_commands_str = os.getenv(
            "ALLOWED_COMMANDS",
            "date,rg,ripgrep,grep,fd,find,cat,ls,emacs,git,sed,head,tail,wc"
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

    def get_system_prompt(self) -> str:
        """Load and render the system prompt template with configuration values.

        Loads system_prompt.txt and optionally user_context.txt (if present).
        Personal information in user_context.txt is kept separate for privacy.

        Returns:
            Rendered system prompt string.
        """
        template = self.system_prompt_file.read_text(encoding="utf-8")

        # Load optional user context (personal information)
        user_context_file = Path(__file__).parent / "user_context.txt"
        if user_context_file.exists():
            user_context = user_context_file.read_text(encoding="utf-8")
            # Insert user context after the placeholder comment
            template = template.replace(
                "# USER CONTEXT loaded from user_context.txt (if present)",
                user_context
            )

        # Replace placeholders
        today = datetime.today()
        return template.format(
            ORG_DIR=self.org_dir,
            LOGSEQ_DIR=self.logseq_dir,
            TODAY=today.strftime("%Y-%m-%d")
        )

    def __repr__(self) -> str:
        """String representation for debugging."""
        return (
            f"Config(model={self.model}, org_dir={self.org_dir}, "
            f"logseq_dir={self.logseq_dir}, host={self.host}:{self.port})"
        )
