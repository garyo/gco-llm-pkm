"""Tests for semantic_search's output format and budget."""

import logging
from pathlib import Path
from typing import Any, Dict

import pytest
import yaml

from pkm_bridge.tools.semantic_search import (
    MAX_CHUNKS_PER_DOC,
    MAX_EXCERPT_CHARS,
    MAX_OUTPUT_CHARS,
    SemanticSearchTool,
)

logger = logging.getLogger("test")


class _FakeRetriever:
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []

    def retrieve_context(self, **kwargs):
        self.calls.append(kwargs)
        return self.chunks[: kwargs["limit"]]


def _chunk(path: Path | str, n: int, content: str = "word " * 300) -> Dict[str, Any]:
    return {
        "filename": str(path),
        "content": content,
        "similarity": 0.5,
        "date": "2026-09-01",
        "heading_path": None,
        "start_line": n,
    }


@pytest.fixture
def dirs(tmp_path: Path) -> tuple[Path, Path]:
    org, logseq = tmp_path / "org", tmp_path / "logseq"
    org.mkdir()
    logseq.mkdir()
    return org, logseq


def test_caps_output(dirs):
    org, logseq = dirs
    doc_a = org / "pages" / "a.md"
    chunks = [_chunk(doc_a, i) for i in range(5)] + [
        _chunk(logseq / "DSS" / f"p{i}.md", i) for i in range(30)
    ]
    retriever = _FakeRetriever(chunks)
    tool = SemanticSearchTool(logger, retriever, org, logseq)
    out = tool.execute({"query": "q", "limit": 30})

    assert len(out) <= MAX_OUTPUT_CHARS
    assert out.count("filename: org:pages/a.md") == MAX_CHUNKS_PER_DOC
    assert "logseq:DSS/p0.md" in out
    assert str(org) not in out
    assert "omitted_for_length" in out
    assert "heading_path" not in out  # None fields are dropped
    assert retriever.calls[0]["limit"] == 60  # over-fetched for the per-note cap


def test_excerpts_long_chunks(dirs):
    org, logseq = dirs
    tool = SemanticSearchTool(logger, _FakeRetriever([_chunk(org / "x.md", 1)]), org, logseq)
    content = yaml.safe_load(tool.execute({"query": "q"}))["results"][0]["content"]
    assert len(content) <= MAX_EXCERPT_CHARS + 2
    assert content.endswith("word …")


def test_short_chunks_and_external_paths_unchanged(dirs):
    org, logseq = dirs
    tool = SemanticSearchTool(
        logger, _FakeRetriever([_chunk("gmail://abc", 1, content="Short email")]), org, logseq
    )
    result = yaml.safe_load(tool.execute({"query": "q"}))["results"][0]
    assert result["filename"] == "gmail://abc"
    assert result["content"] == "Short email"


def test_description_has_no_autorag_claim():
    tool = SemanticSearchTool(logger, _FakeRetriever([]))
    assert "already have auto-retrieved" not in tool.description
