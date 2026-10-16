from __future__ import annotations

import sys
import urllib.error
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from gmail_cleanup import act

MARKETING_SENDER = {
    "action": "unsubscribe",
    "from_email": "promo@example.com",
    "count": "3",
    "has_list_unsubscribe": "yes",
    "list_unsubscribe_url": "https://example.com/u",
    "list_unsubscribe_post": "List-Unsubscribe=One-Click",
}


@pytest.fixture
def row(build_senders_row: Callable[..., dict[str, str]]) -> Callable[..., dict[str, str]]:
    """Build a reviewed CSV row for a marketing sender, overriding what a test cares about."""

    def build(**overrides: str) -> dict[str, str]:
        return build_senders_row(**{**MARKETING_SENDER, **overrides})

    return build


# --- one_click_unsubscribe -------------------------------------------------


ONE_CLICK = "List-Unsubscribe=One-Click"


@pytest.mark.parametrize(
    ("url", "post_header"),
    [
        pytest.param("mailto:unsub@example.com", ONE_CLICK, id="unsubscribe url is not http"),
        pytest.param("https://example.com/u", "", id="sender sent no List-Unsubscribe-Post"),
        pytest.param("https://example.com/u", "foo=bar", id="Post header names no one-click"),
        pytest.param("https://example.com/u", "   ", id="Post header is only whitespace"),
    ],
)
def test_one_click_unsubscribe_sends_nothing_it_may_not_send(
    monkeypatch: pytest.MonkeyPatch,
    unreachable_urlopen: Callable[..., object],
    url: str,
    post_header: str,
) -> None:
    monkeypatch.setattr(act.urllib.request, "urlopen", unreachable_urlopen)

    assert act.one_click_unsubscribe(url, post_header=post_header) is False


def test_offers_one_click_accepts_an_https_url_with_the_one_click_post_header() -> None:
    assert act.offers_one_click("https://example.com/u", ONE_CLICK) is True


@pytest.mark.parametrize(
    ("status", "succeeded"),
    [
        pytest.param(200, True, id="2xx counts as unsubscribed"),
        pytest.param(500, False, id="5xx counts as failed"),
    ],
)
def test_one_click_unsubscribe_follows_the_response_status(
    monkeypatch: pytest.MonkeyPatch,
    fake_urlopen: Callable[[int], Callable[..., object]],
    status: int,
    succeeded: bool,
) -> None:
    monkeypatch.setattr(act.urllib.request, "urlopen", fake_urlopen(status))

    assert act.one_click_unsubscribe("https://example.com/u", post_header=ONE_CLICK) is succeeded


def test_one_click_unsubscribe_catches_a_network_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def raise_url_error(req: object, timeout: int) -> None:
        raise urllib.error.URLError("boom")

    monkeypatch.setattr(act.urllib.request, "urlopen", raise_url_error)

    assert act.one_click_unsubscribe("https://example.com/u", post_header=ONE_CLICK) is False
    assert "one-click POST failed" in capsys.readouterr().out


# --- process_row -------------------------------------------------------------


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        pytest.param("", "skip", id="blank action is skipped"),
        pytest.param("keep", "keep", id="keep is counted as kept"),
    ],
)
def test_process_row_leaves_the_sender_untouched(
    fake_gmail_service_builder: Callable[..., Any],
    row: Callable[..., dict[str, str]],
    action: str,
    expected: str,
) -> None:
    service = fake_gmail_service_builder({})

    assert act.process_row(service, row(action=action), execute=True) == expected
    assert service.batch_modify_calls == []


def test_process_row_skips_and_reports_an_action_it_does_not_know(
    fake_gmail_service_builder: Callable[..., Any],
    row: Callable[..., dict[str, str]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    service = fake_gmail_service_builder({})

    assert act.process_row(service, row(action="delete-forever"), execute=True) == "skip"
    assert "unknown action 'delete-forever'" in capsys.readouterr().out
    assert service.batch_modify_calls == []


def test_process_row_accepts_an_action_written_with_stray_case_and_whitespace(
    fake_gmail_service_builder: Callable[..., Any],
    row: Callable[..., dict[str, str]],
    build_messages_page: Callable[..., dict[str, Any]],
    build_trash_body: Callable[..., dict[str, Any]],
) -> None:
    service = fake_gmail_service_builder({"from:promo@example.com": [build_messages_page("m1")]})

    assert act.process_row(service, row(action="  Trash "), execute=True) == "trash"
    assert service.batch_modify_calls == [build_trash_body("m1")]


def test_process_row_trash_action_batch_modifies_under_execute(
    fake_gmail_service_builder: Callable[..., Any],
    row: Callable[..., dict[str, str]],
    build_messages_page: Callable[..., dict[str, Any]],
    build_trash_body: Callable[..., dict[str, Any]],
) -> None:
    service = fake_gmail_service_builder({"from:promo@example.com": [build_messages_page("m1")]})

    result = act.process_row(service, row(action="trash"), execute=True)

    assert result == "trash"
    assert service.batch_modify_calls == [build_trash_body("m1")]


@pytest.mark.parametrize(
    ("overrides", "execute", "expected_line"),
    [
        pytest.param({"action": "trash"}, True, "trashed 1 messages", id="trash executed"),
        pytest.param({"action": "trash"}, False, "would trash 1 messages", id="trash dry run"),
        pytest.param(
            {"action": "filter"}, True, "filter created + trashed 1 existing", id="filter executed"
        ),
        pytest.param(
            {"action": "filter"},
            False,
            "would create filter + trash 1 existing",
            id="filter dry run",
        ),
        pytest.param(
            {"action": "unsubscribe"},
            True,
            "trashed 1 existing messages",
            id="one-click unsubscribe executed",
        ),
        pytest.param(
            {"action": "unsubscribe"},
            False,
            "would unsubscribe (one-click) and trash 1 existing",
            id="one-click unsubscribe dry run",
        ),
        pytest.param(
            {"action": "unsubscribe", "list_unsubscribe_post": ""},
            False,
            "would trash 1 existing messages",
            id="unsubscribe without one-click dry run",
        ),
        pytest.param(
            {"action": "unsubscribe", "has_list_unsubscribe": "no"},
            False,
            "no List-Unsubscribe header — would trash only",
            id="unsubscribe without header dry run",
        ),
        pytest.param(
            {"action": "unsubscribe", "has_list_unsubscribe": "no"},
            True,
            "no List-Unsubscribe header — trashing only",
            id="unsubscribe without header executed",
        ),
    ],
)
def test_process_row_reports_the_past_tense_only_under_execute(
    fake_gmail_service_builder: Callable[..., Any],
    row: Callable[..., dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    fake_urlopen: Callable[[int], Callable[..., object]],
    capsys: pytest.CaptureFixture[str],
    build_messages_page: Callable[..., dict[str, Any]],
    overrides: dict[str, str],
    execute: bool,
    expected_line: str,
) -> None:
    """A dry-run count reads as a plan, so nobody takes it for work already done."""
    service = fake_gmail_service_builder({"from:promo@example.com": [build_messages_page("m1")]})
    monkeypatch.setattr(act.urllib.request, "urlopen", fake_urlopen(200))

    act.process_row(service, row(**overrides), execute=execute)

    out = capsys.readouterr().out
    assert f"    {expected_line}\n" in out
    assert execute or "trashed" not in out


def test_process_row_trash_action_paginates_through_all_pages(
    fake_gmail_service_builder: Callable[..., Any],
    row: Callable[..., dict[str, str]],
    build_messages_page: Callable[..., dict[str, Any]],
    build_trash_body: Callable[..., dict[str, Any]],
) -> None:
    pages = {
        "from:promo@example.com": [
            build_messages_page("m1", next_page_token="1"),
            build_messages_page("m2"),
        ]
    }
    service = fake_gmail_service_builder(pages)

    result = act.process_row(service, row(action="trash"), execute=True)

    assert result == "trash"
    assert service.batch_modify_calls == [build_trash_body("m1"), build_trash_body("m2")]


def test_process_row_trash_action_dry_run_leaves_messages_untouched(
    fake_gmail_service_builder: Callable[..., Any],
    row: Callable[..., dict[str, str]],
    build_messages_page: Callable[..., dict[str, Any]],
) -> None:
    service = fake_gmail_service_builder({"from:promo@example.com": [build_messages_page("m1")]})

    result = act.process_row(service, row(action="trash"), execute=False)

    assert result == "trash"
    assert service.batch_modify_calls == []


def test_process_row_filter_action_creates_a_filter_and_trashes_under_execute(
    fake_gmail_service_builder: Callable[..., Any],
    row: Callable[..., dict[str, str]],
    build_messages_page: Callable[..., dict[str, Any]],
    build_trash_body: Callable[..., dict[str, Any]],
    trash_label_change: dict[str, Any],
) -> None:
    service = fake_gmail_service_builder({"from:promo@example.com": [build_messages_page("m1")]})

    result = act.process_row(service, row(action="filter"), execute=True)

    assert result == "filter"
    assert service.filter_create_calls == [
        {"criteria": {"from": "promo@example.com"}, "action": trash_label_change}
    ]
    assert service.batch_modify_calls == [build_trash_body("m1")]


def test_process_row_filter_action_dry_run_creates_no_filter(
    fake_gmail_service_builder: Callable[..., Any],
    row: Callable[..., dict[str, str]],
    build_messages_page: Callable[..., dict[str, Any]],
) -> None:
    service = fake_gmail_service_builder({"from:promo@example.com": [build_messages_page()]})

    act.process_row(service, row(action="filter"), execute=False)

    assert service.filter_create_calls == []


def test_process_row_unsubscribe_reports_a_successful_one_click(
    fake_gmail_service_builder: Callable[..., Any],
    row: Callable[..., dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    fake_urlopen: Callable[[int], Callable[..., object]],
    capsys: pytest.CaptureFixture[str],
    build_messages_page: Callable[..., dict[str, Any]],
) -> None:
    service = fake_gmail_service_builder({"from:promo@example.com": [build_messages_page()]})
    monkeypatch.setattr(act.urllib.request, "urlopen", fake_urlopen(200))

    result = act.process_row(service, row(action="unsubscribe"), execute=True)

    assert result == "unsubscribe"
    out = capsys.readouterr().out
    assert "one-click: ok" in out
    assert "manual URL" not in out


def test_process_row_unsubscribe_reports_a_failed_one_click(
    fake_gmail_service_builder: Callable[..., Any],
    row: Callable[..., dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    fake_urlopen: Callable[[int], Callable[..., object]],
    capsys: pytest.CaptureFixture[str],
    build_messages_page: Callable[..., dict[str, Any]],
) -> None:
    service = fake_gmail_service_builder({"from:promo@example.com": [build_messages_page()]})
    monkeypatch.setattr(act.urllib.request, "urlopen", fake_urlopen(500))

    act.process_row(service, row(action="unsubscribe"), execute=True)

    assert "not sent" in capsys.readouterr().out


@pytest.mark.parametrize("post_header", ["", "   "])
def test_process_row_unsubscribe_prints_the_manual_url_when_there_is_no_one_click(
    fake_gmail_service_builder: Callable[..., Any],
    row: Callable[..., dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    unreachable_urlopen: Callable[..., object],
    capsys: pytest.CaptureFixture[str],
    build_messages_page: Callable[..., dict[str, Any]],
    post_header: str,
) -> None:
    service = fake_gmail_service_builder({"from:promo@example.com": [build_messages_page()]})
    monkeypatch.setattr(act.urllib.request, "urlopen", unreachable_urlopen)

    act.process_row(
        service, row(action="unsubscribe", list_unsubscribe_post=post_header), execute=True
    )

    out = capsys.readouterr().out
    assert "not sent" in out
    assert "manual URL (no one-click): https://example.com/u" in out


def test_process_row_unsubscribe_dry_run_does_not_fire_the_one_click(
    fake_gmail_service_builder: Callable[..., Any],
    row: Callable[..., dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
    unreachable_urlopen: Callable[..., object],
    capsys: pytest.CaptureFixture[str],
    build_messages_page: Callable[..., dict[str, Any]],
) -> None:
    service = fake_gmail_service_builder({"from:promo@example.com": [build_messages_page()]})
    monkeypatch.setattr(act.urllib.request, "urlopen", unreachable_urlopen)

    act.process_row(service, row(action="unsubscribe"), execute=False)

    assert "one-click:" not in capsys.readouterr().out


def test_process_row_unsubscribe_without_list_unsubscribe_header_only_trashes(
    fake_gmail_service_builder: Callable[..., Any],
    row: Callable[..., dict[str, str]],
    capsys: pytest.CaptureFixture[str],
    build_messages_page: Callable[..., dict[str, Any]],
) -> None:
    service = fake_gmail_service_builder({"from:promo@example.com": [build_messages_page()]})

    act.process_row(service, row(action="unsubscribe", has_list_unsubscribe="no"), execute=True)

    assert "no List-Unsubscribe header" in capsys.readouterr().out


# --- run() -------------------------------------------------------------------


def test_run_trashes_multiple_senders_by_their_own_query(
    tmp_path: Path,
    fake_gmail_service_builder: Callable[..., Any],
    row: Callable[..., dict[str, str]],
    write_csv_of_senders: Callable[[Path, list[dict[str, str]]], None],
    gmail_service_behind: Callable[..., None],
    build_messages_page: Callable[..., dict[str, Any]],
    build_trash_body: Callable[..., dict[str, Any]],
) -> None:
    pages = {
        "from:a@example.com": [build_messages_page("a1", "a2")],
        "from:b@example.com": [build_messages_page("b1")],
    }
    service = fake_gmail_service_builder(pages)
    gmail_service_behind(act, service)

    csv_path = tmp_path / "senders.csv"
    write_csv_of_senders(
        csv_path,
        [
            row(action="trash", from_email="a@example.com"),
            row(action="trash", from_email="b@example.com"),
        ],
    )

    act.run(csv_path, execute=True)

    assert service.batch_modify_calls == [build_trash_body("a1", "a2"), build_trash_body("b1")]


def test_run_counts_an_api_error_as_skipped_and_acts_on_the_next_sender(
    tmp_path: Path,
    fake_gmail_service_builder: Callable[..., Any],
    list_failure: Callable[..., Any],
    row: Callable[..., dict[str, str]],
    write_csv_of_senders: Callable[[Path, list[dict[str, str]]], None],
    capsys: pytest.CaptureFixture[str],
    gmail_service_behind: Callable[..., None],
    build_messages_page: Callable[..., dict[str, Any]],
    build_trash_body: Callable[..., dict[str, Any]],
) -> None:
    pages = {
        "from:broken@example.com": list_failure(500),
        "from:ok@example.com": [build_messages_page("ok1")],
    }
    service = fake_gmail_service_builder(pages)
    gmail_service_behind(act, service)

    csv_path = tmp_path / "senders.csv"
    write_csv_of_senders(
        csv_path,
        [
            row(action="trash", from_email="broken@example.com"),
            row(action="trash", from_email="ok@example.com"),
        ],
    )

    act.run(csv_path, execute=True)

    assert service.batch_modify_calls == [build_trash_body("ok1")]
    out = capsys.readouterr().out
    assert "API error" in out
    assert "skip: 1" in out
    assert "trash: 1" in out


def test_run_dry_run_prints_the_dry_run_notice(
    tmp_path: Path,
    fake_gmail_service_builder: Callable[..., Any],
    row: Callable[..., dict[str, str]],
    write_csv_of_senders: Callable[[Path, list[dict[str, str]]], None],
    capsys: pytest.CaptureFixture[str],
    gmail_service_behind: Callable[..., None],
    build_messages_page: Callable[..., dict[str, Any]],
) -> None:
    service = fake_gmail_service_builder({"from:a@example.com": [build_messages_page()]})
    gmail_service_behind(act, service)

    csv_path = tmp_path / "senders.csv"
    write_csv_of_senders(csv_path, [row(action="trash", from_email="a@example.com")])

    act.run(csv_path, execute=False)

    assert "DRY RUN" in capsys.readouterr().out


def test_run_prints_the_summary_in_the_declared_order_of_the_actions(
    tmp_path: Path,
    fake_gmail_service_builder: Callable[..., Any],
    row: Callable[..., dict[str, str]],
    write_csv_of_senders: Callable[[Path, list[dict[str, str]]], None],
    capsys: pytest.CaptureFixture[str],
    gmail_service_behind: Callable[..., None],
    build_messages_page: Callable[..., dict[str, Any]],
) -> None:
    service = fake_gmail_service_builder({"from:a@example.com": [build_messages_page()]})
    gmail_service_behind(act, service)
    csv_path = tmp_path / "senders.csv"
    write_csv_of_senders(
        csv_path,
        [row(action=action, from_email="a@example.com") for action in ("keep", "filter", "trash")],
    )

    act.run(csv_path, execute=False)

    summary = capsys.readouterr().out.split("Summary:")[1]
    assert summary.index("trash: 1") < summary.index("filter: 1") < summary.index("keep: 1")


# --- main() --------------------------------------------------------------


def test_main_validates_and_forwards_the_parsed_csv_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    row: Callable[..., dict[str, str]],
    write_csv_of_senders: Callable[[Path, list[dict[str, str]]], None],
) -> None:
    monkeypatch.setattr(act, "CSV_DIR", tmp_path)
    csv_path = tmp_path / "senders.csv"
    write_csv_of_senders(csv_path, [row()])
    calls: list[tuple[Any, ...]] = []
    monkeypatch.setattr(act, "run", lambda *args: calls.append(args))
    monkeypatch.setattr(sys, "argv", ["act.py", "--csv", str(csv_path), "--execute"])

    act.main()

    assert calls == [(csv_path.resolve(), True)]


def test_main_refuses_a_csv_path_outside_the_repository_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    allowed = tmp_path / "repository"
    allowed.mkdir()
    monkeypatch.setattr(act, "CSV_DIR", allowed)
    outside = tmp_path / "elsewhere" / "senders.csv"
    monkeypatch.setattr(sys, "argv", ["act.py", "--csv", str(outside)])

    with pytest.raises(SystemExit, match="refusing path outside"):
        act.main()


def test_main_exits_naming_the_command_that_writes_the_missing_csv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(act, "CSV_DIR", tmp_path)
    missing_csv = tmp_path / "missing.csv"
    monkeypatch.setattr(sys, "argv", ["act.py", "--csv", str(missing_csv)])

    with pytest.raises(SystemExit) as exit_info:
        act.main()

    assert str(exit_info.value) == (
        f"Missing {missing_csv.resolve()}. "
        "Run PYTHONPATH=src python -m gmail_cleanup.recon first."
    )
