"""Version extraction: select the source type and extract the versions."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .errors import FirmwareSourceError
from .fetch import fetch_source
from .sources import SOURCE_TYPES


def extract_versions(source_type: str, content: str, device: Mapping[str, Any]) -> list[str]:
    """Select the extractor based on source type and return sorted versions."""
    try:
        source = SOURCE_TYPES[source_type]
    except KeyError:
        raise FirmwareSourceError(f"Unknown source type: {source_type}") from None
    versions = source.extract(device, content)
    if not versions:
        raise FirmwareSourceError("No versions found; stored state remains unchanged")
    return sorted(versions)


def fetch_versions(device: Mapping[str, Any]) -> list[str]:
    """Fetch the source and return the versions found there."""
    content = fetch_source(device)
    return extract_versions(device["source_type"], content, device)
