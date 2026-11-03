"""Exceptions raised by the check pipeline."""
from __future__ import annotations


class FirmwareSourceError(ValueError):
    """The retrieved source does not provide evaluable version information."""
