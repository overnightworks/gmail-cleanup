"""Shared Gmail OAuth helper. OAuth client and cached tokens live in ~/.secrets/gmail-cleanup."""

from __future__ import annotations

import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SECRETS_DIR = Path.home() / ".secrets" / "gmail-cleanup"
CREDENTIALS_FILE = SECRETS_DIR / "credentials.json"


def authorize_in_browser(scopes: list[str]) -> Credentials:  # pragma: no cover
    """Run Google's OAuth consent flow: open a browser and serve the redirect.

    The one function that only wires the external boundary — a real browser, a
    local redirect server, and Google's authorization endpoint — so it is
    excluded from coverage: a stand-in here would assert nothing but itself.
    """
    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), scopes)
    return flow.run_local_server(port=0)


def get_credentials(scopes: list[str], token_file: Path) -> Credentials:
    """Return usable credentials for ``scopes``, caching them in ``token_file``.

    A cached token is reused while it is valid and refreshed when it expired;
    otherwise the browser consent flow runs. Exits naming the missing OAuth
    client file when there is nothing to authorize with.
    """
    creds: Credentials | None = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), scopes)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        token_file.write_text(creds.to_json())
        return creds
    if not CREDENTIALS_FILE.exists():
        sys.exit(
            f"Missing {CREDENTIALS_FILE}.\n"
            "Download OAuth desktop credentials from Google Cloud Console — see README.md."
        )
    creds = authorize_in_browser(scopes)
    token_file.write_text(creds.to_json())
    return creds
