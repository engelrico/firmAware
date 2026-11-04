"""Entry profiles: validation, loading, and resolving entries into flat configurations."""
from __future__ import annotations

import codecs
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .constants import PROFILE_CATEGORIES, PROFILE_ENTRY_FIELDS, PROFILE_ID_PATTERN, PROFILE_SCHEMA_VERSION
from .fetch import validate_url_basic
from .sources import SOURCE_TYPES


def _validate_installed_version(value: Any, context: str) -> None:
    """Require a non-empty string for the user-maintained installed version."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}: Field 'installed_version' is invalid")


MAX_LINK_LENGTH = 2048


def _check_link(url: str, field: str, context: str) -> None:
    """A link shown in the web interface: http(s), a host, a valid port, no credentials."""
    try:
        if len(url) > MAX_LINK_LENGTH:
            raise ValueError("URL is too long")
        validate_url_basic(url)
        parsed = urlparse(url)
        if not parsed.hostname:
            raise ValueError("URL has no host")
        parsed.port  # raises ValueError for an invalid port
    except ValueError as error:
        raise ValueError(f"{context}: Field '{field}' is invalid: {error}") from None


def normalize_device_url(value: Any, context: str = "Entry") -> str:
    """The address of a device in the network as a full URL (``192.168.1.5`` becomes ``http://192.168.1.5``)."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{context}: Field 'device_url' is invalid")
    url = value.strip()
    if "://" not in url:
        url = f"http://{url}"
    _check_link(url, "device_url", context)
    return url


def validate_source_config(source: Mapping[str, Any], context: str) -> None:
    """Validate the technical source configuration of a device or profile.

    ``context`` describes the affected device/profile and is prepended to the
    message.
    """
    source_type = source.get("source_type")
    if source_type not in SOURCE_TYPES:
        raise ValueError(f"{context}: Unknown source type: {source_type!r}")

    source_url = source.get("source_url")
    if not isinstance(source_url, str) or not source_url.strip():
        raise ValueError(f"{context}: Required field 'source_url' missing or invalid")
    try:
        validate_url_basic(source_url)
    except ValueError as error:
        raise ValueError(f"{context}: {error}") from None

    if "source_page" in source:
        page = source["source_page"]
        if not isinstance(page, str) or not page.strip():
            raise ValueError(f"{context}: Field 'source_page' is invalid")
        _check_link(page, "source_page", context)

    encoding = source.get("encoding")
    if encoding is not None:
        if not isinstance(encoding, str) or not encoding.strip():
            raise ValueError(f"{context}: Field 'encoding' is invalid")
        try:
            codecs.lookup(encoding)
        except LookupError:
            raise ValueError(f"{context}: Unknown charset '{encoding}'") from None

    SOURCE_TYPES[source_type].validate(source, context)


def validate_profile(profile: Any, profile_id: str, context: str) -> None:
    """Validate structure, schema version, and required fields of a profile."""
    if not isinstance(profile, dict):
        raise ValueError(f"{context}: Profile is not a JSON object")

    schema_version = profile.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise ValueError(f"{context}: Invalid or missing schema version")
    if schema_version != PROFILE_SCHEMA_VERSION:
        raise ValueError(f"{context}: Unsupported schema version: {schema_version}")

    declared_id = profile.get("id")
    if not isinstance(declared_id, str) or not declared_id.strip():
        raise ValueError(f"{context}: Required field 'id' missing or invalid")
    if declared_id != profile_id:
        raise ValueError(
            f"{context}: Profile ID '{declared_id}' does not match the reference"
        )

    name = profile.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{context}: Required field 'name' missing or invalid")

    category = profile.get("category")
    if not isinstance(category, str) or category not in PROFILE_CATEGORIES:
        raise ValueError(
            f"{context}: Required field 'category' missing or invalid; "
            f"allowed values: {', '.join(sorted(PROFILE_CATEGORIES))}"
        )

    for entry_field in ("installed_version", "device_url"):
        if entry_field in profile:
            raise ValueError(
                f"{context}: Field '{entry_field}' belongs to an entry, not to a profile"
            )

    for key in ("manufacturer", "model"):
        value = profile.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{context}: Field '{key}' is invalid")

    validate_source_config(profile, context)


def profile_file_path(profile_id: str, profiles_dir: Path) -> Path:
    """Determine the profile file path and prevent escaping the directory."""
    base = profiles_dir.resolve()
    resolved = (base / f"{profile_id}.json").resolve()
    if not resolved.is_relative_to(base):
        raise ValueError(f"Profile '{profile_id}': File path is outside the profiles directory")
    return resolved


def load_profile(
    profile_id: str, profiles_dir: Path, context: str | None = None
) -> dict[str, Any]:
    """Load and validate a device profile; ``context`` refines error messages."""
    if not isinstance(profile_id, str) or not PROFILE_ID_PATTERN.fullmatch(profile_id):
        raise ValueError(f"Invalid profile ID: {profile_id!r}")

    context = context or f"Profile '{profile_id}'"
    path = profile_file_path(profile_id, profiles_dir)
    try:
        with path.open(encoding="utf-8") as handle:
            profile = json.load(handle)
    except OSError as error:
        raise ValueError(f"{context}: Profile file not readable: {error}") from None
    except json.JSONDecodeError as error:
        raise ValueError(f"{context}: Invalid JSON: {error}") from None

    validate_profile(profile, profile_id, context)
    return profile


def resolve_device(device: Mapping[str, Any], profiles_dir: Path) -> dict[str, Any]:
    """Produce a flat configuration from a (direct or profile-based) entry."""
    if not isinstance(device, Mapping):
        raise ValueError("Invalid entry")

    device_id = device.get("id")
    if not isinstance(device_id, str) or not device_id.strip():
        raise ValueError("Entry ID missing or invalid")

    if "profile" in device:
        extra = set(device) - PROFILE_ENTRY_FIELDS
        if extra:
            raise ValueError(
                f"Entry '{device_id}': Disallowed fields in profile-based entry: "
                f"{', '.join(sorted(extra))}"
            )
        profile_id = device.get("profile")
        if not isinstance(profile_id, str) or not profile_id.strip():
            raise ValueError(f"Entry '{device_id}': Required field 'profile' missing or invalid")
        context = f"Entry '{device_id}', profile '{profile_id}'"
        profile = load_profile(profile_id, profiles_dir, context=context)
        resolved = dict(profile)
        resolved["id"] = device_id
        resolved["profile"] = profile_id
        resolved["profile_name"] = profile["name"]
        if "name" in device:
            local_name = device.get("name")
            if not isinstance(local_name, str) or not local_name.strip():
                raise ValueError(f"Entry '{device_id}': Field 'name' is invalid")
            resolved["name"] = local_name
        if "enabled" in device:
            if not isinstance(device["enabled"], bool):
                raise ValueError(f"Entry '{device_id}': Field 'enabled' must be a boolean")
            resolved["enabled"] = device["enabled"]
        if "installed_version" in device:
            _validate_installed_version(device["installed_version"], context)
            resolved["installed_version"] = device["installed_version"].strip()
        if "device_url" in device:
            resolved["device_url"] = normalize_device_url(device["device_url"], context)
        return resolved

    context = f"Entry '{device_id}'"
    resolved = dict(device)
    if "name" in resolved:
        local_name = resolved.get("name")
        if not isinstance(local_name, str) or not local_name.strip():
            raise ValueError(f"{context}: Field 'name' is invalid")
    if "enabled" in resolved:
        if not isinstance(resolved["enabled"], bool):
            raise ValueError(f"{context}: Field 'enabled' must be a boolean")
    if "installed_version" in resolved:
        _validate_installed_version(resolved["installed_version"], context)
        resolved["installed_version"] = resolved["installed_version"].strip()
    if "device_url" in resolved:
        resolved["device_url"] = normalize_device_url(resolved["device_url"], context)
    validate_source_config(resolved, context)
    return resolved


def resolve_devices(
    devices: Sequence[Mapping[str, Any]],
    profiles_dir: Path,
) -> list[dict[str, Any]]:
    """Resolve all devices and validate their technical source configurations."""
    return [resolve_device(device, profiles_dir) for device in devices]
