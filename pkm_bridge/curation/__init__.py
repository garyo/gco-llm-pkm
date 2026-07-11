"""Note curation: background organization proposals with user review.

The curator is a system-owned ScheduledTask (see task.py) that scans notes
and files NoteProposal rows via the propose_note_organization tool. It never
edits files. Proposals are reviewed conversationally in chat (or via MCP)
with list_note_proposals / resolve_note_proposal; approval applies the
change through FileEditor's atomic write path (apply.py).
"""
