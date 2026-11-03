"""Interactive entry manager (--interactive)."""
from __future__ import annotations

import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .checking import check_device, display_name, record_check, version_sort_key
from .constants import CATEGORY_DISPLAY_NAMES, PROFILE_ID_PATTERN
from .profiles import resolve_device, resolve_devices
from .report import format_timestamp
from .state import load_state, read_json, save_state


def load_devices_file(path: Path) -> dict[str, Any]:
    """Load devices.json from disk."""
    if not path.exists():
        return {"devices": []}
    return read_json(path)


def save_devices_file(path: Path, data: dict[str, Any]) -> None:
    """Write devices.json to disk with consistent formatting."""
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def interactive_category_label(device: Mapping[str, Any]) -> str:
    """Return a display label for the entry's category."""
    category = device.get("category", "")
    return CATEGORY_DISPLAY_NAMES.get(category, "Other")


def interactive_entry_line(index: int, device: Mapping[str, Any], selected: bool) -> str:
    """Render a single line in the interactive list."""
    marker = "►" if selected else " "
    enabled = device.get("enabled", True)
    status = "" if enabled else "  [off]"
    name = display_name(device)
    label = interactive_category_label(device)
    return f"  {marker} {index + 1:>2}. {name}{status}  ({label})"


def clear_screen() -> None:
    """Clear the terminal screen."""
    os.system("cls" if os.name == "nt" else "clear")


def print_interactive_list(
    entries: Sequence[Mapping[str, Any]], selected: int
) -> None:
    """Print the current entry list with a selection marker."""
    clear_screen()
    print()
    print("  ── firmAware – Manage Entries ─────────────────────────────")
    print()
    for i, entry in enumerate(entries):
        print(interactive_entry_line(i, entry, i == selected))
    print()
    print("  ↑/↓  Navigate    Enter  Open actions    q  Quit")
    print()


def print_entry_actions(
    device: Mapping[str, Any], state: Mapping[str, Any], index: int, total: int,
    selected_action: int = 0,
) -> None:
    """Print the action submenu for a selected entry with arrow-key navigation."""
    clear_screen()
    entry_state = state.get("devices", {}).get(device["id"]) or {}
    discovered = entry_state.get("discovered_at") or {}
    if discovered:
        latest = max(discovered, key=version_sort_key)
        first_seen = format_timestamp(discovered[latest])
    else:
        latest = "–"
        first_seen = "–"
    is_enabled = device.get("enabled", True)
    status_label = "Enabled" if is_enabled else "Disabled [off]"
    toggle_label = "Disable entry" if is_enabled else "Enable entry"

    actions = [
        "Check now",
        "Version history",
        "Move up",
        "Move down",
        "Edit name",
        "Duplicate entry",
        "Delete entry",
        toggle_label,
        "Back to list",
    ]

    print()
    print("  ── Entry Actions ──────────────────────────────────────────")
    print(f"  Entry:      {display_name(device)}")
    print(f"  ID:         {device['id']}")
    print(f"  Category:   {interactive_category_label(device)}")
    if "profile" in device:
        print(f"  Profile:    {device['profile']}")
    print(f"  Status:     {status_label}")
    print(f"  Last check: {format_timestamp(entry_state.get('last_checked'))}")
    print(f"  Version:    {latest}")
    print(f"  First seen: {first_seen}")
    print(f"  Known:      {len(discovered)} version(s)")
    print(f"  Position:   {index + 1} of {total}")
    print()
    for i, label in enumerate(actions):
        marker = "►" if i == selected_action else " "
        print(f"  {marker} {label}")
    print()
    print("  ↑/↓  Navigate    Enter  Select    q  Back to list")
    print()


def prompt_choice(prompt: str, valid: set[str]) -> str:
    """Read a single choice from the user, looping until valid."""
    while True:
        answer = input(prompt).strip().lower()
        if answer in valid:
            return answer
        print(f"  Please enter one of: {', '.join(sorted(valid))}")


def prompt_nonempty(prompt: str) -> str:
    """Read a non-empty string from the user."""
    while True:
        answer = input(prompt).strip()
        if answer:
            return answer
        print("  Value cannot be empty.")


def move_entry(raw: list[Any], index: int, direction: int) -> int:
    """Move an entry up (-1) or down (+1); return the new index."""
    target = index + direction
    if 0 <= target < len(raw):
        raw[index], raw[target] = raw[target], raw[index]
        return target
    return index


def edit_entry_name(raw: list[Any], index: int, resolved_name: str = "") -> None:
    """Prompt for a new display name; the current value is pre-filled for editing."""
    import readline

    entry = raw[index]
    current = entry.get("name") or resolved_name or ""
    display_label = current or "(from profile)"

    def prefill() -> None:
        readline.set_startup_hook(lambda: readline.insert_text(current))

    print(f"  Editing name: {display_label}")
    print("  (Edit the text and press Enter; leave empty to keep the profile name)")
    try:
        prefill()
        new_name = input("  Name: ").strip()
    finally:
        readline.set_startup_hook()

    if new_name and new_name != current:
        entry["name"] = new_name
    elif not new_name and "name" in entry:
        del entry["name"]


def duplicate_entry(raw: list[Any], index: int, existing_ids: set[str]) -> None:
    """Duplicate an entry; the user supplies a new unique ID."""
    original = raw[index]
    print(f"  Duplicating: {display_name(original)}")
    while True:
        new_id = prompt_nonempty("  New ID for the copy: ")
        if not PROFILE_ID_PATTERN.fullmatch(new_id):
            print("  ID must match [a-z0-9][a-z0-9._-]*")
            continue
        if new_id in existing_ids:
            print(f"  ID '{new_id}' already exists.")
            continue
        break
    import copy as _copy

    clone = _copy.deepcopy(original)
    clone["id"] = new_id
    raw.append(clone)
    existing_ids.add(new_id)
    print(f"  Duplicated as '{new_id}' (added at end of list).")


def delete_entry(raw: list[Any], index: int, existing_ids: set[str]) -> None:
    """Delete an entry after confirmation."""
    entry = raw[index]
    name = display_name(entry)
    answer = prompt_choice(f"  Delete '{name}'? (y/n): ", {"y", "n"})
    if answer == "y":
        existing_ids.discard(entry["id"])
        del raw[index]
        print(f"  Deleted '{name}'.")
    else:
        print("  Cancelled.")


def toggle_entry(raw: list[Any], index: int) -> None:
    """Toggle the enabled flag on an entry."""
    entry = raw[index]
    old = entry.get("enabled", True)
    entry["enabled"] = not old
    state_label = "enabled" if not old else "disabled"
    print(f"  Entry is now {state_label}.")


def check_entry_now(
    device: Mapping[str, Any], state: dict[str, Any], state_path: Path
) -> None:
    """Check a single entry against its source and show the result."""
    name = display_name(device)
    print()
    print(f"  Checking {name} …")
    result = check_device(device, state)
    if not result.ok:
        print(f"  Check error: {result.error}")
    else:
        print(f"  Result: {result.summary}")
        record_check(state, device["id"], result)
        try:
            save_state(state_path, state)
            print("  State saved.")
        except OSError as save_error:
            print(f"  Storage error: {save_error}")
    print()
    input("  Press Enter to continue …")


def show_version_history(
    device: Mapping[str, Any], state: Mapping[str, Any]
) -> None:
    """List all known versions for an entry from state.json with discovery dates."""
    entry_state = state.get("devices", {}).get(device["id"]) or {}
    discovered = entry_state.get("discovered_at") or {}
    name = display_name(device)

    print()
    print(f"  ── Version History: {name} ───────────────────────────")
    print(f"  Last check: {format_timestamp(entry_state.get('last_checked'))}")
    print()
    if not discovered:
        print("  No known versions yet. Run a check first.")
    else:
        sorted_versions = sorted(discovered, key=version_sort_key)
        version_width = max(len(v) for v in sorted_versions)
        newest_discovered = max(discovered, key=lambda v: discovered[v])
        for i, version in enumerate(sorted_versions):
            marker = "►" if i == len(sorted_versions) - 1 else " "
            tag = "  ◄ newest" if version == newest_discovered else ""
            discovered_at = format_timestamp(discovered[version])
            print(
                f"  {marker} {version:<{version_width}}  "
                f"discovered: {discovered_at}{tag}"
            )
        print()
        print(f"  Total: {len(discovered)} version(s)")
    print()
    input("  Press Enter to continue …")


def run_interactive(devices_path: Path, profiles_dir: Path, state_path: Path) -> int:
    """Interactive session to manage entries in devices.json."""
    print()
    print("  firmAware – Interactive Entry Manager")
    print("  Loading configuration …")

    try:
        device_state = load_state(state_path)
    except (OSError, ValueError, TypeError):
        device_state = {"devices": {}}

    try:
        config = load_devices_file(devices_path)
    except (OSError, ValueError, TypeError) as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 1

    raw_entries = config.get("devices")
    if not isinstance(raw_entries, list):
        print("Configuration error: 'devices' must be a list", file=sys.stderr)
        return 1

    # Resolve entries for display (best-effort; unresolved entries still shown).
    try:
        resolved = resolve_devices(raw_entries, profiles_dir)
    except Exception:
        resolved = [dict(entry) for entry in raw_entries if isinstance(entry, Mapping)]

    # Ensure every raw entry has a resolved counterpart for display.
    if len(resolved) != len(raw_entries):
        resolved = []
        for entry in raw_entries:
            if not isinstance(entry, Mapping):
                continue
            try:
                flat = resolve_device(entry, profiles_dir)
            except Exception:
                flat = dict(entry)
            resolved.append(flat)

    existing_ids = {e["id"] for e in raw_entries if isinstance(e, Mapping) and "id" in e}

    selected = 0
    dirty = False

    def reload_all() -> None:
        nonlocal resolved
        try:
            resolved = resolve_devices(raw_entries, profiles_dir)
        except Exception:
            resolved = []
            for entry in raw_entries:
                if not isinstance(entry, Mapping):
                    continue
                try:
                    flat = resolve_device(entry, profiles_dir)
                except Exception:
                    flat = dict(entry)
                resolved.append(flat)

    def read_key() -> str:
        """Read a single keypress (arrow keys return 'up'/'down')."""
        import termios
        import tty

        fd = sys.stdin.fileno()
        saved = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
            if ch == "\x1b":
                seq = sys.stdin.read(2)
                if seq == "[A":
                    return "up"
                if seq == "[B":
                    return "down"
                return "escape"
            return ch
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, saved)

    try:
        while True:
            if not raw_entries:
                print()
                print("  No entries configured. Edit devices.json to add entries.")
                return 0

            if selected >= len(raw_entries):
                selected = len(raw_entries) - 1
            if selected < 0:
                selected = 0

            # Build a display list from the resolved entries.
            display_list = []
            for entry in raw_entries:
                if not isinstance(entry, Mapping):
                    display_list.append(dict(entry))
                    continue
                try:
                    flat = resolve_device(entry, profiles_dir)
                except Exception:
                    flat = dict(entry)
                display_list.append(flat)

            print_interactive_list(display_list, selected)

            key = read_key()
            if key == "q":
                if dirty:
                    answer = prompt_choice(
                        "  Save changes to devices.json? (y/n): ", {"y", "n"}
                    )
                    if answer == "y":
                        try:
                            save_devices_file(
                                devices_path, {"devices": raw_entries}
                            )
                            print("  Saved.")
                        except OSError as error:
                            print(
                                f"Storage error: {error}", file=sys.stderr
                            )
                            return 1
                    else:
                        print("  Discarded.")
                else:
                    print("  No changes to save.")
                return 0
            elif key == "up":
                selected -= 1
                if selected < 0:
                    selected = len(raw_entries) - 1
            elif key == "down":
                selected += 1
                if selected >= len(raw_entries):
                    selected = 0
            elif key == "\r" or key == "\n":
                # Open actions submenu with arrow-key navigation.
                entry = raw_entries[selected]
                action_selected = 0
                max_action = 8  # last action: Back to list

                while True:
                    # Re-resolve the entry to reflect any changes.
                    try:
                        display_entry = resolve_device(
                            raw_entries[selected], profiles_dir
                        )
                        display_list[selected] = display_entry
                    except Exception:
                        display_entry = dict(raw_entries[selected])

                    print_entry_actions(
                        display_entry, device_state, selected,
                        len(raw_entries), action_selected,
                    )
                    action_key = read_key()

                    if action_key == "up":
                        action_selected -= 1
                        if action_selected < 0:
                            action_selected = max_action
                    elif action_key == "down":
                        action_selected += 1
                        if action_selected > max_action:
                            action_selected = 0
                    elif action_key == "q" or action_key == "escape":
                        break
                    elif action_key == "\r" or action_key == "\n":
                        if action_selected == 0:  # Check now
                            check_entry_now(display_entry, device_state, state_path)
                        elif action_selected == 1:  # Version history
                            show_version_history(display_entry, device_state)
                        elif action_selected == 2:  # Move up
                            new_index = move_entry(raw_entries, selected, -1)
                            if new_index != selected:
                                selected = new_index
                                dirty = True
                                print("  Moved up.")
                            else:
                                print("  Already at top.")
                        elif action_selected == 3:  # Move down
                            new_index = move_entry(raw_entries, selected, 1)
                            if new_index != selected:
                                selected = new_index
                                dirty = True
                                print("  Moved down.")
                            else:
                                print("  Already at bottom.")
                        elif action_selected == 4:  # Edit name
                            resolved_name = display_list[selected].get("name", "")
                            edit_entry_name(raw_entries, selected, resolved_name)
                            dirty = True
                            print("  Name updated.")
                        elif action_selected == 5:  # Duplicate
                            duplicate_entry(raw_entries, selected, existing_ids)
                            dirty = True
                        elif action_selected == 6:  # Delete
                            before = len(raw_entries)
                            delete_entry(raw_entries, selected, existing_ids)
                            if len(raw_entries) < before:
                                dirty = True
                                if selected >= len(raw_entries) and selected > 0:
                                    selected -= 1
                                if not raw_entries:
                                    break
                        elif action_selected == 7:  # Toggle enabled
                            toggle_entry(raw_entries, selected)
                            dirty = True
                        elif action_selected == 8:  # Back to list
                            break
                    else:
                        pass
            else:
                pass

    except (KeyboardInterrupt, EOFError):
        print()
        if dirty:
            answer = prompt_choice(
                "  Save changes to devices.json? (y/n): ", {"y", "n"}
            )
            if answer == "y":
                try:
                    save_devices_file(devices_path, {"devices": raw_entries})
                    print("  Saved.")
                except OSError as error:
                    print(f"Storage error: {error}", file=sys.stderr)
                    return 1
            else:
                print("  Discarded.")
        else:
            print("  No changes to save.")
        return 0
