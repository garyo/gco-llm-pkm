"""Keeps query blocks current: after notes change, shortly after startup, nightly and on
request. Server-only; the engine itself stays stdlib-only so Emacs can run it.

Pages are written through FileEditor with the hash of the text their results were computed
from, so edits made meanwhile to the rest of a page are merged, never overwritten.
"""

import logging
import os
import threading
import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import asdict
from datetime import date, datetime, tzinfo
from typing import Any

from .. import ntfy
from ..events import SSEEventManager
from ..file_editor import ConflictError, FileEditor
from ..job_lock import run_exclusive
from ..tools.note_writing import rewrite_note
from . import BlockReport, Corpus, refresh_text
from . import preview as preview_results

logger = logging.getLogger(__name__)

SETTLE = 20.0  # seconds without note changes before an event-driven pass
MAX_WAIT = 120.0  # longest a change waits for its pass while edits keep coming
JOB = "query_refresh"

Response = tuple[dict[str, Any], int]


def auto_refresh_enabled(debug: bool) -> bool:
    """Whether passes run automatically: QUERY_REFRESH=on|off, else on unless in debug mode."""
    setting = os.getenv("QUERY_REFRESH", "").strip().lower()
    return setting == "on" if setting in ("on", "off") else not debug


class QueryRefresher:
    """Refreshes the query blocks in the org notes, writing only pages whose results changed."""

    def __init__(
        self,
        editor: FileEditor,
        tz: tzinfo | None,
        settle: float = SETTLE,
        max_wait: float = MAX_WAIT,
    ):
        self.editor = editor
        self.tz = tz  # zone of "today" for date windows; None means local time
        self.settle = settle
        self.max_wait = max_wait
        if editor.org_dir is None:
            raise ValueError("Query blocks need an org notes directory")
        self.org_dir = editor.org_dir
        self.corpus = Corpus(self.org_dir)
        self._changed = threading.Condition()
        self._first: float | None = None  # when the oldest change not yet refreshed came in
        self._last = 0.0

    # -- automatic passes ----------------------------------------------------------------

    def start(self, events: SSEEventManager) -> None:
        """Refresh after notes change, and once shortly after startup."""
        events.add_listener(self.on_event)
        threading.Thread(target=self._work, name="query-refresh", daemon=True).start()
        self.note_change(time.monotonic())  # notes may have changed while the server was down

    def on_event(self, event_type: str, data: dict) -> None:
        """Event listener: a change to any org Markdown note schedules a pass."""
        file = data.get("file") or ""
        if file.startswith("org:") and file.endswith(".md"):
            self.note_change(time.monotonic())

    def note_change(self, now: float) -> None:
        """Record a change at monotonic time `now`; a pass follows once changes settle."""
        with self._changed:
            if self._first is None:
                self._first = now
            self._last = now
            self._changed.notify()

    def due_in(self, now: float) -> float | None:
        """Seconds from `now` until the pending pass is due, or None if none is pending."""
        if self._first is None:
            return None
        return min(self._last + self.settle, self._first + self.max_wait) - now

    def run_pass(self) -> bool:
        """Refresh every page, unless a pass is already running (then returns False)."""
        return run_exclusive(JOB, self.refresh_all, logger=logger)

    def _work(self) -> None:
        while True:
            with self._changed:
                while (delay := self.due_in(time.monotonic())) is None or delay > 0:
                    self._changed.wait(delay)
                self._first = None
            try:
                if not self.run_pass():
                    self.note_change(time.monotonic())  # run again once that pass is done
            except Exception:
                logger.exception("Query refresh pass failed")

    # -- refreshing ----------------------------------------------------------------------

    def refresh_all(self) -> list[dict[str, Any]]:
        """Refresh every note with query blocks. A note that fails is logged and reported
        with status "error"; the others are still refreshed."""
        pages = []
        for rel in self.corpus.pages_with_queries():
            path = f"org:{rel}"
            try:
                pages.append(self.refresh_note(path))
            except Exception as e:
                logger.error(f"Query refresh failed for {path}: {e}", exc_info=True)
                pages.append({"path": path, "status": "error", "error": str(e), "blocks": []})
        counts = Counter(page["status"] for page in pages)
        logger.info(
            f"Query refresh: {counts['saved'] + counts['merged']} written, "
            f"{counts['unchanged']} unchanged, {counts['error']} errors"
        )
        return pages

    def refresh_note(self, path: str) -> dict[str, Any]:
        """Refresh the query blocks in note `path` ("org:…").

        Returns {"path", "status": "saved"|"merged"|"unchanged", "blocks"}. Raises
        ValueError if `path` isn't an existing org note, ConflictError if the note kept
        changing underneath every attempt to write it.
        """
        current = self.editor.read_file(f"org:{self._relative(path)}", max_chars=None)
        path, today = current["path"], self._today()
        rel = path.removeprefix("org:")
        result = refresh_text(current["content"], rel, self.corpus, today)
        status = "unchanged"
        if result.changed:
            # rewrite_note reads the note again (and after any conflict); refresh what it read.
            def refreshed(text: str) -> str:
                nonlocal result
                result = refresh_text(text, rel, self.corpus, today)
                return result.text

            status = rewrite_note(self.editor, path, refreshed)["status"]
            if any(block.hand_edited for block in result.blocks):
                self._report_hand_edit(rel)
        blocks = [asdict(block) for block in result.blocks]
        logger.debug(f"Query refresh {path}: {status}, {blocks}")
        return {"path": path, "status": status, "blocks": blocks}

    def preview(self, query: str, path: str) -> tuple[list[str], BlockReport]:
        """The result lines `query` would get in note `path`, without writing anything."""
        return preview_results(query, self._relative(path), self.corpus, self._today())

    def _relative(self, path: str) -> str:
        """`path` ("org:…") relative to the org dir; ValueError unless it lies inside it."""
        if not path.startswith("org:"):
            raise ValueError(f"Expected an org note path such as org:pages/x.md, not {path!r}")
        full = self.editor._resolve_prefixed_path(path)
        return full.relative_to(self.org_dir.resolve()).as_posix()

    def _today(self) -> date:
        return datetime.now(self.tz).date()

    def _report_hand_edit(self, rel: str) -> None:
        message = (
            f"The query results in {rel} had been edited by hand, so they were regenerated. "
            "Make changes in the source notes the entry headings link to. The edited text "
            "stays in the version store for 7 days."
        )
        try:
            ntfy.send(message, title="Query results regenerated")
        except ntfy.NtfyError as e:
            message += f" (Not pushed: {e})"
        logger.warning(message)


def handle_refresh(refresher: QueryRefresher, body: Mapping[str, Any] | None) -> Response:
    """POST /api/queries/refresh: body {path?}; refreshes that note, or every note with queries."""
    path = (body or {}).get("path")
    if path is not None and not isinstance(path, str):
        return {"error": "'path' must be a string such as org:pages/x.md"}, 400
    try:
        pages = refresher.refresh_all() if path is None else [refresher.refresh_note(path)]
    except ValueError as e:
        return {"error": str(e)}, 400
    except ConflictError as e:
        logger.info(f"Conflict refreshing query blocks in {path}: {e}")
        return e.to_response(), 409
    except Exception as e:
        logger.error(f"Query refresh failed for {path or 'all notes'}: {e}", exc_info=True)
        return {"error": f"Query refresh failed: {e}"}, 500
    return {"pages": pages}, 200


def handle_preview(refresher: QueryRefresher, body: Mapping[str, Any] | None) -> Response:
    """POST /api/queries/preview: body {query, path}; the results, without writing anything."""
    body = body or {}
    query, path = body.get("query"), body.get("path")
    if not isinstance(query, str) or not isinstance(path, str):
        return {"error": "Pass 'query' and 'path' (such as org:pages/x.md) as strings"}, 400
    try:
        lines, report = refresher.preview(query, path)
    except ValueError as e:
        return {"error": str(e)}, 400
    except Exception as e:
        logger.error(f"Query preview failed for {query!r} in {path}: {e}", exc_info=True)
        return {"error": f"Query preview failed: {e}"}, 500
    return {"markdown": "\n".join(lines), "entries": report.entries, "error": report.error}, 200
