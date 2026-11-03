"""Operations on entries, profiles, and checks without any terminal output.

The web interface calls these functions; they hold no state of their own, so every
call re-reads ``devices.json`` and ``state.json`` and writes under a file lock.
"""
from __future__ import annotations

import copy
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .checking import (
    STATE_NOT_SET,
    CheckResult,
    check_device,
    display_name,
    installed_status,
    record_check,
    source_key,
    stored_record,
    version_sort_key,
)
from .constants import PROFILE_ID_PATTERN
from .locking import file_lock
from .profiles import load_profile, resolve_device
from .state import (
    backup_state_file,
    load_devices_file,
    load_state,
    migrated_copy,
    save_devices_file,
    state_changes,
    update_state,
)

EDITABLE_FIELDS = frozenset({"name", "enabled", "installed_version"})


class ServiceError(Exception):
    """Base class for errors the caller can report; ``status`` suggests an HTTP code."""

    status = 400


class NotFoundError(ServiceError):
    status = 404


class ConflictError(ServiceError):
    status = 409


def set_installed_version(entry: dict[str, Any], value: str | None) -> None:
    """Set the installed version of an entry; an empty value clears it."""
    value = (value or "").strip()
    if value:
        entry["installed_version"] = value
    else:
        entry.pop("installed_version", None)


def mark_installed_latest(
    entry: dict[str, Any], discovered: Mapping[str, Any] | None
) -> str | None:
    """Set the installed version to the highest known version; return it, or None."""
    if not discovered:
        return None
    latest = max(discovered, key=version_sort_key)
    entry["installed_version"] = latest
    return latest


def format_check_summary(result: Any, status: Any) -> str:
    """The per-entry line of a check as the command line prints it (without the name)."""
    suffix = (
        f"  outdated: installed {status.installed}, latest {status.latest}"
        if status.outdated
        else ""
    )
    return f"{result.summary}{suffix}"


@dataclass(frozen=True)
class Workspace:
    """The three locations a firmAware installation works on."""

    devices_path: Path
    state_path: Path
    profiles_dir: Path

    # --- reading ---------------------------------------------------------

    def _raw_entries(self) -> list[dict[str, Any]]:
        config = load_devices_file(self.devices_path)
        entries = config.get("devices") if isinstance(config, Mapping) else None
        if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
            raise ServiceError("devices.json is invalid: 'devices' must be a list of objects")
        return entries

    def _resolve(self, entry: Mapping[str, Any]) -> tuple[dict[str, Any], str | None]:
        """Resolve an entry for display; an invalid entry keeps its raw fields and an error."""
        try:
            return resolve_device(entry, self.profiles_dir), None
        except (ValueError, KeyError, TypeError) as error:
            return dict(entry), str(error)

    def _state(
        self, raw: Sequence[Mapping[str, Any]], persist: bool = False
    ) -> dict[str, Any]:
        """The state with per-entry records of profile-based entries folded per profile.

        Reading never writes. With ``persist`` the migrated state is saved, after a
        backup copy of the old file.
        """
        stored = load_state(self.state_path)
        state, migrated = migrated_copy(stored, raw)
        if migrated and persist:
            devices, profiles, removed = state_changes(stored, state)
            backup_state_file(self.state_path)
            update_state(self.state_path, devices, profiles=profiles, remove_devices=removed)
        return state

    def entries(self) -> list[dict[str, Any]]:
        """All entries in file order with their stored state, as shown by ``--status``."""
        raw = self._raw_entries()
        state = self._state(raw)
        per_profile: dict[str, int] = {}
        for entry in raw:
            if isinstance(entry.get("profile"), str):
                per_profile[entry["profile"]] = per_profile.get(entry["profile"], 0) + 1
        views = []
        for entry in raw:
            resolved, error = self._resolve(entry)
            view = _entry_view(entry, resolved, stored_record(state, entry) or {}, error)
            view["type_devices"] = per_profile.get(entry.get("profile"), 1)
            views.append(view)
        return views

    def entry(self, entry_id: str) -> dict[str, Any]:
        """One entry with the version history of its source (newest version first)."""
        raw = self._raw_entries()
        view = next((v for v in self.entries() if v["id"] == entry_id), None)
        if view is None:
            raise NotFoundError(f"No entry with ID '{entry_id}'")
        entry = raw[self._find(raw, entry_id)]
        discovered = (stored_record(self._state(raw), entry) or {}).get("discovered_at") or {}
        newest = max(discovered, key=lambda v: discovered[v]) if discovered else None
        view["history"] = [
            {"version": version, "discovered_at": discovered[version], "newest": version == newest}
            for version in sorted(discovered, key=version_sort_key, reverse=True)
        ]
        return view

    def profiles(self) -> list[dict[str, Any]]:
        """Valid profiles in the profiles directory, sorted by ID."""
        found = []
        if self.profiles_dir.is_dir():
            for path in sorted(self.profiles_dir.glob("*.json")):
                try:
                    profile = load_profile(path.stem, self.profiles_dir)
                except ValueError:
                    continue
                found.append(
                    {
                        "id": profile["id"],
                        "name": profile["name"],
                        "category": profile["category"],
                        "manufacturer": profile.get("manufacturer"),
                        "model": profile.get("model"),
                    }
                )
        return found

    # --- editing ---------------------------------------------------------

    def _mutate(self, change: Callable[[list[dict[str, Any]]], Any]) -> Any:
        """Read devices.json, apply ``change`` to the entry list, and save it atomically."""
        with file_lock(self.devices_path):
            entries = self._raw_entries()
            result = change(entries)
            save_devices_file(self.devices_path, {"devices": entries})
        return result

    @staticmethod
    def _find(entries: Sequence[Mapping[str, Any]], entry_id: str) -> int:
        for index, entry in enumerate(entries):
            if entry.get("id") == entry_id:
                return index
        raise NotFoundError(f"No entry with ID '{entry_id}'")

    def update_entry(self, entry_id: str, changes: Mapping[str, Any]) -> None:
        """Change ``name``, ``enabled``, or ``installed_version`` of an entry.

        An empty ``name`` removes the local name; an empty ``installed_version`` clears it.
        """
        unknown = set(changes) - EDITABLE_FIELDS
        if unknown:
            raise ServiceError(f"Fields cannot be changed: {', '.join(sorted(unknown))}")

        def change(entries: list[dict[str, Any]]) -> None:
            entry = entries[self._find(entries, entry_id)]
            candidate = copy.deepcopy(entry)
            if "name" in changes:
                name = changes["name"]
                if name is None or (isinstance(name, str) and not name.strip()):
                    candidate.pop("name", None)
                else:
                    candidate["name"] = name.strip() if isinstance(name, str) else name
            if "enabled" in changes:
                candidate["enabled"] = changes["enabled"]
            if "installed_version" in changes:
                value = changes["installed_version"]
                if value is not None and not isinstance(value, str):
                    raise ServiceError("Field 'installed_version' is invalid")
                set_installed_version(candidate, value)
            try:
                resolve_device(candidate, self.profiles_dir)
            except ValueError as error:
                raise ServiceError(str(error)) from None
            entry.clear()
            entry.update(candidate)

        self._mutate(change)

    def mark_installed_latest(self, entry_id: str) -> str:
        """Set the installed version of an entry to its highest known version."""
        raw = self._raw_entries()
        discovered = (
            stored_record(self._state(raw), raw[self._find(raw, entry_id)]) or {}
        ).get("discovered_at")

        def change(entries: list[dict[str, Any]]) -> str:
            entry = entries[self._find(entries, entry_id)]
            latest = mark_installed_latest(entry, discovered)
            if latest is None:
                raise ConflictError("No known versions yet; run a check first")
            return latest

        return self._mutate(change)

    def add_entry(self, profile_id: str, entry_id: str, name: str | None = None) -> None:
        """Add a profile-based entry at the end of the list."""
        if not isinstance(entry_id, str) or not PROFILE_ID_PATTERN.fullmatch(entry_id):
            raise ServiceError("ID must match [a-z0-9][a-z0-9._-]*")
        try:
            load_profile(profile_id, self.profiles_dir)
        except ValueError as error:
            raise ServiceError(str(error)) from None

        def change(entries: list[dict[str, Any]]) -> None:
            if any(entry.get("id") == entry_id for entry in entries):
                raise ConflictError(f"ID '{entry_id}' already exists")
            entry: dict[str, Any] = {"id": entry_id, "profile": profile_id}
            if name and name.strip():
                entry["name"] = name.strip()
            entries.append(entry)

        self._mutate(change)

    def duplicate_entry(self, entry_id: str, new_id: str) -> None:
        """Copy an entry below the original; the copy has no installed version."""
        if not isinstance(new_id, str) or not PROFILE_ID_PATTERN.fullmatch(new_id):
            raise ServiceError("ID must match [a-z0-9][a-z0-9._-]*")

        def change(entries: list[dict[str, Any]]) -> None:
            index = self._find(entries, entry_id)
            if any(entry.get("id") == new_id for entry in entries):
                raise ConflictError(f"ID '{new_id}' already exists")
            clone = copy.deepcopy(entries[index])
            clone["id"] = new_id
            clone.pop("installed_version", None)
            entries.insert(index + 1, clone)

        self._mutate(change)

    def delete_entry(self, entry_id: str) -> None:
        """Remove an entry; its record in state.json is kept, as before."""

        def change(entries: list[dict[str, Any]]) -> None:
            del entries[self._find(entries, entry_id)]

        self._mutate(change)

    def reorder(self, ids: Sequence[str]) -> None:
        """Put the entries into the given order; ``ids`` must list every entry once."""

        def change(entries: list[dict[str, Any]]) -> None:
            by_id = {entry["id"]: entry for entry in entries}
            if (
                not isinstance(ids, list)
                or len(ids) != len(set(ids))
                or set(ids) != set(by_id)
            ):
                raise ServiceError("The order must list every entry exactly once")
            entries[:] = [by_id[entry_id] for entry_id in ids]

        self._mutate(change)

    # --- checking --------------------------------------------------------

    def check(
        self,
        ids: Sequence[str] | None = None,
        progress: Callable[[int, int, dict[str, Any]], None] | None = None,
        started: Callable[[int], None] | None = None,
    ) -> list[dict[str, Any]]:
        """Check entries (all enabled ones, or the given IDs) and save what was found.

        Returns one result per selected entry: ``status`` is ``ok``, ``error``, or
        ``disabled``; ``summary`` is the text the command line prints. No mail is sent.
        ``started`` receives the number of selected entries before the first check.
        """
        raw = self._raw_entries()
        known = {entry.get("id") for entry in raw}
        if ids is not None:
            missing = [entry_id for entry_id in ids if entry_id not in known]
            if missing:
                raise NotFoundError(f"No entry with ID '{missing[0]}'")
        selected = [entry for entry in raw if ids is None or entry.get("id") in ids]
        if started is not None:
            started(len(selected))

        state = self._state(raw, persist=True)
        cache: dict[tuple[str, str | None], str] = {}
        sources: dict[tuple[str, str], CheckResult] = {}
        results: list[dict[str, Any]] = []
        for entry in selected:
            result = self._check_one(entry, state, cache, sources)
            results.append(result)
            if progress is not None:
                progress(len(results), len(selected), result)
        return results

    def _check_one(
        self,
        entry: Mapping[str, Any],
        state: dict[str, Any],
        cache: dict[tuple[str, str | None], str],
        sources: dict[tuple[str, str], CheckResult],
    ) -> dict[str, Any]:
        """Check one entry; devices of one type share a single check of their source."""
        entry_id = entry.get("id")
        resolved, error = self._resolve(entry)
        name = display_name(resolved) if "id" in resolved else str(entry_id)
        base = {"id": entry_id, "name": name, "new_versions": [], "error": None}
        if error is not None:
            return {**base, "status": "error", "summary": f"Configuration error: {error}", "error": error}
        if resolved.get("enabled") is False:
            return {**base, "status": "disabled", "summary": "[disabled]"}

        key = source_key(resolved)
        result = sources.get(key)
        if result is None:
            result = check_device(resolved, state, cache)
            if result.ok:
                record_check(state, resolved, result)
                section, record_key = key
                try:
                    if section == "profiles":
                        update_state(self.state_path, profiles={record_key: state[section][record_key]})
                    else:
                        update_state(self.state_path, {record_key: state[section][record_key]})
                except (OSError, ValueError) as storage_error:
                    result = CheckResult(error=f"Storage error: {storage_error}")
            sources[key] = result
        if not result.ok:
            message = result.error or ""
            summary = message if message.startswith("Storage error") else f"Check error: {message}"
            return {**base, "status": "error", "summary": summary, "error": message}
        status = installed_status(resolved, stored_record(state, resolved)["discovered_at"])
        return {
            **base,
            "status": "ok",
            "summary": format_check_summary(result, status),
            "new_versions": list(result.new_versions),
        }


def _entry_view(
    raw: Mapping[str, Any],
    resolved: Mapping[str, Any],
    stored: Mapping[str, Any],
    error: str | None,
) -> dict[str, Any]:
    """The fields the status table shows for one entry."""
    discovered = stored.get("discovered_at") or {}
    latest = max(discovered, key=version_sort_key) if discovered else None
    status = installed_status(resolved, discovered)
    return {
        "id": raw.get("id"),
        "name": display_name(resolved) if "id" in resolved else str(raw.get("id")),
        "custom_name": raw.get("name"),
        "category": resolved.get("category", ""),
        "kind": "profile" if "profile" in raw else "direct",
        "profile": raw.get("profile"),
        "enabled": resolved.get("enabled", True),
        "source_url": resolved.get("source_url"),
        "source_type": resolved.get("source_type"),
        "last_checked": stored.get("last_checked"),
        "latest_version": latest,
        "first_seen": discovered.get(latest) if latest else None,
        "known_versions": len(discovered),
        "installed_version": status.installed,
        "installed_state": None if status.state == STATE_NOT_SET else status.state,
        "error": error,
    }
