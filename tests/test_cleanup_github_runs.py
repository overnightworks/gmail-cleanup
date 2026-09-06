from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

import cleanup_github_runs


def test_every_page_of_run_failure_mail_is_trashed_and_counted(
    capsys: pytest.CaptureFixture[str],
    fake_gmail_service_builder: Callable[..., Any],
    gmail_service_behind: Callable[..., None],
    build_messages_page: Callable[..., dict[str, Any]],
    build_trash_body: Callable[..., dict[str, Any]],
) -> None:
    service = fake_gmail_service_builder(
        {
            cleanup_github_runs.QUERY: [
                build_messages_page("m1", "m2", next_page_token="1"),
                build_messages_page("m3"),
            ]
        }
    )
    gmail_service_behind(cleanup_github_runs, service)

    cleanup_github_runs.main()

    assert service.batch_modify_calls == [build_trash_body("m1", "m2"), build_trash_body("m3")]
    assert "Trashed 3 workflow-failure emails." in capsys.readouterr().out


def test_an_inbox_without_run_failure_mail_is_left_untouched(
    capsys: pytest.CaptureFixture[str],
    fake_gmail_service_builder: Callable[..., Any],
    gmail_service_behind: Callable[..., None],
    build_messages_page: Callable[..., dict[str, Any]],
) -> None:
    service = fake_gmail_service_builder({cleanup_github_runs.QUERY: [build_messages_page()]})
    gmail_service_behind(cleanup_github_runs, service)

    cleanup_github_runs.main()

    assert service.batch_modify_calls == []
    assert "Trashed 0 workflow-failure emails." in capsys.readouterr().out


def test_the_auto_trash_filter_is_created_and_its_id_reported(
    capsys: pytest.CaptureFixture[str],
    fake_gmail_service_builder: Callable[..., Any],
    gmail_service_behind: Callable[..., None],
    build_messages_page: Callable[..., dict[str, Any]],
    trash_label_change: dict[str, Any],
) -> None:
    """Future 'Run failed' mail is auto-trashed, so the one-off never has to run again."""
    service = fake_gmail_service_builder({cleanup_github_runs.QUERY: [build_messages_page()]})
    gmail_service_behind(cleanup_github_runs, service)

    cleanup_github_runs.main()

    assert service.filter_create_calls == [
        {
            "criteria": {"from": "notifications@github.com", "subject": "Run failed"},
            "action": trash_label_change,
        }
    ]
    (created_filter_id,) = service.created_filter_ids
    assert f"Filter created: id={created_filter_id}" in capsys.readouterr().out
