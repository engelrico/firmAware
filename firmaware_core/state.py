"""JSON input and the persisted check state (state.json)."""
from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .checking import current_timestamp
from .locking import file_lock


def read_json(path: Path) -> Any:
    """Read a JSON file as UTF-8 and return the unchanged content."""
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def save_state(path: Path, state: Mapping[str, Any]) -> None:
    """Write state atomically via a neighbouring ``.tmp`` file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


def load_devices_file(path: Path) -> dict[str, Any]:
    """Load devices.json; a missing file is an empty configuration."""
    if not path.exists():
        return {"devices": []}
    return read_json(path)


def save_devices_file(path: Path, data: Mapping[str, Any]) -> None:
    """Write devices.json atomically via a neighbouring ``.tmp`` file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")
    os.replace(temporary, path)


def update_state(path: Path, updates: Mapping[str, Any]) -> dict[str, Any]:
    """Merge per-entry records into the stored state under a lock and save it.

    Reads the current file again, so records of other entries written by another
    process in the meantime are kept. Returns the saved state.
    """
    with file_lock(path):
        state = load_state(path)
        state.setdefault("devices", {}).update(updates)
        save_state(path, state)
    return state


def load_state(path: Path) -> dict[str, Any]:
    """Load the state or start with an empty state; migrates legacy format."""
    if not path.exists():
        return {"devices": {}}
    state = read_json(path)
    migrated = False
    devices_state = state.get("devices")
    if isinstance(devices_state, dict):
        for entry in devices_state.values():
            if not isinstance(entry, dict):
                continue
            if "known_versions" in entry and "discovered_at" not in entry:
                last_checked = entry.get("last_checked") or current_timestamp()
                entry["discovered_at"] = {
                    version: last_checked for version in entry["known_versions"]
                }
                del entry["known_versions"]
                migrated = True
            elif "known_versions" in entry:
                del entry["known_versions"]
                migrated = True
    if migrated:
        save_state(path, state)
    return state


def validate_config(config: Mapping[str, Any], state: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Validate the basic structure and return the device list."""
    devices = config["devices"]
    if not isinstance(devices, list) or not isinstance(state.get("devices"), dict):
        raise ValueError("Invalid configuration or state structure")
    ids = []
    for device in devices:
        if not isinstance(device, Mapping):
            raise ValueError("Invalid entry")
        device_id = device.get("id")
        if not isinstance(device_id, str) or not device_id.strip():
            raise ValueError("Entry ID missing or invalid")
        ids.append(device_id)
    if len(ids) != len(set(ids)):
        raise ValueError("Entry IDs must be unique")
    return devices
