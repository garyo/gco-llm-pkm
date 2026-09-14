"""Request handling for the editor's save and merge endpoints.

Kept out of the server script so the status codes and payloads can be tested
without importing Flask.
"""

import logging
from typing import Any, Dict, Mapping

from .file_editor import ConflictError, FileEditor

Response = tuple[Dict[str, Any], int]


def handle_save(
    editor: FileEditor,
    logger: logging.Logger,
    filepath: str,
    body: Mapping[str, Any] | None,
    args: Mapping[str, str],
) -> Response:
    """PUT /api/file/<path>: body {content, base_hash?, expected_mtime?}, ?create_only=true."""
    if not body or "content" not in body:
        return {"error": "Missing 'content' in request body"}, 400

    expected_mtime = body.get("expected_mtime")
    try:
        result = editor.write_file(
            filepath,
            body["content"],
            create_only=args.get("create_only", "").lower() == "true",
            expected_mtime=float(expected_mtime) if expected_mtime is not None else None,
            base_hash=body.get("base_hash"),
        )
    except ValueError as e:
        logger.warning(f"Invalid file path for save: {filepath} - {e}")
        return {"error": str(e)}, 400
    except ConflictError as e:
        logger.info(f"Conflict saving {filepath} ({e.reason}): {e}")
        return e.to_response(), 409

    # The editor does not need the text echoed back unless it was merged.
    if result["status"] != "merged":
        result.pop("content", None)
    return result, 200


def handle_merge(
    editor: FileEditor,
    logger: logging.Logger,
    filepath: str,
    body: Mapping[str, Any] | None,
) -> Response:
    """POST /api/file/<path>/merge: body {content, base_hash}; merges without writing."""
    if not body or "content" not in body or not body.get("base_hash"):
        return {"error": "Missing 'content' or 'base_hash' in request body"}, 400
    try:
        return editor.merge_preview(filepath, body["content"], body["base_hash"]), 200
    except ValueError as e:
        return {"error": str(e)}, 400
    except ConflictError as e:
        logger.info(f"Conflict merging {filepath} ({e.reason}): {e}")
        return e.to_response(), 409
