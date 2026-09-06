"""Shared test fixtures for the Gmail cleanup scripts.

Two things live here. A fake Gmail service standing in for the googleapiclient
chains act.py and recon.py use — ``messages().list(...)`` with its
``nextPageToken`` pagination, the mutating ``messages().batchModify()`` and
``settings().filters().create()`` calls act.py fires under ``--execute``, and
the batched ``messages().get()`` chain recon.py fetches headers with — so
those tests run without a live Gmail API or OAuth flow. And the sender-CSV
builders plus the fake HTTP response every script test needs.
"""

from __future__ import annotations

import csv
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from googleapiclient.errors import HttpError

from recon import CSV_FIELDNAMES


@dataclass(frozen=True)
class MessageFailure:
    """Outcome: ``messages().get()`` for this id fails with ``status``."""

    status: int = 500


@dataclass(frozen=True)
class BatchFailure:
    """Outcome: ``batch.execute()`` itself raises while this id is in the batch.

    That is the whole call failing rather than one message in it — recon.py
    branches differently on the two. The batch fails ``times`` executes, then
    the id resolves to ``then``, so a test can prove that a retry actually
    re-fetched the message.
    """

    then: dict[str, Any]
    status: int = 500
    times: int = 1


@dataclass(frozen=True)
class ListFailure:
    """Outcome: ``messages().list()`` for this query raises ``HttpError(status)``."""

    status: int = 500


MessageOutcome = dict[str, Any] | MessageFailure | BatchFailure
"""What the fake answers one ``messages().get()`` id with."""

ListOutcome = list[dict[str, Any]] | ListFailure
"""What the fake answers one ``messages().list()`` query with: its pages, or a failure."""


class _FakeExecuteResult:
    """Wraps a canned response behind ``.execute()``, like a googleapiclient request."""

    def __init__(self, response: Any) -> None:
        self._response = response

    def execute(self) -> Any:
        return self._response


class _FakeGetRequest:
    """A ``messages().get()`` request added to a fake batch; carries its target id."""

    def __init__(self, message_id: str) -> None:
        self.message_id = message_id


def _fake_http_error(status: int) -> HttpError:
    """Build a real ``HttpError`` carrying ``status``, for the scripts' error branches."""

    class _FakeHttpErrorResponse:
        def __init__(self) -> None:
            self.status = status
            self.reason = "fake error"

    return HttpError(_FakeHttpErrorResponse(), b"{}")


class _FakeBatchHttpRequest:
    """Mimics googleapiclient's ``BatchHttpRequest`` for ``messages().get()`` calls."""

    def __init__(
        self,
        callback: Callable[[str, dict[str, Any] | None, Exception | None], None],
        message_responses: dict[str, MessageOutcome],
        batch_failures_fired: dict[str, int],
    ) -> None:
        self._callback = callback
        self._message_responses = message_responses
        self._batch_failures_fired = batch_failures_fired
        self._requests: list[_FakeGetRequest] = []

    def add(self, request: _FakeGetRequest) -> None:
        self._requests.append(request)

    def _outcome_for(self, message_id: str) -> MessageOutcome:
        outcome = self._message_responses[message_id]
        already_fired = self._batch_failures_fired[message_id]
        if isinstance(outcome, BatchFailure) and already_fired >= outcome.times:
            return outcome.then
        return outcome

    def execute(self) -> None:
        outcomes = [self._outcome_for(request.message_id) for request in self._requests]
        batch_failure = next((o for o in outcomes if isinstance(o, BatchFailure)), None)
        if batch_failure is not None:
            for request, outcome in zip(self._requests, outcomes):
                if isinstance(outcome, BatchFailure):
                    self._batch_failures_fired[request.message_id] += 1
            raise _fake_http_error(batch_failure.status)
        for position, outcome in enumerate(outcomes, start=1):
            if isinstance(outcome, MessageFailure):
                self._callback(str(position), None, _fake_http_error(outcome.status))
            else:
                self._callback(str(position), outcome, None)


class _FakeMessagesResource:
    """Mimics ``service.users().messages()``: list pagination, batchModify, and get()."""

    def __init__(self, pages_by_query: dict[str | None, ListOutcome]) -> None:
        self._pages_by_query = pages_by_query
        # Page tokens are per query (``q``), not per service: act.py restarts
        # each sender's own query at page 0 on the same service object, and
        # that must not trip the "dropped the token" guard below.
        self._queries_with_next_page_token: set[str | None] = set()
        self.batch_modify_calls: list[dict[str, Any]] = []

    def _pages_for(self, q: str | None) -> ListOutcome:
        if q in self._pages_by_query:
            return self._pages_by_query[q]
        if None in self._pages_by_query:
            return self._pages_by_query[None]
        raise AssertionError(f"no fake messages().list() page configured for q={q!r}")

    def list(
        self, *, q: str | None = None, pageToken: str | None = None, **_kwargs: Any
    ) -> _FakeExecuteResult:
        pages = self._pages_for(q)
        if isinstance(pages, ListFailure):
            raise _fake_http_error(pages.status)
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
        response = pages[page_index]
        if response.get("nextPageToken") is not None:
            self._queries_with_next_page_token.add(q)
        return _FakeExecuteResult(response)

    def get(self, **kwargs: Any) -> _FakeGetRequest:
        return _FakeGetRequest(kwargs["id"])

    def batchModify(self, **kwargs: Any) -> _FakeExecuteResult:
        self.batch_modify_calls.append(kwargs["body"])
        return _FakeExecuteResult({})


class _FakeFiltersResource:
    """Mimics ``service.users().settings().filters()``."""

    def __init__(self) -> None:
        self.create_calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> _FakeExecuteResult:
        self.create_calls.append(kwargs["body"])
        return _FakeExecuteResult({})


class _FakeSettingsResource:
    """Mimics ``service.users().settings()``."""

    def __init__(self) -> None:
        self._filters = _FakeFiltersResource()

    def filters(self) -> _FakeFiltersResource:
        return self._filters


class FakeGmailService:
    """Mimics the googleapiclient Gmail service used by act.py and recon.py."""

    def __init__(
        self,
        pages_by_query: dict[str | None, ListOutcome],
        message_responses: dict[str, MessageOutcome] | None = None,
    ) -> None:
        self._messages = _FakeMessagesResource(pages_by_query)
        self._settings = _FakeSettingsResource()
        self._message_responses = message_responses or {}
        self._batch_failures_fired: dict[str, int] = dict.fromkeys(self._message_responses, 0)

    def users(self) -> "FakeGmailService":
        return self

    def messages(self) -> _FakeMessagesResource:
        return self._messages

    def settings(self) -> _FakeSettingsResource:
        return self._settings

    def new_batch_http_request(
        self, *, callback: Callable[[str, dict[str, Any] | None, Exception | None], None]
    ) -> _FakeBatchHttpRequest:
        return _FakeBatchHttpRequest(
            callback, self._message_responses, self._batch_failures_fired
        )

    @property
    def batch_modify_calls(self) -> list[dict[str, Any]]:
        """Bodies passed to ``messages().batchModify()`` — act.py's trash side effect."""
        return self._messages.batch_modify_calls

    @property
    def filter_create_calls(self) -> list[dict[str, Any]]:
        """Bodies passed to ``settings().filters().create()`` — act.py's filter side effect."""
        return self._settings.filters().create_calls


def build_fake_gmail_service(
    pages_by_query: dict[str | None, ListOutcome],
    message_responses: dict[str, MessageOutcome] | None = None,
) -> FakeGmailService:
    """Build a fake Gmail service for ``messages().list()`` and the ``get()``/batch chain.

    ``pages_by_query`` maps a ``messages().list`` query string to its sequence
    of canned pages (a response's own ``nextPageToken``, e.g. ``"1"``, selects
    the next page by index) or to a ``ListFailure``; a ``None`` key answers
    every query with no exact entry, since recon.py's scan query embeds today's
    date and tests should not have to reproduce it, while a multi-sender act.py
    test needs distinct pages per ``from:`` query. ``message_responses`` maps a
    message id to its ``messages().get`` response body or to a failure outcome.
    """
    return FakeGmailService(pages_by_query, message_responses)


class FakeHttpResponse:
    """Stands in for what ``urllib.request.urlopen`` returns: a status, as a context manager."""

    def __init__(self, status: int) -> None:
        self.status = status

    def __enter__(self) -> "FakeHttpResponse":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False


def senders_csv_row(**values: str) -> dict[str, str]:
    """A sender CSV row: every column recon.py writes, blank unless given."""
    return {**dict.fromkeys(CSV_FIELDNAMES, ""), **values}


def write_senders_csv(path: Path, rows: list[dict[str, str]]) -> None:
    """Write ``rows`` as the sender CSV recon.py produces and the other scripts read."""
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDNAMES)
        writer.writeheader()
        for row in rows:
            writer.writerow(senders_csv_row(**row))


@pytest.fixture
def fake_gmail_service_builder() -> Callable[..., FakeGmailService]:
    """Expose ``build_fake_gmail_service`` so tests need not import the conftest module."""
    return build_fake_gmail_service


@pytest.fixture
def message_failure() -> Callable[..., MessageFailure]:
    """Outcome making one message id fail inside an otherwise healthy batch."""
    return MessageFailure


@pytest.fixture
def batch_failure() -> Callable[..., BatchFailure]:
    """Outcome making the whole batch call raise before it heals into ``then``."""
    return BatchFailure


@pytest.fixture
def list_failure() -> Callable[..., ListFailure]:
    """Outcome making ``messages().list()`` for one query raise."""
    return ListFailure


@pytest.fixture
def fake_urlopen() -> Callable[[int], Callable[..., FakeHttpResponse]]:
    """Build a stand-in for ``urllib.request.urlopen`` answering with a status."""

    def urlopen_returning(status: int) -> Callable[..., FakeHttpResponse]:
        def urlopen(request: object, timeout: int) -> FakeHttpResponse:
            return FakeHttpResponse(status)

        return urlopen

    return urlopen_returning


@pytest.fixture
def unreachable_urlopen() -> Callable[..., FakeHttpResponse]:
    """A ``urllib.request.urlopen`` stand-in that fails the test if anything calls it."""

    def urlopen(request: object, timeout: int) -> FakeHttpResponse:
        raise AssertionError("no HTTP request may leave the script here")

    return urlopen


@pytest.fixture
def build_senders_row() -> Callable[..., dict[str, str]]:
    """Expose ``senders_csv_row`` so tests need not import the conftest module."""
    return senders_csv_row


@pytest.fixture
def write_csv_of_senders() -> Callable[[Path, list[dict[str, str]]], None]:
    """Expose ``write_senders_csv`` so tests need not import the conftest module."""
    return write_senders_csv
