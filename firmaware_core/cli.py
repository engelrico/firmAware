"""Command-line interface: argument parsing and the check/status run."""
from __future__ import annotations

import argparse
import copy
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from .checking import name_column_width
from .constants import (
    CATEGORY_DISPLAY_NAMES,
    CATEGORY_ORDER,
    DEFAULT_WEB_HOST,
    DEFAULT_WEB_PORT,
    DESCRIPTION,
)
from .notify import build_notification, format_failure, notify_check_failed, send_notification
from .profiles import resolve_devices
from .report import print_status, process_device
from .service import Workspace
from .state import load_state, read_json, update_state, validate_config
from .web.server import serve


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
        "--serve",
        action="store_true",
        help="Run the web interface (status, checks, entry management)",
    )
    mode.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help=argparse.SUPPRESS,  # replaced by --serve; kept to point users there
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
        "--host",
        default=None,
        metavar="ADDRESS",
        help="Address for --serve (default: 127.0.0.1; other addresses need a token)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        metavar="PORT",
        help="Port for --serve (default: 8765)",
    )
    parser.add_argument(
        "--web-config",
        type=Path,
        default=Path.home() / ".config" / "firmaware" / "web.json",
        metavar="PATH",
        help="Path to the web access token config (default: ~/.config/firmaware/web.json)",
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
    args = parser.parse_args(argv)
    if (args.host is not None or args.port is not None) and not args.serve:
        parser.error("--host and --port require --serve")
    return args


def run(args: argparse.Namespace) -> int:
    """Run the check or status output depending on the argument."""
    if args.interactive:
        print(
            "The interactive mode was replaced by the web interface. Run: "
            "python3 firmaware.py --serve",
            file=sys.stderr,
        )
        return 1

    if args.serve:
        workspace = Workspace(args.devices, args.state, args.profiles_dir)
        return serve(workspace, args.host or DEFAULT_WEB_HOST, args.port or DEFAULT_WEB_PORT, args.web_config)

    if not args.devices.exists():
        print(
            f"No configuration yet: {args.devices.name} does not exist. "
            f"Copy devices.template.json to {args.devices.name} and add your "
            "entries, or run --serve to create it in the web interface.",
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

    stored_before = copy.deepcopy(state["devices"])
    failed = False
    width = name_column_width(devices)
    changes: list[Mapping[str, Any]] = []
    errors: list[Mapping[str, Any]] = []
    outdated: list[Mapping[str, Any]] = []
    cache: dict[tuple[str, str | None], str] = {}

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
                device,
                state,
                width,
                changes=changes,
                errors=errors,
                outdated=outdated,
                cache=cache,
                quiet=args.quiet,
            ):
                failed = True

    if other:
        if not args.quiet:
            print()
            print("Other:")
        # No short-circuit: all entries are checked even after an error.
        for device in other:
            if not process_device(
                device,
                state,
                width,
                changes=changes,
                errors=errors,
                outdated=outdated,
                cache=cache,
                quiet=args.quiet,
            ):
                failed = True

    changed = {
        entry_id: record
        for entry_id, record in state["devices"].items()
        if record != stored_before.get(entry_id)
    }
    try:
        update_state(args.state, changed)
    except (OSError, ValueError) as error:
        print(f"Storage error: {error}", file=sys.stderr)
        notify_check_failed(args, f"Storage error: {error}")
        return 1

    if changes or errors:
        subject, body = build_notification(changes, errors, outdated)
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
        if not (args.interactive or args.serve):
            notify_check_failed(args, format_failure(error))
        print(f"Check error: {error}", file=sys.stderr)
        return 1
