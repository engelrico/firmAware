"""JSON input and the persisted check state (state.json)."""
from __future__ import annotations

import copy
import json
import os
import shutil
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
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


def update_state(
    path: Path,
    updates: Mapping[str, Any] | None = None,
    *,
    profiles: Mapping[str, Any] | None = None,
    remove_devices: Iterable[str] = (),
) -> dict[str, Any]:
    """Merge records into the stored state under a lock and save it.

    ``updates`` are records of direct entries (``devices``), ``profiles`` records of
    device types, ``remove_devices`` entry records to drop. The file is read again, so
    records written by another process in the meantime are kept. Returns the saved state.
    """
    with file_lock(path):
        state = load_state(path)
        devices = state.setdefault("devices", {})
        devices.update(updates or {})
        for entry_id in remove_devices:
            devices.pop(entry_id, None)
        if profiles:
            state.setdefault("profiles", {}).update(profiles)
        save_state(path, state)
    return state


def forget_version(path: Path, section: str, key: str, version: str) -> bool:
    """Remove one version from the known versions of a source; False if it was not known.

    Meant for versions a profile detected by mistake. A source left without any known
    version is dropped, so its next check starts with a fresh baseline.
    """
    with file_lock(path):
        state = load_state(path)
        record = (state.get(section) or {}).get(key)
        discovered = (record or {}).get("discovered_at") or {}
        if version not in discovered:
            return False
        del discovered[version]
        if not discovered:
            del state[section][key]
        save_state(path, state)
    return True


def _moment(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _earlier(first: Any, second: Any) -> bool:
    """True if timestamp ``first`` is before ``second``; unreadable values compare as text."""
    a, b = _moment(first), _moment(second)
    if a is not None and b is not None:
        return a < b
    return str(first) < str(second)


def migrate_state(state: dict[str, Any], entries: Sequence[Mapping[str, Any]]) -> bool:
    """Fold per-entry records of profile-based entries into one record per profile.

    Versions belong to the device type: several devices with the same profile share the
    union of their known versions (earliest discovery time wins) and the latest check
    time. Returns True if the state changed. Records of unknown entries stay untouched.
    """
    devices = state.get("devices")
    if not isinstance(devices, dict):
        return False
    changed = False
    for entry in entries:
        profile, entry_id = entry.get("profile"), entry.get("id")
        record = devices.get(entry_id)
        if not isinstance(profile, str) or not isinstance(record, dict):
            continue
        profiles = state.setdefault("profiles", {})
        target = profiles.setdefault(
            profile, {"last_checked": record.get("last_checked"), "discovered_at": {}}
        )
        discovered = target.setdefault("discovered_at", {})
        for version, when in (record.get("discovered_at") or {}).items():
            if version not in discovered or _earlier(when, discovered[version]):
                discovered[version] = when
        last = record.get("last_checked")
        if last and (not target.get("last_checked") or _earlier(target["last_checked"], last)):
            target["last_checked"] = last
        del devices[entry_id]
        changed = True
    return changed


def state_changes(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Records changed between two states: direct entries, profiles, removed entry records."""
    old_devices, new_devices = before.get("devices", {}), after.get("devices", {})
    old_profiles, new_profiles = before.get("profiles", {}), after.get("profiles", {})
    devices = {k: v for k, v in new_devices.items() if v != old_devices.get(k)}
    profiles = {k: v for k, v in new_profiles.items() if v != old_profiles.get(k)}
    removed = [k for k in old_devices if k not in new_devices]
    return devices, profiles, removed


def backup_state_file(path: Path) -> None:
    """Keep a copy of the state file as ``<name>.bak`` before it is migrated (never overwritten)."""
    backup = path.with_name(path.name + ".bak")
    if path.exists() and not backup.exists():
        shutil.copy2(path, backup)


def migrated_copy(
    state: Mapping[str, Any], entries: Sequence[Mapping[str, Any]]
) -> tuple[dict[str, Any], bool]:
    """A migrated deep copy of the state and whether anything changed."""
    result = copy.deepcopy(dict(state))
    return result, migrate_state(result, entries)


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
