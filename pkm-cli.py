#!/usr/bin/env -S uv run --script
# /// script
# dependencies = [
#   "requests>=2.31.0",
#   "python-dotenv>=1.0.0",
# ]
# ///
"""
gco-pkm-llm CLI Client

Provides command-line access to the PKM bridge server.
Supports both one-off queries and interactive REPL mode.
"""

import argparse
import sys
import os
import requests
import json
import getpass
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
DEFAULT_HOST = os.getenv("HOST", "127.0.0.1")
DEFAULT_PORT = os.getenv("PORT", "8000")
BASE_URL = f"http://{DEFAULT_HOST}:{DEFAULT_PORT}"
TOKEN_FILE = Path.home() / ".pkm-cli-token"
AUTH_ENABLED = os.getenv("AUTH_ENABLED", "true").lower() == "true"


class PKMClient:
    """Client for PKM bridge server"""

    def __init__(self, base_url: str = BASE_URL):
        self.base_url = base_url
        self.session_id = None
        self.token = None

        # Load token if auth is enabled
        if AUTH_ENABLED:
            self._load_token()

    def _load_token(self):
        """Load authentication token from file or environment"""
        # Try loading from file first
        if TOKEN_FILE.exists():
            try:
                self.token = TOKEN_FILE.read_text().strip()
                # Verify token is still valid
                if not self._verify_token():
                    self.token = None
                    TOKEN_FILE.unlink(missing_ok=True)
            except Exception:
                pass

        # If no valid token, try to authenticate
        if not self.token:
            self._authenticate()

    def _save_token(self, token: str):
        """Save authentication token to file"""
        try:
            TOKEN_FILE.write_text(token)
            TOKEN_FILE.chmod(0o600)  # Readable only by owner
            self.token = token
        except Exception as e:
            print(f"Warning: Could not save token: {e}", file=sys.stderr)

    def _verify_token(self) -> bool:
        """Check if current token is valid"""
        if not self.token:
            return False

        try:
            response = requests.post(
                f"{self.base_url}/verify-token",
                json={"token": self.token},
                timeout=5
            )
            return response.status_code == 200 and response.json().get("valid", False)
        except Exception:
            return False

    def _authenticate(self):
        """Authenticate with the server"""
        # Try environment variable first
        password = os.getenv("PKM_PASSWORD")

        # If no env var, prompt user
        if not password:
            print("Authentication required.", file=sys.stderr)
            try:
                password = getpass.getpass("Password: ")
            except (KeyboardInterrupt, EOFError):
                print("\nAuthentication cancelled.", file=sys.stderr)
                sys.exit(1)

        # Login
        try:
            response = requests.post(
                f"{self.base_url}/login",
                json={"password": password},
                timeout=10
            )

            if response.status_code == 200:
                data = response.json()
                token = data.get("token")
                if token:
                    self._save_token(token)
                    print("✓ Authenticated successfully", file=sys.stderr)
                    return
            elif response.status_code == 429:
                print("❌ Too many login attempts. Please wait a minute.", file=sys.stderr)
            else:
                print("❌ Authentication failed: Invalid password", file=sys.stderr)

        except requests.exceptions.RequestException as e:
            print(f"❌ Authentication error: {e}", file=sys.stderr)

        sys.exit(1)

    def _get_headers(self) -> dict:
        """Get request headers with authentication if needed"""
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _handle_401(self):
        """Handle 401 Unauthorized by re-authenticating"""
        print("Token expired. Re-authenticating...", file=sys.stderr)
        self.token = None
        TOKEN_FILE.unlink(missing_ok=True)
        self._authenticate()

    def query(self, message: str, session_id: Optional[str] = None, model: Optional[str] = None) -> dict:
        """Send a query to the server"""
        if session_id is None:
            session_id = self.session_id or "cli-session"

        payload = {"message": message, "session_id": session_id}
        if model:
            payload["model"] = model

        try:
            response = requests.post(
                f"{self.base_url}/query",
                json=payload,
                headers=self._get_headers(),
                timeout=60
            )

            # Handle 401 and retry once
            if response.status_code == 401 and AUTH_ENABLED:
                self._handle_401()
                response = requests.post(
                    f"{self.base_url}/query",
                    json=payload,
                    headers=self._get_headers(),
                    timeout=60
                )

            response.raise_for_status()
            data = response.json()

            # Update session ID for next request
            self.session_id = data.get("session_id", session_id)

            return data
        except requests.exceptions.RequestException as e:
            return {"error": str(e)}

    def health(self) -> dict:
        """Check server health"""
        try:
            response = requests.get(f"{self.base_url}/health", timeout=5)
            response.raise_for_status()
            return response.json()
        except requests.exceptions.RequestException as e:
            return {"error": str(e)}

    def clear_session(self, session_id: Optional[str] = None):
        """Clear conversation history"""
        if session_id is None:
            session_id = self.session_id or "cli-session"

        try:
            response = requests.delete(
                f"{self.base_url}/sessions/{session_id}",
                headers=self._get_headers(),
                timeout=5
            )

            # Handle 401 and retry once
            if response.status_code == 401 and AUTH_ENABLED:
                self._handle_401()
                response = requests.delete(
                    f"{self.base_url}/sessions/{session_id}",
                    headers=self._get_headers(),
                    timeout=5
                )

            response.raise_for_status()
            self.session_id = None
        except requests.exceptions.RequestException as e:
            print(f"Error clearing session: {e}")


def repl_mode(client: PKMClient):
    """Interactive REPL mode"""
    print("=" * 60)
    print("PKM Assistant - Interactive Mode")
    print("=" * 60)
    print("Commands:")
    print("  /help    - Show this help")
    print("  /clear   - Clear conversation history")
    print("  /health  - Check server status")
    print("  /quit    - Exit REPL")
    print("  Ctrl+C   - Exit REPL")
    print()
    print("Type your questions and press Enter.")
    print("=" * 60)
    print()

    # Check server health first
    health = client.health()
    if "error" in health:
        print(f"❌ Cannot connect to server at {client.base_url}")
        print(f"   Error: {health['error']}")
        print(f"\nMake sure the server is running:")
        print(f"  ./pkm-bridge-server.py")
        return

    print(f"✓ Connected to server")
    print(f"  Org dir: {health.get('org_dir', 'unknown')}")
    if health.get('logseq_dir'):
        print(f"  Logseq dir: {health['logseq_dir']}")
    print(f"  Skills: {', '.join(health.get('skills_available', []))}")
    print()

    while True:
        try:
            # Prompt
            user_input = input("\n\033[1;34mYou:\033[0m ").strip()

            if not user_input:
                continue

            # Handle commands
            if user_input.startswith("/"):
                command = user_input.lower()

                if command in ["/quit", "/exit", "/q"]:
                    print("\nGoodbye!")
                    break

                elif command == "/help":
                    print("\nCommands:")
                    print("  /help    - Show this help")
                    print("  /clear   - Clear conversation history")
                    print("  /health  - Check server status")
                    print("  /quit    - Exit REPL")
                    continue

                elif command == "/clear":
                    client.clear_session()
                    print("✓ Conversation history cleared")
                    continue

                elif command == "/health":
                    health = client.health()
                    if "error" in health:
                        print(f"❌ Error: {health['error']}")
                    else:
                        print(json.dumps(health, indent=2))
                    continue

                else:
                    print(f"Unknown command: {user_input}")
                    print("Type /help for available commands")
                    continue

            # Send query
            result = client.query(user_input)

            if "error" in result:
                print(f"\n\033[1;31m❌ Error:\033[0m {result['error']}")
            else:
                print(f"\n\033[1;32mAssistant:\033[0m")
                print(result.get("response", "(no response)"))

        except KeyboardInterrupt:
            print("\n\nGoodbye!")
            break
        except EOFError:
            print("\n\nGoodbye!")
            break


def one_off_mode(client: PKMClient, query: str, session_id: Optional[str] = None, model: Optional[str] = None):
    """One-off query mode"""
    result = client.query(query, session_id, model)

    if "error" in result:
        print(f"Error: {result['error']}", file=sys.stderr)
        sys.exit(1)
    else:
        print(result.get("response", ""))


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="CLI client for gco-pkm-llm bridge server",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Interactive REPL mode
  %(prog)s

  # One-off query
  %(prog)s "What did I write about music?"

  # Query with custom session ID
  %(prog)s --session my-session "List my org files"

  # Check server health
  %(prog)s --health
        """
    )

    parser.add_argument(
        "query",
        nargs="?",
        help="Query to send (if not provided, enters REPL mode)"
    )

    parser.add_argument(
        "--session", "-s",
        help="Session ID for conversation context"
    )

    parser.add_argument(
        "--health",
        action="store_true",
        help="Check server health and exit"
    )

    parser.add_argument(
        "--url",
        default=BASE_URL,
        help=f"Server URL (default: {BASE_URL})"
    )

    parser.add_argument(
        "--model", "-m",
        help="Claude model to use (overrides server default). Options: claude-haiku-4-5, claude-sonnet-4-5, claude-opus-4-1"
    )

    args = parser.parse_args()

    # Create client
    client = PKMClient(args.url)

    # Health check mode
    if args.health:
        health = client.health()
        if "error" in health:
            print(f"Error: {health['error']}", file=sys.stderr)
            sys.exit(1)
        else:
            print(json.dumps(health, indent=2))
            sys.exit(0)

    # One-off query mode
    if args.query:
        one_off_mode(client, args.query, args.session, args.model)
    else:
        # REPL mode
        if args.model:
            print(f"Note: Using model {args.model} for this session\n")
        repl_mode(client)


if __name__ == "__main__":
    main()
