"""OAuth 2.1 authentication for MCP server.

Implements a minimal OAuth authorization server so Claude.ai desktop
can authenticate via its standard OAuth flow. Uses a simple password
check (from MCP_AUTH_PASSWORD env var).

Tokens are signed JWTs (survive container restarts). Client registrations
are persisted to PostgreSQL. Auth codes remain in-memory (short-lived).

The server is behind Traefik with HTTPS at oberbrunner.com.
"""

import html
import logging
import os
import secrets
import time
import uuid

import jwt
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    construct_redirect_uri,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyHttpUrl
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

logger = logging.getLogger("mcp_server.auth")

# Token lifetimes
ACCESS_TOKEN_TTL = 24 * 3600  # 24 hours
REFRESH_TOKEN_TTL = 90 * 24 * 3600  # 90 days
AUTH_CODE_TTL = 300  # 5 minutes

JWT_ALGORITHM = "HS256"


def _get_jwt_secret() -> str:
    """Get JWT secret from environment."""
    secret = os.getenv("JWT_SECRET")
    if not secret:
        raise RuntimeError("JWT_SECRET environment variable is required for MCP OAuth")
    return secret


class PKMOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """OAuth 2.1 provider for PKM MCP server.

    Uses a simple password for authentication. Tokens are signed JWTs.
    Client registrations are persisted to PostgreSQL.
    Auth codes are in-memory (short-lived, consumed during same flow).
    """

    def __init__(self, server_url: str, password: str):
        self.server_url = server_url
        self.password = password
        self.jwt_secret = _get_jwt_secret()
        # In-memory stores
        self.clients: dict[str, OAuthClientInformationFull] = {}
        self.auth_codes: dict[str, AuthorizationCode] = {}
        self.revoked_jtis: set[str] = set()  # Best-effort revocation (in-memory)
        # Maps state → authorization params for the login callback
        self.pending_auth: dict[str, dict[str, str | None]] = {}
        # Load persisted client registrations from DB
        self._load_clients_from_db()

    def _load_clients_from_db(self) -> None:
        """Load persisted client registrations from PostgreSQL."""
        try:
            from pkm_bridge.database import McpClientRegistration, get_db, init_db
            init_db()
            db = get_db()
            try:
                rows = db.query(McpClientRegistration).all()
                for row in rows:
                    client_info = OAuthClientInformationFull.model_validate_json(
                        row.client_info_json
                    )
                    if client_info.client_id:
                        self.clients[client_info.client_id] = client_info
                if rows:
                    logger.info(f"Loaded {len(rows)} client registrations from DB")
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"Could not load client registrations from DB: {e}")

    def _save_client_to_db(self, client_info: OAuthClientInformationFull) -> None:
        """Persist a client registration to PostgreSQL (upsert)."""
        try:
            from pkm_bridge.database import McpClientRegistration, get_db
            db = get_db()
            try:
                existing = db.query(McpClientRegistration).filter_by(
                    client_id=client_info.client_id
                ).first()
                json_data = client_info.model_dump_json()
                if existing:
                    existing.client_info_json = json_data
                else:
                    db.add(McpClientRegistration(
                        client_id=client_info.client_id,
                        client_info_json=json_data,
                    ))
                db.commit()
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"Could not persist client registration to DB: {e}")

    def _encode_token(
        self,
        token_type: str,
        client_id: str,
        scopes: list[str],
        ttl: int,
        resource: str | None = None,
    ) -> tuple[str, str]:
        """Encode a JWT token. Returns (token_string, jti)."""
        jti = str(uuid.uuid4())
        now = int(time.time())
        payload: dict = {
            "type": token_type,
            "sub": client_id,
            "scopes": scopes,
            "iat": now,
            "exp": now + ttl,
            "jti": jti,
        }
        if resource is not None:
            payload["resource"] = resource
        token_str = jwt.encode(payload, self.jwt_secret, algorithm=JWT_ALGORITHM)
        return token_str, jti

    def _decode_token(self, token_str: str, expected_type: str) -> dict | None:
        """Decode and verify a JWT token. Returns payload or None."""
        try:
            payload = jwt.decode(
                token_str, self.jwt_secret, algorithms=[JWT_ALGORITHM]
            )
        except jwt.ExpiredSignatureError:
            logger.debug(f"Token expired ({expected_type})")
            return None
        except jwt.InvalidTokenError as e:
            logger.debug(f"Invalid token ({expected_type}): {e}")
            return None

        if payload.get("type") != expected_type:
            logger.debug(f"Token type mismatch: expected {expected_type}, got {payload.get('type')}")
            return None

        if payload.get("jti") in self.revoked_jtis:
            logger.debug("Token has been revoked")
            return None

        return payload

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return self.clients.get(client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        if not client_info.client_id:
            raise ValueError("No client_id provided")
        self.clients[client_info.client_id] = client_info
        self._save_client_to_db(client_info)
        logger.info(f"Registered OAuth client: {client_info.client_id}")

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        """Return URL to redirect user to for authentication."""
        state = params.state or secrets.token_hex(16)
        self.pending_auth[state] = {
            "redirect_uri": str(params.redirect_uri),
            "code_challenge": params.code_challenge,
            "redirect_uri_provided_explicitly": str(
                params.redirect_uri_provided_explicitly
            ),
            "client_id": client.client_id,
            "scopes": " ".join(params.scopes) if params.scopes else "",
            "resource": params.resource,
        }
        return f"{self.server_url}/login?state={state}"

    async def handle_login(self, request: Request) -> Response:
        """Show the login form."""
        state = request.query_params.get("state", "")
        safe_state = html.escape(state)
        return HTMLResponse(f"""<!DOCTYPE html>
<html>
<head>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>PKM Bridge — Sign In</title>
    <style>
        body {{ font-family: system-ui, sans-serif; display: flex;
               justify-content: center; align-items: center; min-height: 100vh;
               margin: 0; background: #f5f5f5; }}
        .card {{ background: white; padding: 2rem; border-radius: 8px;
                 box-shadow: 0 2px 8px rgba(0,0,0,0.1); max-width: 360px;
                 width: 100%; }}
        h1 {{ margin: 0 0 1.5rem; font-size: 1.25rem; text-align: center; }}
        input {{ display: block; width: 100%; padding: 0.5rem;
                 margin: 0.5rem 0 1rem; border: 1px solid #ccc;
                 border-radius: 4px; box-sizing: border-box; font-size: 1rem; }}
        button {{ display: block; width: 100%; padding: 0.75rem;
                  background: #2563eb; color: white; border: none;
                  border-radius: 4px; cursor: pointer; font-size: 1rem; }}
        button:hover {{ background: #1d4ed8; }}
        .error {{ color: #dc2626; margin-bottom: 1rem; text-align: center; }}
    </style>
</head>
<body>
    <div class="card">
        <h1>PKM Bridge</h1>
        <form action="/login/callback" method="post">
            <input type="hidden" name="state" value="{safe_state}">
            <input name="password" type="password" placeholder="Password"
                   autofocus required>
            <button type="submit">Sign In</button>
        </form>
    </div>
</body>
</html>""")

    async def handle_login_callback(self, request: Request) -> Response:
        """Validate password, create auth code, redirect back to client."""
        form = await request.form()
        password = str(form.get("password", ""))
        state = str(form.get("state", ""))

        if password != self.password:
            logger.warning("Failed login attempt")
            raise HTTPException(status_code=401, detail="Invalid password")

        state_data = self.pending_auth.pop(state, None)
        if not state_data:
            raise HTTPException(status_code=400, detail="Invalid or expired state")

        # Create authorization code
        code = secrets.token_urlsafe(32)
        scopes = state_data["scopes"].split() if state_data["scopes"] else ["pkm"]
        self.auth_codes[code] = AuthorizationCode(
            code=code,
            client_id=state_data["client_id"],
            redirect_uri=AnyHttpUrl(state_data["redirect_uri"]),
            redirect_uri_provided_explicitly=(
                state_data["redirect_uri_provided_explicitly"] == "True"
            ),
            expires_at=time.time() + AUTH_CODE_TTL,
            scopes=scopes,
            code_challenge=state_data["code_challenge"],
            resource=state_data.get("resource"),
        )

        redirect_url = construct_redirect_uri(
            state_data["redirect_uri"], code=code, state=state
        )
        logger.info("User authenticated, redirecting with auth code")
        return RedirectResponse(url=redirect_url, status_code=302)

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        code = self.auth_codes.get(authorization_code)
        if code and code.expires_at < time.time():
            del self.auth_codes[authorization_code]
            return None
        return code

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        # Remove used code (one-time use)
        self.auth_codes.pop(authorization_code.code, None)

        # Generate JWT tokens
        access_token_str, _ = self._encode_token(
            "access",
            client.client_id or "",
            authorization_code.scopes,
            ACCESS_TOKEN_TTL,
            resource=authorization_code.resource,
        )
        refresh_token_str, _ = self._encode_token(
            "refresh",
            client.client_id or "",
            authorization_code.scopes,
            REFRESH_TOKEN_TTL,
        )

        logger.info(f"Issued JWT access token for client {client.client_id}")
        return OAuthToken(
            access_token=access_token_str,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL,
            scope=" ".join(authorization_code.scopes),
            refresh_token=refresh_token_str,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        payload = self._decode_token(token, "access")
        if payload is None:
            return None
        return AccessToken(
            token=token,
            client_id=payload["sub"],
            scopes=payload.get("scopes", []),
            expires_at=payload["exp"],
            resource=payload.get("resource"),
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        payload = self._decode_token(refresh_token, "refresh")
        if payload is None:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=payload["sub"],
            scopes=payload.get("scopes", []),
            expires_at=payload["exp"],
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> OAuthToken:
        # Revoke old refresh token's JTI
        old_payload = self._decode_token(refresh_token.token, "refresh")
        if old_payload and old_payload.get("jti"):
            self.revoked_jtis.add(old_payload["jti"])

        # Issue new tokens
        effective_scopes = scopes or refresh_token.scopes
        new_access, _ = self._encode_token(
            "access",
            client.client_id or "",
            effective_scopes,
            ACCESS_TOKEN_TTL,
        )
        new_refresh, _ = self._encode_token(
            "refresh",
            client.client_id or "",
            effective_scopes,
            REFRESH_TOKEN_TTL,
        )

        logger.info(f"Refreshed JWT token for client {client.client_id}")
        return OAuthToken(
            access_token=new_access,
            token_type="Bearer",
            expires_in=ACCESS_TOKEN_TTL,
            scope=" ".join(effective_scopes),
            refresh_token=new_refresh,
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        # Extract JTI from the token string and add to revoked set
        token_str = token.token
        for token_type in ("access", "refresh"):
            payload = self._decode_token(token_str, token_type)
            if payload and payload.get("jti"):
                self.revoked_jtis.add(payload["jti"])
                logger.info(f"Revoked {token_type} token (jti={payload['jti']})")
                return
        logger.info("Token revoked (could not extract JTI)")


def get_oauth_provider() -> PKMOAuthProvider | None:
    """Create an OAuth provider if MCP_AUTH_PASSWORD is configured."""
    password = os.getenv("MCP_AUTH_PASSWORD")
    if not password:
        logger.warning("MCP_AUTH_PASSWORD not set — MCP server has no authentication")
        return None

    server_url = os.getenv("MCP_BASE_URL", "https://mcp.oberbrunner.com")
    logger.info("OAuth authentication enabled for MCP server")
    return PKMOAuthProvider(server_url=server_url, password=password)


def get_auth_settings(server_url: str) -> AuthSettings:
    """Create AuthSettings for the OAuth provider."""
    return AuthSettings(
        issuer_url=AnyHttpUrl(server_url),
        client_registration_options=ClientRegistrationOptions(
            enabled=True,
            valid_scopes=["pkm"],
            default_scopes=["pkm"],
        ),
        revocation_options=RevocationOptions(enabled=True),
        required_scopes=["pkm"],
        resource_server_url=None,  # Combined AS+RS mode
    )
