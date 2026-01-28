"""
Antigravity OAuth Flow

Implements Google OAuth with PKCE for Antigravity (Cloud Code Assist) authentication.
Uses hardcoded client credentials - no client_secret.json needed.

Based on: https://github.com/nicepkg/opencode-antigravity-auth
"""
import asyncio
import base64
import hashlib
import json
import logging
import secrets
import webbrowser
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread
from typing import Optional
from urllib.parse import urlencode, urlparse, parse_qs

from ..storage import OAuthToken

logger = logging.getLogger("autowrkers.antigravity.oauth")

# Hardcoded Antigravity OAuth credentials (from opencode-antigravity-auth)
ANTIGRAVITY_CLIENT_ID = "1071006060591-tmhssin2h21lcre235vtolojh4g403ep.apps.googleusercontent.com"
ANTIGRAVITY_CLIENT_SECRET = "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"
ANTIGRAVITY_REDIRECT_URI = "http://localhost:51121/oauth-callback"
ANTIGRAVITY_REDIRECT_PORT = 51121
ANTIGRAVITY_TOKEN_URI = "https://oauth2.googleapis.com/token"

ANTIGRAVITY_SCOPES = [
    "https://www.googleapis.com/auth/cloud-platform",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/cclog",
    "https://www.googleapis.com/auth/experimentsandconfigs",
]

# Endpoints for project ID discovery and API requests
ANTIGRAVITY_ENDPOINTS = [
    "https://cloudcode-pa.googleapis.com",
    "https://daily-cloudcode-pa.sandbox.googleapis.com",
    "https://autopush-cloudcode-pa.sandbox.googleapis.com",
]

ANTIGRAVITY_HEADERS = {
    "User-Agent": "antigravity/1.11.5 windows/amd64",
    "X-Goog-Api-Client": "google-cloud-sdk vscode_cloudshelleditor/0.1",
    "Client-Metadata": '{"ideType":"IDE_UNSPECIFIED","platform":"PLATFORM_UNSPECIFIED","pluginType":"GEMINI"}',
}

DEFAULT_PROJECT_ID = "rising-fact-p41fc"


class AntigravityOAuthError(Exception):
    pass


def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE code_verifier and code_challenge (S256)."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _build_auth_url(code_challenge: str) -> str:
    """Build Google OAuth authorization URL with PKCE."""
    params = {
        "client_id": ANTIGRAVITY_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": ANTIGRAVITY_REDIRECT_URI,
        "scope": " ".join(ANTIGRAVITY_SCOPES),
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent",
    }
    return f"https://accounts.google.com/o/oauth2/v2/auth?{urlencode(params)}"


def pack_refresh_token(refresh_token: str, project_id: str) -> str:
    """Pack refresh token and project ID into a single string."""
    return f"{refresh_token}|{project_id}"


def unpack_refresh_token(packed: str) -> tuple[str, str]:
    """Unpack refresh token and project ID from packed string."""
    parts = packed.split("|", 1)
    return parts[0], parts[1] if len(parts) > 1 else DEFAULT_PROJECT_ID


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """HTTP handler that captures the OAuth callback code."""

    auth_code: Optional[str] = None
    error: Optional[str] = None

    def do_GET(self):
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        if parsed.path == "/oauth-callback":
            if "code" in params:
                _OAuthCallbackHandler.auth_code = params["code"][0]
                self._send_success()
            elif "error" in params:
                _OAuthCallbackHandler.error = params["error"][0]
                self._send_error(params["error"][0])
            else:
                _OAuthCallbackHandler.error = "No code or error in callback"
                self._send_error("Unknown error")
            # Shut down the server after handling the callback
            Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self.send_response(404)
            self.end_headers()

    def _send_success(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        html = """<!DOCTYPE html><html><head><title>Antigravity Auth</title>
        <style>body{font-family:system-ui;background:#1a1a2e;color:#e0e0e0;display:flex;
        justify-content:center;align-items:center;height:100vh;margin:0}
        .card{background:#16213e;padding:40px;border-radius:12px;text-align:center;
        box-shadow:0 4px 20px rgba(0,0,0,0.3)}</style></head>
        <body><div class="card"><h2>Authentication Successful</h2>
        <p>You can close this tab and return to Autowrkers.</p></div></body></html>"""
        self.wfile.write(html.encode())

    def _send_error(self, error: str):
        self.send_response(400)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        html = f"""<!DOCTYPE html><html><head><title>Auth Error</title>
        <style>body{{font-family:system-ui;background:#1a1a2e;color:#e0e0e0;display:flex;
        justify-content:center;align-items:center;height:100vh;margin:0}}
        .card{{background:#16213e;padding:40px;border-radius:12px;text-align:center}}</style></head>
        <body><div class="card"><h2>Authentication Failed</h2>
        <p>{error}</p></div></body></html>"""
        self.wfile.write(html.encode())

    def log_message(self, format, *args):
        pass  # Suppress default logging


_active_flow: Optional["AntigravityOAuthFlow"] = None


class AntigravityOAuthFlow:
    """Antigravity OAuth flow using PKCE with hardcoded credentials."""

    def __init__(self):
        self._verifier: Optional[str] = None
        self._server: Optional[HTTPServer] = None
        self._server_thread: Optional[Thread] = None

    def prepare_flow(self) -> str:
        """Prepare the OAuth flow: start callback server, return auth URL.

        This is non-blocking - it starts the callback server in a background
        thread and returns the auth URL immediately. Call wait_for_callback_and_exchange()
        afterwards to complete the flow.
        """
        global _active_flow
        # Clean up any previous flow's server
        if _active_flow is not None and _active_flow is not self:
            _active_flow._cleanup_server()
        _active_flow = self
        verifier, challenge = _generate_pkce()
        self._verifier = verifier
        auth_url = _build_auth_url(challenge)

        # Reset handler state
        _OAuthCallbackHandler.auth_code = None
        _OAuthCallbackHandler.error = None

        # Start local callback server using serve_forever for proper shutdown
        try:
            server = HTTPServer(("localhost", ANTIGRAVITY_REDIRECT_PORT), _OAuthCallbackHandler)
        except OSError as e:
            raise AntigravityOAuthError(
                f"Port {ANTIGRAVITY_REDIRECT_PORT} is in use. "
                "Close any other Antigravity/OpenCode instances and try again."
            ) from e

        self._server = server
        self._server_thread = Thread(target=server.serve_forever, daemon=True)
        self._server_thread.start()

        logger.info("Antigravity OAuth callback server started on port %d", ANTIGRAVITY_REDIRECT_PORT)
        return auth_url

    def _cleanup_server(self):
        """Shut down and close the callback server."""
        if self._server:
            try:
                self._server.shutdown()
            except Exception:
                pass
            try:
                self._server.server_close()
            except Exception:
                pass
            self._server = None

    async def wait_for_callback_and_exchange(self, timeout: int = 120) -> OAuthToken:
        """Wait for the OAuth callback and exchange the code for tokens.

        Must be called after prepare_flow(). Blocks until the callback is received
        or the timeout expires.
        """
        if not self._server_thread or not self._verifier:
            raise AntigravityOAuthError("prepare_flow() must be called first")

        try:
            # Wait for server thread to finish (handler calls shutdown on callback)
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, self._server_thread.join, timeout)

            if _OAuthCallbackHandler.error:
                raise AntigravityOAuthError(f"OAuth error: {_OAuthCallbackHandler.error}")

            if not _OAuthCallbackHandler.auth_code:
                raise AntigravityOAuthError("OAuth flow timed out - no callback received")

            code = _OAuthCallbackHandler.auth_code

            # Exchange code for tokens
            token_data = await self._exchange_code(code, self._verifier)

            # Get user info
            account_email = await self._fetch_user_info(token_data["access_token"])

            # Discover project ID
            project_id = await self._fetch_project_id(token_data["access_token"])
            logger.info(f"Antigravity project ID: {project_id}")

            expires_at = datetime.now() + timedelta(seconds=token_data.get("expires_in", 3600))

            return OAuthToken(
                provider="antigravity",
                access_token=token_data["access_token"],
                refresh_token=pack_refresh_token(
                    token_data.get("refresh_token", ""),
                    project_id,
                ),
                token_uri=ANTIGRAVITY_TOKEN_URI,
                client_id=ANTIGRAVITY_CLIENT_ID,
                client_secret=ANTIGRAVITY_CLIENT_SECRET,
                scopes=ANTIGRAVITY_SCOPES,
                expires_at=expires_at,
                account_email=account_email,
            )
        finally:
            self._cleanup_server()

    async def _exchange_code(self, code: str, verifier: str) -> dict:
        """Exchange authorization code for tokens."""
        try:
            import httpx
        except ImportError:
            raise AntigravityOAuthError("httpx package required. Install with: pip install httpx")

        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                ANTIGRAVITY_TOKEN_URI,
                data={
                    "client_id": ANTIGRAVITY_CLIENT_ID,
                    "client_secret": ANTIGRAVITY_CLIENT_SECRET,
                    "code": code,
                    "grant_type": "authorization_code",
                    "redirect_uri": ANTIGRAVITY_REDIRECT_URI,
                    "code_verifier": verifier,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            if response.status_code != 200:
                error_text = response.text
                raise AntigravityOAuthError(
                    f"Token exchange failed ({response.status_code}): {error_text}"
                )

            return response.json()

    async def _fetch_user_info(self, access_token: str) -> Optional[str]:
        """Fetch user email from Google userinfo endpoint."""
        try:
            import httpx
        except ImportError:
            return None

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    "https://www.googleapis.com/oauth2/v2/userinfo",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if response.status_code == 200:
                    data = response.json()
                    return data.get("email")
        except Exception as e:
            logger.warning(f"Failed to fetch user info: {e}")
        return None

    async def _fetch_project_id(self, access_token: str) -> str:
        """Discover Antigravity project ID via loadCodeAssist endpoint."""
        try:
            import httpx
        except ImportError:
            return DEFAULT_PROJECT_ID

        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
            **ANTIGRAVITY_HEADERS,
        }

        body = json.dumps({
            "metadata": {
                "ideType": "IDE_UNSPECIFIED",
                "platform": "PLATFORM_UNSPECIFIED",
                "pluginType": "GEMINI",
            }
        })

        async with httpx.AsyncClient(timeout=15.0) as client:
            for endpoint in ANTIGRAVITY_ENDPOINTS:
                try:
                    url = f"{endpoint}/v1internal:loadCodeAssist"
                    response = await client.post(url, content=body, headers=headers)

                    if response.status_code == 200:
                        data = response.json()
                        project_id = data.get("cloudaicompanionProject")
                        if project_id and isinstance(project_id, str):
                            return project_id
                except Exception as e:
                    logger.debug(f"loadCodeAssist failed on {endpoint}: {e}")
                    continue

        logger.warning(f"Could not discover project ID, using default: {DEFAULT_PROJECT_ID}")
        return DEFAULT_PROJECT_ID


async def refresh_antigravity_token(token: OAuthToken) -> OAuthToken | None:
    """Refresh an Antigravity OAuth token using the hardcoded credentials."""
    if not token.refresh_token:
        return None

    actual_refresh, project_id = unpack_refresh_token(token.refresh_token)
    if not actual_refresh:
        return None

    try:
        import httpx
    except ImportError:
        return None

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                ANTIGRAVITY_TOKEN_URI,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": actual_refresh,
                    "client_id": ANTIGRAVITY_CLIENT_ID,
                    "client_secret": ANTIGRAVITY_CLIENT_SECRET,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            )

            if response.status_code != 200:
                error_text = response.text
                logger.warning(f"Antigravity token refresh failed ({response.status_code}): {error_text}")

                # Check for revoked token
                try:
                    error_data = response.json()
                    if isinstance(error_data.get("error"), str) and error_data["error"] == "invalid_grant":
                        logger.warning("Antigravity refresh token revoked - re-authentication required")
                except Exception:
                    pass

                return None

            data = response.json()
            new_refresh = data.get("refresh_token", actual_refresh)
            expires_at = datetime.now() + timedelta(seconds=data.get("expires_in", 3600))

            return OAuthToken(
                provider="antigravity",
                access_token=data["access_token"],
                refresh_token=pack_refresh_token(new_refresh, project_id),
                token_uri=ANTIGRAVITY_TOKEN_URI,
                client_id=ANTIGRAVITY_CLIENT_ID,
                client_secret=ANTIGRAVITY_CLIENT_SECRET,
                scopes=token.scopes,
                expires_at=expires_at,
                account_email=token.account_email,
                user_id=token.user_id,
            )
    except Exception as e:
        logger.error(f"Unexpected error refreshing Antigravity token: {e}")
        return None
