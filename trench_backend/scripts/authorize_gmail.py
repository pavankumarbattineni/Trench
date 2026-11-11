"""One-time authorization for sending email through the Gmail API.

Opens Google's consent screen in your browser for the Gmail account that
should send Trench's emails, receives the authorization code on a temporary
local web server (the standard "loopback" flow for Desktop OAuth clients),
exchanges it for a long-lived refresh token, and writes the values
TRENCH_CONFIG.GMAIL_API needs to a git-ignored file. Secrets are never
printed.

    uv run python scripts/authorize_gmail.py path/to/client_secret_XXXX.json

The JSON is the "Desktop app" OAuth client downloaded from Google Cloud
Console > APIs & Services > Credentials (the project must have the Gmail API
enabled and its OAuth consent screen published "In production" -- refresh
tokens issued while it is in "Testing" expire after 7 days).
"""

import base64
import hashlib
import http.server
import json
import secrets
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path

import httpx

SCOPE = "https://www.googleapis.com/auth/gmail.send"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
OUTPUT = Path(__file__).resolve().parent.parent / ".env.gmail"


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit("usage: authorize_gmail.py path/to/client_secret.json")
    client = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    client = client.get("installed") or client.get("web") or client
    client_id, client_secret = client["client_id"], client["client_secret"]

    state = secrets.token_urlsafe(16)
    verifier = secrets.token_urlsafe(64)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        .decode()
        .rstrip("=")
    )
    received: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 -- http.server API
            query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            received.update({k: v[0] for k, v in query.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            ok = "code" in received and received.get("state") == state
            self.wfile.write(
                (
                    "<h2>Trench: Gmail authorized. You can close this tab.</h2>"
                    if ok
                    else "<h2>Authorization failed. Check the terminal.</h2>"
                ).encode()
            )

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    redirect_uri = f"http://127.0.0.1:{server.server_port}/"
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": SCOPE,
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    url = f"{AUTH_URL}?{urllib.parse.urlencode(params)}"
    print("Opening Google's consent page. If it doesn't open, visit:\n" + url)
    webbrowser.open(url)
    thread = threading.Thread(target=server.handle_request)
    thread.start()
    thread.join(timeout=600)
    server.server_close()

    if received.get("state") != state or "code" not in received:
        sys.exit(f"Authorization failed: {received.get('error', 'no code received')}")

    response = httpx.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": received["code"],
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
        },
        timeout=30,
    )
    body = response.json()
    if response.status_code >= 400 or "refresh_token" not in body:
        sys.exit(f"Token exchange failed: {body.get('error', response.status_code)}")

    email = (
        input("Gmail address you just authorized (sender address): ").strip()
        if sys.stdin.isatty()
        else ""
    )
    OUTPUT.write_text(
        "# Gmail API credentials for TRENCH_CONFIG.GMAIL_API (git-ignored).\n"
        f"GMAIL_CLIENT_ID={client_id}\n"
        f"GMAIL_CLIENT_SECRET={client_secret}\n"
        f"GMAIL_REFRESH_TOKEN={body['refresh_token']}\n"
        f"GMAIL_FROM_ADDRESS={email}\n",
        encoding="utf-8",
    )
    print(f"Saved to {OUTPUT} (git-ignored). Scope: {body.get('scope')}")


if __name__ == "__main__":
    main()
