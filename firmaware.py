#!/usr/bin/env python3
"""firmAware – Check firmware sources for new version labels.

Checks the sources configured in ``devices.json`` and reports previously
unknown version labels. Known versions are stored in ``state.json``.
Devices can be configured directly or via reusable profiles in
``profiles/*.json``. Only the Python standard library is required; see
``specification.md`` for details.
"""
from __future__ import annotations

import argparse
import codecs
import html
import json
import multiprocessing
import os
import re
import sys
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

# --- Constants -------------------------------------------------------------

DESCRIPTION = "firmAware – Check firmware sources for new version labels."

USER_AGENT = "firmAware/0.1"
ACCEPT_HEADER = "application/json,text/html,*/*"
REQUEST_TIMEOUT_SECONDS = 30
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
PATTERN_TEST_TIMEOUT_SECONDS = 3.0
CONTEXT_CHARS = 150
MAX_EXAMPLE_LENGTH = 100

SOURCE_TEXT = "text"
SOURCE_JSON = "json"
SOURCE_JSON_VALUE = "json_value"

SUPPORTED_SOURCE_TYPES = frozenset({SOURCE_TEXT, SOURCE_JSON, SOURCE_JSON_VALUE})

PROFILE_SCHEMA_VERSION = 2
PROFILE_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]*")
PROFILE_ENTRY_FIELDS = frozenset({"id", "profile", "name"})

PROFILE_CATEGORIES = frozenset({"firmware", "software", "operating_system"})
CATEGORY_DISPLAY_NAMES = {
    "firmware": "Firmware",
    "software": "Software",
    "operating_system": "Operating System",
}
CATEGORY_ORDER = ("firmware", "software", "operating_system")

BINARY_MEDIA_TYPES = frozenset({"image", "audio", "video", "font"})
BINARY_SUBTYPES = frozenset({"octet-stream", "pdf", "zip", "gzip"})


class FirmwareSourceError(ValueError):
    """The retrieved source does not provide evaluable version information."""


# --- Input / Output --------------------------------------------------------


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


# --- Fetch -----------------------------------------------------------------


@dataclass(frozen=True)
class FetchedDocument:
    """Result of a source fetch along with metadata for preview and export."""

    text: str
    status: int | None
    final_url: str
    charset: str
    requested_url: str
    requested_encoding: str | None


def validate_url_basic(url: Any) -> None:
    """Validate protocol, host, control characters, and missing URL credentials."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Required field 'source_url' is missing or invalid")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in url):
        raise ValueError("URL contains control characters")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"Invalid source URL '{url}'")
    if parsed.username or parsed.password:
        raise ValueError("URL must not contain credentials")


class _RedirectValidationHandler(HTTPRedirectHandler):
    """Validate each redirect target again for protocol, host, and credentials."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        try:
            validate_url_basic(newurl)
        except ValueError as error:
            raise ValueError(f"Invalid redirect target: {error}") from None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _reject_binary_content_type(content_type: str) -> None:
    """Reject obviously binary responses unless a charset was chosen."""
    main_type, _, sub_type = content_type.partition("/")
    if main_type in BINARY_MEDIA_TYPES or sub_type in BINARY_SUBTYPES:
        raise FirmwareSourceError(f"Unreadable content type: {content_type}")


def fetch_document(url: str, encoding: str | None = None) -> FetchedDocument:
    """Fetch a source and return text, status, final URL, and charset."""
    opener = build_opener(_RedirectValidationHandler())
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": ACCEPT_HEADER})
    with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        status = getattr(response, "status", None) or response.getcode()
        final_url = response.geturl()
        content_type = response.headers.get_content_type()
        data = response.read(MAX_RESPONSE_BYTES + 1)
        charset = encoding or response.headers.get_content_charset() or "utf-8"

    if len(data) > MAX_RESPONSE_BYTES:
        raise FirmwareSourceError("Response exceeds the size limit of 5 MiB")
    if encoding is None:
        _reject_binary_content_type(content_type)

    try:
        text = data.decode(charset)
    except LookupError:
        raise FirmwareSourceError(f"Unknown charset '{charset}'") from None
    except UnicodeDecodeError as error:
        raise FirmwareSourceError(
            f"Decoding with '{charset}' failed: {error}"
        ) from None

    return FetchedDocument(
        text=text,
        status=status,
        final_url=final_url,
        charset=charset,
        requested_url=url,
        requested_encoding=encoding,
    )


def fetch_source(device: Mapping[str, Any]) -> str:
    """Fetch the source and decode the response based on its charset."""
    return fetch_document(device["source_url"], device.get("encoding")).text


# --- Extraction per source type --------------------------------------------


def versions_from_text(device: Mapping[str, Any], content: str) -> set[str]:
    """Extract versions from a text/HTML source via a search pattern."""
    pattern = re.compile(device["version_pattern"])
    if pattern.groups > 1:
        raise FirmwareSourceError("Search pattern may contain at most one capturing group")
    group = 1 if pattern.groups else 0
    return {match.group(group) for match in pattern.finditer(html.unescape(content))}


def versions_from_json(device: Mapping[str, Any], content: str) -> set[str]:
    """Extract versions from a list within a JSON object."""
    items = json.loads(content)[device["items_key"]]
    if not isinstance(items, list):
        raise FirmwareSourceError("Expected JSON list is missing")
    versions: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise FirmwareSourceError("Invalid JSON entry")
        if item.get("enabled") is False:
            continue
        value = item.get(device["version_key"])
        if not isinstance(value, str) or not value.strip():
            raise FirmwareSourceError("Version field is missing or invalid")
        versions.add(value.strip())
    return versions


def versions_from_json_value(device: Mapping[str, Any], content: str) -> set[str]:
    """Extract a single version via a path within a JSON object."""
    value: Any = json.loads(content)
    for key in device["value_path"]:
        value = value[key]
    if not isinstance(value, str) or not value.strip():
        raise FirmwareSourceError("Version field is missing or invalid")
    return {value.strip()}


EXTRACTORS: dict[str, Callable[[Mapping[str, Any], str], set[str]]] = {
    SOURCE_TEXT: versions_from_text,
    SOURCE_JSON: versions_from_json,
    SOURCE_JSON_VALUE: versions_from_json_value,
}


def extract_versions(source_type: str, content: str, device: Mapping[str, Any]) -> list[str]:
    """Select the extractor based on source type and return sorted versions."""
    try:
        extractor = EXTRACTORS[source_type]
    except KeyError:
        raise FirmwareSourceError(f"Unknown source type: {source_type}") from None
    versions = extractor(device, content)
    if not versions:
        raise FirmwareSourceError("No versions found; stored state remains unchanged")
    return sorted(versions)


def fetch_versions(device: Mapping[str, Any]) -> list[str]:
    """Fetch the source and return the versions found there."""
    content = fetch_source(device)
    return extract_versions(device["source_type"], content, device)


# --- State and comparison --------------------------------------------------


def load_state(path: Path) -> dict[str, Any]:
    """Load the state or start with an empty state."""
    if path.exists():
        return read_json(path)
    return {"devices": {}}


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


# --- Device profiles -------------------------------------------------------


def validate_source_config(source: Mapping[str, Any], context: str) -> None:
    """Validate the technical source configuration of a device or profile.

    ``context`` describes the affected device/profile and is prepended to the
    message.
    """
    source_type = source.get("source_type")
    if source_type not in SUPPORTED_SOURCE_TYPES:
        raise ValueError(f"{context}: Unknown source type: {source_type!r}")

    source_url = source.get("source_url")
    if not isinstance(source_url, str) or not source_url.strip():
        raise ValueError(f"{context}: Required field 'source_url' missing or invalid")
    try:
        validate_url_basic(source_url)
    except ValueError as error:
        raise ValueError(f"{context}: {error}") from None

    encoding = source.get("encoding")
    if encoding is not None:
        if not isinstance(encoding, str) or not encoding.strip():
            raise ValueError(f"{context}: Field 'encoding' is invalid")
        try:
            codecs.lookup(encoding)
        except LookupError:
            raise ValueError(f"{context}: Unknown charset '{encoding}'") from None

    if source_type == SOURCE_TEXT:
        pattern = source.get("version_pattern")
        if not isinstance(pattern, str) or not pattern:
            raise ValueError(f"{context}: Required field 'version_pattern' missing")
        try:
            compiled = re.compile(pattern)
        except re.error as error:
            raise ValueError(f"{context}: Invalid regular expression: {error}") from None
        if compiled.groups > 1:
            raise ValueError(
                f"{context}: 'version_pattern' may contain at most one capturing group"
            )
    elif source_type == SOURCE_JSON:
        for key in ("items_key", "version_key"):
            value = source.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{context}: Required field '{key}' missing or invalid")
    elif source_type == SOURCE_JSON_VALUE:
        value_path = source.get("value_path")
        if (
            not isinstance(value_path, list)
            or not value_path
            or not all(isinstance(part, str) and part.strip() for part in value_path)
        ):
            raise ValueError(f"{context}: Required field 'value_path' missing or invalid")


def validate_profile(profile: Any, profile_id: str, context: str) -> None:
    """Validate structure, schema version, and required fields of a profile."""
    if not isinstance(profile, dict):
        raise ValueError(f"{context}: Profile is not a JSON object")

    schema_version = profile.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise ValueError(f"{context}: Invalid or missing schema version")
    if schema_version != PROFILE_SCHEMA_VERSION:
        raise ValueError(f"{context}: Unsupported schema version: {schema_version}")

    declared_id = profile.get("id")
    if not isinstance(declared_id, str) or not declared_id.strip():
        raise ValueError(f"{context}: Required field 'id' missing or invalid")
    if declared_id != profile_id:
        raise ValueError(
            f"{context}: Profile ID '{declared_id}' does not match the reference"
        )

    name = profile.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{context}: Required field 'name' missing or invalid")

    category = profile.get("category")
    if not isinstance(category, str) or category not in PROFILE_CATEGORIES:
        raise ValueError(
            f"{context}: Required field 'category' missing or invalid; "
            f"allowed values: {', '.join(sorted(PROFILE_CATEGORIES))}"
        )

    for key in ("manufacturer", "model"):
        value = profile.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{context}: Field '{key}' is invalid")

    validate_source_config(profile, context)


def profile_file_path(profile_id: str, profiles_dir: Path) -> Path:
    """Determine the profile file path and prevent escaping the directory."""
    base = profiles_dir.resolve()
    resolved = (base / f"{profile_id}.json").resolve()
    if not resolved.is_relative_to(base):
        raise ValueError(f"Profile '{profile_id}': File path is outside the profiles directory")
    return resolved


def load_profile(
    profile_id: str, profiles_dir: Path, context: str | None = None
) -> dict[str, Any]:
    """Load and validate a device profile; ``context`` refines error messages."""
    if not isinstance(profile_id, str) or not PROFILE_ID_PATTERN.fullmatch(profile_id):
        raise ValueError(f"Invalid profile ID: {profile_id!r}")

    context = context or f"Profile '{profile_id}'"
    path = profile_file_path(profile_id, profiles_dir)
    try:
        with path.open(encoding="utf-8") as handle:
            profile = json.load(handle)
    except OSError as error:
        raise ValueError(f"{context}: Profile file not readable: {error}") from None
    except json.JSONDecodeError as error:
        raise ValueError(f"{context}: Invalid JSON: {error}") from None

    validate_profile(profile, profile_id, context)
    return profile


def resolve_device(device: Mapping[str, Any], profiles_dir: Path) -> dict[str, Any]:
    """Produce a flat configuration from a (direct or profile-based) entry."""
    if not isinstance(device, Mapping):
        raise ValueError("Invalid entry")

    device_id = device.get("id")
    if not isinstance(device_id, str) or not device_id.strip():
        raise ValueError("Entry ID missing or invalid")

    if "profile" in device:
        extra = set(device) - PROFILE_ENTRY_FIELDS
        if extra:
            raise ValueError(
                f"Entry '{device_id}': Disallowed fields in profile-based entry: "
                f"{', '.join(sorted(extra))}"
            )
        profile_id = device.get("profile")
        if not isinstance(profile_id, str) or not profile_id.strip():
            raise ValueError(f"Entry '{device_id}': Required field 'profile' missing or invalid")
        context = f"Entry '{device_id}', profile '{profile_id}'"
        profile = load_profile(profile_id, profiles_dir, context=context)
        resolved = dict(profile)
        resolved["id"] = device_id
        resolved["profile"] = profile_id
        if "name" in device:
            local_name = device.get("name")
            if not isinstance(local_name, str) or not local_name.strip():
                raise ValueError(f"Device '{device_id}': Field 'name' is invalid")
            resolved["name"] = local_name
        return resolved

    context = f"Entry '{device_id}'"
    resolved = dict(device)
    if "name" in resolved:
        local_name = resolved.get("name")
        if not isinstance(local_name, str) or not local_name.strip():
            raise ValueError(f"{context}: Field 'name' is invalid")
    validate_source_config(resolved, context)
    return resolved


def resolve_devices(
    devices: Sequence[Mapping[str, Any]],
    profiles_dir: Path,
) -> list[dict[str, Any]]:
    """Resolve all devices and validate their technical source configurations."""
    return [resolve_device(device, profiles_dir) for device in devices]


def compare_versions(
    versions: Sequence[str], previous: Mapping[str, Any] | None
) -> tuple[list[str], list[str]]:
    """Determine known and newly added versions from the state."""
    if previous is None:
        return [], []
    known = previous["known_versions"]
    if not isinstance(known, list) or not all(isinstance(version, str) for version in known):
        raise ValueError("Invalid stored version state")
    new_versions = sorted(set(versions) - set(known))
    return known, new_versions


def display_name(device: Mapping[str, Any]) -> str:
    """Display name of a device; falls back to the ID."""
    return device.get("name", device["id"])


def name_column_width(devices: Sequence[Mapping[str, Any]]) -> int:
    """Width of the name column for tabular output."""
    return max((len(display_name(device)) for device in devices), default=0)


def version_sort_key(version: str) -> tuple:
    """Sort key for version labels: numeric blocks as integers, rest as text.

    Makes ``"1.10"`` sort above ``"1.9"`` and ``"2014-01-07"`` above
    ``"2013-12-18"`` regardless of the source's ordering.
    """
    parts = re.findall(r"[0-9]+|[^0-9]+", version or "")
    key: list[tuple[int, int, str]] = []
    for part in parts:
        if part.isdigit():
            key.append((0, int(part), ""))
        else:
            key.append((1, 0, part))
    return tuple(key)


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


def process_device(device: Mapping[str, Any], state: dict[str, Any], width: int) -> bool:
    """Check a single device and update the state on success."""
    name = display_name(device)
    try:
        versions = fetch_versions(device)
        previous = state["devices"].get(device["id"])
        known, new_versions = compare_versions(versions, previous)
        print(f"{name:<{width}}  {check_summary(previous, versions, new_versions)}")
        state["devices"][device["id"]] = {
            "last_checked": current_timestamp(),
            "known_versions": sorted(set(known) | set(versions)),
        }
        return True
    except Exception as error:  # a device error does not stop the remaining devices
        print(f"{name:<{width}}  Check error: {error}", file=sys.stderr)
        return False


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
    stored = state.get("devices", {})

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
            entry = stored.get(device["id"]) or {}
            versions = entry.get("known_versions") or []
            rows.append(
                (
                    display_name(device),
                    format_timestamp(entry.get("last_checked")),
                    max(versions, key=version_sort_key) if versions else "–",
                )
            )

        headers = ("Entry", "Last check", "Last version")
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


# --- Profile wizard --------------------------------------------------------

ASSISTANT_NAME_SUFFIX = " – Firmware"

_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_NUMBER_RUN_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]+)*")


class AssistantAbort(Exception):
    """The user aborted the wizard."""


class ProfileExistsError(ValueError):
    """The target profile file already exists and must not be silently overwritten."""


class ProfileChangedError(ValueError):
    """The target profile file was modified between confirmation and export."""


class _RestartUrl(Exception):
    """The user wants to repeat the source fetch."""


class _RestartExample(Exception):
    """The user wants to re-enter the example version."""


@dataclass(frozen=True)
class Occurrence:
    """An exact occurrence of the sample text in the normalized source text."""

    index: int
    start: int
    end: int
    before: str
    after: str


@dataclass(frozen=True)
class ExampleShape:
    """Structure of the example version for fixed template selection."""

    prefix: str
    left_blocks: int
    right_blocks: int | None = None
    separator: str = ""


@dataclass(frozen=True)
class PatternOptions:
    """Controls whether an additional number block is allowed."""

    allow_extra_block: bool = False


@dataclass(frozen=True)
class PatternCandidate:
    """A rule-based generated regex proposal."""

    label: str
    pattern: str


@dataclass(frozen=True)
class TestResult:
    """Result of a candidate test in the child process."""

    ok: bool
    message: str
    versions: list[str] = field(default_factory=list)


def sanitize_for_terminal(value: str) -> str:
    """Replace control characters from foreign content so the terminal is not manipulated."""
    flattened = value.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return _CONTROL_CHARACTER_PATTERN.sub(" ", flattened)


def suggest_profile_id(manufacturer: str, model: str) -> str:
    """Generate a cleaned ID proposal from manufacturer and model."""
    raw = f"{manufacturer}.{model}"
    ascii_value = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    without_punctuation = re.sub(r"[^a-z0-9 ._-]+", "", ascii_value.lower())
    cleaned = re.sub(r"\s+", "-", without_punctuation)
    cleaned = re.sub(r"-+", "-", cleaned).strip("._-")
    if not cleaned or not cleaned[0].isalnum():
        return ""
    return cleaned if PROFILE_ID_PATTERN.fullmatch(cleaned) else ""


def find_example_occurrences(
    content: str, example: str, context: int = CONTEXT_CHARS
) -> list[Occurrence]:
    """Find all exact occurrences of the sample text with limited context."""
    occurrences: list[Occurrence] = []
    if not example:
        return occurrences
    index = 0
    start = content.find(example)
    while start != -1:
        index += 1
        end = start + len(example)
        occurrences.append(
            Occurrence(
                index=index,
                start=start,
                end=end,
                before=content[max(0, start - context) : start],
                after=content[end : end + context],
            )
        )
        start = content.find(example, end)
    return occurrences


def analyze_example(example: str) -> ExampleShape | None:
    """Detect prefix, number of blocks, and composite values."""
    matches = list(_NUMBER_RUN_PATTERN.finditer(example))
    if not matches or example[matches[-1].end() :] != "":
        return None
    prefix = example[: matches[0].start()]
    if len(matches) == 1:
        return ExampleShape(prefix=prefix, left_blocks=matches[0].group().count(".") + 1)
    if len(matches) == 2 and example[matches[0].end() : matches[1].start()] == "/":
        return ExampleShape(
            prefix=prefix,
            left_blocks=matches[0].group().count(".") + 1,
            right_blocks=matches[1].group().count(".") + 1,
            separator="/",
        )
    return None


def expected_value(example: str, shape: ExampleShape) -> str:
    """Expected extracted value without fixed prefix."""
    return example[len(shape.prefix) :].strip()


def _dotted_number(blocks: int, options: PatternOptions) -> str:
    """Regex fragment for a dotted version with fixed or extended block count."""
    extra = blocks - 1
    if options.allow_extra_block:
        quantifier = "{0,1}" if extra == 0 else f"{{{extra},{extra + 1}}}"
    else:
        quantifier = "" if extra == 0 else f"{{{extra}}}"
    return "[0-9]+" if not quantifier else f"[0-9]+(?:\\.[0-9]+){quantifier}"


def _describe_blocks(blocks: int, options: PatternOptions) -> str:
    return f"{blocks}–{blocks + 1}" if options.allow_extra_block else str(blocks)


def build_candidate(shape: ExampleShape, options: PatternOptions) -> PatternCandidate:
    """Build a candidate with exactly one capturing group from the example structure."""
    if shape.separator == "/":
        left = _dotted_number(shape.left_blocks, options)
        right = _dotted_number(shape.right_blocks or 1, options)
        pattern = f"(?<![0-9./])({left}/{right})(?![0-9./])"
        label = (
            f"Composite version "
            f"({_describe_blocks(shape.left_blocks, options)}/{_describe_blocks(shape.right_blocks or 1, options)} blocks)"
        )
    else:
        number = _dotted_number(shape.left_blocks, options)
        prefix = shape.prefix.rstrip()
        if prefix:
            boundary = "\\b" if prefix[:1].isalnum() else ""
            pattern = f"{boundary}{re.escape(prefix)}\\s*({number})(?![0-9.])"
            label = f'Prefix version "{sanitize_for_terminal(prefix)}" ({_describe_blocks(shape.left_blocks, options)} blocks)'
        else:
            pattern = f"(?<![0-9.])({number})(?![0-9.])"
            label = f"Dotted version ({_describe_blocks(shape.left_blocks, options)} blocks)"
    return PatternCandidate(label=label, pattern=pattern)


def suggest_version_patterns(
    example: str, options: PatternOptions | None = None
) -> list[PatternCandidate]:
    """Return rule-based pattern candidates for an example version."""
    shape = analyze_example(example)
    if shape is None:
        return []
    variants = (PatternOptions(False), PatternOptions(True)) if options is None else (options,)
    return [build_candidate(shape, variant) for variant in variants]


def pattern_contains_occurrence(pattern: str, content: str, occurrence: Occurrence) -> bool:
    """Check whether the candidate captures exactly the selected occurrence."""
    for match in re.finditer(pattern, content):
        if match.start() <= occurrence.start and occurrence.end <= match.end():
            return True
    return False


def _pattern_test_worker(content: str, version_pattern: str, connection: Any) -> None:
    """Run the candidate test in a child process using the existing extractor."""
    try:
        versions = extract_versions(SOURCE_TEXT, content, {"version_pattern": version_pattern})
        connection.send(("ok", versions))
    except BaseException as error:  # Every error is reported to the parent process.
        connection.send(("error", str(error)))
    finally:
        connection.close()


def test_pattern(
    content: str, pattern: str, timeout: float = PATTERN_TEST_TIMEOUT_SECONDS
) -> TestResult:
    """Test a candidate isolated in a process with a time limit."""
    try:
        compiled = re.compile(pattern)
    except re.error as error:
        return TestResult(False, f"Invalid regular expression: {error}")
    if compiled.groups != 1:
        return TestResult(False, "Pattern must have exactly one capturing group")

    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_pattern_test_worker, args=(content, pattern, child))
    process.start()
    child.close()
    try:
        if not parent.poll(timeout):
            process.terminate()
            process.join()
            return TestResult(False, "Time limit exceeded; pattern rejected")
        status, payload = parent.recv()
    finally:
        parent.close()
    process.join()
    if status == "ok":
        return TestResult(True, "", payload)
    return TestResult(False, payload)


# --- Dialog helpers --------------------------------------------------------


def ask_line(prompt: str, default: str | None = None) -> str:
    """Read a line; on empty input the default is taken."""
    suffix = f" [{default}]" if default is not None else ""
    answer = input(f"{prompt}{suffix}: ").strip()
    if not answer and default is not None:
        return default
    return answer


def ask_required(prompt: str, default: str | None = None) -> str:
    """Read a non-empty line, optionally with a default."""
    while True:
        answer = ask_line(prompt, default)
        if answer:
            return answer
        print("Input must not be empty.")


def ask_yes_no(prompt: str, default: bool = False) -> bool:
    """Ask a yes/no question."""
    hint = "Y/n" if default else "y/N"
    while True:
        answer = input(f"{prompt} [{hint}]: ").strip().lower()
        if not answer:
            return default
        if answer in {"y", "yes", "j", "ja"}:
            return True
        if answer in {"n", "no", "nein"}:
            return False
        print("Please enter 'y' or 'n'.")


def ask_profile_id(default: str) -> str:
    """Ask for a profile ID and validate it completely."""
    while True:
        answer = ask_line("Profile ID", default or None)
        if not answer:
            print("Please enter a profile ID.")
            continue
        if PROFILE_ID_PATTERN.fullmatch(answer):
            return answer
        print("Invalid profile ID: allowed are a–z, 0–9, '.', '_' and '-', starting with a letter or digit.")


def ask_encoding() -> str:
    """Ask for a valid Python charset designation."""
    while True:
        candidate = ask_required("Python charset (e. g. utf-8, latin-1)")
        try:
            codecs.lookup(candidate)
        except LookupError:
            print(f"Unknown charset: {candidate}")
            continue
        return candidate


def ask_category() -> str:
    """Ask for a profile category."""
    hint = "/".join(sorted(PROFILE_CATEGORIES))
    while True:
        answer = ask_line(f"Category ({hint})", default="firmware")
        if answer in PROFILE_CATEGORIES:
            return answer
        print(f"Invalid category: allowed values are {hint}.")


def acquire_document() -> FetchedDocument:
    """Fetch a URL and allow correction of URL or charset."""
    encoding: str | None = None
    last_url: str | None = None
    while True:
        raw = ask_required("Source URL", default=last_url)
        last_url = raw.split("#", 1)[0].strip()
        try:
            validate_url_basic(last_url)
            document = fetch_document(last_url, encoding)
        except (ValueError, OSError) as error:
            print(f"Fetch failed: {error}")
            choice = input("[u]rL correct, [c]harset choose, [a]bort: ").strip().lower()
            if choice.startswith("c"):
                encoding = ask_encoding()
            elif choice.startswith("a"):
                raise AssistantAbort()
            continue
        if document.final_url != last_url:
            print(f"Note: Redirected to {sanitize_for_terminal(document.final_url)}")
        return document


def _retry_or_abort() -> bool:
    """Ask whether to re-enter; True means repeat."""
    action = input("[w]ork on again, [a]bort: ").strip().lower()
    return not action.startswith("a")


def _format_occurrence(occurrence: Occurrence) -> str:
    before = sanitize_for_terminal(occurrence.before)
    after = sanitize_for_terminal(occurrence.after)
    return f"[{occurrence.index}] …{before} «HERE» {after}…"


def choose_occurrence(occurrences: list[Occurrence]) -> Occurrence | None:
    """Let the user explicitly select an occurrence."""
    total = len(occurrences)
    if total == 1:
        print(_format_occurrence(occurrences[0]))
        return occurrences[0] if ask_yes_no("Use this occurrence?", default=True) else None

    page = 0
    page_size = 10
    while True:
        start = page * page_size
        chunk = occurrences[start : start + page_size]
        for occurrence in chunk:
            print(_format_occurrence(occurrence))
        print(f"Occurrences {start + 1}–{start + len(chunk)} of {total}.")
        answer = input("Number to choose, [w]ork on, [b]ack, [a]bort: ").strip().lower()
        if answer.startswith("a"):
            raise AssistantAbort()
        if answer.startswith("w") and start + page_size < total:
            page += 1
            continue
        if answer.startswith("b") and page > 0:
            page -= 1
            continue
        if answer.isdigit() and 1 <= int(answer) <= total:
            return occurrences[int(answer) - 1]
        print("Invalid selection.")


def _select_example(normalized: str) -> tuple[str, Occurrence]:
    """Ask for the example version and let the occurrence be selected."""
    while True:
        example = ask_required("Which version label do you see on this page?")
        if "\n" in example or "\r" in example:
            print("Please enter without line breaks.")
            continue
        if len(example) > MAX_EXAMPLE_LENGTH:
            print(f"The sample text may be at most {MAX_EXAMPLE_LENGTH} characters long.")
            continue
        occurrences = find_example_occurrences(normalized, example)
        if not occurrences:
            print("This text does not occur in the fetched content.")
            action = input("[c]hange entry, [u]rL/charset change, [a]bort: ").strip().lower()
            if action.startswith("a"):
                raise AssistantAbort()
            if action.startswith("u"):
                raise _RestartUrl()
            continue
        occurrence = choose_occurrence(occurrences)
        if occurrence is None:
            continue
        return example, occurrence


def _gather_profile_inputs() -> tuple[
    FetchedDocument, str, Occurrence, PatternCandidate, TestResult, str
]:
    """Combine source fetch, example selection, and candidate choice into one result."""
    while True:
        document = acquire_document()
        normalized = html.unescape(document.text)
        try:
            while True:
                example, occurrence = _select_example(normalized)
                try:
                    candidate, result, expected = _select_candidate(
                        normalized, example, occurrence
                    )
                except _RestartExample:
                    continue
                return document, example, occurrence, candidate, result, expected
        except _RestartUrl:
            continue


def _select_candidate(
    normalized: str, example: str, occurrence: Occurrence
) -> tuple[PatternCandidate, TestResult, str]:
    """Generate candidates, test them, and let a capturing candidate be chosen."""
    shape = analyze_example(example)
    if shape is None:
        print("The format of the sample text is not supported by the fixed templates.")
        print("An expert mode is not part of this scope; please correct the sample text.")
        if _retry_or_abort():
            raise _RestartExample()
        raise AssistantAbort()

    expected = expected_value(example, shape)
    candidates: list[PatternCandidate] = []
    for candidate in suggest_version_patterns(example, None):
        if candidate.pattern not in {existing.pattern for existing in candidates}:
            candidates.append(candidate)

    print()
    print("Pattern candidates:")
    for candidate in candidates:
        print(f"  {candidate.label}: {candidate.pattern}")

    viable: list[tuple[PatternCandidate, TestResult]] = []
    for candidate in candidates:
        result = test_pattern(normalized, candidate.pattern)
        if result.ok:
            print(f"    -> {candidate.label}: {', '.join(result.versions)}")
        else:
            print(f"    -> {candidate.label}: {result.message}")
        if (
            result.ok
            and expected in result.versions
            and pattern_contains_occurrence(candidate.pattern, normalized, occurrence)
        ):
            viable.append((candidate, result))

    if not viable:
        print("No candidate captures the selected occurrence with the expected value.")
        if _retry_or_abort():
            raise _RestartExample()
        raise AssistantAbort()

    if len(viable) == 1:
        print(f"Using: {viable[0][0].label}")
        return viable[0][0], viable[0][1], expected

    print()
    for number, (candidate, _) in enumerate(viable, start=1):
        print(f"  [{number}] {candidate.label}: {candidate.pattern}")
    while True:
        answer = ask_required("Number of the candidate to choose")
        if answer.isdigit() and 1 <= int(answer) <= len(viable):
            candidate, result = viable[int(answer) - 1]
            return candidate, result, expected
        print("Invalid selection.")


def _resolve_export_target(profile: dict[str, Any], profiles_dir: Path) -> tuple[Path, bool, int | None]:
    """Determine the target path and resolve a possible collision with the user."""
    while True:
        path = profile_file_path(profile["id"], profiles_dir)
        if not path.exists():
            return path, False, None
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Target path is not a regular file: {path}")
        action = input(
            f"File '{path.name}' exists. [o]verwrite, [n]ew ID, [a]bort: "
        ).strip().lower()
        if action.startswith("a"):
            raise AssistantAbort()
        if action.startswith("n"):
            profile["id"] = ask_profile_id("")
            continue
        return path, True, path.stat().st_mtime_ns


def export_profile(
    profile: Mapping[str, Any],
    profiles_dir: Path,
    *,
    overwrite: bool = False,
    expected_exists: bool | None = None,
    expected_mtime: int | None = None,
) -> Path:
    """Write a profile atomically, without silently overwriting an existing file."""
    path = profile_file_path(profile["id"], profiles_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    exists = path.exists()
    if exists and not overwrite:
        raise ProfileExistsError(f"Profile file already exists: {path}")
    if expected_exists is not None and exists != expected_exists:
        raise ProfileChangedError(f"Target was modified in the meantime: {path}")
    if expected_mtime is not None:
        current = path.stat().st_mtime_ns if exists else None
        if current != expected_mtime:
            raise ProfileChangedError(f"Target was modified in the meantime: {path}")

    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(profile, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    except BaseException:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise

    load_profile(profile["id"], profiles_dir, context=f"Profile '{profile['id']}'")
    return path


def run_profile_assistant(profiles_dir: Path) -> int:
    """Run the dialog-driven profile wizard and export a profile."""
    print()
    print("firmAware profile wizard")
    print("Creates a text/HTML device profile rule-based. No AI, no external services.")
    print("Local CLI fetch; not intended as a public URL fetch service.")
    print()

    manufacturer = ask_required("Manufacturer")
    model = ask_required("Model")
    default_name = f"{manufacturer} {model}{ASSISTANT_NAME_SUFFIX}"
    name = ask_required("Display name", default=default_name)
    profile_id = ask_profile_id(suggest_profile_id(manufacturer, model))
    category = ask_category()

    document, example, occurrence, candidate, result, expected = _gather_profile_inputs()
    normalized = html.unescape(document.text)
    occurrences = find_example_occurrences(normalized, example)

    configured_url = document.requested_url
    if document.final_url != configured_url and ask_yes_no(
        "Adopt redirect target as source URL?", default=False
    ):
        configured_url = document.final_url

    print()
    print("Preview")
    print(f"  Profile ID:     {profile_id}")
    print(f"  Name:           {name}")
    print(f"  Manufacturer:   {manufacturer}")
    print(f"  Model:          {model}")
    print(f"  Category:       {category}")
    print(f"  Source URL:     {sanitize_for_terminal(configured_url)}")
    if document.final_url != configured_url:
        print(f"  Final URL:      {sanitize_for_terminal(document.final_url)}")
    print(f"  Charset:        {document.charset}")
    print("  Scope:          entire source")
    print(f"  Expected:       {sanitize_for_terminal(expected)}")
    print(f"  Versions:       {', '.join(sanitize_for_terminal(v) for v in result.versions)}")
    print(f"  Occurrences:    {len(occurrences)} (distinct values: {len(set(result.versions))})")
    print(f"  Regex:          {candidate.pattern}")

    profile: dict[str, Any] = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "id": profile_id,
        "name": name,
        "manufacturer": manufacturer,
        "model": model,
        "category": category,
        "source_type": SOURCE_TEXT,
        "source_url": configured_url,
        "version_pattern": candidate.pattern,
    }
    if document.requested_encoding:
        profile["encoding"] = document.requested_encoding

    try:
        validate_profile(profile, profile_id, f"Profile '{profile_id}'")
    except ValueError as error:
        print(f"Internal validation error: {error}", file=sys.stderr)
        return 1

    snapshot_time = current_timestamp()
    if ask_yes_no("Re-fetch and test before saving?", default=False):
        try:
            fresh = fetch_document(document.requested_url, document.requested_encoding)
            fresh_result = test_pattern(html.unescape(fresh.text), candidate.pattern)
        except (ValueError, OSError) as error:
            fresh_result = TestResult(False, str(error))
        if not (fresh_result.ok and expected in fresh_result.versions):
            print("Re-test failed; export blocked.")
            if not ask_yes_no("Use the previously tested snapshot instead?", default=False):
                return 1
            print(f"Note: Snapshot from {format_timestamp(snapshot_time)}.")

    if not ask_yes_no("Save profile?", default=False):
        print("Not saved.", file=sys.stderr)
        return 1

    try:
        target, overwrite, mtime = _resolve_export_target(profile, profiles_dir)
        written = export_profile(
            profile,
            profiles_dir,
            overwrite=overwrite,
            expected_exists=target.exists(),
            expected_mtime=mtime,
        )
    except AssistantAbort:
        print("Aborted; no profile file was created.", file=sys.stderr)
        return 1
    except (ValueError, OSError) as error:
        print(f"Save error: {error}", file=sys.stderr)
        return 1

    print()
    print(f"Profile saved: {written}")
    print("Tested successfully on the current response.")
    print("Not checked: future page changes and future version formats.")
    print()
    print("Integration into devices.json (example):")
    print(json.dumps({"id": "my-device", "profile": profile["id"]}, ensure_ascii=False, indent=2))
    print("For an existing device, keep the local ID; devices.json and state.json are not modified.")
    return 0


# --- Entry point -----------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments; default paths are next to the script."""
    base = Path(__file__).resolve().parent
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
        "--create-profile",
        action="store_true",
        help="Interactively create and export a device profile",
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
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the check or status output depending on the argument."""
    args = parse_args(argv)

    if args.create_profile:
        try:
            return run_profile_assistant(args.profiles_dir)
        except KeyboardInterrupt:
            print("\nAborted (Ctrl+C).", file=sys.stderr)
            return 130
        except EOFError:
            print("Abort: end of input reached.", file=sys.stderr)
            return 1
        except AssistantAbort:
            print("Aborted; no profile file was created.", file=sys.stderr)
            return 1

    try:
        config = read_json(args.devices)
        state = load_state(args.state)
        raw_devices = validate_config(config, state)
        devices = resolve_devices(raw_devices, args.profiles_dir)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Configuration error: {error}", file=sys.stderr)
        return 1

    if args.status:
        print_status(devices, state)
        return 0

    failed = False
    width = name_column_width(devices)

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
        print()
        print(f"{CATEGORY_DISPLAY_NAMES.get(category, category)}:")
        # No short-circuit: all entries are checked even after an error.
        for device in category_devices:
            if not process_device(device, state, width):
                failed = True

    if other:
        print()
        print("Other:")
        # No short-circuit: all entries are checked even after an error.
        for device in other:
            if not process_device(device, state, width):
                failed = True

    try:
        save_state(args.state, state)
    except OSError as error:
        print(f"Storage error: {error}", file=sys.stderr)
        return 1
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
