"""Query blocks: live views of matching journal sections, kept inside notes.

A note holds `<!-- query: … -->` … `<!-- /query HASH -->`, and everything between the
markers is regenerated from the other notes. See doc/query-blocks-design.md.

Stdlib only and Python 3.9+ (macOS's own python3), so Emacs can run it anywhere:
`python3 -m pkm_bridge.queries --help`.
"""

from .engine import BlockReport, Corpus, RefreshResult, preview, refresh_text, results_hash
from .notes import query_block_ranges, query_result_lines, set_task_state, strip_machine_blocks
from .syntax import Query, QuerySyntaxError, parse_query
from .tasks import TaskChange, TaskError, TaskRef, check_task, locate_task

__all__ = [
    "BlockReport",
    "Corpus",
    "Query",
    "QuerySyntaxError",
    "RefreshResult",
    "TaskChange",
    "TaskError",
    "TaskRef",
    "check_task",
    "locate_task",
    "parse_query",
    "preview",
    "query_block_ranges",
    "query_result_lines",
    "refresh_text",
    "results_hash",
    "set_task_state",
    "strip_machine_blocks",
]
