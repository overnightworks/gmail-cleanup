from __future__ import annotations

from pathlib import Path

import pytest

from gmail_cleanup import paths


@pytest.fixture
def allowed_directory(tmp_path: Path) -> Path:
    """An allowed base with a sibling directory the scripts must never reach."""
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "senders.csv").write_text("", encoding="utf-8")
    return allowed


def test_resolve_within_directory_accepts_a_file_inside_the_allowed_directory(
    allowed_directory: Path,
) -> None:
    target = allowed_directory / "senders.csv"
    target.write_text("", encoding="utf-8")

    assert paths.resolve_within_directory(target, allowed_directory) == target.resolve()


def test_resolve_within_directory_accepts_a_path_that_does_not_exist_yet(
    allowed_directory: Path,
) -> None:
    """recon.py resolves --out before writing the file it names."""
    target = allowed_directory / "not-written-yet.csv"

    assert paths.resolve_within_directory(target, allowed_directory) == target.resolve()


def test_resolve_within_directory_refuses_a_path_outside_the_allowed_directory(
    tmp_path: Path, allowed_directory: Path
) -> None:
    outside = tmp_path / "elsewhere" / "senders.csv"

    with pytest.raises(ValueError, match="refusing path outside") as refusal:
        paths.resolve_within_directory(outside, allowed_directory)

    assert str(allowed_directory) in str(refusal.value)


def test_resolve_within_directory_refuses_a_path_escaping_through_parent_segments(
    allowed_directory: Path,
) -> None:
    escaping = allowed_directory / ".." / "elsewhere" / "senders.csv"

    with pytest.raises(ValueError, match="refusing path outside"):
        paths.resolve_within_directory(escaping, allowed_directory)


def test_resolve_within_directory_refuses_a_symlink_pointing_out_of_the_allowed_directory(
    tmp_path: Path, allowed_directory: Path
) -> None:
    link = allowed_directory / "senders.csv"
    link.symlink_to(tmp_path / "elsewhere" / "senders.csv")

    with pytest.raises(ValueError, match="refusing path outside"):
        paths.resolve_within_directory(link, allowed_directory)
