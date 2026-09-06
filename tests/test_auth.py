from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from google.oauth2.credentials import Credentials

import auth

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# An OAuth client and refresh token that exist only in this test run; the real
# ones live in ~/.secrets and no test may read them.
FAKE_CLIENT = {
    "client_id": "fake-client-id",
    "client_secret": "fake-client-secret",
    "refresh_token": "fake-refresh-token",
}


@dataclass(frozen=True)
class _TokenEndpointResponse:
    """What Google's OAuth token endpoint answers a refresh POST with."""

    status: int
    data: bytes


def _cached_token_file(path: Path, access_token: str, valid_for: timedelta) -> Path:
    """Write the authorized-user JSON that a previous run cached in ``path``."""
    expiry = datetime.now(timezone.utc).replace(tzinfo=None) + valid_for
    path.write_text(
        json.dumps(
            {
                **FAKE_CLIENT,
                "token": access_token,
                "scopes": SCOPES,
                "expiry": expiry.isoformat(timespec="seconds"),
            }
        ),
        encoding="utf-8",
    )
    return path


@pytest.fixture(autouse=True)
def oauth_client_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the OAuth client file at a temporary path, never the operator's own."""
    client_file = tmp_path / "credentials.json"
    monkeypatch.setattr(auth, "CREDENTIALS_FILE", client_file)
    return client_file


@pytest.fixture
def unreachable_token_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail the test if anything tries to talk to Google's token endpoint."""

    def unreachable(**_kwargs: object) -> None:
        raise AssertionError("no token refresh may leave the script here")

    monkeypatch.setattr(auth, "Request", lambda: unreachable)


@pytest.fixture
def token_endpoint_granting(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[str], None]:
    """Answer the next refresh with ``access_token``, as Google's endpoint would."""

    def grant(access_token: str) -> None:
        body = json.dumps(
            {
                "access_token": access_token,
                "expires_in": 3600,
                "scope": " ".join(SCOPES),
            }
        ).encode("utf-8")

        def post(**_kwargs: object) -> _TokenEndpointResponse:
            return _TokenEndpointResponse(status=200, data=body)

        monkeypatch.setattr(auth, "Request", lambda: post)

    return grant


def test_a_still_valid_cached_token_is_reused_without_a_refresh(
    tmp_path: Path, unreachable_token_endpoint: None
) -> None:
    token_file = _cached_token_file(
        tmp_path / "token.json", "cached-access-token", valid_for=timedelta(hours=1)
    )

    creds = auth.get_credentials(SCOPES, token_file)

    assert creds.token == "cached-access-token"


def test_an_expired_cached_token_is_refreshed_and_written_back(
    tmp_path: Path, token_endpoint_granting: Callable[[str], None]
) -> None:
    token_file = _cached_token_file(
        tmp_path / "token.json", "stale-access-token", valid_for=-timedelta(hours=1)
    )
    token_endpoint_granting("refreshed-access-token")

    creds = auth.get_credentials(SCOPES, token_file)

    assert creds.token == "refreshed-access-token"
    assert json.loads(token_file.read_text(encoding="utf-8"))["token"] == "refreshed-access-token"


def test_missing_oauth_client_file_exits_naming_the_file_and_the_readme(
    tmp_path: Path, oauth_client_file: Path
) -> None:
    """Without a cached token and without an OAuth client there is nothing to authorize with."""
    with pytest.raises(SystemExit, match=str(oauth_client_file)) as exit_message:
        auth.get_credentials(SCOPES, tmp_path / "no-token-yet.json")

    assert "README.md" in str(exit_message.value)


def test_credentials_from_the_browser_flow_are_cached_for_the_next_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, oauth_client_file: Path
) -> None:
    oauth_client_file.write_text(json.dumps({"installed": FAKE_CLIENT}), encoding="utf-8")
    token_file = tmp_path / "no-token-yet.json"
    authorized = Credentials.from_authorized_user_info(
        {**FAKE_CLIENT, "token": "freshly-authorized-token"}, SCOPES
    )
    monkeypatch.setattr(auth, "authorize_in_browser", lambda scopes: authorized)

    creds = auth.get_credentials(SCOPES, token_file)

    assert creds is authorized
    assert json.loads(token_file.read_text(encoding="utf-8"))["token"] == "freshly-authorized-token"
