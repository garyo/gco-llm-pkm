"""System prompt assembly for the self-improvement agent.

Builds the agent's system prompt from:
1. Identity & mission
2. Agent's own persistent memory (.pkm/memory/)
3. Current run context (stats since last run)
4. Guidance on what to look for
5. Budget information
"""

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict

from .budget import Budget
from .filesystem import MEMORY_CATEGORIES, get_memory_dir, read_memory_file

AGENT_SYSTEM_PROMPT = """\
You are the self-improvement agent for a Personal Knowledge Management (PKM) system.
Your job is to review how the system has been performing and make it better for the user.

You run periodically (typically daily) and have access to meta-tools that let you inspect
the system's state and make targeted improvements.

## Your Memory

These are your notes from previous runs. Use them to build continuity across runs.

{memory_section}

## Current Run Context

{run_context}

## Dual Interface Awareness

The PKM system has two user-facing interfaces that share the same backend:
1. **Custom Web App** (pkm.oberbrunner.com) — Flask + Astro frontend, uses Anthropic API key,
   has auto-RAG injection, interactive checkboxes, editor integration. System prompt: `config/system_prompt.txt`
2. **Claude.ai MCP Server** (mcp.oberbrunner.com) — accessed via Claude.ai desktop/mobile,
   uses user's Claude subscription, must call semantic_search explicitly. System prompt: `config/system_prompt_mcp.txt`

Both interfaces share the same tools, skills, and learned rules. When you:
- Create or modify **skills**: they affect both interfaces
- Create or modify **learned rules**: they are injected into both system prompts
- Propose **amendments**: consider impact on both system prompts — core PKM behavior
  (search strategy, file safety, adding notes) should stay aligned, while interface-specific
  sections (file links, checkboxes, RAG) are intentionally different
- Inspect **conversations**: note which interface was used if distinguishable
  (MCP conversations may look different — no auto-RAG context, different tool names)

## What to Look For

Inspect the system's recent activity and look for:

1. **Skill duplication or gaps** — Are there redundant skills that do the same thing?
   Are there recurring multi-tool patterns that should be saved as skills?

2. **Rules that aren't helping** — Are there rules with low confidence or that haven't
   been reinforced? Are rules being injected but the system still makes the same mistakes?

3. **User patterns** — What does the user ask about? When? How? What terminology do they use?
   Update user-profile.md with insights.

4. **Tool chains that work or fail** — Which tool sequences consistently produce good results?
   Which ones lead to errors or corrections?

5. **Consolidation opportunities** — Can multiple rules be merged? Can the system be simplified?

6. **Whether your past changes helped** — Check if skills you created are being used,
   if rules you reinforced are still relevant, if amendments you proposed were approved.

## How to Work

1. **Start by reading your memory** (`read_memory`) to recall what you noticed last time
   and what you planned to investigate.
2. **Inspect** the system: feedback, conversations, tool logs, skills, rules.
3. **Act** on what you find: create/update/delete skills and rules, propose amendments.
4. **Always write_memory** with your observations before finishing, even if you take no actions.
   This is how you maintain continuity across runs.
5. **Write rules_snapshot** at the end of each run for human visibility.
6. **Manage memory size** — Each memory file should stay under 50KB. When the
   run context shows a file approaching that limit, consolidate: summarize
   older dated sections into a brief "## Historical Summary" at the top,
   keeping the last ~7 days verbatim. Use `write_memory` with mode=replace.

## Budget

{budget_section}

Use inspection tools first, then act on what you find. Prioritize high-impact changes
over minor tweaks. Quality over quantity.
"""


_DATED_SECTION_RE = re.compile(r"^(## (\d{4}-\d{2}-\d{2}).*)", re.MULTILINE)


def _truncate_dated_sections(content: str, keep_days: int = 7) -> str:
    """Truncate old dated sections to heading-only for prompt injection.

    Sections headed with ``## YYYY-MM-DD ...`` older than *keep_days* are
    collapsed to just the heading line.  Recent sections and any preamble
    (content before the first dated heading) are kept verbatim.

    Returns the (possibly shortened) content string.
    """
    cutoff = datetime.utcnow().date() - timedelta(days=keep_days)

    # Split into (preamble, [(heading_line, date_str, body), ...])
    parts = _DATED_SECTION_RE.split(content)
    # parts layout: [preamble, heading1, date1, rest_until_next_heading, heading2, date2, ...]
    preamble = parts[0]
    chunks: list[tuple[str, str, str]] = []
    i = 1
    while i < len(parts):
        heading_line = parts[i]
        date_str = parts[i + 1]
        body = parts[i + 2] if i + 2 < len(parts) else ""
        chunks.append((heading_line, date_str, body))
        i += 3

    if not chunks:
        return content  # no dated sections, return as-is

    collapsed_any = False
    result_parts = [preamble.rstrip("\n")]
    for heading_line, date_str, body in chunks:
        try:
            section_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            # Unparseable date — keep verbatim
            result_parts.append(heading_line + body)
            continue

        if section_date >= cutoff:
            result_parts.append(heading_line + body)
        else:
            # Collapse: heading only, no body
            result_parts.append(heading_line)
            collapsed_any = True

    text = "\n\n".join(part for part in result_parts if part)

    if collapsed_any:
        text = (
            "*Older entries collapsed to headings only. "
            "Use `read_memory` to see full details.*\n\n" + text
        )

    return text


def build_memory_section(org_dir: str | Path) -> str:
    """Read all memory files and format them for the prompt.

    Large files with dated ``## YYYY-MM-DD`` sections are truncated so only
    the last 7 days appear in full; older sections are collapsed to headings.
    """
    sections = []
    for category in MEMORY_CATEGORIES:
        content = read_memory_file(category, org_dir)
        if content:
            truncated = _truncate_dated_sections(content)
            sections.append(f"### {category}\n\n{truncated}")

    if not sections:
        return (
            "*No memory from previous runs.* This appears to be your first run. "
            "Take time to thoroughly inspect the system and establish baseline observations."
        )
    return "\n\n".join(sections)


def build_run_context(stats: Dict[str, Any]) -> str:
    """Format run context stats into readable text."""
    lines = []
    if stats.get("days_since_last_run") is not None:
        lines.append(f"- Days since last run: {stats['days_since_last_run']}")
    else:
        lines.append("- This is the first run (no previous run found)")

    lines.append(f"- Queries since last run: {stats.get('queries_since_last_run', 'unknown')}")
    lines.append(f"- Unprocessed feedback records: {stats.get('unprocessed_feedback', 0)}")

    fb = stats.get("feedback_signals", {})
    if fb:
        lines.append(f"- Retrieval misses: {fb.get('retrieval_misses', 0)}")
        lines.append(f"- User corrections: {fb.get('user_corrections', 0)}")
        lines.append(f"- Positive feedback: {fb.get('positive', 0)}")
        lines.append(f"- Negative feedback: {fb.get('negative', 0)}")

    lines.append(f"- Active rules: {stats.get('active_rules', 0)}")
    lines.append(f"- Total skills: {stats.get('total_skills', 0)}")
    lines.append(f"- Current time: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")

    # Memory file sizes
    mem_sizes = stats.get("memory_sizes")
    if mem_sizes:
        parts = [f"{cat}={sz}KB" for cat, sz in mem_sizes.items()]
        lines.append(f"- Memory sizes: {', '.join(parts)} (target: <50KB each)")

    return "\n".join(lines)


def build_budget_section(budget: Budget) -> str:
    """Format budget info for the prompt."""
    return (
        f"- Max turns: {budget.max_turns} API round-trips\n"
        f"- Max write actions: {budget.max_actions} (skills + rules + amendments)\n"
        f"- Inspection tools: unlimited\n"
        f"- Always save your observations to memory before stopping."
    )


def build_system_prompt(
    org_dir: str | Path,
    budget: Budget,
    run_stats: Dict[str, Any],
) -> str:
    """Assemble the complete system prompt for the agent."""
    return AGENT_SYSTEM_PROMPT.format(
        memory_section=build_memory_section(org_dir),
        run_context=build_run_context(run_stats),
        budget_section=build_budget_section(budget),
    )


def gather_run_stats(org_dir: str | Path) -> Dict[str, Any]:
    """Gather statistics about system state since the last agent run.

    Returns a dict with stats used to populate the run context in the prompt.
    """
    from ..database import QueryFeedback, get_db
    from ..db_repository import LearnedRuleRepository, QueryFeedbackRepository
    from .filesystem import get_runs_dir, get_skills_dir

    stats: Dict[str, Any] = {}
    db = get_db()
    try:
        # Days since last run
        runs_dir = get_runs_dir(org_dir)
        run_files = sorted(runs_dir.glob("*.md"), reverse=True)
        if run_files:
            # Parse date from filename: YYYY-MM-DD-HHMM.md
            try:
                last_run_name = run_files[0].stem  # e.g., "2025-12-01-0300"
                last_run_date = datetime.strptime(last_run_name, "%Y-%m-%d-%H%M")
                stats["days_since_last_run"] = (datetime.utcnow() - last_run_date).days
            except ValueError:
                stats["days_since_last_run"] = None
        else:
            stats["days_since_last_run"] = None

        # Feedback stats
        fb_stats = QueryFeedbackRepository.get_stats(db)
        stats["queries_since_last_run"] = fb_stats.get("total_queries", 0)
        stats["feedback_signals"] = {
            "retrieval_misses": fb_stats.get("retrieval_misses", 0),
            "user_corrections": fb_stats.get("user_corrections", 0),
        }

        # Count explicit feedback types
        from sqlalchemy import func
        cutoff = datetime.utcnow() - timedelta(days=7)
        positive = db.query(func.count(QueryFeedback.id)).filter(
            QueryFeedback.created_at >= cutoff,
            QueryFeedback.explicit_feedback.in_(["positive", "positive_implicit"]),
        ).scalar() or 0
        negative = db.query(func.count(QueryFeedback.id)).filter(
            QueryFeedback.created_at >= cutoff,
            QueryFeedback.explicit_feedback == "negative",
        ).scalar() or 0
        stats["feedback_signals"]["positive"] = positive
        stats["feedback_signals"]["negative"] = negative

        # Unprocessed feedback count
        unprocessed = QueryFeedbackRepository.get_unprocessed(db, limit=1000)
        stats["unprocessed_feedback"] = len(unprocessed)

        # Active rules
        active_rules = LearnedRuleRepository.get_active(db)
        stats["active_rules"] = len(active_rules)

        # Total skills
        skills_dir = get_skills_dir(org_dir)
        skill_count = sum(1 for f in skills_dir.iterdir() if f.suffix in (".sh", ".md"))
        stats["total_skills"] = skill_count

        # Memory file sizes (KB)
        mem_dir = get_memory_dir(org_dir)
        mem_sizes: Dict[str, int] = {}
        for category in MEMORY_CATEGORIES:
            fp = mem_dir / f"{category}.md"
            if fp.exists():
                mem_sizes[category] = fp.stat().st_size // 1024
        if mem_sizes:
            stats["memory_sizes"] = mem_sizes

    except Exception:
        # Don't fail the agent if stats gathering hits an error
        pass
    finally:
        db.close()

    return stats
