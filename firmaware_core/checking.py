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


def check_device(device: Mapping[str, Any], state: Mapping[str, Any]) -> CheckResult:
    """Fetch an entry's source and compare it with the stored state."""
    previous = state.get("devices", {}).get(device["id"])
    try:
        versions = fetch_versions(device)
        _, new_versions = compare_versions(versions, previous)
    except Exception as error:  # a device error does not stop the remaining devices
        return CheckResult(previous=previous, error=str(error))
    return CheckResult(versions=versions, new_versions=new_versions, previous=previous)


def record_check(state: dict[str, Any], device_id: str, result: CheckResult) -> None:
    """Store the versions of a successful check in the state."""
    previous = result.previous
    discovered = dict(previous.get("discovered_at") or {}) if previous else {}
    now = current_timestamp()
    for version in result.versions:
        if version not in discovered:
            discovered[version] = now
    state["devices"][device_id] = {"last_checked": now, "discovered_at": discovered}
