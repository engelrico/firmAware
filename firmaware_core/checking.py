"""Checking one entry: comparison, version ordering, and recording results."""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .extract import fetch_versions


def compare_versions(
    versions: Sequence[str], previous: Mapping[str, Any] | None
) -> tuple[list[str], list[str]]:
    """Determine known and newly added versions from the state."""
    if previous is None:
        return [], []
    discovered = previous.get("discovered_at")
    if discovered is None:
        return [], []
    if not isinstance(discovered, dict) or not all(
        isinstance(k, str) for k in discovered
    ):
        raise ValueError("Invalid stored version state")
    known = list(discovered.keys())
    new_versions = sorted(set(versions) - set(known))
    return known, new_versions


def source_key(device: Mapping[str, Any]) -> tuple[str, str]:
    """Where the known versions of an entry live in the state.

    Devices of one type share their versions: profile-based entries use the profile
    (``profiles``), direct entries are their own source (``devices``).
    """
    profile = device.get("profile")
    if isinstance(profile, str) and profile:
        return "profiles", profile
    return "devices", device["id"]


def stored_record(state: Mapping[str, Any], device: Mapping[str, Any]) -> Mapping[str, Any] | None:
    """The stored record (``last_checked``, ``discovered_at``) of an entry's source."""
    section, key = source_key(device)
    return (state.get(section) or {}).get(key)


def source_name(device: Mapping[str, Any]) -> str:
    """Name of an entry's source for messages: the device type for profile-based entries."""
    return device.get("profile_name") or display_name(device)


def display_name(device: Mapping[str, Any]) -> str:
    """Display name of a device; falls back to the ID."""
    return device.get("name", device["id"])


def name_column_width(devices: Sequence[Mapping[str, Any]]) -> int:
    """Width of the name column for tabular output."""
    return max((len(display_name(device)) for device in devices), default=0)


def version_sort_key(version: str) -> tuple:
    """Sort key for version labels: numeric block comparison with zero padding.

    Blocks are compared as integers position by position, shorter labels are
    zero-padded on the right so ``"27.1"`` sorts above ``"27.0.1"`` and
    ``"1.10"`` above ``"1.9"``.

    A single numeric block of one hundred million or more is treated as a
    legacy label (e. g. Philips Hue ``1978074000``) and ranked below
    multi-block labels (e. g. ``6.1.1978418030``).
    """
    blocks = [int(block) for block in re.findall(r"[0-9]+", version or "")]
    legacy = 0 if len(blocks) == 1 and blocks[0] >= 10**8 else 1
    padded = (blocks + [0] * 12)[:12]
    return (legacy, *padded, version or "")


def check_summary(
    previous: Mapping[str, Any] | None,
    versions: Sequence[str],
    new_versions: Sequence[str],
) -> str:
    """Compact status line of a successful device check."""
    if previous is None:
        return f"Baseline ({len(versions)}): {', '.join(versions)}"
    if new_versions:
        return f"new: {', '.join(new_versions)}"
    return "no changes"


STATE_CURRENT = "current"
STATE_OUTDATED = "outdated"
STATE_UNKNOWN = "unknown"
STATE_NOT_SET = "not set"


@dataclass(frozen=True)
class InstalledStatus:
    """How an entry's installed version relates to the latest known version."""

    state: str
    installed: str | None = None
    latest: str | None = None

    @property
    def outdated(self) -> bool:
        return self.state == STATE_OUTDATED


def installed_status(
    device: Mapping[str, Any], discovered: Mapping[str, Any] | None
) -> InstalledStatus:
    """Compare ``installed_version`` of an entry with the highest discovered version."""
    installed = device.get("installed_version")
    if not installed:
        return InstalledStatus(STATE_NOT_SET)
    if not discovered:
        return InstalledStatus(STATE_UNKNOWN, installed=installed)
    latest = max(discovered, key=version_sort_key)
    state = (
        STATE_CURRENT
        if version_sort_key(installed) >= version_sort_key(latest)
        else STATE_OUTDATED
    )
    return InstalledStatus(state, installed=installed, latest=latest)


def current_timestamp() -> str:
    """Time of the last successful check in local time zone."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class CheckResult:
    """Outcome of checking one entry; carries no output and changes no state."""

    versions: list[str] = field(default_factory=list)
    new_versions: list[str] = field(default_factory=list)
    previous: Mapping[str, Any] | None = None
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def summary(self) -> str:
        return check_summary(self.previous, self.versions, self.new_versions)


def check_device(
    device: Mapping[str, Any],
    state: Mapping[str, Any],
    cache: dict[tuple[str, str | None], str] | None = None,
) -> CheckResult:
    """Fetch an entry's source and compare it with the stored state."""
    previous = stored_record(state, device)
    try:
        versions = fetch_versions(device, cache)
        _, new_versions = compare_versions(versions, previous)
    except Exception as error:  # a device error does not stop the remaining devices
        return CheckResult(previous=previous, error=str(error))
    return CheckResult(versions=versions, new_versions=new_versions, previous=previous)


def record_check(state: dict[str, Any], device: Mapping[str, Any], result: CheckResult) -> None:
    """Store the versions of a successful check in the state (once per source)."""
    previous = result.previous
    discovered = dict(previous.get("discovered_at") or {}) if previous else {}
    now = current_timestamp()
    for version in result.versions:
        if version not in discovered:
            discovered[version] = now
    section, key = source_key(device)
    state.setdefault(section, {})[key] = {"last_checked": now, "discovered_at": discovered}
