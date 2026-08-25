"""Packaged Web assets for Constella."""

from pathlib import Path

__version__ = "0.1.3"


def frontend_dist() -> Path:
    """Return the installed production frontend directory."""
    return Path(__file__).resolve().parent / "dist"
