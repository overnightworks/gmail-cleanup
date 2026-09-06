from __future__ import annotations

import csv
import sys
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

import recon


@pytest.mark.parametrize(
    ("header_value", "expected"),
    [
        pytest.param("<https://example.com/unsub>", "https://example.com/unsub", id="http only"),
        pytest.param("<mailto:unsub@example.com>", "mailto:unsub@example.com", id="mailto only"),
        pytest.param(
            "<mailto:unsub@example.com>, <https://example.com/unsub>",
            "https://example.com/unsub",
            id="http preferred over mailto",
        ),
        pytest.param("", "", id="no recognizable url"),
    ],
)
def test_parse_list_unsubscribe(header_value: str, expected: str) -> None:
    assert recon.parse_list_unsubscribe(header_value) == expected


def test_iter_message_ids_paginates_and_stops_at_max_messages(
    fake_gmail_service_builder: Callable[..., Any],
    build_messages_page: Callable[..., dict[str, Any]],
) -> None:
    pages = {
        None: [
            build_messages_page("m1", "m2", next_page_token="1"),
            build_messages_page("m3", "m4"),
        ]
    }
    service = fake_gmail_service_builder(pages)

    ids = list(recon.iter_message_ids(service, days=30, max_messages=3, include_spam_trash=False))

    assert ids == ["m1", "m2", "m3"]


def test_iter_message_ids_stops_when_gmail_reports_no_more_pages(
    fake_gmail_service_builder: Callable[..., Any],
    build_messages_page: Callable[..., dict[str, Any]],
) -> None:
    pages = {None: [build_messages_page("m1")]}
    service = fake_gmail_service_builder(pages)

    ids = list(recon.iter_message_ids(service, days=30, max_messages=100, include_spam_trash=True))

    assert ids == ["m1"]


def test_iter_message_ids_returns_nothing_when_max_messages_is_zero(
    fake_gmail_service_builder: Callable[..., Any],
) -> None:
    service = fake_gmail_service_builder({})

    assert list(recon.iter_message_ids(service, days=30, max_messages=0, include_spam_trash=False)) == []


def test_iter_message_ids_returns_nothing_when_gmail_has_no_matches(
    fake_gmail_service_builder: Callable[..., Any],
    build_messages_page: Callable[..., dict[str, Any]],
) -> None:
    service = fake_gmail_service_builder({None: [build_messages_page()]})

    assert list(recon.iter_message_ids(service, days=30, max_messages=10, include_spam_trash=False)) == []


def _get_response(*, headers: list[dict[str, str]], internal_date: str) -> dict[str, Any]:
    return {"payload": {"headers": headers}, "internalDate": internal_date}


def test_aggregate_merges_repeated_headers_from_the_same_sender(
    fake_gmail_service_builder: Callable[..., Any],
) -> None:
    message_responses = {
        "m1": _get_response(
            headers=[{"name": "From", "value": "promo@example.com"}, {"name": "Subject", "value": "First"}],
            internal_date="500",
        ),
        "m2": _get_response(
            headers=[
                {"name": "From", "value": "Promo Team <promo@example.com>"},
                {"name": "Subject", "value": "Second"},
                {"name": "List-Unsubscribe", "value": "<mailto:x@example.com>, <https://example.com/u>"},
                {"name": "List-Unsubscribe-Post", "value": "List-Unsubscribe=One-Click"},
            ],
            internal_date="1000",
        ),
        "m3": _get_response(
            headers=[
                {"name": "From", "value": "Other Name <promo@example.com>"},
                {"name": "Subject", "value": "Third"},
                {"name": "List-Unsubscribe", "value": "<mailto:other@example.com>"},
            ],
            internal_date="200",
        ),
        "m4": _get_response(
            headers=[{"name": "From", "value": "promo@example.com"}, {"name": "Subject", "value": "Fourth"}],
            internal_date="800",
        ),
        "m5": _get_response(headers=[{"name": "Subject", "value": "No sender"}], internal_date="900"),
    }
    service = fake_gmail_service_builder({}, message_responses=message_responses)

    senders = recon.aggregate(service, ["m1", "m2", "m3", "m4", "m5"])

    assert list(senders) == ["promo@example.com"]
    stats = senders["promo@example.com"]
    assert stats.from_name == "Promo Team"
    assert stats.sample_subject == "First"
    assert stats.count == 4
    assert stats.has_list_unsubscribe is True
    assert stats.list_unsubscribe_url == "https://example.com/u"
    assert stats.list_unsubscribe_post == "List-Unsubscribe=One-Click"
    assert stats.oldest_ts == 200
    assert stats.newest_ts == 1000


def test_aggregate_reports_progress_across_many_batches(
    fake_gmail_service_builder: Callable[..., Any], capsys: pytest.CaptureFixture[str]
) -> None:
    message_ids = [f"m{i}" for i in range(1000)]
    message_responses = {mid: _get_response(headers=[], internal_date="0") for mid in message_ids}
    service = fake_gmail_service_builder({}, message_responses=message_responses)

    senders = recon.aggregate(service, message_ids)

    assert senders == {}
    assert "1000/1000" in capsys.readouterr().out


def test_aggregate_drops_a_message_after_exhausting_retries(
    monkeypatch: pytest.MonkeyPatch,
    fake_gmail_service_builder: Callable[..., Any],
    message_failure: Callable[..., Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(recon.time, "sleep", lambda seconds: None)
    service = fake_gmail_service_builder({}, message_responses={"m1": message_failure(500)})

    senders = recon.aggregate(service, ["m1"])

    assert senders == {}
    assert "1 messages dropped after 5 retries" in capsys.readouterr().out


def test_aggregate_silently_drops_a_message_on_a_non_retryable_error(
    fake_gmail_service_builder: Callable[..., Any], message_failure: Callable[..., Any]
) -> None:
    service = fake_gmail_service_builder({}, message_responses={"m1": message_failure(404)})

    assert recon.aggregate(service, ["m1"]) == {}


def test_aggregate_refetches_a_message_after_a_whole_batch_http_error(
    monkeypatch: pytest.MonkeyPatch,
    fake_gmail_service_builder: Callable[..., Any],
    batch_failure: Callable[..., Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(recon.time, "sleep", lambda seconds: None)
    healed = _get_response(
        headers=[{"name": "From", "value": "promo@example.com"}], internal_date="1000"
    )
    service = fake_gmail_service_builder({}, message_responses={"m1": batch_failure(then=healed)})

    senders = recon.aggregate(service, ["m1"])

    assert list(senders) == ["promo@example.com"]
    out = capsys.readouterr().out
    assert "full-batch error" in out
    assert "retry pass 1: 0 still pending" in out


def test_write_csv_sorts_by_count_and_formats_known_and_unknown_dates(tmp_path: Path) -> None:
    senders = {
        "b@example.com": recon.SenderStats(from_email="b@example.com", count=1),
        "a@example.com": recon.SenderStats(
            from_email="a@example.com",
            from_name="A Name",
            count=5,
            has_list_unsubscribe=True,
            list_unsubscribe_url="https://example.com/u",
            list_unsubscribe_post="One-Click",
            sample_subject="Hi",
            oldest_ts=1000,
            newest_ts=86_401_000,
        ),
    }
    out_path = tmp_path / "out.csv"

    recon.write_csv(senders, out_path)

    rows = list(csv.DictReader(out_path.open(encoding="utf-8")))
    assert [r["from_email"] for r in rows] == ["a@example.com", "b@example.com"]
    assert rows[0]["oldest_date"] == str(datetime.fromtimestamp(1, tz=timezone.utc).date())
    assert rows[0]["newest_date"] == str(datetime.fromtimestamp(86_401, tz=timezone.utc).date())
    assert rows[1]["oldest_date"] == ""
    assert rows[1]["newest_date"] == ""


def test_run_recon_writes_a_csv_of_discovered_senders(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    fake_gmail_service_builder: Callable[..., Any],
    gmail_service_behind: Callable[..., None],
    build_messages_page: Callable[..., dict[str, Any]],
) -> None:
    service = fake_gmail_service_builder(
        {None: [build_messages_page("m1")]},
        message_responses={
            "m1": _get_response(
                headers=[{"name": "From", "value": "promo@example.com"}], internal_date="1000"
            )
        },
    )
    gmail_service_behind(recon, service)
    out_path = tmp_path / "senders.csv"

    recon.run_recon(days=30, max_messages=10, output_csv=out_path, include_spam_trash=False)

    rows = list(csv.DictReader(out_path.open(encoding="utf-8")))
    assert [r["from_email"] for r in rows] == ["promo@example.com"]
    assert "1 unique senders" in capsys.readouterr().out


def test_main_validates_and_forwards_parsed_arguments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(recon, "SCRIPT_DIR", tmp_path)
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(recon, "run_recon", lambda *args: calls.append(args))
    out_path = tmp_path / "out.csv"
    monkeypatch.setattr(
        sys,
        "argv",
        ["recon.py", "--days", "7", "--max", "50", "--out", str(out_path), "--include-spam-trash"],
    )

    recon.main()

    assert calls == [(7, 50, out_path.resolve(), True)]


def test_main_refuses_an_out_path_outside_the_script_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed = tmp_path / "scripts"
    allowed.mkdir()
    monkeypatch.setattr(recon, "SCRIPT_DIR", allowed)
    outside = tmp_path / "elsewhere" / "senders.csv"
    monkeypatch.setattr(sys, "argv", ["recon.py", "--out", str(outside)])

    with pytest.raises(SystemExit, match="refusing path outside"):
        recon.main()
