"""Tests for the note chunker that feeds the RAG index."""

from pathlib import Path

from pkm_bridge.embeddings.chunker import NoteChunker

PAGE = """---
title: "Egypt Trip"
id: X
---

<!-- backlinks -->
- 2026-02-19 Thu 15:09 <- [2025-11-25 Tue](../journals/2025-11-25.md)
- 2026-02-19 Thu 15:09 <- [2025-11-26 Wed](../journals/2025-11-26.md)
<!-- /backlinks -->

# Overview
Pyramids and temples, then the 2027 total eclipse from Luxor.

# Journal
<!-- query: Egypt -->

## [2026-10-13 Tue · Travel Egypt](../journals/2026-10-13.md#travel-egypt)
Flights look cheaper this week.

### Hotels
- Jolie Ville
<!-- /query 1234abcd -->

Choosing a guide in Luxor still needs a decision.
"""


def test_markdown_chunks_leave_out_backlinks_and_query_results(tmp_path: Path):
    page = tmp_path / "egypt.md"
    page.write_text(PAGE)
    chunks = NoteChunker(min_tokens=1).chunk_file(page)

    assert [c.heading_path for c in chunks] == ["Overview", "Journal"]
    journal = chunks[1]
    assert journal.content == "# Journal\n\nChoosing a guide in Luxor still needs a decision."
    assert PAGE.split("\n")[journal.start_line - 1] == "# Journal"
    for copied in ("<-", "Flights", "Jolie", "query"):
        assert not any(copied in c.content for c in chunks)
