"""Note-organization proposal tools: propose (curator), list and resolve (review).

The curator files proposals; the user reviews them conversationally in chat
(web app or MCP) and approves, rejects, or modifies each one. Approval applies
the change immediately through FileEditor's atomic write path.
"""

from pathlib import Path
from typing import Any, Dict, Optional

from ..file_editor import FileEditor
from .base import BaseTool

EDITS_SCHEMA = {
    "type": "array",
    "description": "Text edits, each anchored to exact unique text in the target file",
    "items": {
        "type": "object",
        "properties": {
            "file": {
                "type": "string",
                "description": "Prefixed path, e.g. 'logseq:pages/Foo.md' or 'org:notes.org'",
            },
            "find": {
                "type": "string",
                "description": "Exact text currently in the file (must occur exactly once)",
            },
            "replace": {
                "type": "string",
                "description": "Replacement text (typically the same text with [[links]] added)",
            },
        },
        "required": ["file", "find", "replace"],
    },
}


def _build_payload(params: Dict[str, Any]) -> Dict[str, Any]:
    """Assemble a NoteProposal payload from flat tool params."""
    payload: Dict[str, Any] = {"edits": params.get("edits") or []}
    if params.get("page_file") or params.get("page_content"):
        payload["page"] = {
            "file": params.get("page_file", ""),
            "content": params.get("page_content", ""),
        }
    return payload


def _render_proposal(proposal, full: bool = True) -> str:
    """Render a proposal for conversational review."""
    lines = [
        f"### Proposal #{proposal.id} [{proposal.kind}] — {proposal.title}",
        f"Status: {proposal.status} | Confidence: {proposal.confidence:.2f} | "
        f"Created: {proposal.created_at:%Y-%m-%d}",
        f"Rationale: {proposal.rationale}",
    ]
    if proposal.resolution_note:
        lines.append(f"Note: {proposal.resolution_note}")
    if not full:
        return "\n".join(lines)

    payload = proposal.payload or {}
    page = payload.get("page")
    if page:
        lines.append(f"\nNew page: {page.get('file')}")
        lines.append("```markdown\n" + page.get("content", "") + "\n```")
    for edit in payload.get("edits", []):
        lines.append(f"\nEdit {edit.get('file')}:")
        lines.append(f"- {edit.get('find')}")
        lines.append(f"+ {edit.get('replace')}")
    return "\n".join(lines)


class _ProposalToolBase(BaseTool):
    """Shared FileEditor construction for proposal tools."""

    def __init__(self, logger, org_dir: Path, logseq_dir: Optional[Path] = None):
        super().__init__(logger)
        self.editor = FileEditor(logger, str(org_dir), str(logseq_dir) if logseq_dir else None)


class ProposeNoteOrganizationTool(_ProposalToolBase):
    """File a note-organization proposal for user review (never edits files)."""

    @property
    def name(self) -> str:
        return "propose_note_organization"

    @property
    def description(self) -> str:
        return (
            "Propose a note-organization change for the user to review later. "
            "Does NOT edit any files — it files a proposal. "
            "kind='add_links': supply 'edits' adding [[links]] to existing notes. "
            "kind='new_page': supply 'page_file' + 'page_content' (the COMPLETE draft — "
            "the user reviews and publishes it as-is), plus optional 'edits' for backlinks "
            "and for replacing journal content that the new page now covers. "
            "kind='insight': a pure observation with NO file changes — an unexpected "
            "connection, a pattern or outlier, or something worth looking into; put the "
            "substance in title + rationale and supply no edits or page. "
            "Every edit's 'find' must quote exact text occurring exactly once in the file; "
            "anchors are validated immediately and invalid proposals are refused."
        )

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["add_links", "new_page", "insight"]},
                "title": {
                    "type": "string",
                    "description": "Short label, e.g. 'Create Woodworking Projects page'",
                },
                "rationale": {
                    "type": "string",
                    "description": "Why this helps, with evidence (files, recurrence)",
                },
                "confidence": {
                    "type": "number",
                    "description": "0-1 honest confidence",
                    "default": 0.5,
                },
                "edits": EDITS_SCHEMA,
                "page_file": {
                    "type": "string",
                    "description": "new_page only: prefixed path for the new page",
                },
                "page_content": {
                    "type": "string",
                    "description": "new_page only: complete draft page content",
                },
            },
            "required": ["kind", "title", "rationale"],
        }

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        from ..curation.apply import validate_payload
        from ..curation.repository import NoteProposalRepository
        from ..database import get_db

        kind = params.get("kind", "")
        title = params.get("title", "").strip()
        rationale = params.get("rationale", "").strip()
        if not title or not rationale:
            return "❌ Error: title and rationale are required"

        payload = _build_payload(params)
        problems = validate_payload(kind, payload, self.editor)
        if problems:
            return "❌ Proposal refused — fix these and retry:\n" + "\n".join(
                f"- {p}" for p in problems
            )

        source = "curator" if context is None else "chat"
        db = get_db()
        try:
            proposal = NoteProposalRepository.create(
                db,
                kind=kind,
                title=title,
                rationale=rationale,
                payload=payload,
                confidence=float(params.get("confidence", 0.5)),
                source=source,
            )
            n_edits = len(payload.get("edits", []))
            return (
                f"✅ Filed proposal #{proposal.id} [{kind}] '{title}' "
                f"({n_edits} edit(s){', 1 new page' if payload.get('page') else ''}). "
                f"It is pending user review."
            )
        finally:
            db.close()


class ListNoteProposalsTool(BaseTool):
    """List note-organization proposals for review."""

    @property
    def name(self) -> str:
        return "list_note_proposals"

    @property
    def description(self) -> str:
        return (
            "List note-organization proposals. Default shows pending proposals in full "
            "(including complete new-page drafts and exact edits) for conversational review. "
            "Also use status='rejected' to learn what the user has declined before proposing."
        )

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "status": {
                    "type": "string",
                    "enum": ["pending", "applied", "rejected", "stale"],
                    "default": "pending",
                },
                "limit": {"type": "integer", "default": 10},
            },
        }

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        from ..curation.repository import NoteProposalRepository
        from ..database import get_db

        status = params.get("status", "pending")
        limit = int(params.get("limit", 10))
        db = get_db()
        try:
            proposals = NoteProposalRepository.get_by_status(db, status, limit=limit)
            if not proposals:
                return f"No {status} proposals."
            full = status == "pending"
            body = "\n\n".join(_render_proposal(p, full=full) for p in proposals)
            return f"{len(proposals)} {status} proposal(s):\n\n{body}"
        finally:
            db.close()


class ResolveNoteProposalTool(_ProposalToolBase):
    """Approve, reject, or modify a pending note-organization proposal."""

    @property
    def name(self) -> str:
        return "resolve_note_proposal"

    @property
    def description(self) -> str:
        return (
            "Resolve a pending note-organization proposal after discussing it with the user. "
            "action='approve' applies the change to the note files immediately (atomic writes; "
            "if the files changed since proposal the apply is refused and the proposal marked "
            "stale instead). action='reject' declines it (give the user's reason so the curator "
            "learns). action='modify' replaces the proposal's content with the supplied "
            "edits/page fields (per the user's requested changes) and keeps it pending — "
            "approve it afterwards to apply. Only use this to enact what the user decided."
        )

    @property
    def input_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "proposal_id": {"type": "integer"},
                "action": {"type": "string", "enum": ["approve", "reject", "modify"]},
                "reason": {
                    "type": "string",
                    "description": "Reject reason or modification summary",
                },
                "title": {"type": "string", "description": "modify only: new title"},
                "edits": EDITS_SCHEMA,
                "page_file": {"type": "string", "description": "modify only: new page path"},
                "page_content": {"type": "string", "description": "modify only: new page draft"},
            },
            "required": ["proposal_id", "action"],
        }

    def execute(self, params: Dict[str, Any], context: Dict[str, Any] = None) -> str:
        from ..curation.apply import apply_proposal, validate_payload
        from ..curation.repository import NoteProposalRepository
        from ..database import get_db

        proposal_id = params.get("proposal_id")
        action = params.get("action", "")
        reason = params.get("reason", "").strip()

        db = get_db()
        try:
            proposal = NoteProposalRepository.get_by_id(db, proposal_id)
            if not proposal:
                return f"❌ No proposal #{proposal_id}"
            if proposal.status != "pending":
                return f"❌ Proposal #{proposal_id} is '{proposal.status}', not pending"

            if action == "reject":
                NoteProposalRepository.resolve(db, proposal_id, "rejected", reason or None)
                return f"✅ Rejected proposal #{proposal_id}" + (f" ({reason})" if reason else "")

            if action == "modify":
                payload = _build_payload(params)
                problems = validate_payload(proposal.kind, payload, self.editor)
                if problems:
                    return "❌ Modified payload invalid:\n" + "\n".join(f"- {p}" for p in problems)
                NoteProposalRepository.update_payload(
                    db, proposal_id, payload,
                    title=params.get("title"), resolution_note=reason or None,
                )
                return (
                    f"✅ Updated proposal #{proposal_id}; still pending — "
                    f"approve to apply the new version."
                )

            if action == "approve":
                result = apply_proposal(proposal.kind, proposal.payload, self.editor, self.logger)
                if result["status"] == "applied":
                    NoteProposalRepository.resolve(db, proposal_id, "applied")
                    if not result["written"]:
                        return f"✅ Marked insight #{proposal_id} as reviewed (no file changes)"
                    files = ", ".join(result["written"])
                    return f"✅ Applied proposal #{proposal_id} — wrote: {files}"
                note = "; ".join(result.get("problems", []))
                if result.get("written"):
                    note = f"partially applied ({', '.join(result['written'])}); " + note
                NoteProposalRepository.resolve(db, proposal_id, "stale", note)
                return (
                    f"⚠️ Could not apply proposal #{proposal_id} — files changed since it "
                    f"was filed. Marked stale: {note}"
                )

            return f"❌ Unknown action '{action}'"
        finally:
            db.close()
