#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#   "pyyaml>=6.0.2",
# ]
# ///
"""Test the find_context tool."""

import sys
from pathlib import Path

# Add pkm_bridge to path
sys.path.insert(0, str(Path(__file__).parent))

from pkm_bridge.tools.find_context import FindContextTool
from pkm_bridge.logging_config import setup_logging

# Setup logger
logger = setup_logging("INFO")

# Initialize tool
org_dir = Path("/Users/garyo/Documents/org-agenda")
logseq_dir = Path("/Users/garyo/Logseq Notes")

tool = FindContextTool(logger, org_dir, logseq_dir)

# Test query
pattern = "Emacs.*PKM"
max_results = 3

print(f"Searching for pattern: {pattern}")
print(f"Max results: {max_results}")
print("=" * 80)

result = tool.execute({"pattern": pattern, "max_results": max_results})
print(result)
