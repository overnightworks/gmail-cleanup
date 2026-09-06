"""Shared test fixtures for the Gmail cleanup scripts.

What every script test shares lives here. A fake Gmail service standing in for
the googleapiclient chains the scripts use — ``messages().list(...)`` with its
``nextPageToken`` pagination, the mutating ``messages().batchModify()`` and
``settings().filters().create()`` calls act.py, setup_personal.py and
cleanup_github_runs.py fire, the ``labels().list()``/``labels().create()``
chain setup_personal.py ensures its label with, and the batched
``messages().get()`` chain recon.py fetches headers with — so those tests run
without a live Gmail API or OAuth flow. The fixtures that put that fake, and a
local sender config, behind the boundaries the scripts reach them through. The
builders for the shapes those calls carry — a ``messages().list()`` page, the
batchModify body that trashes mail — and the sender-CSV builders plus the fake
HTTP response.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from googleapiclient.errors import HttpError

import classify
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


class _FakeLabelsResource:
    """Mimics ``service.users().labels()``: the mailbox's labels, and creating one."""

    def __init__(self, existing_labels: Sequence[dict[str, str]]) -> None:
        self._labels = list(existing_labels)
        self.create_calls: list[dict[str, Any]] = []
        self.created_ids: list[str] = []

    def list(self, **_kwargs: Any) -> _FakeExecuteResult:
        return _FakeExecuteResult({"labels": list(self._labels)})

    def create(self, **kwargs: Any) -> _FakeExecuteResult:
        body = kwargs["body"]
        self.create_calls.append(body)
        created = {"id": f"label-{len(self._labels) + 1}", "name": body["name"]}
        self._labels.append(created)
        self.created_ids.append(created["id"])
        return _FakeExecuteResult(created)


class _FakeFiltersResource:
    """Mimics ``service.users().settings().filters()``."""

    def __init__(self) -> None:
        self.create_calls: list[dict[str, Any]] = []
        self.created_ids: list[str] = []

    def create(self, **kwargs: Any) -> _FakeExecuteResult:
        self.create_calls.append(kwargs["body"])
        created = {"id": f"filter-{len(self.create_calls)}"}
        self.created_ids.append(created["id"])
        return _FakeExecuteResult(created)


class _FakeSettingsResource:
    """Mimics ``service.users().settings()``."""

    def __init__(self) -> None:
        self._filters = _FakeFiltersResource()

    def filters(self) -> _FakeFiltersResource:
        return self._filters


class FakeGmailService:
    """Mimics the googleapiclient Gmail service the scripts build."""

    def __init__(
        self,
        pages_by_query: dict[str | None, ListOutcome],
        message_responses: dict[str, MessageOutcome] | None = None,
        existing_labels: Sequence[dict[str, str]] = (),
    ) -> None:
        self._messages = _FakeMessagesResource(pages_by_query)
        self._settings = _FakeSettingsResource()
        self._labels = _FakeLabelsResource(existing_labels)
        self._message_responses = message_responses or {}
        self._batch_failures_fired: dict[str, int] = dict.fromkeys(self._message_responses, 0)

    def users(self) -> "FakeGmailService":
        return self

    def messages(self) -> _FakeMessagesResource:
        return self._messages

    def settings(self) -> _FakeSettingsResource:
        return self._settings

    def labels(self) -> _FakeLabelsResource:
        return self._labels

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
        """Bodies passed to ``settings().filters().create()`` — the filter side effect."""
        return self._settings.filters().create_calls

    @property
    def label_create_calls(self) -> list[dict[str, Any]]:
        """Bodies passed to ``labels().create()`` — setup_personal.py's label side effect."""
        return self._labels.create_calls

    @property
    def created_filter_ids(self) -> list[str]:
        """Ids ``settings().filters().create()`` minted, in the order the API handed them back."""
        return self._settings.filters().created_ids

    @property
    def created_label_ids(self) -> list[str]:
        """Ids ``labels().create()`` minted, in the order the API handed them back."""
        return self._labels.created_ids


def build_fake_gmail_service(
    pages_by_query: dict[str | None, ListOutcome],
    message_responses: dict[str, MessageOutcome] | None = None,
    existing_labels: Sequence[dict[str, str]] = (),
) -> FakeGmailService:
    """Build a fake Gmail service for ``messages().list()`` and the ``get()``/batch chain.

    ``pages_by_query`` maps a ``messages().list`` query string to its sequence
    of canned pages (a response's own ``nextPageToken``, e.g. ``"1"``, selects
    the next page by index) or to a ``ListFailure``; a ``None`` key answers
    every query with no exact entry, since recon.py's scan query embeds today's
    date and tests should not have to reproduce it, while a multi-sender act.py
    test needs distinct pages per ``from:`` query. ``message_responses`` maps a
    message id to its ``messages().get`` response body or to a failure outcome.
    ``existing_labels`` are the labels the mailbox already carries, each an
    ``{"id", "name"}`` entry as ``labels().list()`` returns them.
    """
    return FakeGmailService(pages_by_query, message_responses, existing_labels)


TRASH_LABEL_CHANGE = {"addLabelIds": ["TRASH"], "removeLabelIds": ["INBOX"]}
"""The label change that moves mail to Trash.

What act.py, setup_personal.py and cleanup_github_runs.py send to
``messages().batchModify()``, and what cleanup_github_runs.py's auto-trash
filter applies to future mail.
"""


def messages_list_page(*message_ids: str, next_page_token: str | None = None) -> dict[str, Any]:
    """One ``messages().list()`` page holding these ids, tokened when a page follows it."""
    page: dict[str, Any] = {"messages": [{"id": message_id} for message_id in message_ids]}
    if next_page_token is not None:
        page["nextPageToken"] = next_page_token
    return page


def trash_message_body(*message_ids: str) -> dict[str, Any]:
    """The ``messages().batchModify()`` body that moves these messages to Trash."""
    return {"ids": list(message_ids), **TRASH_LABEL_CHANGE}


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
def build_messages_page() -> Callable[..., dict[str, Any]]:
    """Expose ``messages_list_page`` so tests need not import the conftest module."""
    return messages_list_page


@pytest.fixture
def build_trash_body() -> Callable[..., dict[str, Any]]:
    """Expose ``trash_message_body`` so tests need not import the conftest module."""
    return trash_message_body


@pytest.fixture
def trash_label_change() -> dict[str, Any]:
    """The label change a trashing call applies, for tests asserting a body around it."""
    return dict(TRASH_LABEL_CHANGE)


@pytest.fixture
def personal_senders_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., None]:
    """Install a local sender config in place of the operator's own ~/.secrets file.

    classify.py owns where that file lives, and setup_personal.py reads it
    through classify.py, so both scripts' tests point the same name here.
    """

    def configure(
        personal_senders: Sequence[str] = (),
        trash_senders: Sequence[str] = (),
        personal_keep_patterns: Sequence[str] = (),
    ) -> None:
        config_file = tmp_path / "personal_senders.json"
        config_file.write_text(
            json.dumps(
                {
                    "personal_senders": list(personal_senders),
                    "trash_senders": list(trash_senders),
                    "personal_keep_patterns": list(personal_keep_patterns),
                }
            ),
            encoding="utf-8",
        )
        monkeypatch.setattr(classify, "PERSONAL_SENDERS_FILE", config_file)

    return configure


@pytest.fixture
def gmail_service_behind(
    monkeypatch: pytest.MonkeyPatch,
) -> Callable[[ModuleType, FakeGmailService], None]:
    """Put a fake service behind a script's OAuth and ``build()`` boundary.

    The scripts reach Gmail through ``get_credentials`` and ``build``; this is
    the one place a test replaces that pair, so main() runs end to end without
    a token file or a network call.
    """

    def install(module: ModuleType, service: FakeGmailService) -> None:
        monkeypatch.setattr(module, "get_credentials", lambda scopes, token_file: object())
        monkeypatch.setattr(module, "build", lambda *args, **kwargs: service)

    return install


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
