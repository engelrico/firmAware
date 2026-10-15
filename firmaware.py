#!/usr/bin/env python3
"""firmAware – Firmwarequellen auf neue Versionsbezeichnungen prüfen.

Prüft die in ``devices.json`` konfigurierten Quellen und meldet bislang
unbekannte Versionsbezeichnungen. Bekannte Versionen werden in ``state.json``
gespeichert. Geräte können direkt oder über wiederverwendbare Profile in
``profiles/*.json`` konfiguriert werden. Es wird ausschließlich die
Python-Standardbibliothek benötigt; Details siehe ``specification.md``.
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

# --- Konstanten ------------------------------------------------------------

DESCRIPTION = "firmAware – Firmwarequellen auf neue Versionsbezeichnungen prüfen."

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

PROFILE_SCHEMA_VERSION = 1
PROFILE_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]*")
PROFILE_ENTRY_FIELDS = frozenset({"id", "profile", "name"})

BINARY_MEDIA_TYPES = frozenset({"image", "audio", "video", "font"})
BINARY_SUBTYPES = frozenset({"octet-stream", "pdf", "zip", "gzip"})


class FirmwareSourceError(ValueError):
    """Die abgerufene Quelle liefert keine auswertbaren Versionsangaben."""


# --- Ein-/Ausgabe ----------------------------------------------------------


def read_json(path: Path) -> Any:
    """Liest eine JSON-Datei als UTF-8 und gibt den unveränderten Inhalt zurück."""
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def save_state(path: Path, state: Mapping[str, Any]) -> None:
    """Schreibt den Zustand atomar über eine benachbarte ``.tmp``-Datei."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    os.replace(temporary, path)


# --- Abruf -----------------------------------------------------------------


@dataclass(frozen=True)
class FetchedDocument:
    """Ergebnis eines Quellabrufs samt Metadaten für Vorschau und Export."""

    text: str
    status: int | None
    final_url: str
    charset: str
    requested_url: str
    requested_encoding: str | None


def validate_url_basic(url: Any) -> None:
    """Prüft Protokoll, Host, Steuerzeichen und fehlende URL-Zugangsdaten."""
    if not isinstance(url, str) or not url.strip():
        raise ValueError("Pflichtfeld 'source_url' fehlt oder ist ungültig")
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in url):
        raise ValueError("URL enthält Steuerzeichen")
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise ValueError(f"Ungültige Quell-URL '{url}'")
    if parsed.username or parsed.password:
        raise ValueError("URL darf keine Zugangsdaten enthalten")


class _RedirectValidationHandler(HTTPRedirectHandler):
    """Prüft jedes Weiterleitungsziel erneut auf Protokoll, Host und Zugangsdaten."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        try:
            validate_url_basic(newurl)
        except ValueError as error:
            raise ValueError(f"Ungültiges Weiterleitungsziel: {error}") from None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _reject_binary_content_type(content_type: str) -> None:
    """Lehnt offensichtlich binäre Antworten ab, solange kein Zeichensatz gewählt wurde."""
    main_type, _, sub_type = content_type.partition("/")
    if main_type in BINARY_MEDIA_TYPES or sub_type in BINARY_SUBTYPES:
        raise FirmwareSourceError(f"Nicht lesbarer Inhaltstyp: {content_type}")


def fetch_document(url: str, encoding: str | None = None) -> FetchedDocument:
    """Ruft eine Quelle ab und liefert Text, Status, finale URL und Zeichensatz."""
    opener = build_opener(_RedirectValidationHandler())
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": ACCEPT_HEADER})
    with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        status = getattr(response, "status", None) or response.getcode()
        final_url = response.geturl()
        content_type = response.headers.get_content_type()
        data = response.read(MAX_RESPONSE_BYTES + 1)
        charset = encoding or response.headers.get_content_charset() or "utf-8"

    if len(data) > MAX_RESPONSE_BYTES:
        raise FirmwareSourceError("Antwort überschreitet das Größenlimit von 5 MiB")
    if encoding is None:
        _reject_binary_content_type(content_type)

    try:
        text = data.decode(charset)
    except LookupError:
        raise FirmwareSourceError(f"Unbekannter Zeichensatz '{charset}'") from None
    except UnicodeDecodeError as error:
        raise FirmwareSourceError(
            f"Dekodierung mit '{charset}' fehlgeschlagen: {error}"
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
    """Ruft die Quelle ab und dekodiert die Antwort anhand ihres Zeichensatzes."""
    return fetch_document(device["source_url"], device.get("encoding")).text


# --- Extraktion je Quelltyp ------------------------------------------------


def versions_from_text(device: Mapping[str, Any], content: str) -> set[str]:
    """Extrahiert Versionen aus einer Text-/HTML-Quelle über ein Suchmuster."""
    pattern = re.compile(device["version_pattern"])
    if pattern.groups > 1:
        raise FirmwareSourceError("Suchmuster darf höchstens eine Klammergruppe enthalten")
    group = 1 if pattern.groups else 0
    return {match.group(group) for match in pattern.finditer(html.unescape(content))}


def versions_from_json(device: Mapping[str, Any], content: str) -> set[str]:
    """Extrahiert Versionen aus einer Liste innerhalb eines JSON-Objekts."""
    items = json.loads(content)[device["items_key"]]
    if not isinstance(items, list):
        raise FirmwareSourceError("Erwartete JSON-Liste fehlt")
    versions: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            raise FirmwareSourceError("Ungültiger JSON-Eintrag")
        if item.get("enabled") is False:
            continue
        value = item.get(device["version_key"])
        if not isinstance(value, str) or not value.strip():
            raise FirmwareSourceError("Versionsfeld fehlt oder ist ungültig")
        versions.add(value.strip())
    return versions


def versions_from_json_value(device: Mapping[str, Any], content: str) -> set[str]:
    """Extrahiert eine einzelne Version über einen Pfad innerhalb eines JSON-Objekts."""
    value: Any = json.loads(content)
    for key in device["value_path"]:
        value = value[key]
    if not isinstance(value, str) or not value.strip():
        raise FirmwareSourceError("Versionsfeld fehlt oder ist ungültig")
    return {value.strip()}


EXTRACTORS: dict[str, Callable[[Mapping[str, Any], str], set[str]]] = {
    SOURCE_TEXT: versions_from_text,
    SOURCE_JSON: versions_from_json,
    SOURCE_JSON_VALUE: versions_from_json_value,
}


def extract_versions(source_type: str, content: str, device: Mapping[str, Any]) -> list[str]:
    """Wählt anhand des Quelltyps die Extraktion und liefert sortierte Versionen."""
    try:
        extractor = EXTRACTORS[source_type]
    except KeyError:
        raise FirmwareSourceError(f"Unbekannter Quelltyp: {source_type}") from None
    versions = extractor(device, content)
    if not versions:
        raise FirmwareSourceError("Keine Versionen gefunden; gespeicherter Stand bleibt erhalten")
    return sorted(versions)


def fetch_versions(device: Mapping[str, Any]) -> list[str]:
    """Ruft die Quelle ab und liefert die dort gefundenen Versionen."""
    content = fetch_source(device)
    return extract_versions(device["source_type"], content, device)


# --- Zustand und Vergleich -------------------------------------------------


def load_state(path: Path) -> dict[str, Any]:
    """Lädt den Zustand oder beginnt mit einem leeren Zustand."""
    if path.exists():
        return read_json(path)
    return {"devices": {}}


def validate_config(config: Mapping[str, Any], state: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Prüft die Grundstruktur und liefert die Geräteliste."""
    devices = config["devices"]
    if not isinstance(devices, list) or not isinstance(state.get("devices"), dict):
        raise ValueError("Ungültige Konfigurations- oder Zustandsstruktur")
    ids = []
    for device in devices:
        if not isinstance(device, Mapping):
            raise ValueError("Ungültiger Geräteeintrag")
        device_id = device.get("id")
        if not isinstance(device_id, str) or not device_id.strip():
            raise ValueError("Geräte-ID fehlt oder ist ungültig")
        ids.append(device_id)
    if len(ids) != len(set(ids)):
        raise ValueError("Geräte-IDs müssen eindeutig sein")
    return devices


# --- Geräteprofile ---------------------------------------------------------


def validate_source_config(source: Mapping[str, Any], context: str) -> None:
    """Validiert die technische Quellkonfiguration eines Geräts oder Profils.

    ``context`` beschreibt das betroffene Gerät/Profil und wird der Meldung
    vorangestellt.
    """
    source_type = source.get("source_type")
    if source_type not in SUPPORTED_SOURCE_TYPES:
        raise ValueError(f"{context}: Unbekannter Quelltyp: {source_type!r}")

    source_url = source.get("source_url")
    if not isinstance(source_url, str) or not source_url.strip():
        raise ValueError(f"{context}: Pflichtfeld 'source_url' fehlt oder ist ungültig")
    try:
        validate_url_basic(source_url)
    except ValueError as error:
        raise ValueError(f"{context}: {error}") from None

    encoding = source.get("encoding")
    if encoding is not None:
        if not isinstance(encoding, str) or not encoding.strip():
            raise ValueError(f"{context}: Feld 'encoding' ist ungültig")
        try:
            codecs.lookup(encoding)
        except LookupError:
            raise ValueError(f"{context}: Unbekannter Zeichensatz '{encoding}'") from None

    if source_type == SOURCE_TEXT:
        pattern = source.get("version_pattern")
        if not isinstance(pattern, str) or not pattern:
            raise ValueError(f"{context}: Pflichtfeld 'version_pattern' fehlt")
        try:
            compiled = re.compile(pattern)
        except re.error as error:
            raise ValueError(f"{context}: Ungültiger regulärer Ausdruck: {error}") from None
        if compiled.groups > 1:
            raise ValueError(
                f"{context}: 'version_pattern' darf höchstens eine Klammergruppe enthalten"
            )
    elif source_type == SOURCE_JSON:
        for key in ("items_key", "version_key"):
            value = source.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{context}: Pflichtfeld '{key}' fehlt oder ist ungültig")
    elif source_type == SOURCE_JSON_VALUE:
        value_path = source.get("value_path")
        if (
            not isinstance(value_path, list)
            or not value_path
            or not all(isinstance(part, str) and part.strip() for part in value_path)
        ):
            raise ValueError(f"{context}: Pflichtfeld 'value_path' fehlt oder ist ungültig")


def validate_profile(profile: Any, profile_id: str, context: str) -> None:
    """Prüft Struktur, Schema-Version und Pflichtfelder eines Profils."""
    if not isinstance(profile, dict):
        raise ValueError(f"{context}: Profil ist kein JSON-Objekt")

    schema_version = profile.get("schema_version")
    if isinstance(schema_version, bool) or not isinstance(schema_version, int):
        raise ValueError(f"{context}: Ungültige oder fehlende Schema-Version")
    if schema_version != PROFILE_SCHEMA_VERSION:
        raise ValueError(f"{context}: Nicht unterstützte Schema-Version: {schema_version}")

    declared_id = profile.get("id")
    if not isinstance(declared_id, str) or not declared_id.strip():
        raise ValueError(f"{context}: Pflichtfeld 'id' fehlt oder ist ungültig")
    if declared_id != profile_id:
        raise ValueError(
            f"{context}: Profil-ID '{declared_id}' stimmt nicht mit der Referenz überein"
        )

    name = profile.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValueError(f"{context}: Pflichtfeld 'name' fehlt oder ist ungültig")

    for key in ("manufacturer", "model"):
        value = profile.get(key)
        if value is not None and (not isinstance(value, str) or not value.strip()):
            raise ValueError(f"{context}: Feld '{key}' ist ungültig")

    validate_source_config(profile, context)


def profile_file_path(profile_id: str, profiles_dir: Path) -> Path:
    """Bestimmt den Profildateipfad und verhindert Ausbrüche aus dem Verzeichnis."""
    base = profiles_dir.resolve()
    resolved = (base / f"{profile_id}.json").resolve()
    if not resolved.is_relative_to(base):
        raise ValueError(f"Profil '{profile_id}': Dateipfad liegt außerhalb des Profilverzeichnisses")
    return resolved


def load_profile(
    profile_id: str, profiles_dir: Path, context: str | None = None
) -> dict[str, Any]:
    """Lädt und validiert ein Geräteprofil; ``context`` verfeinert Fehlermeldungen."""
    if not isinstance(profile_id, str) or not PROFILE_ID_PATTERN.fullmatch(profile_id):
        raise ValueError(f"Ungültige Profil-ID: {profile_id!r}")

    context = context or f"Profil '{profile_id}'"
    path = profile_file_path(profile_id, profiles_dir)
    try:
        with path.open(encoding="utf-8") as handle:
            profile = json.load(handle)
    except OSError as error:
        raise ValueError(f"{context}: Profildatei nicht lesbar: {error}") from None
    except json.JSONDecodeError as error:
        raise ValueError(f"{context}: Ungültiges JSON: {error}") from None

    validate_profile(profile, profile_id, context)
    return profile


def resolve_device(device: Mapping[str, Any], profiles_dir: Path) -> dict[str, Any]:
    """Erzeugt aus einem (direkten oder profilbasierten) Eintrag eine flache Konfiguration."""
    if not isinstance(device, Mapping):
        raise ValueError("Ungültiger Geräteeintrag")

    device_id = device.get("id")
    if not isinstance(device_id, str) or not device_id.strip():
        raise ValueError("Geräte-ID fehlt oder ist ungültig")

    if "profile" in device:
        extra = set(device) - PROFILE_ENTRY_FIELDS
        if extra:
            raise ValueError(
                f"Gerät '{device_id}': Unerlaubte Felder in profilbasiertem Eintrag: "
                f"{', '.join(sorted(extra))}"
            )
        profile_id = device.get("profile")
        if not isinstance(profile_id, str) or not profile_id.strip():
            raise ValueError(f"Gerät '{device_id}': Pflichtfeld 'profile' fehlt oder ist ungültig")
        context = f"Gerät '{device_id}', Profil '{profile_id}'"
        profile = load_profile(profile_id, profiles_dir, context=context)
        resolved = dict(profile)
        resolved["id"] = device_id
        resolved["profile"] = profile_id
        if "name" in device:
            local_name = device.get("name")
            if not isinstance(local_name, str) or not local_name.strip():
                raise ValueError(f"Gerät '{device_id}': Feld 'name' ist ungültig")
            resolved["name"] = local_name
        return resolved

    context = f"Gerät '{device_id}'"
    resolved = dict(device)
    if "name" in resolved:
        local_name = resolved.get("name")
        if not isinstance(local_name, str) or not local_name.strip():
            raise ValueError(f"{context}: Feld 'name' ist ungültig")
    validate_source_config(resolved, context)
    return resolved


def resolve_devices(
    devices: Sequence[Mapping[str, Any]],
    profiles_dir: Path,
) -> list[dict[str, Any]]:
    """Löst alle Geräte auf und validiert ihre technischen Quellkonfigurationen."""
    return [resolve_device(device, profiles_dir) for device in devices]


def compare_versions(
    versions: Sequence[str], previous: Mapping[str, Any] | None
) -> tuple[list[str], list[str]]:
    """Ermittelt bekannte und neu hinzugekommene Versionen aus dem Zustand."""
    if previous is None:
        return [], []
    known = previous["known_versions"]
    if not isinstance(known, list) or not all(isinstance(version, str) for version in known):
        raise ValueError("Ungültiger gespeicherter Versionsstand")
    new_versions = sorted(set(versions) - set(known))
    return known, new_versions


def display_name(device: Mapping[str, Any]) -> str:
    """Anzeigename eines Geräts; ersatzweise die ID."""
    return device.get("name", device["id"])


def name_column_width(devices: Sequence[Mapping[str, Any]]) -> int:
    """Breite der Namensspalte für die tabellarische Ausgabe."""
    return max((len(display_name(device)) for device in devices), default=0)


def check_summary(
    previous: Mapping[str, Any] | None,
    versions: Sequence[str],
    new_versions: Sequence[str],
) -> str:
    """Kompakte Statuszeile einer erfolgreichen Geräteprüfung."""
    if previous is None:
        return f"Ausgangsstand ({len(versions)}): {', '.join(versions)}"
    if new_versions:
        return f"neu: {', '.join(new_versions)}"
    return "keine Änderung"


def current_timestamp() -> str:
    """Zeitpunkt der letzten erfolgreichen Prüfung in lokaler Zeitzone."""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def process_device(device: Mapping[str, Any], state: dict[str, Any], width: int) -> bool:
    """Prüft ein einzelnes Gerät und aktualisiert bei Erfolg den Zustand."""
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
    except Exception as error:  # ein Gerätefehler stoppt die übrigen Geräte nicht
        print(f"{name:<{width}}  Prüffehler: {error}", file=sys.stderr)
        return False


def format_timestamp(value: Any) -> str:
    """Formatiert den gespeicherten Prüfzeitpunkt kompakt und lesbar."""
    if not value:
        return "nie geprüft"
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
    """Gibt den ASCII-Schriftzug „firmAware" aus."""
    print()
    for line in BANNER_LINES:
        print(line)


def print_status(devices: Sequence[Mapping[str, Any]], state: Mapping[str, Any]) -> None:
    """Gibt je Gerät letzten Prüfzeitpunkt und neueste bekannte Version aus."""
    print_banner()
    stored = state.get("devices", {})
    rows = []
    for device in devices:
        entry = stored.get(device["id"]) or {}
        versions = entry.get("known_versions") or []
        rows.append(
            (
                display_name(device),
                format_timestamp(entry.get("last_checked")),
                versions[-1] if versions else "–",
            )
        )

    headers = ("Gerät", "Letzter Check", "Letzte Version")
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


# --- Profilassistent -------------------------------------------------------

ASSISTANT_NAME_SUFFIX = " – Firmware"

_CONTROL_CHARACTER_PATTERN = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")
_NUMBER_RUN_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]+)*")


class AssistantAbort(Exception):
    """Der Nutzer hat den Assistenten abgebrochen."""


class ProfileExistsError(ValueError):
    """Die Zielprofildatei existiert bereits und darf nicht still überschrieben werden."""


class ProfileChangedError(ValueError):
    """Die Zielprofildatei wurde zwischen Bestätigung und Export verändert."""


class _RestartUrl(Exception):
    """Der Nutzer möchte den Quellabruf wiederholen."""


class _RestartExample(Exception):
    """Der Nutzer möchte die Beispielversion erneut eingeben."""


@dataclass(frozen=True)
class Occurrence:
    """Ein exaktes Vorkommen der Beispielangabe im normalisierten Quelltext."""

    index: int
    start: int
    end: int
    before: str
    after: str


@dataclass(frozen=True)
class ExampleShape:
    """Struktur der Beispielversion für die Auswahl fester Vorlagen."""

    prefix: str
    left_blocks: int
    right_blocks: int | None = None
    separator: str = ""


@dataclass(frozen=True)
class PatternOptions:
    """Steuert, ob ein zusätzlicher Zahlenblock zugelassen wird."""

    allow_extra_block: bool = False


@dataclass(frozen=True)
class PatternCandidate:
    """Ein regelbasiert erzeugter Regex-Vorschlag."""

    label: str
    pattern: str


@dataclass(frozen=True)
class TestResult:
    """Ergebnis eines Kandidatentests im Kindprozess."""

    ok: bool
    message: str
    versions: list[str] = field(default_factory=list)


def sanitize_for_terminal(value: str) -> str:
    """Ersetzt Steuerzeichen fremder Inhalte, damit das Terminal nicht manipuliert wird."""
    flattened = value.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return _CONTROL_CHARACTER_PATTERN.sub(" ", flattened)


def suggest_profile_id(manufacturer: str, model: str) -> str:
    """Erzeugt einen bereinigten ID-Vorschlag aus Hersteller und Modell."""
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
    """Findet alle exakten Vorkommen der Beispielangabe samt begrenztem Kontext."""
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
    """Erkennt Präfix, Anzahl der Zahlenblöcke und zusammengesetzte Angaben."""
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
    """Erwarteter extrahierter Wert ohne festes Präfix."""
    return example[len(shape.prefix) :].strip()


def _dotted_number(blocks: int, options: PatternOptions) -> str:
    """Regex-Fragment für eine Punktversion mit fester oder erweiterter Blockzahl."""
    extra = blocks - 1
    if options.allow_extra_block:
        quantifier = "{0,1}" if extra == 0 else f"{{{extra},{extra + 1}}}"
    else:
        quantifier = "" if extra == 0 else f"{{{extra}}}"
    return "[0-9]+" if not quantifier else f"[0-9]+(?:\\.[0-9]+){quantifier}"


def _describe_blocks(blocks: int, options: PatternOptions) -> str:
    return f"{blocks}–{blocks + 1}" if options.allow_extra_block else str(blocks)


def build_candidate(shape: ExampleShape, options: PatternOptions) -> PatternCandidate:
    """Baut aus der Beispielstruktur einen Kandidaten mit genau einer Klammergruppe."""
    if shape.separator == "/":
        left = _dotted_number(shape.left_blocks, options)
        right = _dotted_number(shape.right_blocks or 1, options)
        pattern = f"(?<![0-9./])({left}/{right})(?![0-9./])"
        label = (
            f"Zusammengesetzte Version "
            f"({_describe_blocks(shape.left_blocks, options)}/{_describe_blocks(shape.right_blocks or 1, options)} Blöcke)"
        )
    else:
        number = _dotted_number(shape.left_blocks, options)
        prefix = shape.prefix.rstrip()
        if prefix:
            boundary = "\\b" if prefix[:1].isalnum() else ""
            pattern = f"{boundary}{re.escape(prefix)}\\s*({number})(?![0-9.])"
            label = f"Präfixversion „{sanitize_for_terminal(prefix)}“ ({_describe_blocks(shape.left_blocks, options)} Blöcke)"
        else:
            pattern = f"(?<![0-9.])({number})(?![0-9.])"
            label = f"Punktversion ({_describe_blocks(shape.left_blocks, options)} Blöcke)"
    return PatternCandidate(label=label, pattern=pattern)


def suggest_version_patterns(
    example: str, options: PatternOptions | None = None
) -> list[PatternCandidate]:
    """Liefert regelbasierte Musterkandidaten für eine Beispielversion."""
    shape = analyze_example(example)
    if shape is None:
        return []
    variants = (PatternOptions(False), PatternOptions(True)) if options is None else (options,)
    return [build_candidate(shape, variant) for variant in variants]


def pattern_contains_occurrence(pattern: str, content: str, occurrence: Occurrence) -> bool:
    """Prüft, ob der Kandidat genau die gewählte Fundstelle erfasst."""
    for match in re.finditer(pattern, content):
        if match.start() <= occurrence.start and occurrence.end <= match.end():
            return True
    return False


def _pattern_test_worker(content: str, version_pattern: str, connection: Any) -> None:
    """Führt den Kandidatentest im Kindprozess mit dem vorhandenen Extraktor aus."""
    try:
        versions = extract_versions(SOURCE_TEXT, content, {"version_pattern": version_pattern})
        connection.send(("ok", versions))
    except BaseException as error:  # Jeder Fehler wird an den Elternprozess gemeldet.
        connection.send(("error", str(error)))
    finally:
        connection.close()


def test_pattern(
    content: str, pattern: str, timeout: float = PATTERN_TEST_TIMEOUT_SECONDS
) -> TestResult:
    """Testet einen Kandidaten isoliert in einem Prozess mit Zeitlimit."""
    try:
        compiled = re.compile(pattern)
    except re.error as error:
        return TestResult(False, f"Ungültiger regulärer Ausdruck: {error}")
    if compiled.groups != 1:
        return TestResult(False, "Muster muss genau eine erfassende Klammergruppe haben")

    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_pattern_test_worker, args=(content, pattern, child))
    process.start()
    child.close()
    try:
        if not parent.poll(timeout):
            process.terminate()
            process.join()
            return TestResult(False, "Zeitlimit überschritten; Muster abgelehnt")
        status, payload = parent.recv()
    finally:
        parent.close()
    process.join()
    if status == "ok":
        return TestResult(True, "", payload)
    return TestResult(False, payload)


# --- Dialoghilfen ----------------------------------------------------------


def ask_line(prompt: str, default: str | None = None) -> str:
    """Liest eine Zeile; bei leerer Eingabe wird der Vorschlag übernommen."""
    suffix = f" [{default}]" if default is not None else ""
    answer = input(f"{prompt}{suffix}: ").strip()
    if not answer and default is not None:
        return default
    return answer


def ask_required(prompt: str, default: str | None = None) -> str:
    """Liest eine nicht leere Zeile, optional mit Vorschlag."""
    while True:
        answer = ask_line(prompt, default)
        if answer:
            return answer
        print("Eingabe darf nicht leer sein.")


def ask_yes_no(prompt: str, default: bool = False) -> bool:
    """Stellt eine Ja/Nein-Frage."""
    hint = "J/n" if default else "j/N"
    while True:
        answer = input(f"{prompt} [{hint}]: ").strip().lower()
        if not answer:
            return default
        if answer in {"j", "ja", "y", "yes"}:
            return True
        if answer in {"n", "nein", "no"}:
            return False
        print("Bitte 'j' oder 'n' eingeben.")


def ask_profile_id(default: str) -> str:
    """Fragt eine Profil-ID ab und validiert sie vollständig."""
    while True:
        answer = ask_line("Profil-ID", default or None)
        if not answer:
            print("Bitte eine Profil-ID eingeben.")
            continue
        if PROFILE_ID_PATTERN.fullmatch(answer):
            return answer
        print("Ungültige Profil-ID: erlaubt sind a–z, 0–9, '.', '_' und '-', Beginn mit Buchstabe/Ziffer.")


def ask_encoding() -> str:
    """Fragt eine gültige Python-Zeichensatzbezeichnung ab."""
    while True:
        candidate = ask_required("Python-Zeichensatz (z. B. utf-8, latin-1)")
        try:
            codecs.lookup(candidate)
        except LookupError:
            print(f"Unbekannter Zeichensatz: {candidate}")
            continue
        return candidate


def acquire_document() -> FetchedDocument:
    """Fragt eine URL ab und erlaubt Korrektur von URL oder Zeichensatz."""
    encoding: str | None = None
    last_url: str | None = None
    while True:
        raw = ask_required("Quell-URL", default=last_url)
        last_url = raw.split("#", 1)[0].strip()
        try:
            validate_url_basic(last_url)
            document = fetch_document(last_url, encoding)
        except (ValueError, OSError) as error:
            print(f"Abruf fehlgeschlagen: {error}")
            choice = input("[u]rl korrigieren, [z]eichensatz wählen, [a]bbruch: ").strip().lower()
            if choice.startswith("z"):
                encoding = ask_encoding()
            elif choice.startswith("a"):
                raise AssistantAbort()
            continue
        if document.final_url != last_url:
            print(f"Hinweis: Weiterleitung auf {sanitize_for_terminal(document.final_url)}")
        return document


def _retry_or_abort() -> bool:
    """Fragt, ob erneut eingegeben werden soll; True bedeutet Wiederholung."""
    action = input("[w]eitere Eingabe, [a]bbruch: ").strip().lower()
    return not action.startswith("a")


def _format_occurrence(occurrence: Occurrence) -> str:
    before = sanitize_for_terminal(occurrence.before)
    after = sanitize_for_terminal(occurrence.after)
    return f"[{occurrence.index}] …{before} «HIER» {after}…"


def choose_occurrence(occurrences: list[Occurrence]) -> Occurrence | None:
    """Lässt den Nutzer eine Fundstelle ausdrücklich auswählen."""
    total = len(occurrences)
    if total == 1:
        print(_format_occurrence(occurrences[0]))
        return occurrences[0] if ask_yes_no("Diese Fundstelle verwenden?", default=True) else None

    page = 0
    page_size = 10
    while True:
        start = page * page_size
        chunk = occurrences[start : start + page_size]
        for occurrence in chunk:
            print(_format_occurrence(occurrence))
        print(f"Fundstellen {start + 1}–{start + len(chunk)} von {total}.")
        answer = input("Nummer wählen, [w]eiter, [z]urück, [a]bbruch: ").strip().lower()
        if answer.startswith("a"):
            raise AssistantAbort()
        if answer.startswith("w") and start + page_size < total:
            page += 1
            continue
        if answer.startswith("z") and page > 0:
            page -= 1
            continue
        if answer.isdigit() and 1 <= int(answer) <= total:
            return occurrences[int(answer) - 1]
        print("Ungültige Auswahl.")


def _select_example(normalized: str) -> tuple[str, Occurrence]:
    """Fragt die Beispielversion ab und lässt die Fundstelle auswählen."""
    while True:
        example = ask_required("Welche Versionsangabe siehst du auf dieser Seite?")
        if "\n" in example or "\r" in example:
            print("Bitte ohne Zeilenumbrüche eingeben.")
            continue
        if len(example) > MAX_EXAMPLE_LENGTH:
            print(f"Die Beispielangabe darf höchstens {MAX_EXAMPLE_LENGTH} Zeichen lang sein.")
            continue
        occurrences = find_example_occurrences(normalized, example)
        if not occurrences:
            print("Diese Angabe kommt im abgerufenen Inhalt nicht vor.")
            action = input("[e]ingabe ändern, [u]rl/Zeichensatz ändern, [a]bbruch: ").strip().lower()
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
    """Führt Quellabruf, Beispielauswahl und Kandidatenwahl zu einem Ergebnis zusammen."""
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
    """Erzeugt Kandidaten, testet sie und lässt einen erfassenden Kandidaten wählen."""
    shape = analyze_example(example)
    if shape is None:
        print("Das Format der Beispielangabe wird von den festen Vorlagen nicht unterstützt.")
        print("Ein Expertenmodus ist nicht Teil dieses Umfangs; bitte die Beispielangabe korrigieren.")
        if _retry_or_abort():
            raise _RestartExample()
        raise AssistantAbort()

    expected = expected_value(example, shape)
    candidates: list[PatternCandidate] = []
    for candidate in suggest_version_patterns(example, None):
        if candidate.pattern not in {existing.pattern for existing in candidates}:
            candidates.append(candidate)

    print()
    print("Musterkandidaten:")
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
        print("Kein Kandidat erfasst die gewählte Fundstelle mit dem erwarteten Wert.")
        if _retry_or_abort():
            raise _RestartExample()
        raise AssistantAbort()

    if len(viable) == 1:
        print(f"Verwende: {viable[0][0].label}")
        return viable[0][0], viable[0][1], expected

    print()
    for number, (candidate, _) in enumerate(viable, start=1):
        print(f"  [{number}] {candidate.label}: {candidate.pattern}")
    while True:
        answer = ask_required("Nummer des gewünschten Kandidaten wählen")
        if answer.isdigit() and 1 <= int(answer) <= len(viable):
            candidate, result = viable[int(answer) - 1]
            return candidate, result, expected
        print("Ungültige Auswahl.")


def _resolve_export_target(profile: dict[str, Any], profiles_dir: Path) -> tuple[Path, bool, int | None]:
    """Bestimmt den Zielpfad und klärt eine eventuelle Kollision mit dem Nutzer."""
    while True:
        path = profile_file_path(profile["id"], profiles_dir)
        if not path.exists():
            return path, False, None
        if path.is_symlink() or not path.is_file():
            raise ValueError(f"Zielpfad ist keine reguläre Datei: {path}")
        action = input(
            f"Datei '{path.name}' existiert. [u]eberschreiben, [n]eue ID, [a]bbruch: "
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
    """Schreibt ein Profil atomar, ohne eine vorhandene Datei still zu überschreiben."""
    path = profile_file_path(profile["id"], profiles_dir)
    path.parent.mkdir(parents=True, exist_ok=True)

    exists = path.exists()
    if exists and not overwrite:
        raise ProfileExistsError(f"Profildatei existiert bereits: {path}")
    if expected_exists is not None and exists != expected_exists:
        raise ProfileChangedError(f"Ziel wurde zwischenzeitlich geändert: {path}")
    if expected_mtime is not None:
        current = path.stat().st_mtime_ns if exists else None
        if current != expected_mtime:
            raise ProfileChangedError(f"Ziel wurde zwischenzeitlich geändert: {path}")

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

    load_profile(profile["id"], profiles_dir, context=f"Profil '{profile['id']}'")
    return path


def run_profile_assistant(profiles_dir: Path) -> int:
    """Führt den dialoggeführten Profilassistenten aus und exportiert ein Profil."""
    print()
    print("firmAware Profilassistent")
    print("Erstellt regelbasiert ein Text-/HTML-Geräteprofil. Keine KI, keine externen Dienste.")
    print("Lokaler CLI-Abruf; nicht als öffentlicher URL-Abrufdienst gedacht.")
    print()

    manufacturer = ask_required("Hersteller")
    model = ask_required("Modell")
    default_name = f"{manufacturer} {model}{ASSISTANT_NAME_SUFFIX}"
    name = ask_required("Anzeigename", default=default_name)
    profile_id = ask_profile_id(suggest_profile_id(manufacturer, model))

    document, example, occurrence, candidate, result, expected = _gather_profile_inputs()
    normalized = html.unescape(document.text)
    occurrences = find_example_occurrences(normalized, example)

    configured_url = document.requested_url
    if document.final_url != configured_url and ask_yes_no(
        "Weiterleitungsziel als Quell-URL übernehmen?", default=False
    ):
        configured_url = document.final_url

    print()
    print("Vorschau")
    print(f"  Profil-ID:      {profile_id}")
    print(f"  Name:           {name}")
    print(f"  Hersteller:     {manufacturer}")
    print(f"  Modell:         {model}")
    print(f"  Quell-URL:      {sanitize_for_terminal(configured_url)}")
    if document.final_url != configured_url:
        print(f"  Finale URL:     {sanitize_for_terminal(document.final_url)}")
    print(f"  Zeichensatz:    {document.charset}")
    print("  Bereich:        gesamte Quelle")
    print(f"  Erwartet:       {sanitize_for_terminal(expected)}")
    print(f"  Versionen:      {', '.join(sanitize_for_terminal(v) for v in result.versions)}")
    print(f"  Fundstellen:    {len(occurrences)} (eindeutige Werte: {len(set(result.versions))})")
    print(f"  Regex:          {candidate.pattern}")

    profile: dict[str, Any] = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "id": profile_id,
        "name": name,
        "manufacturer": manufacturer,
        "model": model,
        "source_type": SOURCE_TEXT,
        "source_url": configured_url,
        "version_pattern": candidate.pattern,
    }
    if document.requested_encoding:
        profile["encoding"] = document.requested_encoding

    try:
        validate_profile(profile, profile_id, f"Profil '{profile_id}'")
    except ValueError as error:
        print(f"Interner Validierungsfehler: {error}", file=sys.stderr)
        return 1

    snapshot_time = current_timestamp()
    if ask_yes_no("Vor dem Speichern erneut abrufen und testen?", default=False):
        try:
            fresh = fetch_document(document.requested_url, document.requested_encoding)
            fresh_result = test_pattern(html.unescape(fresh.text), candidate.pattern)
        except (ValueError, OSError) as error:
            fresh_result = TestResult(False, str(error))
        if not (fresh_result.ok and expected in fresh_result.versions):
            print("Erneuter Test fehlgeschlagen; Export blockiert.")
            if not ask_yes_no("Stattdessen den zuvor getesteten Snapshot verwenden?", default=False):
                return 1
            print(f"Achtung: Snapshot vom {format_timestamp(snapshot_time)}.")

    if not ask_yes_no("Profil speichern?", default=False):
        print("Nicht gespeichert.", file=sys.stderr)
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
        print("Abgebrochen; es wurde keine Profildatei erstellt.", file=sys.stderr)
        return 1
    except (ValueError, OSError) as error:
        print(f"Speicherfehler: {error}", file=sys.stderr)
        return 1

    print()
    print(f"Profil gespeichert: {written}")
    print("Auf der aktuellen Antwort erfolgreich getestet.")
    print("Nicht geprüft: zukünftige Seitenänderungen und zukünftige Versionsformate.")
    print()
    print("Einbindung in devices.json (Beispiel):")
    print(json.dumps({"id": "mein-geraet", "profile": profile["id"]}, ensure_ascii=False, indent=2))
    print("Bei einem bestehenden Gerät die lokale ID beibehalten; devices.json und state.json werden nicht verändert.")
    return 0


# --- Einstiegspunkt --------------------------------------------------------


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Liest die CLI-Argumente; Standardpfade liegen neben dem Skript."""
    base = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=DESCRIPTION)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--check",
        action="store_true",
        help="Quellen prüfen und Zustand aktualisieren (Standard)",
    )
    mode.add_argument(
        "--status",
        action="store_true",
        help="Nur gespeicherten Zustand kompakt anzeigen, keine Netzabfrage",
    )
    mode.add_argument(
        "--create-profile",
        action="store_true",
        help="Interaktiv ein Geräteprofil erstellen und exportieren",
    )
    parser.add_argument("--devices", type=Path, default=base / "devices.json")
    parser.add_argument("--state", type=Path, default=base / "state.json")
    parser.add_argument(
        "--profiles-dir",
        type=Path,
        default=base / "profiles",
        metavar="PATH",
        help="Verzeichnis mit Geräteprofilen (Standard: ./profiles neben dem Skript)",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Führt je nach Argument die Prüfung oder die Statusausgabe aus."""
    args = parse_args(argv)

    if args.create_profile:
        try:
            return run_profile_assistant(args.profiles_dir)
        except KeyboardInterrupt:
            print("\nAbgebrochen (Ctrl+C).", file=sys.stderr)
            return 130
        except EOFError:
            print("Abbruch: Eingabeende erreicht.", file=sys.stderr)
            return 1
        except AssistantAbort:
            print("Abgebrochen; es wurde keine Profildatei erstellt.", file=sys.stderr)
            return 1

    try:
        config = read_json(args.devices)
        state = load_state(args.state)
        raw_devices = validate_config(config, state)
        devices = resolve_devices(raw_devices, args.profiles_dir)
    except (OSError, ValueError, KeyError, TypeError) as error:
        print(f"Konfigurationsfehler: {error}", file=sys.stderr)
        return 1

    if args.status:
        print_status(devices, state)
        return 0

    failed = False
    width = name_column_width(devices)
    for device in devices:
        # Bewusst kein Kurzschluss: auch nach einem Fehler werden alle Geräte geprüft.
        if not process_device(device, state, width):
            failed = True

    try:
        save_state(args.state, state)
    except OSError as error:
        print(f"Speicherfehler: {error}", file=sys.stderr)
        return 1
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
