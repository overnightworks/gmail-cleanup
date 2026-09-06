"""The directory the cleanup scripts read and write their CSVs in.

Sole owner of the allowed base: act.py, recon.py, and unsubscribe.py all
confine their ``--csv``/``--out`` argument through ``resolve_within_directory``
against ``SCRIPT_DIR`` from here, so "where may a CLI path point" has one
answer. Kept free of the Gmail API and OAuth imports so every script can
depend on it.
"""

from __future__ import annotations

from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent

PATH_CONFINEMENT_HELP = (
    f"must be inside {SCRIPT_DIR}; a path outside it is refused, including a "
    "relative path when the script runs from another working directory"
)


def resolve_within_directory(raw_path: Path, allowed_directory: Path) -> Path:
    """Resolve ``raw_path`` and guarantee it falls inside ``allowed_directory``.

    Resolving before comparing is what makes the guarantee hold for ``..``
    segments and for a symlink that points out of the directory; a
    CLI-supplied path reaches the file system unvalidated otherwise
    (SonarCloud S8707).
    """
    resolved = raw_path.resolve()
    allowed_root = allowed_directory.resolve()
    try:
        resolved.relative_to(allowed_root)
    except ValueError:
        raise ValueError(
            f"refusing path outside the allowed directory {allowed_root}: {raw_path}"
        ) from None
    return resolved
