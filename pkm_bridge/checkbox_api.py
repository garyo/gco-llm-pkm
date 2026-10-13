"""Request handling for /api/checkbox/toggle: ticking items off in notes and in TickTick.

A task in a query block's results is a copy, so ticking it ticks the item it came from,
with a completion stamp, and flips the copy to match (doc/query-todos-design.md). Kept
out of the server script so it can be tested without Flask.
"""

import logging
import re
from collections.abc import Callable, Mapping
from datetime import date
from typing import Any

from .file_editor import ConflictError
from .queries import TaskError, check_task, locate_task, query_result_lines, set_task_state
from .queries.notes import TASK_RE
from .queries.refresher import QueryRefresher
from .ticktick_client import TickTickClient
from .tools.note_writing import rewrite_note

logger = logging.getLogger(__name__)

Response = tuple[dict[str, Any], int]
TickTickLookup = Callable[[], TickTickClient | None]  # the connected client, or None

NEARBY = 10  # lines either side of line_hint searched before the rest of the note
_TICKTICK_RE = re.compile(r"\{ticktick:([^}\s]+)\}")


def handle_toggle(
    refresher: QueryRefresher, ticktick: TickTickLookup, body: Mapping[str, Any] | None
) -> Response:
    """POST /api/checkbox/toggle.

    {"type": "ticktick", "task_id", "checked"} completes a TickTick task.
    {"type": "file", "path": "org:…", "item_text", "line_hint" (1-based), "checked"} ticks
    or unticks an item in a note. For a task copied into query results it ticks the item
    the copy came from and answers {"status": "ok", "source", "source_line", "checked"}.
    """
    if not body:
        return {"error": "Missing request body"}, 400
    checked = body.get("checked", True)
    if body.get("type") == "ticktick":
        if not body.get("task_id"):
            return {"error": "Missing task_id"}, 400
        return _complete_in_ticktick(ticktick, body["task_id"], checked)
    if body.get("type") == "file":
        if not body.get("path"):
            return {"error": "Missing path"}, 400
        return _toggle_in_note(refresher, ticktick, body, checked)
    return {"error": f"Unknown toggle type: {body.get('type')}"}, 400


def _toggle_in_note(
    refresher: QueryRefresher, ticktick: TickTickLookup, body: Mapping[str, Any], checked: bool
) -> Response:
    """Toggle the item meant: the nearest to line_hint that matches item_text, either a task
    copied into query results or an item of the note's own; beyond NEARBY lines, the latter."""
    path, item_text, line_hint = body["path"], body.get("item_text") or "", body.get("line_hint", 0)
    logger.info(
        f"Checkbox toggle (file): path={path!r}, item_text={item_text!r}, "
        f"line_hint={line_hint}, checked={checked}"
    )
    try:
        # Full content required: the whole note is written back.
        note = refresher.editor.read_file(path, max_chars=None)
        lines = note["content"].split("\n")
        copied = query_result_lines(note["content"])
        hint = max(0, line_hint - 1)
        nearby = _nearby(hint, len(lines))
        needle = item_text.lower()

        def is_copy(i: int) -> bool:
            return i in copied and bool(TASK_RE.match(lines[i])) and needle in lines[i].lower()

        def is_item(i: int, state: bool = checked) -> bool:
            return (
                i not in copied
                and _can_toggle(lines[i], state)
                and _text_matches(lines[i], item_text)
            )

        line = next((i for i in nearby if is_copy(i) or is_item(i)), None)
        if line is None:
            line = next((i for i in range(len(lines)) if is_item(i)), None)

        if line is None:
            already = next((i for i in nearby if is_item(i, not checked)), None)
            if already is not None:
                logger.info(f"Checkbox already in desired state at line {already + 1} in {path}")
                return {"status": "ok"}, 200
            if item_text and any(_text_matches(lines[i], item_text) for i in copied):
                return {
                    "error": "That item is a copy in a query block's results; "
                    "check it off in the note it came from"
                }, 409
            start, end = max(0, hint - 3), min(len(lines), hint + 4)
            shown = "\n".join(f"  {i + 1}: {lines[i]!r}" for i in range(start, end))
            logger.warning(
                f"Checkbox not found in {path} (hint line {line_hint}, text={item_text!r}, "
                f"checked={checked}). Lines {start + 1}-{end} near hint:\n{shown}"
            )
            return {"error": "Checkbox item not found in file"}, 404

        if line in copied:
            return _write_through(refresher, ticktick, note, line, checked)
        lines[line] = _toggled(lines[line], checked, refresher.today())
        refresher.editor.write_file(note["path"], "\n".join(lines), base_hash=note["hash"])
        logger.info(
            f"Checkbox toggle: {'checked' if checked else 'unchecked'} line {line + 1} in {path}"
        )
        return {"status": "ok"}, 200

    except TaskError as e:  # a ValueError too, so first
        logger.info(f"Checkbox toggle refused in {path}: {e}")
        return {"error": str(e)}, 409
    except ValueError as e:
        return {"error": str(e)}, 400
    except ConflictError as e:
        logger.info(f"Conflict toggling a checkbox in {path}: {e}")
        return {"error": str(e)}, 409
    except Exception as e:
        logger.error(f"Checkbox toggle error (file): {e}")
        return {"error": str(e)}, 500


def _write_through(
    refresher: QueryRefresher,
    ticktick: TickTickLookup,
    page: dict[str, Any],
    line: int,
    checked: bool,
) -> Response:
    """Tick or untick the item that the task copied onto `line` (0-based) of `page` came
    from, and in TickTick too if it carries a {ticktick:ID}; then flip the copy."""
    editor, today = refresher.editor, refresher.today()
    rel = refresher.relative(page["path"])
    source_path = f"org:{locate_task(page['content'], rel, line).source_path}"
    source = editor.read_file(source_path, max_chars=None)["content"]
    change = check_task(page["content"], rel, line, source, checked, today)
    if m := _TICKTICK_RE.search(change.item):
        body, status = _complete_in_ticktick(ticktick, m.group(1), checked)
        if status != 200:
            return body, status

    # rewrite_note reads the source again (and after any conflict); tick what it read.
    def ticked(text: str) -> str:
        nonlocal change
        change = check_task(page["content"], rel, line, text, checked, today)
        return change.source_text

    written = rewrite_note(editor, source_path, ticked)
    try:
        editor.write_file(page["path"], change.page_text, base_hash=page["hash"])
    except Exception as e:  # cosmetic: the next refresh pass sets the copy right
        logger.warning(f"Checkbox toggle: wrote {written['path']} but not the copy: {e}")
    logger.info(
        f"Checkbox toggle: {'checked' if checked else 'unchecked'} line "
        f"{change.source_line + 1} in {written['path']} through its copy at line "
        f"{line + 1} in {page['path']}"
    )
    return {
        "status": "ok",
        "source": written["path"],
        "source_line": change.source_line + 1,
        "checked": checked,
    }, 200


def _complete_in_ticktick(ticktick: TickTickLookup, task_id: str, checked: bool) -> Response:
    """Complete TickTick task `task_id`; reopening one isn't supported."""
    if not checked:
        return {"error": "Unchecking TickTick tasks is not supported yet"}, 400
    try:
        client = ticktick()
        if not client:
            return {"error": "TickTick not connected"}, 503
        client.complete_task(task_id)
    except Exception as e:
        logger.error(f"Checkbox toggle error (ticktick): {e}")
        return {"error": str(e)}, 500
    logger.info(f"Checkbox toggle: completed TickTick task {task_id}")
    return {"status": "ok"}, 200


def _nearby(hint: int, size: int) -> list[int]:
    """Lines within NEARBY of `hint` in a note of `size` lines, nearest first."""
    around = [hint] + [i for d in range(1, NEARBY + 1) for i in (hint + d, hint - d)]
    return [i for i in around if 0 <= i < size]


def _can_toggle(line: str, checked: bool) -> bool:
    """Whether `line` is an item that can be set to `checked`: to check, an open checkbox,
    a TODO or a plain list item; to uncheck, a ticked checkbox or a DONE."""
    stripped = line.strip()
    keyword = stripped.lstrip("*").strip()
    if checked:
        return (
            stripped.startswith(("- [ ]", "+ [ ]"))
            or keyword.startswith("TODO")
            or (stripped.startswith("- ") and not stripped.startswith(("- [x]", "- [X]")))
        )
    return stripped.startswith(("- [x]", "- [X]", "+ [x]", "+ [X]")) or keyword.startswith("DONE")


def _text_matches(line: str, text: str) -> bool:
    """Whether item `text` is in `line`, ignoring case, or at least its first two words are
    (Claude may paraphrase). Empty text matches any line."""
    line, text = line.lower(), text.lower()
    words = text.split()
    return text in line or (len(words) >= 2 and words[0] in line and words[1] in line)


def _toggled(line: str, checked: bool, today: date) -> str:
    """`line` ticked or unticked: a checkbox by the completion-stamp rule, a plain list item
    made a ticked task, an org-style TODO or DONE keyword swapped."""
    stripped = line.strip()
    if TASK_RE.match(line):
        return set_task_state(line, checked, today)
    if not checked:
        return line.replace("DONE", "TODO", 1)
    if "TODO" in stripped:
        return line.replace("TODO", "DONE", 1)
    if stripped.startswith("- ") and not stripped.startswith(("- [", "- *")):
        return set_task_state(line.replace("- ", "- [ ] ", 1), True, today)
    return line
