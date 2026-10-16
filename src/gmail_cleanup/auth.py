"""Shared Gmail OAuth helper. OAuth client and cached tokens live in ~/.secrets/gmail-cleanup."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SECRETS_DIR = Path.home() / ".secrets" / "gmail-cleanup"
CREDENTIALS_FILE = SECRETS_DIR / "credentials.json"

# RFC 6749 section 5.2: the refresh token is expired or revoked. Google
# Testing-mode apps see it after seven days; only a new consent cures it.
GRANT_REVOKED_ERROR = "invalid_grant"


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
    otherwise, or when Google reports the grant expired or revoked, the browser
    consent flow runs. Any other refresh error propagates. Exits naming the
    missing OAuth client file when there is nothing to authorize with.
    """
    creds: Credentials | None = None
    if token_file.exists():
        creds = Credentials.from_authorized_user_file(str(token_file), scopes)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except RefreshError as error:
            if not _grant_was_revoked(error):
                raise
        else:
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


def _grant_was_revoked(error: RefreshError) -> bool:
    """Whether the token endpoint answered with the revoked-grant error code."""
    response = error.args[1] if len(error.args) > 1 else None
    if isinstance(response, Mapping):
        return response.get("error") == GRANT_REVOKED_ERROR
    return str(error.args[0] if error.args else "").startswith(GRANT_REVOKED_ERROR)
