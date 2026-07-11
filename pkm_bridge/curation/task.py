"""The curator scheduled task — scans notes and files organization proposals.

Rides the generic DB scheduler (TaskExecutor) with a restricted, read-only
tool set plus propose_note_organization. The task row is system-owned: its
prompt and tool list are upserted from this file on every startup (so prompt
improvements deploy with the code), while schedule/enabled/budgets are left
alone for the user to tune.
"""

import logging

from ..database import get_db
from ..models import get_role_model
from ..scheduler.repository import ScheduledTaskRepository

CURATION_TASK_NAME = "note_curation"
DEFAULT_CURATION_INTERVAL = "3d"

CURATION_TOOLS = [
    "list_files",
    "read_note",
    "search_notes",
    "find_context",
    "semantic_search",
    "list_note_proposals",
    "propose_note_organization",
]

CURATION_PROMPT = """\
You are the note curator, running as a background agent over Gary's PKM
(org-mode + Logseq notes). Your job is to find organization opportunities and
file them as PROPOSALS for later review. You cannot edit files — only propose.

First, call list_note_proposals with status="pending" and status="rejected" to
see what is already queued and what Gary has recently declined. Never duplicate
a pending proposal or re-file a rejected idea.

Then scan for opportunities, focusing on recently modified files
(list_files with show_stats=true). Look for:

1. add_links — a note mentions a topic that has its own page but isn't linked.
   Use semantic_search and search_notes to find related-but-unlinked content.
   Each edit must quote an exact, unique snippet of the current file text as
   the anchor ("find") and reproduce it with only the link added ("replace").

2. new_page — a project, topic, or area of study recurs across journals and
   notes but has no dedicated page. Draft the COMPLETE page content yourself
   (summarizing and organizing what exists — Gary reviews the actual draft, so
   it must be publication-ready). Include backlink edits in existing notes,
   and where journal entries contain substantial content that now lives on the
   new page, include edits replacing that content with a link to the page so
   no stale duplicates remain.

3. insight — no file changes; an observation Gary would find genuinely
   interesting. Examples: a connection between notes he likely hasn't noticed
   (two projects using the same technique, a person appearing in unrelated
   contexts); a pattern or trend across journals (a topic heating up or going
   quiet, a recurring blocker); an outlier that doesn't fit; a dangling thread
   worth looking into (a question he raised and never answered, an idea noted
   once and dropped). Put the substance in the title and rationale, citing the
   specific notes that support it. Do NOT file generic advice, summaries of
   what he obviously knows, or observations without evidence in the notes.

Quality over quantity: file at most 3 proposals per run, and only ones you are
confident Gary will value. Write a clear title and a rationale that explains
the evidence (which files, how often the topic recurs). Set confidence
honestly. If nothing worthwhile turns up, file nothing and say so.
"""


def ensure_curation_task(logger: logging.Logger) -> None:
    """Create the curator task if missing; refresh its prompt/tools/model if present."""
    model = get_role_model("curation")
    db = get_db()
    try:
        existing = ScheduledTaskRepository.get_by_name(db, CURATION_TASK_NAME)
        if existing:
            if (
                existing.prompt != CURATION_PROMPT
                or existing.tools_allowed != CURATION_TOOLS
                or existing.model != model
            ):
                existing.prompt = CURATION_PROMPT
                existing.tools_allowed = CURATION_TOOLS
                existing.model = model
                db.commit()
                logger.info(f"Refreshed note_curation task (model={model})")
            return

        ScheduledTaskRepository.create(
            db,
            name=CURATION_TASK_NAME,
            description="Scan notes for organization opportunities and file proposals",
            prompt=CURATION_PROMPT,
            schedule_type="interval",
            schedule_expr=DEFAULT_CURATION_INTERVAL,
            model=model,
            tools_allowed=CURATION_TOOLS,
            enabled=True,
            max_turns=20,
            max_input_tokens=300_000,
            max_output_tokens=15_000,
            created_by="system",
        )
        logger.info(f"Created note_curation scheduled task (every {DEFAULT_CURATION_INTERVAL})")
    finally:
        db.close()
