# firmAware – Spezifikation

Stand: 13. Oktober 2026  
Version: 0.3 (CLI-Prototyp mit Geräteprofilen und regelbasiertem Profilassistenten)

## 1. Ziel und Umfang

firmAware prüft öffentliche Firmwarequellen für konfigurierte Geräte und meldet bislang unbekannte Versionsbezeichnungen im Terminal. Die Anwendung wird lokal vom Nutzer betrieben und benötigt keinen eigenen Cloud-Dienst.

Der aktuelle Prototyp erkennt Änderungen an veröffentlichten Versionslisten. Er prüft nicht die installierte Firmware eines Geräts und bestätigt nicht die Verfügbarkeit eines Updates für ein konkretes Gerät.

Diese Spezifikation beschreibt den aktuellen Stand von `firmaware.py`, `devices.json`, `profiles/*.json`, `state.json` und `tests/test_firmaware.py`. Geräte lassen sich direkt oder über wiederverwendbare Geräteprofile konfigurieren. Zukunftsideen sind getrennt in Abschnitt 12 aufgeführt und nicht Bestandteil der Implementierung.

## 2. Technische Grundlage

- Python 3; ausschließlich Standardbibliothek, keine pip-Abhängigkeiten.
- CLI ohne Weboberfläche oder dauerhaft laufenden Hintergrunddienst.
- HTTP-Abfragen über `urllib.request`.
- JSON-Verarbeitung über `json`, reguläre Ausdrücke über `re`.
- Automatisierte Tests über `unittest` (Standardbibliothek), ohne Netzzugang.
- Speicherung in lokalen JSON-Dateien, keine Datenbank.
- Keine Firmwareinstallation und keine Downloads von Firmwaredateien.
- Internetzugang zu den konfigurierten Quellen erforderlich.

## 3. Projektdateien und Aufruf

| Datei | Zweck |
|---|---|
| `firmaware.py` | Abruf, Extraktion, Vergleich, Profilauflösung und Speicherung |
| `devices.json` | Persönliche Geräteauswahl (direkt oder über Profile) |
| `profiles/*.json` | Wiederverwendbare Geräteprofile (Abruf und Versionsextraktion) |
| `state.json` | Bekannte Versionen und letzte erfolgreiche Prüfungen |
| `tests/test_firmaware.py` | Automatisierte Tests (nur Standardbibliothek) |
| `specification.md` | Projektbegleitende Beschreibung |

Standardaufruf (entspricht `--check`):

```bash
python3 firmaware.py
python3 firmaware.py --check
```

Gespeicherten Zustand anzeigen, ohne Netzabfrage:

```bash
python3 firmaware.py --status
```

Interaktiv ein Geräteprofil erstellen:

```bash
python3 firmaware.py --create-profile --profiles-dir ./profiles
```

`--check`, `--status` und `--create-profile` schließen sich gegenseitig aus. `--profiles-dir PATH` gilt für alle Modi und ist im Erstellungsmodus das Exportverzeichnis. Der Erstellungsmodus verzweigt vor dem Laden von `devices.json` und `state.json`; beide Dateien müssen dafür nicht existieren.

Abweichende Dateipfade:

```bash
python3 firmaware.py --devices /pfad/devices.json --state /pfad/state.json
```

Abweichendes Profilverzeichnis:

```bash
python3 firmaware.py --devices ./devices.json --profiles-dir ./profiles --check
```

Ohne Argumente werden beide JSON-Dateien relativ zum Verzeichnis der Python-Datei gesucht, nicht relativ zum aktuellen Terminalverzeichnis. Ebenso liegt das Standard-Profilverzeichnis als `profiles` neben der Python-Datei und ist unabhängig vom aktuellen Arbeitsverzeichnis. Ein explizit übergebenes relatives `--profiles-dir` wird dagegen relativ zum aktuellen Arbeitsverzeichnis aufgelöst. Existiert die Zustandsdatei nicht, wird mit einem leeren Zustand begonnen. Konfiguration und Zustand werden als UTF-8 gelesen und geschrieben.

Automatisierte Tests:

```bash
python3 -m unittest discover -s tests -t .
```

Die Tests nutzen `unittest` und benötigen keinen Netzzugang; die Quellenabfrage wird durch Testdoubles ersetzt.

## 4. Konfiguration: Geräteauswahl und Geräteprofile

Die Konfiguration ist in zwei Ebenen getrennt:

- `devices.json` beschreibt die **persönliche Geräteauswahl** und enthält je Gerät eine lokale ID sowie optional einen Anzeigenamen.
- `profiles/*.json` beschreiben **wiederverwendbar** Abruf und Versionsextraktion einer Quelle.

Ein Gerät wird entweder direkt (mit allen technischen Feldern in `devices.json`) oder profilbasiert (über eine Profilreferenz) konfiguriert. Beide Formen dürfen gemischt werden. Das Wurzelobjekt enthält jeweils eine Liste `devices`; jede lokale Geräte-ID muss eindeutig sein. Die Implementierung unterstützt die Quelltypen `text`, `json` und `json_value`.

### 4.1 Gemeinsame Felder in devices.json

| Feld | Pflicht | Bedeutung |
|---|---|---|
| `id` | Ja | Lokale Geräte-ID; eindeutiger Zustandsschlüssel |
| `name` | Nein | Lokaler Anzeigename; überschreibt bei Profilen den Profilnamen, sonst wird die ID bzw. der Profilname angezeigt |
| `profile` | Nein | Referenz auf ein Geräteprofil; vorhanden = profilbasierter Eintrag |
| `source_type` | Nur direkt | `text`, `json` oder `json_value` |
| `source_url` | Nur direkt | URL der abzufragenden Quelle |

Ist `profile` gesetzt, dürfen **ausschließlich** die Felder `id`, `profile` und `name` vorkommen. Technische Felder wie `source_url`, `source_type`, `encoding` oder `version_pattern` sind dann ein Konfigurationsfehler; es gibt kein stilles Überschreiben oder Ignorieren. Individuelle technische Einstellungen bleiben über direkte Einträge möglich.

Weitere beschreibende Felder wie Hersteller, Modell oder Hardwareversion werden nicht ausgewertet.

### 4.2 Textquelle: TP-Link RE200 V4 EU

```json
{
  "id": "tplink-re200-v4-eu",
  "name": "TP-Link RE200 V4 EU",
  "source_type": "text",
  "source_url": "https://www.tp-link.com/de/support/download/re200/v4/",
  "version_pattern": "(RE200\\(EU\\)_V4_[0-9]+)"
}
```

`version_pattern` ist ein Python-kompatibler regulärer Ausdruck. Backslashes müssen in JSON maskiert sein. Das Muster darf keine oder genau eine Capture-Gruppe enthalten:

- Ohne Capture-Gruppe wird der vollständige Treffer gespeichert.
- Mit einer Capture-Gruppe wird deren Inhalt gespeichert.
- Mehr als eine Capture-Gruppe führt zu einem Prüffehler.

Der abgerufene Text wird mit `html.unescape` verarbeitet. Es gibt keine HTML-zu-Text-Konvertierung, DOM-Auswertung oder JavaScript-Ausführung. Ein Muster muss deshalb im direkt erhaltenen Inhalt funktionieren.

Beispiel einer erkannten Bezeichnung: `RE200(EU)_V4_200922`.

### 4.3 JSON-Quelle: NanoStation M5 XM

```json
{
  "id": "nanostation-m5-xm",
  "name": "NanoStation M5 XM",
  "source_type": "json",
  "source_url": "https://download.svc.ui.com/v1/downloads/products/slugs/nsm5-xm",
  "items_key": "downloads",
  "version_key": "version"
}
```

Die Antwort muss ein JSON-Objekt mit einer Liste unter `items_key` enthalten. Jedes Listenelement muss ein Objekt sein. Das Feld unter `version_key` muss eine nichtleere Zeichenfolge enthalten; führende und nachfolgende Leerzeichen werden entfernt.

Einträge mit `enabled: false` werden übersprungen. Bei fehlendem `enabled` wird der Eintrag berücksichtigt. Ein fehlerhafter berücksichtigter Eintrag führt zum Fehler für die gesamte Geräteprüfung; es wird keine Teilliste gespeichert.

Die Schlüssel sind direkte Feldnamen, keine JSONPath-Ausdrücke. Verschachtelte Pfade werden nicht unterstützt.

Beispielantwort:

```json
{
  "downloads": [
    {
      "version": "6.3.26",
      "date_published": "2026-09-11",
      "enabled": true
    }
  ]
}
```

Nur `version` und der optionale Aktivierungsstatus werden derzeit ausgewertet. Veröffentlichungsdatum, Release Notes, Downloadlink, Produktlisten und Kanäle werden nicht verarbeitet. Die produktbezogene URL liefert die Zuordnung zum XM-Geräteprofil; das Programm überprüft diese Zuordnung nicht zusätzlich.

### 4.4 JSON-Einzelwert: Shelly 1 Gen1

```json
{
  "id": "shelly-1-gen1-stable",
  "name": "Shelly 1 Gen1 – stabile Firmware",
  "source_type": "json_value",
  "source_url": "https://api.shelly.cloud/files/firmware",
  "value_path": ["data", "SHSW-1", "version"]
}
```

Die Antwort muss ein JSON-Objekt sein. `value_path` ist eine Liste von Schlüsseln, die der Reihe nach vom Wurzelobjekt aus aufgelöst werden. Der daraus erhaltene Wert muss eine nichtleere Zeichenfolge sein; führende und nachfolgende Leerzeichen werden entfernt. Genau dieser eine Wert bildet die erkannte Version.

Die Schlüssel sind direkte Feldnamen, keine JSONPath-Ausdrücke. Ein fehlender Pfad oder ein Wert, der keine Zeichenfolge ist, führt zum Prüffehler. Anders als bei `json` gibt es keine Liste, kein `enabled` und keine Zusammenführung mehrerer Einträge.

Beispielauszug:

```json
{
  "data": {
    "SHSW-1": {
      "version": "20230913-112003/v1.14.0-gcb84623"
    }
  }
}
```

### 4.5 Vollständige Struktur

Profilbasierte und direkte Einträge dürfen gemischt werden:

```json
{
  "devices": [
    {
      "id": "coros-dura",
      "profile": "coros.dura"
    },
    {
      "id": "tplink-re200-v4-eu",
      "profile": "tplink.re200-v4-eu",
      "name": "Mein Repeater"
    },
    {
      "id": "nanostation-m5-xm",
      "name": "NanoStation M5 XM",
      "source_type": "json",
      "source_url": "https://download.svc.ui.com/v1/downloads/products/slugs/nsm5-xm",
      "items_key": "downloads",
      "version_key": "version"
    }
  ]
}
```

Das zweite Gerät überschreibt den Profilnamen lokal, das dritte ist ein direkter Eintrag.

### 4.6 Geräteprofile: Format und Pflichtfelder

Beispiel `profiles/coros.dura.json`:

```json
{
  "schema_version": 1,
  "id": "coros.dura",
  "name": "COROS DURA – Firmware",
  "manufacturer": "COROS",
  "model": "DURA",
  "source_type": "text",
  "source_url": "https://support.coros.com/hc/en-us/articles/32924317141652-COROS-DURA-Release-Notes",
  "version_pattern": "\\bV\\s*([0-9]+(?:\\.[0-9]+){2,3})\\b"
}
```

| Feld | Pflicht | Anforderung |
|---|---|---|
| `schema_version` | Ja | Ganzzahl, ausschließlich `1`; boolesche Werte unzulässig |
| `id` | Ja | Gültige Profil-ID; entspricht dem Dateinamen ohne `.json` |
| `name` | Ja | Nicht leere Zeichenkette |
| `source_type` | Ja | `text`, `json` oder `json_value` |
| `source_url` | Ja | Nicht leere HTTP-/HTTPS-URL mit Host |
| `encoding` | Nein | Nicht leere, von Python unterstützte Zeichensatzbezeichnung |
| `manufacturer` | Nein | Nicht leere Zeichenkette |
| `model` | Nein | Nicht leere Zeichenkette |

Quelltypspezifische Pflichtfelder:

| Quelltyp | Pflichtfelder |
|---|---|
| `text` | `version_pattern`: nicht leerer, gültiger regulärer Ausdruck |
| `json` | `items_key`, `version_key`: nicht leere Zeichenketten |
| `json_value` | `value_path`: nicht leere Liste nicht leerer Zeichenketten |

Für `version_pattern` gilt: ohne Klammergruppe ist der vollständige Treffer die Version, mit genau einer Klammergruppe deren Inhalt; mehr als eine Klammergruppe ist ein Konfigurationsfehler. Regex-Gültigkeit und Gruppenanzahl werden vor einem Netzwerkabruf geprüft.

Zusätzliche Metadaten dürfen erhalten bleiben. Sie dürfen keine vorgeschriebenen Validierungen ersetzen und das Abrufverhalten nicht verändern. Zugangsdaten und Laufzeitzustände gehören nicht in Profile.

### 4.7 Profil-IDs und sichere Dateipfade

Profil-IDs müssen vollständig dem Muster `[a-z0-9][a-z0-9._-]*` entsprechen. Unzulässig sind leere IDs, `/`, `\`, absolute Pfade, Leerzeichen, Großbuchstaben und Pfadangaben wie `../coros.dura`.

Eine Profildatei wird ausschließlich als `<profiles_dir>/<profile_id>.json` bestimmt. Der aufgelöste Dateipfad muss innerhalb des Profilverzeichnisses liegen; symbolische Links dürfen keinen Zugriff außerhalb dieses Verzeichnisses ermöglichen. Profil-IDs sind keine Importpfade; es gibt keine dynamischen Python-Imports oder Programmausführung anhand dieser IDs.

### 4.8 Profilauflösung und Zusammenführen

Vor jeder Prüfung und Statusausgabe werden alle Einträge aufgelöst und validiert:

1. Lokale Geräte-ID prüfen.
2. Profil-ID prüfen.
3. Profildatei bestimmen.
4. UTF-8-JSON laden.
5. Profilstruktur, Schema-Version und Pflichtfelder validieren.
6. Übereinstimmung der Profil-ID mit dem Dateinamen prüfen.
7. Flache Gerätekonfiguration erstellen.
8. Lokale Geräte-ID übernehmen.
9. Optionalen lokalen Namen übernehmen.

Beispiel der aufgelösten Konfiguration:

```json
{
  "schema_version": 1,
  "id": "coros-dura",
  "profile": "coros.dura",
  "name": "COROS DURA – Firmware",
  "manufacturer": "COROS",
  "model": "DURA",
  "source_type": "text",
  "source_url": "https://support.coros.com/hc/en-us/articles/32924317141652-COROS-DURA-Release-Notes",
  "version_pattern": "\\bV\\s*([0-9]+(?:\\.[0-9]+){2,3})\\b"
}
```

Das Ergebnis ist mit `fetch_source()`, `extract_versions()`, `process_device()` und `print_status()` verwendbar. Eingelesene Geräte- und Profilobjekte werden beim Zusammenführen nicht verändert.

### 4.9 Direkte Konfiguration (Rückwärtskompatibilität)

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

Für direkte Einträge gilt: `id` ist eine nicht leere Zeichenkette; `name` bleibt optional und ersatzweise wird die lokale ID angezeigt; technische Felder werden nach denselben Regeln wie Profile validiert; `schema_version` ist nicht erforderlich; ein fehlendes Profilverzeichnis beeinträchtigt direkte Einträge nicht.

## 5. Zustand: state.json

Initialzustand:

```json
{
  "devices": {}
}
```

Beispiel nach erfolgreicher Prüfung:

```json
{
  "devices": {
    "nanostation-m5-xm": {
      "last_checked": "2026-10-11T18:00:00+02:00",
      "known_versions": ["6.3.24", "6.3.26"]
    },
    "shelly-1-gen1-stable": {
      "last_checked": "2026-10-11T18:00:05+02:00",
      "known_versions": ["20230913-112003/v1.14.0-gcb84623"]
    }
  }
}
```

- `last_checked`: Zeitpunkt der letzten erfolgreichen Geräteprüfung, in lokaler Zeitzone mit UTC-Offset und Sekundengenauigkeit. Kein Zeitpunkt eines fehlgeschlagenen Versuchs.
- `known_versions`: Alle bislang erfolgreich erkannten Versionsbezeichnungen dieses Geräteprofils.
- Versionsbezeichnungen werden dedupliziert und lexikografisch sortiert.
- Die Sortierung ist kein fachlicher Versionsvergleich.
- Aus der Herstellerquelle verschwundene Versionen bleiben bekannt.
- Nicht mehr konfigurierte Geräte werden nicht automatisch aus dem Zustand entfernt.

Die Zustandsdatei wird vom Programm verwaltet. Gespeichert wird unter `state["devices"][device["id"]]`, wobei `device["id"]` immer die **lokale** Geräte-ID ist (nicht die Profil-ID). Profil-ID und lokale Geräte-ID sind getrennt: Die Profil-ID beschreibt die gemeinsam verwendete Quelle, die lokale ID ist der Zustandsschlüssel. Bei Umstellung eines Geräts auf ein Profil bleibt bei gleichbleibender lokaler ID derselbe gespeicherte Versionsstand erhalten; eine Migration ist nicht nötig. Mehrere lokale Geräte dürfen dasselbe Profil referenzieren, lokale IDs müssen aber eindeutig sein. Das Zustandsformat selbst bleibt unverändert.

Wird die Zustandsdatei gelöscht oder eine Geräte-ID geändert, beginnt für das betreffende Gerät eine neue Initialisierung. Bei Änderung der Quelle oder Extraktionslogik muss ein bewusster Umgang mit dem bisherigen Zustand erfolgen; es gibt keine automatische Migration.

## 6. Ablauf einer Prüfung

1. CLI-Argumente einlesen.
2. Konfiguration und vorhandenen Zustand laden.
3. Listen-/Zustandsstruktur und eindeutige lokale Geräte-IDs prüfen.
4. Alle Einträge auflösen: Profile laden sowie Profil- und Quellkonfiguration validieren. Alle Konfigurationsfehler werden hier vor jedem Netzwerkzugriff erkannt.
5. Geräte nacheinander verarbeiten.
6. Quelle mit einem Netzwerk-Timeout von 30 Sekunden abrufen.
7. Antwort anhand ihres Zeichensatz-Headers oder ersatzweise UTF-8 dekodieren.
8. Versionen je nach Quelltyp extrahieren.
9. Doppelte Treffer entfernen; bei null Treffern Prüfung als Fehler behandeln.
10. Mit dem gespeicherten Zustand vergleichen.
11. Ergebnis ausgeben und bei Erfolg den Gerätezustand aktualisieren.
12. Nach Verarbeitung aller Geräte den Zustand speichern.

HTTP-Header:

```text
User-Agent: firmAware/0.1
Accept: application/json,text/html,*/*
```

Es gibt keine eigene Wiederholungslogik und keine Browserautomatisierung.

## 7. Vergleichslogik und Initialisierung

Beim ersten erfolgreichen Abruf eines Geräteprofils werden sämtliche erkannten Versionen als Ausgangsstand gespeichert und angezeigt. Sie werden nicht als neue Firmwareeinträge gemeldet.

Bei folgenden erfolgreichen Abrufen gilt:

```text
Neue Einträge = aktuell erkannte Versionen − bislang bekannte Versionen
Gespeicherte Versionen = bislang bekannte Versionen ∪ aktuell erkannte Versionen
```

Beispiel:

```text
Bekannt: 6.3.24, 6.3.26
Aktuell: 6.3.24, 6.3.26, 6.3.27
Neu:     6.3.27
```

`6.3.27` ist hier nur eine hypothetische Beispielversion.

Ein neu auftauchender älterer Eintrag wird ebenfalls gemeldet. Änderungen an Release Notes oder Downloadlinks bei unveränderter Versionsbezeichnung werden nicht erkannt. Eine bereits bekannte Version wird nicht erneut gemeldet, auch wenn sie zwischenzeitlich aus der Quelle entfernt wurde.

## 8. Ausgabe und Rückgabecodes

Reguläre Ergebnisse gehen an stdout, Fehler an stderr. Die Ausgabe erfolgt kompakt, eine Zeile pro Gerät, ohne Leerzeilen. Gerätename und Status werden spaltenweise ausgerichtet.

`--check` (Standard) zeigt je Gerät eine Zeile:

```text
<Gerät>  Ausgangsstand (N): v1, v2, ...   # erste erfolgreiche Prüfung
<Gerät>  neu: v3, v4                       # neu gefundene Versionen
<Gerät>  keine Änderung                    # keine neuen Versionen
```

`--status` gibt den gespeicherten Zustand tabellarisch aus:

```text
Gerät    Letzter Check        Letzte Version
-------  -------------------  --------------
<Gerät>  2026-10-12 19:29:44  RE200(EU)_V4_210916
```

`Letzte Version` ist die lexikografisch letzte gespeicherte Version (kein fachlicher Versionsvergleich). Noch nie geprüfte Geräte erscheinen mit `nie geprüft` und `–`. Es erfolgt keine Netzabfrage und keine Änderung der Zustandsdatei.

Mögliche Meldungen:

```text
Ausgangsstand (N): ...
neu: ...
keine Änderung
Prüffehler: ...
Konfigurationsfehler: ...
Speicherfehler: ...
```

Konfigurationsfehler werden vor jeder Netzabfrage erkannt und nennen lokale ID, Profil-ID und gegebenenfalls das Feld:

```text
Konfigurationsfehler: Gerät 'coros-dura', Profil 'coros.dura': Pflichtfeld 'version_pattern' fehlt
```

| Exit-Code | Bedeutung |
|---|---|
| `0` | Alle konfigurierten Prüfungen und Speicherung erfolgreich (auch ohne neue Einträge) bzw. `--status` erfolgreich |
| `1` | Konfigurations-, Prüf- oder Speicherfehler |

Es gibt keinen gesonderten Exit-Code für gefundene Updates und keine strukturierte JSON-Ausgabe.

## 9. Fehlerbehandlung und Speicherung

- Ein Gerätefehler verhindert nicht die Prüfung weiterer Geräte.
- Bei fehlgeschlagener Prüfung bleibt der bisherige Zustand dieses Geräts erhalten.
- Null Treffer werden nicht als „keine Updates“ interpretiert.
- Erfolgreiche Geräteprüfungen werden auch dann gespeichert, wenn andere Geräte fehlschlagen.
- Schreiben erfolgt zunächst in eine benachbarte `.tmp`-Datei; anschließend wird die Zieldatei mit `os.replace` ersetzt.
- Das Elternverzeichnis der Zustandsdatei wird bei Bedarf angelegt.
- Es gibt keine Dateisperre. Parallele Aufrufe mit derselben Zustandsdatei sind nicht unterstützt.
- Terminalmeldung und Speicherung bilden keine Transaktion. Nach einem Speicherfehler kann eine Meldung beim nächsten Lauf erneut erscheinen.
- Bei einem Konfigurationsfehler erfolgt eine verständliche Meldung auf `stderr`, Exitcode `1`, keine Netzabfrage, keine Änderung von `state.json` und kein unkontrollierter Traceback.

Mindestens behandelt werden: fehlende oder nicht lesbare Profildatei; ungültiges JSON oder Profil ist kein Objekt; nicht unterstützte Schema-Version; ungültige oder abweichende Profil-ID; fehlende oder falsch typisierte Pflichtfelder; unbekannter Quelltyp; ungültiger regulärer Ausdruck oder mehr als eine Klammergruppe; unbekannter Zeichensatz; ungültige Quell-URL; unerlaubte Felder im profilbasierten Eintrag; doppelte lokale Geräte-IDs; Dateipfad außerhalb des Profilverzeichnisses.

Die Konfigurationsprüfung der Grundstruktur ist bewusst einfach. Profile und technische Quellkonfigurationen werden dagegen vor dem Abruf vollständig validiert. Abruffehler bleiben gerätebezogen und verhindern die Prüfung anderer korrekt konfigurierter Geräte nicht.

## 10. Aktuelle Grenzen und Sicherheit

- Kein automatischer Zeitplan, kein laufender Dienst.
- Keine Push-, E-Mail- oder MQTT-Benachrichtigungen.
- Keine Erfassung installierter Gerätefirmware.
- Kein Vergleich „höher/niedriger“, keine Ermittlung der neuesten Version.
- Keine Bestätigung gerätespezifischer Updatefreigabe.
- Keine automatische Modell-, Hardware- oder Regionserkennung.
- Keine Unterscheidung zwischen Beta und stabil, außer wenn die Quelle selbst passend eingeschränkt ist.
- Keine JavaScript-Ausführung, Authentifizierung oder Pagination.
- Keine automatische Firmwareinstallation.
- Keine garantierte langfristige Stabilität der verwendeten Herstellerendpunkte.
- URLs und Suchmuster werden als vertrauenswürdige lokale Konfiguration behandelt; nicht für ungeprüfte öffentliche Eingaben vorgesehen.
- Geräteprofile werden nur über sichere Profil-IDs innerhalb des Profilverzeichnisses geladen; Symlinks dürfen nicht aus dem Verzeichnis herausführen. Profile enthalten keine Zugangsdaten und keine Laufzeitzustände.
- Der User-Agent, Abruffrequenz und Netzwerkzugriff können das Verhalten der Herstellerquellen beeinflussen.

Live-Verhalten: Die Ubiquiti-JSON-Struktur wurde anhand der vom Nutzer bereitgestellten Antwort konfiguriert. Die ursprüngliche Ubiquiti-HTML-Seite lieferte im Terminal keine Treffer für das XM-Dateinamenmuster. Für Shelly wurde der Einzelwert über `data → SHSW-1 → version` erfolgreich extrahiert. Die TP-Link-Bezeichnungen wurden mit urlwatch erfolgreich extrahiert; der direkte Abruf durch diesen Python-Prototyp muss separat bestätigt werden. Die Extraktions- und Vergleichslogik ist durch `tests/test_firmaware.py` offline abgedeckt; ein vollständiger Live-Integrationstest war nicht möglich. Das COROS-DURA-Profil ändert nichts am bekannten COROS-HTTP-403-Verhalten und ist keine Lösung dafür.

## 11. Akzeptanzkriterien / manuelle Tests

| Fall | Erwartung |
|---|---|
| Erste erfolgreiche Prüfung | Versionen als Ausgangsstand speichern, keine Update-Meldung |
| Identische Quelle beim zweiten Lauf | Keine neuen Firmwareeinträge |
| Zusätzliche unbekannte Version | Einmalige Meldung und anschließende Speicherung |
| Derselbe Eintrag beim nächsten Lauf | Keine erneute Meldung |
| Zusätzliche ältere Version | Ebenfalls als neu gefundener Eintrag melden |
| Doppelte Treffer | Nur einmal speichern und melden |
| Version verschwindet aus Quelle | Nicht aus `known_versions` entfernen |
| Leere Extraktion | Prüffehler, bisherigen Zustand erhalten |
| HTTP-, Dekodierungs- oder JSON-Fehler | Prüffehler, weiteres Gerät trotzdem prüfen |
| JSON-Eintrag mit `enabled: false` | Überspringen |
| Fehlendes Versionsfeld in aktivem JSON-Eintrag | Geräteprüfung fehlschlagen lassen |
| `json_value` mit gültigem Pfad | Genau eine Version als Ausgangsstand speichern |
| `json_value`: fehlender Pfad oder Wert ist keine Zeichenfolge | Prüffehler, bisherigen Zustand erhalten |
| Doppelte Geräte-IDs | Konfigurationsfehler |
| Fehlende Zustandsdatei | Leeren Zustand verwenden und Datei bei Speicherung erzeugen |
| Aufruf aus anderem Arbeitsverzeichnis | Standarddateien beim Skript verwenden |
| Geänderte Geräte-ID | Neues Gerät initialisieren; alter Zustandsdatensatz bleibt erhalten |
| Gültiges Textprofil | Technische Felder korrekt übernommen |
| Gültiges JSON-Profil | Vorhandener JSON-Extraktor verwendbar |
| Gültiges JSON-Value-Profil | Vorhandener Pfadextraktor verwendbar |
| Lokale ID unterscheidet sich von Profil-ID | Lokale ID bleibt Zustandsschlüssel |
| Lokaler Name vorhanden | Überschreibt den Profilnamen |
| Kein lokaler Name | Profilname wird verwendet |
| Gemischte profilbasierte und direkte Einträge | Beide Formate werden aufgelöst |
| Zwei Geräte mit demselben Profil | Jeweils eigene lokale ID |
| Umstellung auf Profil mit gleicher lokaler ID | Gespeicherter Versionsstand bleibt erhalten |
| Technische Felder im profilbasierten Eintrag | Konfigurationsfehler, kein stilles Ignorieren |
| Fehlende Profildatei / ungültiges JSON / kein Objekt | Konfigurationsfehler |
| Ungültige Profil-ID / abweichende Profil-ID / Schema-Version | Konfigurationsfehler |
| Ungültige Quellfelder (Typ, URL, Regex, Zeichensatz) | Konfigurationsfehler vor dem Abruf |
| Pfadmanipulation / Symlink außerhalb des Profilverzeichnisses | Zugriff abgelehnt |
| `--profiles-dir` | Gewähltes Verzeichnis wird verwendet |
| `--status` mit Profilen | Keine Netzwerkabfrage |
| Ein ungültiges Profil | Keine Prüfung und keine Zustandsänderung |
| Direkte Konfiguration ohne Profilverzeichnis | Funktioniert |
| COROS-Extraktion aus Beispieldaten | `["3.0808.0", "3.0910.0"]` |

## 12. Mögliche nächste Schritte – nicht implementiert

Priorität und Umfang sind offen:

1. Weitere Verbesserungen der Fehlerdiagnosen bei Abrufproblemen.
2. Release-Datum und Release-Notes-Link in Meldungen aufnehmen.
3. Optional getrennte Initialisierung und Prüfmodus.
4. Ein einfacher Zeitplan außerhalb des Skripts.
5. Ein Benachrichtigungskanal.
6. Gerätebezogene Erfassung installierter Versionen.
7. Herstellerabhängiger fachlicher Versionsvergleich.
8. Weitere Geräteprofile, beispielsweise für zusätzliche Modelle.

Automatisierte Tests mit gespeicherten Beispielantworten sind inzwischen in `tests/test_firmaware.py` umgesetzt (siehe Abschnitt 3 und 11) und daher nicht mehr als offener Punkt geführt. Wiederverwendbare Geräteprofile sind mit Version 0.2 umgesetzt.

Weboberfläche, Docker-Paket und Home-Assistant-Anbindung sind mögliche spätere Erweiterungen, keine Voraussetzung für den aktuellen Prototyp.

## 13. Profilassistent (`--create-profile`)

Der Profilassistent erstellt dialoggeführt ein Text-/HTML-Geräteprofil, ohne dass der Nutzer das JSON-Schema oder reguläre Ausdrücke kennen muss. Er arbeitet ausschließlich regelbasiert: **keine KI, keine externen KI-Dienste, keine zusätzlichen Python-Abhängigkeiten**. Die einzige Datenübertragung ist der vom Nutzer ausgelöste Quellabruf.

### 13.1 Aufruf und Ablauf

```bash
python3 firmaware.py --create-profile --profiles-dir ./profiles
```

Dialogschritte:

1. **Gerät beschreiben:** Hersteller, Modell, Anzeigename (Vorschlag `<Hersteller> <Modell> – Firmware`) und Profil-ID. Der ID-Vorschlag wird aus Hersteller und Modell kleingeschrieben, auf ASCII normalisiert und bereinigt; die endgültige ID muss `[a-z0-9][a-z0-9._-]*` erfüllen.
2. **Quelle abrufen:** URL eingeben. Zulässig sind nur HTTP/HTTPS mit Host, ohne Zugangsdaten und ohne Steuerzeichen; ein Fragment wird entfernt. Abruf über dieselbe Logik wie reguläre Prüfungen (30 s Timeout, feste Header), zusätzlich mit Größenlimit (5 MiB) und Prüfung jedes Weiterleitungsziels. Bei Dekodier-/HTTP-Fehlern kann die URL korrigiert oder ein expliziter Python-Zeichensatz gewählt werden.
3. **Beispielversion erfassen:** eine auf der Seite sichtbare Versionsangabe (max. 100 Zeichen). Gemeint ist die **Quellversion** (veröffentlichte Firmwarebezeichnung), **nicht** die installierte Geräteversion.
4. **Fundstelle auswählen:** alle exakten Vorkommen werden mit begrenztem Kontext (je 150 Zeichen) angezeigt. Bei mehreren Fundstellen ist eine ausdrückliche Auswahl nötig (Paginierung bei vielen Treffern); die erste Fundstelle wird nie automatisch übernommen. Steuerzeichen fremder Inhalte werden vor der Ausgabe entfernt.
5. **Versionsformat vorschlagen:** aus einer festen Vorlagensammlung. Unterstützt werden Punktversion (`16.2.1`), Präfixversion (`V 3.0910.0`) und zusammengesetzte Version (`1.13/1.02`). Aus einer einzelnen Beispielversion ist die künftige Blockzahl nicht sicher ableitbar, daher werden zwei Varianten angeboten: exakte Blockzahl und „ein Block mehr". Jeder Kandidat hat genau eine erfassende Klammergruppe; Hilfsgruppen sind nicht erfassend. Führende Nullen bleiben erhalten, ein festes Präfix wird nicht in den Wert aufgenommen.
6. **Validierung und Vorschau:** jeder Kandidat wird kompiliert, auf genau eine Gruppe geprüft und mit dem vorhandenen Extraktor getestet. Die Vorschau zeigt ID, Name, Hersteller/Modell, Quell-URL (und finale URL bei Weiterleitung), Zeichensatz, Bereich, erwarteten Wert, alle eindeutigen Versionen, Zahl der Fundstellen getrennt von der Zahl eindeutiger Werte sowie den Regex. Der Export verlangt eine ausdrückliche Bestätigung; optional wird vorher erneut abgerufen und getestet.
7. **Export:** atomar über eine temporäre Nachbardatei, nur innerhalb des Profilverzeichnisses, kein stilles Überschreiben.

Exitcodes: `0` Profil exportiert, `1` technischer Fehler oder Eingabeende, `130` Abbruch durch `Ctrl+C`. Bei Abbruch oder Fehler entsteht keine finale Profildatei und keine vorhandene Datei wird verändert.

### 13.2 Unterschied Ganzquellenmodus und Tabellenbereich

Dieser Umfang deckt den **Ganzquellenmodus** ab: das Versionsmuster gilt für den gesamten, per `html.unescape()` normalisierten Inhalt, und alle Treffer werden zur Bestätigung angezeigt. Der in der ursprünglichen Anforderung beschriebene **Tabellenzeilen-Modus** (Begrenzung des Musters auf eine ausgewählte HTML-Tabellenzeile über einen Textanker) ist **nicht** enthalten. Ebenso ist der **Expertenmodus** (manuelle Eingabe eines vollständigen `version_pattern`) **nicht** enthalten. Formate außerhalb der festen Vorlagen werden deshalb mit einem Hinweis auf Korrektur abgelehnt. Eine Erweiterung um Tabellenbereich und Expertenmodus bleibt möglich; das Profilschema muss dafür unverändert bleiben (der Bereich wäre in `version_pattern` zu integrieren, keine neue `selector`-Eigenschaft).

### 13.3 Schutz vor problematischen Regex

Alle Kandidatentests laufen in einem **separaten Prozess** mit Zeitlimit (3 Sekunden, `multiprocessing` mit `spawn`-Kontext, plattformübergreifend ohne Unix-Signale). Bei Überschreitung wird der Prozess beendet und der Kandidat abgelehnt; es wird keine Profildatei exportiert. Diese Absicherung betrifft nur den Assistenten, nicht die regulären Prüfläufe.

### 13.4 Grenzen

- Keine KI und keine automatische semantische Bewertung.
- Keine Weboberfläche, DOM-Auswahl, JavaScript-Ausführung oder Browserautomatisierung.
- Keine Anmeldung, Cookies oder Umgehung von HTTP-Sperren (z. B. HTTP 403).
- Keine automatischen Änderungen an `devices.json` oder `state.json`.
- Kein allgemeingültiger HTML-Parser; die Version muss im direkt erhaltenen Inhalt stehen.
- Ein Präfix mit Ziffern (z. B. `RE200(EU)_V4_`) wird von den Vorlagen nicht erkannt.
- Der Assistent ist ein lokaler CLI-Abruf und **nicht** als öffentlich erreichbarer URL-Abrufdienst gedacht. Eine spätere Weboberfläche benötigt eine eigenständige SSRF-Sicherheitsanforderung.

### 13.5 Testergebnis und Export

Nach Erfolg meldet der Assistent ausdrücklich, dass nur die aktuelle Antwort erfolgreich getestet wurde und zukünftige Seitenänderungen oder Versionsformate nicht geprüft sind. Es werden keine Garantien wie „dauerhaft verifiziert" angegeben.

Das exportierte Profil entspricht Schema-Version 1 und ist mit dem vorhandenen Resolver nutzbar. Beispiel:

```json
{
  "schema_version": 1,
  "id": "coros.dura",
  "name": "COROS DURA – Firmware",
  "manufacturer": "COROS",
  "model": "DURA",
  "source_type": "text",
  "source_url": "https://example.org/releases",
  "version_pattern": "\\bV\\s*([0-9]+(?:\\.[0-9]+){2,3})\\b"
}
```

Das exportierte Muster enthält die im Dialog bestätigten Grenzen; die Beispieladresse ist ein Platzhalter. `encoding` wird nur exportiert, wenn der Nutzer einen Zeichensatz ausdrücklich gewählt hat. Nach dem Export wird die Datei erneut geladen und validiert. Zur Einbindung zeigt der Assistent ein Beispiel für `devices.json`; bei einem bestehenden Gerät bleibt die lokale ID erhalten. `devices.json` und `state.json` werden durch den Assistenten nie verändert.
