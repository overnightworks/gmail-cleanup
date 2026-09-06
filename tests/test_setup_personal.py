from __future__ import annotations

from pathlib import Path

import pytest

import classify
import setup_personal


def test_main_raises_the_loaders_missing_file_error_before_requesting_credentials(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """main() surfaces the config loader's error and never reaches the Gmail OAuth flow."""
    missing_path = tmp_path / "personal_senders.json"
    monkeypatch.setattr(classify, "PERSONAL_SENDERS_FILE", missing_path)

    def fail_if_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("get_credentials must not run before the config loads")

    monkeypatch.setattr(setup_personal, "get_credentials", fail_if_called)

    with pytest.raises(FileNotFoundError, match=str(missing_path)):
        setup_personal.main()
