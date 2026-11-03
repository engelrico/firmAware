"""Command-line interface: argument parsing and the check/status run."""
from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .checking import name_column_width
from .constants import CATEGORY_DISPLAY_NAMES, CATEGORY_ORDER, DESCRIPTION
from .interactive import run_interactive
from .notify import build_notification, format_failure, notify_check_failed, send_notification
from .profiles import resolve_devices
from .report import print_status, process_device
from .state import load_state, read_json, save_state, validate_config


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments; default paths are next to the script."""
    base = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        help="Check sources and update state (default)",
    )
    mode.add_argument(
        "--status",
        action="store_true",
        help="Only show stored state compactly, no network request",
    )
    mode.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Interactively manage entries (reorder, edit, duplicate, delete, disable)",
    )
    parser.add_argument("--devices", type=Path, default=base / "devices.json")
    parser.add_argument("--state", type=Path, default=base / "state.json")
    parser.add_argument(
        "--profiles-dir",
        type=Path,
        default=base / "profiles",
        metavar="PATH",
        help="Directory with device profiles (default: ./profiles next to the script)",
    )
    parser.add_argument(
        "--notify",
        action="store_true",
        help="Send an e-mail if new versions were found or the check failed",
    )
    parser.add_argument(
        "--notify-config",
        type=Path,
        default=Path.home() / ".config" / "firmaware" / "notify.json",
        metavar="PATH",
        help="Path to the SMTP notification config (default: ~/.config/firmaware/notify.json)",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress normal output (for cron logs); errors still go to stderr",
    )
    parser.add_argument(
        "--device",
        "-d",
        type=str,
        metavar="ID",
        help="Check or show only the entry with this ID",
    )
    parser.add_argument(
        "device_id",
        type=str,
        metavar="ID",
        nargs="?",
        help="ID of the entry to check or show (shortcut for --device)",
    )
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    """Run the check or status output depending on the argument."""
    if args.interactive:
        return run_interactive(args.devices, args.profiles_dir, args.state)

    if not args.devices.exists():
        print(
            f"No configuration yet: {args.devices.name} does not exist. "
            f"Copy devices.template.json to {args.devices.name} and add your "
            "entries, or run --interactive to create one.",
            file=sys.stderr,
        )
        notify_check_failed(
            args, f"Configuration error: {args.devices.name} does not exist"
        )
        return 1

    try:
        config = read_json(args.devices)
        state_existed = args.state.exists()
        state = load_state(args.state)
        raw_devices = validate_config(config, state)
        devices = resolve_devices(raw_devices, args.profiles_dir)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        notify_check_failed(args, f"Configuration error: {error}")
        return 1

    if args.device_id and not args.device:
        args.device = args.device_id

    if args.device:
        matching = [device for device in devices if device["id"] == args.device]
        if not matching:
            print(f"Configuration error: No entry with ID '{args.device}'", file=sys.stderr)
            return 1
        devices = matching

    if args.status:
        if not args.quiet:
            print_status(devices, state)
        if not state_existed:
            print(
                "No state yet: state.json does not exist. Run --check first.",
                file=sys.stderr,
            )
        return 0

    failed = False
    width = name_column_width(devices)
    changes: list[Mapping[str, Any]] = []
    errors: list[Mapping[str, Any]] = []

    grouped: dict[str, list[Mapping[str, Any]]] = {category: [] for category in CATEGORY_ORDER}
    other: list[Mapping[str, Any]] = []
    for device in devices:
        category = device.get("category", "")
        if category in grouped:
            grouped[category].append(device)
        else:
            other.append(device)

    for category in CATEGORY_ORDER:
        category_devices = grouped.get(category, [])
        if not category_devices:
            continue
        if not args.quiet:
            print()
            print(f"{CATEGORY_DISPLAY_NAMES.get(category, category)}:")
        # No short-circuit: all entries are checked even after an error.
        for device in category_devices:
            if not process_device(
                device, state, width, changes=changes, errors=errors, quiet=args.quiet
            ):
                failed = True

    if other:
        if not args.quiet:
            print()
            print("Other:")
        # No short-circuit: all entries are checked even after an error.
        for device in other:
            if not process_device(
                device, state, width, changes=changes, errors=errors, quiet=args.quiet
            ):
                failed = True

    try:
        save_state(args.state, state)
    except OSError as error:
        print(f"Storage error: {error}", file=sys.stderr)
        notify_check_failed(args, f"Storage error: {error}")
        return 1

    if changes or errors:
        subject, body = build_notification(changes, errors)
        send_notification(args, subject, body)
    return int(failed)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the check or status output; send a failure notification if it aborts."""
    args = parse_args(argv)
    try:
        return run(args)
    except KeyboardInterrupt:
        # A manual abort is not an error and never triggers a notification.
        print("\nAborted (Ctrl+C).", file=sys.stderr)
        return 130
    except Exception as error:  # unexpected abort: notify before giving up
        if not args.interactive:
            notify_check_failed(args, format_failure(error))
        print(f"Check error: {error}", file=sys.stderr)
        return 1
