"""Bearer token authentication for MCP server.

Uses a static bearer token configured via MCP_BEARER_TOKEN env var.
The server is behind Traefik with HTTPS at oberbrunner.com, so a
static token is adequate for a personal server.

Implements the MCP SDK's TokenVerifier protocol for integration with
the built-in auth middleware.
"""

import logging
import os

from mcp.server.auth.provider import AccessToken

logger = logging.getLogger("mcp_server.auth")


class StaticBearerTokenVerifier:
    """Simple bearer token verifier using a static token from environment.

    Implements the MCP SDK's TokenVerifier protocol.
    """

    def __init__(self, token: str):
        self.token = token

    async def verify_token(self, token: str) -> AccessToken | None:
        """Verify a bearer token against the configured static token."""
        if token == self.token:
            return AccessToken(
                token=token,
                client_id="pkm-user",
                scopes=["pkm:all"],
            )
        logger.warning("Invalid bearer token presented")
        return None


def get_token_verifier() -> StaticBearerTokenVerifier | None:
    """Create a token verifier if MCP_BEARER_TOKEN is configured."""
    token = os.getenv("MCP_BEARER_TOKEN")
    if not token:
        logger.warning("MCP_BEARER_TOKEN not set — MCP server has no authentication")
        return None

    logger.info("Bearer token authentication enabled for MCP server")
    return StaticBearerTokenVerifier(token)
