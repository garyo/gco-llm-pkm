#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#   "python-dotenv>=1.0.0",
#   "pyyaml>=6.0.2",
# ]
# ///
"""
CLI harness for testing PKM tools directly.

Usage:
  ./test-tool.py search_notes pattern="sail" context=2
  ./test-tool.py search_notes '{"pattern": "sail", "context": 2}'
  ./test-tool.py list_files
  ./test-tool.py execute_shell command="date +%Y"
"""

import sys
import json
import logging
from pathlib import Path

# Import configuration
from config.settings import Config

# Import tools
from pkm_bridge.tools.registry import ToolRegistry
from pkm_bridge.tools.shell import ExecuteShellTool
from pkm_bridge.tools.files import ListFilesTool
from pkm_bridge.tools.search_notes import SearchNotesTool


def setup_logging():
    """Setup basic console logging."""
    logging.basicConfig(
        level=logging.DEBUG,
        format='%(levelname)s: %(message)s'
    )
    return logging.getLogger(__name__)


def parse_params(args):
    """Parse parameters from command line args.

    Supports both:
      key=value key2=value2
      '{"key": "value", "key2": "value2"}'
    """
    if not args:
        return {}

    # Try parsing as JSON first
    if len(args) == 1 and args[0].startswith('{'):
        try:
            return json.loads(args[0])
        except json.JSONDecodeError:
            pass

    # Parse as key=value pairs
    params = {}
    for arg in args:
        if '=' in arg:
            key, value = arg.split('=', 1)
            # Try to convert to int if possible
            try:
                value = int(value)
            except ValueError:
                pass
            params[key] = value
        else:
            print(f"Warning: Ignoring invalid parameter: {arg}")

    return params


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        print("\nAvailable tools:")
        print("  - search_notes")
        print("  - list_files")
        print("  - execute_shell")
        print("  - add_journal_note")
        sys.exit(1)

    tool_name = sys.argv[1]
    param_args = sys.argv[2:]

    # Setup
    logger = setup_logging()
    config = Config()

    print(f"=" * 60)
    print(f"Testing tool: {tool_name}")
    print(f"ORG_DIR: {config.org_dir}")
    print(f"LOGSEQ_DIR: {config.logseq_dir}")
    print(f"=" * 60)

    # Create tool registry and register tools
    tool_registry = ToolRegistry()

    execute_shell_tool = ExecuteShellTool(
        logger, config.allowed_commands, config.org_dir, config.logseq_dir
    )
    tool_registry.register(execute_shell_tool)
    tool_registry.register(ListFilesTool(logger, config.org_dir, config.logseq_dir))
    tool_registry.register(SearchNotesTool(logger, config.org_dir, config.logseq_dir))

    # Check tool exists
    if tool_name not in tool_registry.list_tools():
        print(f"Error: Unknown tool '{tool_name}'")
        print(f"Available: {', '.join(tool_registry.list_tools())}")
        sys.exit(1)

    # Parse parameters
    params = parse_params(param_args)
    print(f"\nParameters: {params}")
    print(f"=" * 60)
    print()

    # Execute tool
    try:
        result = tool_registry.execute_tool(tool_name, params)
        print(result)
        print()
        print(f"=" * 60)
        print(f"Result length: {len(result)} chars")

    except Exception as e:
        print(f"Error executing tool: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
