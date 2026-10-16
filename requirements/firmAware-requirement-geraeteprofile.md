# Requirement: Wiederverwendbare Geräteprofile für firmAware

## 1. Ziel

firmAware soll Gerätequellen über wiederverwendbare Geräteprofile konfigurieren können.

- `devices.json` beschreibt die persönliche Geräteauswahl.
- JSON-Dateien in `profiles/` beschreiben Abruf und Versionsextraktion.
- Bestehende direkte Gerätekonfigurationen bleiben unterstützt.
- Die vorhandene Abruf-, Extraktions- und Vergleichslogik bleibt verwendbar.

## 2. Umfang und Abgrenzung

### Bestandteil

- Lokale JSON-Geräteprofile laden und validieren.
- Profilverweise aus `devices.json` auflösen.
- Profile und lokale Geräteangaben zusammenführen.
- Alle Konfigurationen vor Netzwerkprüfungen validieren.
- Geräte-IDs und bestehenden Zustand erhalten.
- Tests und Dokumentation ergänzen.
- Einträge nach Kategorie clustern (Firmware, Software, Operating System).
- Einzelne Einträge mit `--device ID` prüfen oder anzeigen.

### Nicht Bestandteil

- Änderungen an Versionsvergleich oder Versionssortierung.
- Strukturierte Releases, Veröffentlichungsdatum und Release Notes.
- Herstellerspezifische Adapter.
- Anmeldung, Cookies oder Browserautomatisierung.
- Behebung des bisherigen COROS-HTTP-403-Problems.
- Automatische Profilupdates, Benachrichtigungen oder Webinterface.

Es dürfen keine zusätzlichen Python-Abhängigkeiten eingeführt werden. Ausschließlich die Standardbibliothek verwenden.

## 3. Dateistruktur

```text
firmAware/
├── firmaware.py
├── devices.json
├── state.json
├── specification.md
├── profiles/
│   └── coros.dura.json
└── tests/
```

Der bestehende Name der Python-Einstiegsdatei bleibt unverändert. Das Standard-Profilverzeichnis liegt neben dieser Datei und hängt nicht vom aktuellen Arbeitsverzeichnis ab.

## 4. Geräteprofilformat

Beispiel `profiles/coros.dura.json`:

```json
{
  "schema_version": 2,
  "id": "coros.dura",
  "name": "COROS DURA – Firmware",
  "manufacturer": "COROS",
  "model": "DURA",
  "category": "firmware",
  "source_type": "text",
  "source_url": "https://support.coros.com/hc/en-us/articles/32924317141652-COROS-DURA-Release-Notes",
  "version_pattern": "\\bV\\s*([0-9]+(?:\\.[0-9]+){2,3})\\b"
}
```

### Allgemeine Felder

| Feld | Pflicht | Anforderung |
|---|---|---|
| `schema_version` | Ja | Ganzzahl, ausschließlich `2`; boolesche Werte unzulässig |
| `id` | Ja | Gültige Profil-ID; entspricht Dateiname ohne `.json` |
| `name` | Ja | Nicht leere Zeichenkette |
| `category` | Ja | `firmware`, `software` oder `operating_system` |
| `source_type` | Ja | `text`, `json` oder `json_value` |
| `source_url` | Ja | Nicht leere HTTP-/HTTPS-URL mit Host |
| `encoding` | Nein | Nicht leere, von Python unterstützte Zeichensatzbezeichnung |
| `manufacturer` | Nein | Nicht leere Zeichenkette |
| `model` | Nein | Nicht leere Zeichenkette |

Zusätzliche Metadaten dürfen erhalten bleiben. Sie dürfen keine vorgeschriebenen Validierungen ersetzen und das Abrufverhalten nicht verändern.

### Quelltypspezifische Pflichtfelder

| Quelltyp | Pflichtfelder |
|---|---|
| `text` | `version_pattern`: nicht leerer, gültiger regulärer Ausdruck |
| `json` | `items_key`, `version_key`: nicht leere Zeichenketten |
| `json_value` | `value_path`: nicht leere Liste nicht leerer Zeichenketten |

Für `version_pattern` gilt:

- Keine Klammergruppe: vollständiger Treffer ist die Version.
- Eine Klammergruppe: deren Inhalt ist die Version.
- Mehr als eine Klammergruppe: Konfigurationsfehler.

Regex-Gültigkeit und Gruppenanzahl müssen vor einem Netzwerkabruf geprüft werden.

## 5. Persönliche Geräteauswahl

```json
{
  "devices": [
    {
      "id": "coros-dura",
      "profile": "coros.dura"
    }
  ]
}
```

Profilbasierte Einträge dürfen ausschließlich folgende Felder enthalten:

| Feld | Pflicht | Bedeutung |
|---|---|---|
| `id` | Ja | Lokale Geräte-ID und Zustandsschlüssel |
| `profile` | Ja | Referenz auf ein Geräteprofil |
| `name` | Nein | Lokaler Anzeigename, überschreibt Profilnamen |

Beispiel mit lokalem Namen:

```json
{
  "devices": [
    {
      "id": "coros-dura",
      "profile": "coros.dura",
      "name": "Mein Fahrradcomputer"
    }
  ]
}
```

Technische Felder wie `source_url`, `source_type`, `encoding` oder `version_pattern` dürfen nicht zusätzlich angegeben werden. Solche Einträge sind Konfigurationsfehler. Kein stilles Überschreiben oder Ignorieren. Individuelle technische Einstellungen bleiben über direkte Konfigurationen möglich.

## 6. Rückwärtskompatibilität

Die bisherige direkte Konfiguration bleibt unterstützt:

```json
{
  "devices": [
    {
      "id": "coros-dura",
      "name": "COROS DURA – Firmware",
      "source_type": "text",
      "source_url": "https://support.coros.com/hc/en-us/articles/32924317141652-COROS-DURA-Release-Notes",
      "version_pattern": "\\bV\\s*([0-9]+(?:\\.[0-9]+){2,3})\\b"
    }
  ]
}
```

Direkte und profilbasierte Einträge dürfen gemischt werden.

Für direkte Einträge gilt:

- `id` ist eine nicht leere Zeichenkette.
- `name` bleibt optional; ohne Namen wird die lokale ID angezeigt.
- Technische Felder werden nach denselben Regeln wie Profile validiert.
- `schema_version` ist nicht erforderlich.
- Ein fehlendes Profilverzeichnis beeinträchtigt direkte Einträge nicht.

## 7. Profilauflösung

Vor Prüfungen und Statusausgabe alle Einträge auflösen und validieren:

1. Lokale Geräte-ID prüfen.
2. Profil-ID prüfen.
3. Profildatei bestimmen.
4. UTF-8-JSON laden.
5. Profilstruktur, Schema-Version und Pflichtfelder validieren.
6. Übereinstimmung der Profil-ID mit Dateiname prüfen.
7. Flache Gerätekonfiguration erstellen.
8. Lokale Geräte-ID übernehmen.
9. Optionalen lokalen Namen übernehmen.

Beispiel der aufgelösten Konfiguration:

```json
{
  "schema_version": 2,
  "id": "coros-dura",
  "profile": "coros.dura",
  "name": "COROS DURA – Firmware",
  "manufacturer": "COROS",
  "model": "DURA",
  "category": "firmware",
  "source_type": "text",
  "source_url": "https://support.coros.com/hc/en-us/articles/32924317141652-COROS-DURA-Release-Notes",
  "version_pattern": "\\bV\\s*([0-9]+(?:\\.[0-9]+){2,3})\\b"
}
```

Das Ergebnis muss mit `fetch_source()`, `extract_versions()`, `process_device()` und `print_status()` verwendbar sein. Eingelesene Geräte- und Profilobjekte dürfen beim Zusammenführen nicht verändert werden.

## 8. Identität und Zustand

- Profil-ID: gemeinsam verwendete Quellenbeschreibung.
- Lokale Geräte-ID: Schlüssel in `state.json`.

Weiterhin speichern unter:

```python
state["devices"][device["id"]]
```

Anforderungen:

- Lokale Geräte-ID niemals durch Profil-ID ersetzen.
- Bestehender Zustand bleibt ohne Migration verwendbar.
- Bei Umstellung auf Profile erhält dieselbe lokale ID denselben gespeicherten Versionsstand.
- Mehrere lokale Geräte dürfen dasselbe Profil referenzieren.
- Lokale Geräte-IDs müssen eindeutig sein.
- Das Zustandsformat bleibt unverändert.

## 9. Profil-IDs und sichere Dateipfade

Profil-IDs müssen vollständig folgendem Muster entsprechen:

```regex
[a-z0-9][a-z0-9._-]*
```

Unzulässig sind leere IDs, `/`, `\`, absolute Pfade, Leerzeichen, Großbuchstaben und Pfadangaben wie `../coros.dura`.

Profildateien werden ausschließlich so bestimmt:

```text
<profiles_dir>/<profile_id>.json
```

Der aufgelöste Dateipfad muss innerhalb des Profilverzeichnisses liegen. Symbolische Links dürfen keinen Zugriff außerhalb dieses Verzeichnisses ermöglichen.

Profil-IDs sind keine Importpfade. Keine dynamischen Python-Imports oder Programmausführung anhand dieser IDs.

## 10. CLI-Erweiterung

Bestehende Argumente erhalten:

```text
--check
--status
--devices
--state
```

Neues Argument:

```text
--profiles-dir PATH
```

- Standard: `profiles` neben der Python-Einstiegsdatei.
- Explizite relative Pfade: relativ zum aktuellen Arbeitsverzeichnis.
- Gilt für `--check` und `--status`.
- CLI-Hilfe ergänzen.

```bash
python firmaware.py --devices ./devices.json --profiles-dir ./profiles --check
```

`--status` darf keine Netzwerkabfrage ausführen. Lokales Laden und Validieren der Profile ist erforderlich.

## 11. Fehlerbehandlung

Alle Konfigurationsfehler vor Netzwerkprüfungen erkennen.

Bei Konfigurationsfehlern:

- Verständliche Meldung auf `stderr`.
- Exitcode `1`.
- Keine Netzwerkabfrage.
- Keine Änderung von `state.json`.
- Kein unkontrollierter Traceback.

Mindestens behandeln:

- Fehlende oder nicht lesbare Profildatei.
- Ungültiges JSON oder Profil ist kein Objekt.
- Nicht unterstützte Schema-Version.
- Ungültige oder abweichende Profil-ID.
- Fehlende/falsch typisierte Pflichtfelder.
- Unbekannter Quelltyp.
- Ungültiger Regex oder mehr als eine Klammergruppe.
- Unbekannter Zeichensatz.
- Ungültige Quell-URL.
- Unerlaubte Felder im profilbasierten Eintrag.
- Doppelte lokale Geräte-IDs.
- Dateipfad außerhalb des Profilverzeichnisses.

Fehlermeldungen sollen lokale ID, Profil-ID und gegebenenfalls das Feld nennen:

```text
Konfigurationsfehler: Gerät 'coros-dura', Profil 'coros.dura':
Pflichtfeld 'version_pattern' fehlt
```

Abruffehler bleiben gerätebezogen. Sie dürfen die Prüfung anderer korrekt konfigurierter Geräte nicht verhindern.

## 12. Interne Struktur

Folgende Funktionen oder funktional gleichwertige Alternativen vorsehen:

```python
def validate_source_config(source: Mapping[str, Any], context: str) -> None:
    ...

def load_profile(profile_id: str, profiles_dir: Path) -> dict[str, Any]:
    ...

def resolve_device(device: Mapping[str, Any], profiles_dir: Path) -> dict[str, Any]:
    ...

def resolve_devices(
    devices: Sequence[Mapping[str, Any]],
    profiles_dir: Path,
) -> list[dict[str, Any]]:
    ...
```

Reihenfolge in `main()`:

```text
CLI lesen
→ devices.json und state.json laden
→ Grundstruktur und lokale Geräte-IDs validieren
→ alle Profile auflösen
→ alle technischen Quellkonfigurationen validieren
→ Status ausgeben oder Geräte prüfen
→ bei Prüfung Zustand wie bisher speichern
```

## 13. Tests

`unittest`, bei Bedarf `unittest.mock`, und `tempfile` verwenden. Keine realen Netzwerkzugriffe erforderlich.

| Testfall | Erwartung |
|---|---|
| Gültiges Textprofil | Technische Felder korrekt übernommen |
| Gültiges JSON-Profil | Vorhandener JSON-Extraktor verwendbar |
| Gültiges JSON-Value-Profil | Vorhandener Pfadextraktor verwendbar |
| Lokale ID unterscheidet sich von Profil-ID | Lokale ID erhalten |
| Lokaler Name vorhanden | Überschreibt Profilnamen |
| Kein lokaler Name | Profilname verwendet |
| Direkter Eintrag | Weiterhin unterstützt |
| Gemischte Einträge | Beide Formate aufgelöst |
| Zwei Geräte mit demselben Profil | Jeweils eigene lokale ID |
| Bestehender Zustand | Bekannte Versionen weiterverwendet |
| Fehlende Datei/ungültiges JSON | Konfigurationsfehler |
| Ungültige Profil-ID/Schema-Version | Konfigurationsfehler |
| Abweichende Profil-ID | Konfigurationsfehler |
| Ungültige Quellenfelder | Fehler vor Abruf |
| Technische Überschreibung | Konfigurationsfehler |
| Pfadmanipulation/Symlink außerhalb | Zugriff abgelehnt |
| `--profiles-dir` | Gewähltes Verzeichnis verwendet |
| `--status` mit Profilen | Keine Netzwerkabfrage |
| Ein ungültiges Profil | Keine Prüfung und keine Zustandsänderung |
| Direkte Konfiguration ohne Profilverzeichnis | Funktioniert |

Zusätzlicher lokaler Extraktionstest für COROS:

```text
V 3.0910.0
V 3.0808.0
```

Erwartung:

```python
["3.0808.0", "3.0910.0"]
```

Dieser Test bestätigt die Extraktion, nicht die Erreichbarkeit der Website.

## 14. Dokumentation

`specification.md` ergänzen um:

- Trennung von Geräteauswahl und Profil.
- Dateiformat und Pflichtfelder.
- Quelltypen.
- `--profiles-dir`.
- Beispiele für profilbasierte und direkte Konfiguration.
- Unterschied lokaler Geräte-ID und Profil-ID.
- Lokale ID bei Umstellung beibehalten.
- Keine Zugangsdaten oder Laufzeitzustände in Profilen.

`profiles/coros.dura.json` mit obigem Inhalt bereitstellen. Nicht als Lösung des bisherigen HTTP-403-Problems dokumentieren.

## 15. Abnahmekriterien

1. COROS DURA lässt sich ausschließlich mit `id` und `profile` konfigurieren.
2. Quelle und Regex werden aus `profiles/coros.dura.json` geladen.
3. Bestehende Extraktionsfunktionen bleiben verwendbar.
4. Lokale ID und vorhandener Zustand bleiben erhalten.
5. Direkte Konfigurationen funktionieren weiterhin.
6. `--status` funktioniert mit Profilen ohne Netzwerkzugriff.
7. Konfigurationsfehler werden vor Netzwerkprüfungen erkannt.
8. Alle geforderten Tests bestehen.
9. Beispielprofil und Dokumentation sind vorhanden.
10. Keine zusätzlichen Python-Abhängigkeiten.
11. Profile haben `schema_version: 2` und Pflichtfeld `category` (`firmware`, `software`, `operating_system`).
12. Einträge werden in `--status` und `--check` nach Kategorie gruppiert ausgegeben.
13. Ein einzelner Eintrag kann mit `--device ID` geprüft oder angezeigt werden.
14. Die ID-Spalte wird im `--status`-Output angezeigt.
