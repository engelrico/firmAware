"""Terminal output: per-entry check lines, status table, and banner."""
from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from .checking import (
    STATE_NOT_SET,
    check_device,
    display_name,
    installed_status,
    record_check,
    source_key,
    source_name,
    stored_record,
    version_sort_key,
)
from .constants import CATEGORY_DISPLAY_NAMES, CATEGORY_ORDER


def process_device(
    device: Mapping[str, Any],
    state: dict[str, Any],
    width: int,
    *,
    changes: list[dict[str, Any]] | None = None,
    errors: list[Mapping[str, Any]] | None = None,
    outdated: list[Mapping[str, Any]] | None = None,
    cache: dict[tuple[str, str | None], str] | None = None,
    sources: dict[tuple[str, str], dict[str, Any]] | None = None,
    quiet: bool = False,
) -> bool:
    """Check a single device, print the result, and update the state on success.

    Devices of one type share their source: with ``sources`` (one dict per run) the
    source is checked and recorded once; later devices of the same type reuse that
    result and only compare their own installed version. New versions and errors are
    reported once per source.
    """
    name = display_name(device)
    if device.get("enabled") is False:
        if not quiet:
            print(f"{name:<{width}}  [disabled]")
        return True

    key = source_key(device)
    shared = sources.get(key) if sources is not None else None
    if shared is None:
        result = check_device(device, state, cache)
        change = None
        if result.ok:
            record_check(state, device, result)
            if result.new_versions and changes is not None:
                change = {
                    "id": key[1],
                    "name": source_name(device),
                    "category": device.get("category", ""),
                    "versions": list(result.new_versions),
                    "devices": [],
                }
                changes.append(change)
        elif errors is not None:
            errors.append({"name": source_name(device), "error": result.error})
        if sources is not None:
            sources[key] = {"result": result, "change": change}
    else:
        result, change = shared["result"], shared["change"]

    if not result.ok:
        print(f"{name:<{width}}  Check error: {result.error}", file=sys.stderr)
        return False

    status = installed_status(device, stored_record(state, device)["discovered_at"])
    if not quiet:
        suffix = (
            f"  outdated: installed {status.installed}, latest {status.latest}"
            if status.outdated
            else ""
        )
        print(f"{name:<{width}}  {result.summary}{suffix}")
    if change is not None and status.installed:
        change["devices"].append({"name": name, "installed": status.installed})
    if status.outdated and outdated is not None:
        outdated.append(
            {
                "id": device["id"],
                "name": name,
                "category": device.get("category", ""),
                "installed": status.installed,
                "latest": status.latest,
            }
        )
    return True


def format_timestamp(value: Any) -> str:
    """Format the stored check time compactly and readably."""
    if not value:
        return "never checked"
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return str(value)
    return moment.strftime("%Y-%m-%d %H:%M:%S")


BANNER_LINES = (
    "███████ ██ ██████  ███    ███  █████  ██     ██  █████  ██████  ███████",
    "██      ██ ██   ██ ████  ████ ██   ██ ██     ██ ██   ██ ██   ██ ██",
    "█████   ██ ██████  ██ ████ ██ ███████ ██  █  ██ ███████ ██████  █████",
    "██      ██ ██   ██ ██  ██  ██ ██   ██ ██ ███ ██ ██   ██ ██   ██ ██",
    "██      ██ ██   ██ ██      ██ ██   ██  ███ ███  ██   ██ ██   ██ ███████",
)


def print_banner() -> None:
    """Print the ASCII art "firmAware" logo."""
    print()
    for line in BANNER_LINES:
        print(line)


def print_status(devices: Sequence[Mapping[str, Any]], state: Mapping[str, Any]) -> None:
    """Print last check time and latest known version per device, grouped by category."""
    print_banner()

    def category_key(device: Mapping[str, Any]) -> int:
        category = device.get("category", "")
        try:
            return CATEGORY_ORDER.index(category)
        except ValueError:
            return len(CATEGORY_ORDER)

    grouped: dict[str, list[Mapping[str, Any]]] = {category: [] for category in CATEGORY_ORDER}
    other: list[Mapping[str, Any]] = []
    for device in devices:
        category = device.get("category", "")
        if category in grouped:
            grouped[category].append(device)
        else:
            other.append(device)

    def print_category_table(category_devices: list[Mapping[str, Any]], header: str) -> None:
        print()
        print(f"{header}:")
        rows = []
        for device in category_devices:
            entry = stored_record(state, device) or {}
            discovered = entry.get("discovered_at") or {}
            enabled = device.get("enabled", True)
            display_id = device["id"] if enabled else f"{device['id']} [off]"
            if discovered:
                latest_version = max(discovered, key=version_sort_key)
                first_seen = format_timestamp(discovered[latest_version])
            else:
                latest_version = "–"
                first_seen = "–"
            status = installed_status(device, discovered)
            installed = status.installed or "–"
            installed_state = "–" if status.state == STATE_NOT_SET else status.state
            rows.append(
                (
                    display_id,
                    display_name(device),
                    format_timestamp(entry.get("last_checked")),
                    latest_version,
                    first_seen,
                    installed,
                    installed_state,
                )
            )

        headers = ("ID", "Entry", "Last check", "Last version", "First seen", "Installed", "State")
        if rows:
            widths = [max(len(headers[i]), *(len(row[i]) for row in rows)) for i in range(len(headers))]
        else:
            widths = [len(h) for h in headers]

        def render(cells: Sequence[str]) -> str:
            return "  ".join(
                cell.ljust(widths[i]) if i < len(widths) - 1 else cell
                for i, cell in enumerate(cells)
            )

        print(render(headers))
        print("  ".join("-" * width for width in widths))
        for row in rows:
            print(render(row))

    for category in CATEGORY_ORDER:
        category_devices = grouped.get(category, [])
        if not category_devices:
            continue
        print_category_table(
            category_devices, CATEGORY_DISPLAY_NAMES.get(category, category)
        )

    if other:
        print_category_table(other, "Other")
