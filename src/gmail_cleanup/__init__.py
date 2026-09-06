"""The Gmail inbox cleanup scripts: recon, classify, act, and their helpers."""

from __future__ import annotations


def module_command(module: str) -> str:
    """The command the operator types to run one of these scripts.

    Sole owner of that spelling: the project is not installed into the venv, so
    a script runs as a module with ``src`` on the import path, and every
    message that hands the operator a next step prints the same form README.md
    documents.
    """
    return f"PYTHONPATH=src python -m {__name__}.{module}"
