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
    ignored = set(device.get("ignore_versions") or ())
    versions = {version for version in source.extract(device, content) if version not in ignored}
    if not versions:
        raise FirmwareSourceError("No versions found; the page layout may have changed (stored state remains unchanged)")
    return sorted(versions)


def fetch_versions(
    device: Mapping[str, Any], cache: dict[tuple[str, str | None], str] | None = None
) -> list[str]:
    """Fetch the source and return the versions found there.

    With a ``cache``, entries sharing a source (several devices of one type) fetch
    it only once per run. Failed fetches are not cached.
    """
    key = (device["source_url"], device.get("encoding"))
    if cache is not None and key in cache:
        content = cache[key]
    else:
        content = fetch_source(device)
        if cache is not None:
            cache[key] = content
    return extract_versions(device["source_type"], content, device)
