"""E-mail notification (SMTP) for new versions and failed checks."""
from __future__ import annotations

import argparse
import smtplib
import sys
import traceback
from collections.abc import Mapping, Sequence
from dataclasses import field
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from .constants import REQUEST_TIMEOUT_SECONDS
from .state import read_json


DEFAULT_NOTIFY_CONFIG = Path.home() / ".config" / "firmaware" / "notify.json"


NOTIFY_REQUIRED_FIELDS = ("smtp_host", "smtp_user", "mail_to")


NOTIFY_SECURITY_MODES = frozenset({"starttls", "ssl", "none"})


def load_notify_config(path: Path) -> dict[str, Any]:
    """Load and validate the SMTP notification configuration."""
    data = read_json(path)
    if not isinstance(data, dict):
        raise ValueError("Notification config must be a JSON object")

    missing = [field for field in NOTIFY_REQUIRED_FIELDS if field not in data]
    if missing:
        raise ValueError(f"Notification config missing: {', '.join(missing)}")

    if not isinstance(data["smtp_host"], str) or not data["smtp_host"].strip():
        raise ValueError("'smtp_host' must be a non-empty string")
    if not isinstance(data["smtp_user"], str):
        raise ValueError("'smtp_user' must be a string")

    mail_to = data["mail_to"]
    if isinstance(mail_to, str):
        mail_to = [mail_to]
        data["mail_to"] = mail_to
    if not isinstance(mail_to, list) or not all(
        isinstance(address, str) and address.strip() for address in mail_to
    ):
        raise ValueError("'mail_to' must be an address or a list of addresses")
    if not mail_to:
        raise ValueError("'mail_to' must not be empty")

    security = data.get("smtp_security", "starttls")
    if security not in NOTIFY_SECURITY_MODES:
        raise ValueError(
            f"'smtp_security' must be one of: {', '.join(sorted(NOTIFY_SECURITY_MODES))}"
        )
    data["smtp_security"] = security

    if "smtp_port" in data and not isinstance(data["smtp_port"], int):
        raise ValueError("'smtp_port' must be an integer")

    if not data.get("mail_from"):
        data["mail_from"] = data["smtp_user"]
    return data


def send_email(config: Mapping[str, Any], subject: str, body: str) -> None:
    """Send a plain-text notification mail via SMTP."""
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = config["mail_from"]
    message["To"] = ", ".join(config["mail_to"])
    message.set_content(body)

    host = config["smtp_host"]
    port = int(config.get("smtp_port") or (465 if config["smtp_security"] == "ssl" else 587))
    security = config["smtp_security"]

    if security == "ssl":
        smtp: Any = smtplib.SMTP_SSL(host, port, timeout=REQUEST_TIMEOUT_SECONDS)
    else:
        smtp = smtplib.SMTP(host, port, timeout=REQUEST_TIMEOUT_SECONDS)

    with smtp:
        if security == "starttls":
            smtp.starttls()
        user = config.get("smtp_user")
        password = config.get("smtp_password")
        if user and password:
            smtp.login(user, password)
        smtp.send_message(message)


def build_notification(
    changes: Sequence[Mapping[str, Any]],
    errors: Sequence[Mapping[str, Any]],
    outdated: Sequence[Mapping[str, Any]] = (),
) -> tuple[str, str]:
    """Compose subject and plain-text body for discovered versions or warnings.

    ``outdated`` lists entries whose installed version is behind the latest known
    one. It is only reported together with new versions or warnings; it never
    triggers a notification by itself.
    """
    new_count = sum(len(change.get("versions") or ()) for change in changes)
    error_count = len(errors)

    parts: list[str] = []
    if new_count == 1:
        parts.append("1 new version found")
    elif new_count > 1:
        parts.append(f"{new_count} new versions found")
    if error_count == 1:
        parts.append("1 warning")
    elif error_count > 1:
        parts.append(f"{error_count} warnings")
    if outdated:
        parts.append(f"{len(outdated)} outdated")
    if not parts:
        parts.append("no changes")
    subject = "firmAware: " + ", ".join(parts)

    lines: list[str] = []
    if changes:
        lines.append("New versions")
        lines.append("-" * 12)
        current_category = None
        for change in changes:
            category = change.get("category") or "Other"
            if category != current_category:
                lines.append("")
                lines.append(category)
                current_category = category
            lines.append(f"  {change['name']}")
            for version in change.get("versions") or ():
                lines.append(f"    new: {version}")
            if change.get("installed"):
                lines.append(f"    installed: {change['installed']}")
    if outdated:
        if lines:
            lines.append("")
        lines.append("Outdated devices")
        lines.append("-" * 16)
        for item in outdated:
            lines.append(f"  {item['name']}: installed {item['installed']}, latest {item['latest']}")
    if errors:
        if lines:
            lines.append("")
        lines.append("Warnings")
        lines.append("-" * 8)
        for error in errors:
            lines.append(f"  {error['name']}: {error['error']}")
    return subject, "\n".join(lines)


def send_notification(args: argparse.Namespace, subject: str, body: str) -> None:
    """Send a notification if --notify is active; failures only go to stderr."""
    if not getattr(args, "notify", False):
        return
    path = getattr(args, "notify_config", None) or DEFAULT_NOTIFY_CONFIG
    try:
        config = load_notify_config(path)
        send_email(config, subject, body)
    except Exception as error:  # a broken notification must not break the CLI
        print(f"Notification error: {error}", file=sys.stderr)


def notify_check_failed(args: argparse.Namespace, detail: str) -> None:
    """Send a failure notification for an aborted check."""
    body = (
        "The firmAware check aborted before it could complete.\n"
        "\n"
        f"Error:\n{detail}\n"
        "\n"
        "Run the check manually for details:\n"
        "  python3 firmaware.py --check\n"
    )
    send_notification(args, "firmAware: check failed", body)


def format_failure(error: BaseException) -> str:
    """Render an exception for a failure notification, including the traceback."""
    traceback_lines = traceback.format_exc().strip().splitlines()
    tail = "\n".join(traceback_lines[-12:])
    return f"{type(error).__name__}: {error}\n\nTraceback:\n{tail}"
