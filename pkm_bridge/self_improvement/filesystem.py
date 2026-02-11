"""Filesystem helpers for the .pkm/ directory structure.

Manages the .pkm/ directory under ORG_DIR, including migration from
the old .pkm-skills/ location.
"""

import os
import shutil
from pathlib import Path


def get_pkm_dir(org_dir: str | Path | None = None) -> Path:
    """Get or create the .pkm/ base directory.

    Args:
        org_dir: ORG_DIR path. If None, reads from environment.

    Returns:
        Path to .pkm/ directory (created if needed).
    """
    if org_dir is None:
        org_dir = os.getenv("ORG_DIR", "")
    pkm_dir = Path(org_dir).expanduser() / ".pkm"
    pkm_dir.mkdir(exist_ok=True)
    return pkm_dir


def ensure_pkm_structure(org_dir: str | Path | None = None) -> Path:
    """Create the full .pkm/ directory structure and migrate skills.

    Creates:
        .pkm/skills/
        .pkm/memory/
        .pkm/runs/

    Also migrates .pkm-skills/ -> .pkm/skills/ if the old dir exists.

    Returns:
        Path to .pkm/ directory.
    """
    pkm_dir = get_pkm_dir(org_dir)

    # Create subdirectories
    (pkm_dir / "skills").mkdir(exist_ok=True)
    (pkm_dir / "memory").mkdir(exist_ok=True)
    (pkm_dir / "runs").mkdir(exist_ok=True)

    # Migrate from old .pkm-skills/ if it exists and .pkm/skills/ is empty
    org_path = pkm_dir.parent
    old_skills_dir = org_path / ".pkm-skills"
    new_skills_dir = pkm_dir / "skills"

    if old_skills_dir.exists() and old_skills_dir.is_dir():
        # Move files from old to new (skip if file already exists in new)
        for src_file in old_skills_dir.iterdir():
            dst_file = new_skills_dir / src_file.name
            if not dst_file.exists():
                shutil.copy2(str(src_file), str(dst_file))

        # Create symlink for backward compat if not already a symlink
        if not old_skills_dir.is_symlink():
            # Remove the old directory (we've copied everything)
            shutil.rmtree(str(old_skills_dir))
            # Create symlink: .pkm-skills -> .pkm/skills
            old_skills_dir.symlink_to(new_skills_dir)

    return pkm_dir


def get_skills_dir(org_dir: str | Path | None = None) -> Path:
    """Get the skills directory (.pkm/skills/), creating if needed."""
    pkm_dir = get_pkm_dir(org_dir)
    skills_dir = pkm_dir / "skills"
    skills_dir.mkdir(exist_ok=True)
    return skills_dir


def get_memory_dir(org_dir: str | Path | None = None) -> Path:
    """Get the memory directory (.pkm/memory/), creating if needed."""
    pkm_dir = get_pkm_dir(org_dir)
    mem_dir = pkm_dir / "memory"
    mem_dir.mkdir(exist_ok=True)
    return mem_dir


def get_runs_dir(org_dir: str | Path | None = None) -> Path:
    """Get the runs directory (.pkm/runs/), creating if needed."""
    pkm_dir = get_pkm_dir(org_dir)
    runs_dir = pkm_dir / "runs"
    runs_dir.mkdir(exist_ok=True)
    return runs_dir


def read_memory_file(category: str, org_dir: str | Path | None = None) -> str:
    """Read a memory file by category name.

    Args:
        category: One of 'observations', 'plans', 'user-profile', 'self-critique'.
        org_dir: ORG_DIR path.

    Returns:
        File contents, or empty string if file doesn't exist.
    """
    mem_dir = get_memory_dir(org_dir)
    filepath = mem_dir / f"{category}.md"
    if filepath.exists():
        return filepath.read_text(encoding="utf-8")
    return ""


def write_memory_file(
    category: str,
    content: str,
    org_dir: str | Path | None = None,
    append: bool = False,
) -> Path:
    """Write or append to a memory file.

    Args:
        category: Memory category name.
        content: Content to write.
        org_dir: ORG_DIR path.
        append: If True, append to existing content.

    Returns:
        Path to the written file.
    """
    mem_dir = get_memory_dir(org_dir)
    filepath = mem_dir / f"{category}.md"

    if append and filepath.exists():
        existing = filepath.read_text(encoding="utf-8")
        content = existing.rstrip("\n") + "\n\n" + content
    filepath.write_text(content, encoding="utf-8")
    return filepath


MEMORY_CATEGORIES = ("observations", "plans", "user-profile", "self-critique")
