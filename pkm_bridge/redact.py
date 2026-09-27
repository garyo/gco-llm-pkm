"""Keep credentials out of logs and out of LLM-driven subprocesses.

Tool parameters are written to the database and the server log verbatim, and
the model sometimes puts credentials in them (e.g. a curl with an inline
Authorization header). `redact_secrets` masks those before anything is
persisted. `subprocess_env` gives shell tools an environment without the
server's own secrets, so a prompt-injected `env` or `curl -d "$(env)"` has
nothing to leak.
"""

import os
import re
from typing import Any, Dict

MASK = "[REDACTED]"

# Environment variables whose *names* mark them as secret.
_SECRET_NAME = re.compile(r"KEY|SECRET|TOKEN|PASS|CREDENTIAL|PRIVATE|^DATABASE_URL$", re.I)

# Each pattern keeps group 1 (the label) and masks the credential after it.
_PATTERNS = [
    # Authorization: Bearer xyz / Basic xyz / Token xyz
    re.compile(r"(authorization\s*[:=]\s*[\"']?\s*(?:bearer|basic|token)\s+)[^\s\"']+", re.I),
    # Bare "Bearer xyz" (e.g. in a JSON header map)
    re.compile(r"(\bbearer\s+)[A-Za-z0-9._~+/=-]{8,}", re.I),
    # curl -u user:pass / --user user:pass (keep the user; not `date -u +%H:%M`)
    re.compile(r"((?:^|\s)(?:-u|--user)\s+[\"']?[\w.@-]+:)[^\s\"']+"),
    # Credentials embedded in URLs: scheme://user:pass@host
    re.compile(r"(://[^\s/:@]+:)[^\s/@]+(?=@)"),
    # key=value / "key": "value" for secret-ish keys (headers, query strings, JSON)
    re.compile(
        r"((?:api[_-]?key|x-api-key|access[_-]?token|refresh[_-]?token|client[_-]?secret"
        r"|password|passwd|secret|token)[\"']?\s*[:=]\s*[\"']?)[^\s\"'&,}]+",
        re.I,
    ),
]

_MIN_SECRET_LEN = 8


def _env_secret_values() -> list[str]:
    """Values of secret-named environment variables, longest first."""
    values = {
        v for k, v in os.environ.items() if _SECRET_NAME.search(k) and len(v) >= _MIN_SECRET_LEN
    }
    return sorted(values, key=len, reverse=True)


def redact_secrets(text: str) -> str:
    """Mask credentials in free text: known secret values plus common credential syntax."""
    if not text:
        return text
    for value in _env_secret_values():
        text = text.replace(value, MASK)
    for pattern in _PATTERNS:
        text = pattern.sub(lambda m: m.group(1) + MASK, text)
    return text


def redact_obj(obj: Any) -> Any:
    """Recursively redact every string inside a JSON-like structure."""
    if isinstance(obj, str):
        return redact_secrets(obj)
    if isinstance(obj, dict):
        return {k: redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v) for v in obj]
    return obj


def subprocess_env() -> Dict[str, str]:
    """The current environment minus secret-named variables, for LLM-driven subprocesses."""
    return {k: v for k, v in os.environ.items() if not _SECRET_NAME.search(k)}
