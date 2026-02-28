#!/usr/bin/env python3
"""MCP server for PKM Bridge.

Exposes PKM tools to Claude.ai via Streamable HTTP transport.
Reuses existing tool implementations from pkm_bridge.tools.
"""

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# Add project root to path so we can import pkm_bridge
sys.path.insert(0, str(Path(__file__).parent.parent))

from mcp_server.auth import get_token_verifier
from mcp_server.resources import register_resources
from mcp_server.tools import register_all_tools

logger = logging.getLogger("mcp_server")


def create_server() -> FastMCP:
    """Create and configure the MCP server with all tools and resources."""
    # Load environment
    if Path(".env.local").exists():
        load_dotenv(".env.local")
    else:
        load_dotenv(".env")

    # Configure logging
    log_level = os.getenv("MCP_LOG_LEVEL", os.getenv("LOG_LEVEL", "INFO")).upper()
    logging.basicConfig(
        level=getattr(logging, log_level, logging.INFO),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    # Set up auth
    token_verifier = get_token_verifier()

    # Create FastMCP server with optional auth
    kwargs: dict = {
        "name": "PKM Bridge",
        "instructions": (
            "Personal Knowledge Management server. Use read_prompt_context at the "
            "start of every conversation to load instructions and context. Use "
            "semantic_search before answering knowledge-base questions."
        ),
    }

    if token_verifier:
        from mcp.server.auth.settings import AuthSettings

        mcp_base_url = os.getenv("MCP_BASE_URL", "https://mcp.oberbrunner.com")
        kwargs["token_verifier"] = token_verifier
        kwargs["auth"] = AuthSettings(
            issuer_url=mcp_base_url,
            resource_server_url=mcp_base_url,
        )

    mcp = FastMCP(**kwargs)

    # Register tools and resources
    register_all_tools(mcp)
    register_resources(mcp)

    logger.info("MCP server configured")
    return mcp


# Module-level server instance for `mcp run` / `mcp dev`
mcp = create_server()


def main():
    """Run the MCP server with Streamable HTTP transport."""
    port = int(os.getenv("MCP_PORT", "8001"))
    host = os.getenv("MCP_HOST", "0.0.0.0")

    logger.info(f"Starting MCP server on {host}:{port}")

    # Get the Starlette app and run it with uvicorn
    app = mcp.streamable_http_app()

    import uvicorn
    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
