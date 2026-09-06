"""Shared test fixtures for the Gmail cleanup scripts.

Slices 2 and 3 (issue #2) drive act.py and recon.py, which both talk to the
Gmail API through the googleapiclient chain
``service.users().messages().list(...).execute()`` (and its paginated
``nextPageToken``). This module provides a minimal fake standing in for that
chain, so those tests can exercise the scripts without a live Gmail API or a
live OAuth flow.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest


class _FakeListRequest:
    """Mimics the request object returned by ``messages().list(...)``."""

    def __init__(self, response: dict[str, Any]) -> None:
        self._response = response

    def execute(self) -> dict[str, Any]:
        return self._response


class _FakeMessagesResource:
    """Mimics ``service.users().messages()`` for a canned sequence of list pages."""

    def __init__(self, list_pages: list[dict[str, Any]]) -> None:
        self._list_pages = list(list_pages)
        # Page tokens are per query (``q``), not per service: act.py restarts
        # each sender's own query at page 0 on the same service object, and
        # that must not trip the "dropped the token" guard below.
        self._queries_with_next_page_token: set[str | None] = set()

    def list(
        self, *, q: str | None = None, pageToken: str | None = None, **_kwargs: Any
    ) -> _FakeListRequest:
        if pageToken is None:
            if q in self._queries_with_next_page_token:
                raise AssertionError(
                    f"messages().list(q={q!r}) was called for page 0 again "
                    "after a nextPageToken had already been handed out for "
                    "this query; the caller dropped the token instead of "
                    "paginating with it."
                )
            page_index = 0
        else:
            page_index = int(pageToken)
        response = self._list_pages[page_index]
        if response.get("nextPageToken") is not None:
            self._queries_with_next_page_token.add(q)
        return _FakeListRequest(response)


class FakeGmailService:
    """Mimics the googleapiclient Gmail service for ``.users().messages().list()``."""

    def __init__(self, list_pages: list[dict[str, Any]]) -> None:
        self._messages = _FakeMessagesResource(list_pages)

    def users(self) -> "FakeGmailService":
        return self

    def messages(self) -> _FakeMessagesResource:
        return self._messages


def build_fake_gmail_service(list_pages: list[dict[str, Any]]) -> FakeGmailService:
    """Build a fake Gmail service driving ``messages().list(...).execute()``.

    ``list_pages`` is the sequence of ``messages.list`` response bodies to
    return: page 0 answers a call with no ``pageToken``, and a response's own
    ``nextPageToken`` (e.g. ``"1"``) selects the next page by index — enough
    to exercise pagination in recon.py/act.py against controlled fixture data.
    """
    return FakeGmailService(list_pages)


@pytest.fixture
def fake_gmail_service_builder() -> Callable[[list[dict[str, Any]]], FakeGmailService]:
    """Expose ``build_fake_gmail_service`` so slices 2 and 3 can depend on it without importing conftest."""
    return build_fake_gmail_service
