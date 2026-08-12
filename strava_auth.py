"""One-time Strava OAuth2 login.

Run this once:  python strava_auth.py

It opens your browser, you click "Authorize", and it captures the redirect,
exchanges the code for a long-lived refresh token, and writes that token into
your .env file. After this you never need to log in again -- export_runs.py
uses the refresh token to mint short-lived access tokens automatically.
"""

import os
import secrets
import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlencode, urlparse

import requests
from dotenv import load_dotenv, set_key

REDIRECT_HOST = "localhost"
REDIRECT_PORT = 8721
REDIRECT_URI = f"http://{REDIRECT_HOST}:{REDIRECT_PORT}/callback"
# activity:read_all also returns activities the athlete marked private.
SCOPE = "activity:read_all"
# How long to wait for the browser redirect before giving up.
AUTH_TIMEOUT_S = 300

_auth_code = None
_auth_error = None
# Ties the redirect we accept to the request we sent, so another page in the
# browser cannot walk our local listener through someone else's grant.
_state = secrets.token_urlsafe(24)


class _CallbackHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _auth_code, _auth_error
        query = parse_qs(urlparse(self.path).query)
        # Browsers also fetch /favicon.ico against this listener. Answering
        # those without recording a result is what lets main() keep waiting for
        # the real redirect instead of exiting empty-handed.
        if not ({"code", "error"} & query.keys()):
            self.send_response(204)
            self.end_headers()
            return

        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        if query.get("state", [None])[0] != _state:
            _auth_error = "state mismatch -- ignoring a redirect we did not initiate"
            self.wfile.write(b"<h2>Authorization failed: unexpected state.</h2>")
        elif "code" in query:
            _auth_code = query["code"][0]
            self.wfile.write(b"<h2>Strava authorized. You can close this tab.</h2>")
        else:
            _auth_error = query.get("error", ["unknown"])[0]
            self.wfile.write(f"<h2>Authorization failed: {_auth_error}</h2>".encode())

    def log_message(self, *args):  # silence the default logging
        pass


def main():
    load_dotenv()
    client_id = os.environ.get("STRAVA_CLIENT_ID")
    client_secret = os.environ.get("STRAVA_CLIENT_SECRET")
    if not client_id or not client_secret:
        sys.exit(
            "Set STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET in .env first "
            "(copy .env.example to .env)."
        )

    params = {
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": SCOPE,
        "state": _state,
    }
    auth_url = "https://www.strava.com/oauth/authorize?" + urlencode(params)
    print("Opening browser for Strava authorization...")
    print("If it doesn't open, paste this URL:\n  " + auth_url)
    webbrowser.open(auth_url)

    # Keep serving until the redirect actually lands. handle_request() returns
    # after any single request, and the browser may well ask for /favicon.ico
    # first -- serving exactly one request could consume that instead.
    server = HTTPServer((REDIRECT_HOST, REDIRECT_PORT), _CallbackHandler)
    server.timeout = 5  # wake up regularly so the deadline below is enforced
    deadline = time.monotonic() + AUTH_TIMEOUT_S
    while _auth_code is None and _auth_error is None and time.monotonic() < deadline:
        server.handle_request()
    server.server_close()

    if _auth_error:
        sys.exit(f"Authorization failed: {_auth_error}")
    if not _auth_code:
        sys.exit(f"No authorization code after {AUTH_TIMEOUT_S // 60} minutes. Try again.")

    print("Exchanging code for tokens...")
    resp = requests.post(
        "https://www.strava.com/oauth/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "code": _auth_code,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    resp.raise_for_status()
    tokens = resp.json()
    refresh_token = tokens["refresh_token"]

    # Persist to .env so export_runs.py can find it.
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    set_key(env_path, "STRAVA_REFRESH_TOKEN", refresh_token)
    print(f"\nSuccess. Refresh token saved to {env_path}")
    print("You can now run:  python export_runs.py")


if __name__ == "__main__":
    main()
