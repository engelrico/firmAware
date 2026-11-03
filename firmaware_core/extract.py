"""Version extraction per source type."""
from __future__ import annotations

import html
import json
import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from .constants import SOURCE_JSON, SOURCE_JSON_VALUE, SOURCE_TEXT
from .errors import FirmwareSourceError
from .fetch import fetch_source


def _string_at_json_path(content: str, value_path: Sequence[str], message: str) -> str:
    """Return the non-empty string found by following ``value_path`` in a JSON document."""
    value: Any = json.loads(content)
    for key in value_path:
        if not isinstance(value, dict) or key not in value:
            raise FirmwareSourceError(message)
        value = value[key]
    if not isinstance(value, str) or not value.strip():
        raise FirmwareSourceError(message)
    return value


def versions_from_text(device: Mapping[str, Any], content: str) -> set[str]:
    """Extract versions from a text/HTML source via a search pattern.

    With ``value_path`` the source is a JSON document and the pattern is applied
    to the string at that path (e. g. Zendesk ``article.body``).
    """
    if "value_path" in device:
        content = _string_at_json_path(
            content, device["value_path"], "Value at json path is missing or invalid"
        )
    pattern = re.compile(device["version_pattern"])
    if pattern.groups > 1:
        raise FirmwareSourceError("Search pattern may contain at most one capturing group")
    group = 1 if pattern.groups else 0
    return {match.group(group) for match in pattern.finditer(html.unescape(content))}


def versions_from_json(device: Mapping[str, Any], content: str) -> set[str]:
    """Extract versions from a list within a JSON object."""
    items = json.loads(content)[device["items_key"]]
    if not isinstance(items, list):
        raise FirmwareSourceError("Expected JSON list is missing")
    versions: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise FirmwareSourceError("Invalid JSON entry")
        if item.get("enabled") is False:
            continue
        value = item.get(device["version_key"])
        if not isinstance(value, str) or not value.strip():
            raise FirmwareSourceError("Version field is missing or invalid")
        versions.add(value.strip())
    return versions


def versions_from_json_value(device: Mapping[str, Any], content: str) -> set[str]:
    """Extract a single version via a path within a JSON object."""
    message = "Version field is missing or invalid"
    return {_string_at_json_path(content, device["value_path"], message).strip()}


EXTRACTORS: dict[str, Callable[[Mapping[str, Any], str], set[str]]] = {
    SOURCE_TEXT: versions_from_text,
    SOURCE_JSON: versions_from_json,
    SOURCE_JSON_VALUE: versions_from_json_value,
}


def extract_versions(source_type: str, content: str, device: Mapping[str, Any]) -> list[str]:
    """Select the extractor based on source type and return sorted versions."""
    try:
        extractor = EXTRACTORS[source_type]
    except KeyError:
        raise FirmwareSourceError(f"Unknown source type: {source_type}") from None
    versions = extractor(device, content)
    if not versions:
        raise FirmwareSourceError("No versions found; stored state remains unchanged")
    return sorted(versions)


def fetch_versions(device: Mapping[str, Any]) -> list[str]:
    """Fetch the source and return the versions found there."""
    content = fetch_source(device)
    return extract_versions(device["source_type"], content, device)
