"""Source type registry: per type, the field validation and the version extraction."""
from __future__ import annotations

import html
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from .errors import FirmwareSourceError


@dataclass(frozen=True)
class SourceType:
    """A supported ``source_type``: how to validate its fields and extract versions."""

    name: str
    validate: Callable[[Mapping[str, Any], str], None]
    extract: Callable[[Mapping[str, Any], str], set[str]]


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


def _validate_value_path(value_path: Any, context: str) -> None:
    """Require a non-empty list of non-empty strings."""
    if (
        not isinstance(value_path, list)
        or not value_path
        or not all(isinstance(part, str) and part.strip() for part in value_path)
    ):
        raise ValueError(f"{context}: Required field 'value_path' missing or invalid")


# --- text ------------------------------------------------------------------


def validate_text_source(source: Mapping[str, Any], context: str) -> None:
    """Validate the fields of a ``text`` source."""
    pattern = source.get("version_pattern")
    if not isinstance(pattern, str) or not pattern:
        raise ValueError(f"{context}: Required field 'version_pattern' missing")
    try:
        compiled = re.compile(pattern)
    except re.error as error:
        raise ValueError(f"{context}: Invalid regular expression: {error}") from None
    if compiled.groups > 1:
        raise ValueError(f"{context}: 'version_pattern' may contain at most one capturing group")
    if "value_path" in source:
        _validate_value_path(source.get("value_path"), context)


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


# --- json ------------------------------------------------------------------


def validate_json_source(source: Mapping[str, Any], context: str) -> None:
    """Validate the fields of a ``json`` source."""
    for key in ("items_key", "version_key"):
        value = source.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{context}: Required field '{key}' missing or invalid")


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


# --- json_value ------------------------------------------------------------


def validate_json_value_source(source: Mapping[str, Any], context: str) -> None:
    """Validate the fields of a ``json_value`` source."""
    _validate_value_path(source.get("value_path"), context)


def versions_from_json_value(device: Mapping[str, Any], content: str) -> set[str]:
    """Extract a single version via a path within a JSON object."""
    message = "Version field is missing or invalid"
    return {_string_at_json_path(content, device["value_path"], message).strip()}


# --- Registry --------------------------------------------------------------

SOURCE_TYPES: dict[str, SourceType] = {
    source_type.name: source_type
    for source_type in (
        SourceType("text", validate_text_source, versions_from_text),
        SourceType("json", validate_json_source, versions_from_json),
        SourceType("json_value", validate_json_value_source, versions_from_json_value),
    )
}

SUPPORTED_SOURCE_TYPES = frozenset(SOURCE_TYPES)
