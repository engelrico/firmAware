# Requirement: Regelbasierter Profilassistent für firmAware

## 1. Ziel

Ein dialoggeführter Profilassistent soll Anwendern ermöglichen, ein Geräteprofil ohne Kenntnis des JSON-Schemas oder regulärer Ausdrücke zu erstellen. Der Assistent fragt eine Firmwarequelle und eine dort sichtbare Beispielversion ab, schlägt ein verallgemeinertes Versionsmuster vor, zeigt dessen tatsächliche Treffer und exportiert nach Bestätigung eine Profildatei.

Die Funktion benötigt keine KI, keine externen KI-Dienste und keine zusätzlichen Python-Abhängigkeiten. Die erste Umsetzung ist ein CLI-Assistent für Text-/HTML-Quellen.

## 2. Voraussetzungen und Abgrenzung

Voraussetzung ist die Umsetzung des lokalen Profilformats mit `schema_version: 2` und Profilauflösung aus `devices.json`. Jedes Profil benötigt das Pflichtfeld `category` (`firmware`, `software` oder `operating_system`).

### Bestandteil des MVP

- Interaktiver CLI-Modus.
- Hersteller, Modell, Name und Profil-ID erfassen.
- HTTP-/HTTPS-Quelle abrufen.
- Sichtbare Beispielversion erfassen.
- Fundstellen mit Kontext anzeigen.
- Regelbasierte Musterkandidaten aus festen Vorlagen erzeugen.
- Optional eine HTML-Tabellenzeile als Extraktionsbereich verwenden.
- Regex und Treffer mit dem vorhandenen Extraktor prüfen.
- Vorschau und ausdrückliche Bestätigung.
- Gültiges Geräteprofil als JSON exportieren.
- Tests und Dokumentation.

### Nicht Bestandteil

- KI oder automatische semantische Bewertung einer Firmwarequelle.
- Weboberfläche, visuelle DOM-Auswahl oder Browsererweiterung.
- JavaScript-Ausführung, Playwright oder Browserautomatisierung.
- Anmeldung, Cookies, Zugangsdaten oder Umgehung von HTTP 403.
- Automatische Geräte- oder Kanalzuordnung.
- JSON-API-Assistent; vorhandene JSON-Profile bleiben unverändert unterstützt.
- Automatische Veröffentlichung oder Übermittlung von Profilen.
- Automatische Änderungen an `devices.json` oder `state.json`.
- Änderungen an Versionssortierung und Versionsvergleich.
- Allgemeingültiger Parser für beliebig komplexes HTML.

## 3. CLI und Modusverhalten

Neues Argument:

```text
--create-profile
```

Es ist mit `--check` und `--status` gegenseitig exklusiv. Ohne Modusargument bleibt die bisherige Prüfung Standard.

Das vorhandene Argument `--profiles-dir PATH` bestimmt das Exportverzeichnis. Standard ist `profiles` neben der Python-Einstiegsdatei. Explizite relative Pfade werden relativ zum aktuellen Arbeitsverzeichnis ausgewertet.

Beispiel:

```bash
python firmaware.py --create-profile --profiles-dir ./profiles
```

Der Erstellungsmodus muss vor dem Laden von `devices.json` und `state.json` verzweigen. Beide Dateien müssen für die Profilerstellung nicht existieren.

Exitcodes:

- `0`: Profil erfolgreich exportiert.
- `1`: Technischer Fehler, nicht unterstützte Quelle oder Eingabeende vor Abschluss.
- `130`: Abbruch durch `Ctrl+C`.

Bei Abbruch oder Fehler keine finale Profildatei erzeugen und keine vorhandene Datei verändern. Ein neu angelegtes leeres Exportverzeichnis darf bestehen bleiben.

## 4. Dialogablauf

### Schritt 1: Gerät beschreiben

Abfragen:

- Hersteller: nicht leere Zeichenkette.
- Modell: nicht leere Zeichenkette.
- Anzeigename: Vorschlag `<Hersteller> <Modell> – Firmware`, editierbar.
- Profil-ID: Vorschlag aus Hersteller und Modell, editierbar.
- Kategorie: `firmware`, `software` oder `operating_system` (Vorschlag: `firmware`), editierbar.

Der ID-Vorschlag soll kleingeschrieben, auf ASCII normalisiert und von nicht erlaubten Zeichen bereinigt werden. Der Nutzer muss den Vorschlag bestätigen oder ändern. Falls kein brauchbarer Vorschlag möglich ist, ID manuell verlangen.

Die endgültige ID muss vollständig passen:

```regex
[a-z0-9][a-z0-9._-]*
```

Dateiname: `<profile_id>.json`. Keine Pfadbestandteile aus Nutzereingaben übernehmen.

### Schritt 2: Quelle abrufen

URL abfragen und validieren:

- Nur HTTP und HTTPS.
- Host erforderlich.
- Keine Zugangsdaten in der URL.
- Keine Steuerzeichen.
- Fragment vor dem Abruf entfernen.
- Vollständige URL mit Query-Parametern erhalten.

Für diese erste lokale CLI-Implementierung sind interne HTTP-/HTTPS-Quellen nicht grundsätzlich ausgeschlossen. Der Assistent darf ausschließlich vom lokalen Anwender gestartete Abrufe ausführen; er darf nicht als öffentlich erreichbarer URL-Abrufdienst bereitgestellt werden. Eine spätere Weboberfläche benötigt eine eigenständige SSRF-Sicherheitsanforderung.

Abruf mit derselben HTTP- und Dekodierungslogik wie reguläre Prüfungen:

- User-Agent und Accept-Header des Projekts verwenden.
- Timeout wie bei regulären Abrufen, derzeit 30 Sekunden.
- Antwortgröße auf beispielsweise 5 MiB begrenzen.
- Weiterleitungen begrenzen; jedes Weiterleitungsziel erneut auf erlaubtes Protokoll, Host und fehlende URL-Zugangsdaten prüfen.
- Status, finale URL und verwendeten Zeichensatz für die Vorschau verfügbar machen.

Neue Abrufbegrenzungen sind in einer gemeinsamen Hilfsfunktion umzusetzen, damit Assistent und reguläre Prüfungen nicht unterschiedliche Abruflogik verwenden. `fetch_source()` darf weiterhin Text zurückgeben und die Hilfsfunktion intern nutzen.

Bei Dekodierungsfehlern kann der Nutzer eine gültige Python-Zeichensatzbezeichnung eingeben und den Abruf erneut versuchen. Ein ausdrücklich gewählter Zeichensatz wird als `encoding` exportiert. Ohne explizite Wahl bleibt die bestehende Reihenfolge: HTTP-Zeichensatz, andernfalls UTF-8.

Bei HTTP-Fehlern, Timeout oder nicht unterstütztem Inhalt klare Meldung und Möglichkeit zur URL-Korrektur anbieten. Keine automatische Browser- oder Loginlösung starten. Nicht lesbare binäre Inhalte sind abzulehnen.

Nicht behaupten, eine Quelle sei offiziell oder verifiziert. Hersteller und Modell stammen zunächst aus Nutzereingaben.

### Schritt 3: Beispielversion erfassen

Abfragen:

```text
Welche Versionsangabe siehst du auf dieser Seite?
```

Gemeint ist eine Versionsangabe der Quelle, nicht die installierte Geräteversion.

Die Eingabe muss nicht leer sein und darf keine Zeilenumbrüche enthalten. Maximale Länge: 100 Zeichen.

Die Suche verwendet denselben durch `html.unescape()` normalisierten Text wie `versions_from_text()`. Die normalisierte Quelle bleibt für Fundstellenpositionen und Kandidatentests einheitlich.

### Schritt 4: Fundstelle auswählen

Alle exakten Vorkommen der Beispielangabe suchen und nummeriert anzeigen. Pro Fundstelle einen begrenzten Kontext davor und danach darstellen, beispielsweise jeweils 150 Zeichen.

- Kein Treffer: Eingabe korrigieren, URL ändern, Zeichensatz ändern oder abbrechen.
- Ein Treffer: Fundstelle anzeigen und bestätigen lassen.
- Mehrere Treffer: ausdrücklich eine Fundstelle auswählen lassen.
- Sehr viele Treffer: Zahl anzeigen und paginieren; keine stillschweigende Wahl.

Terminal-Steuerzeichen aus fremdem Inhalt dürfen nicht ungefiltert ausgegeben werden.

Der Assistent darf nicht automatisch die erste Fundstelle übernehmen.

### Schritt 5: Extraktionsbereich wählen

Zwei Modi anbieten:

1. Ganze Quelle: Versionsmuster gilt für den gesamten Inhalt.
2. Tabellenzeile: Muster auf eine ausgewählte HTML-Tabellenzeile begrenzen.

Bei ganzer Quelle alle Treffer zeigen und vor modellfremden Treffern warnen. Die Auswahl ist besonders für Release-Listen geeignet, darf aber nicht automatisch erfolgen.

Für Tabellenzeilen:

- Mit `html.parser.HTMLParser` die Zeile ermitteln, die die ausgewählte Fundstelle enthält; relevante Text-/Quellpositionen nachvollziehbar zuordnen.
- Zugehörige Zeile zur Bestätigung anzeigen.
- Nutzer nach einem stabilen Textanker innerhalb dieser Zeile fragen, beispielsweise `Firmware Update Software`.
- Anker darf nicht die aktuelle Versionsnummer oder ein aktuelles Datum enthalten.
- Für das MVP muss der Anker vor der gewählten Versionsangabe stehen und im normalisierten Rohtext der Zeile eindeutig vorkommen.
- Anker mit `re.escape()` maskieren; bei erlaubten Leerraumvariationen diese bewusst und begrenzt behandeln.
- Generierter Bereichsteil darf nicht über ein schließendes `</tr>` hinausgehen.
- Die Kombination darf auf der aktuellen Quelle keine weiteren Tabellenzeilen erfassen.

Falls die Zeile verschachtelt, mehrdeutig oder der Anker durch Tags unterbrochen ist und nicht zuverlässig verarbeitet werden kann, keine automatische Bereichsgenerierung. Stattdessen andere Fundstelle, Ganzquellenmodus, Expertenmodus oder Abbruch anbieten.

Ein reines Kontextfenster um die aktuelle Position ist kein dauerhaftes Extraktionskriterium und darf nicht exportiert werden.

Keine neue `selector`-Eigenschaft einführen: Der Bereich wird im MVP in `version_pattern` integriert, damit das aktuelle Profilschema unverändert bleibt.

### Schritt 6: Versionsformat vorschlagen

Eine feste Vorlagensammlung verwenden, keine freie KI-generierte Regex-Synthese.

Mindestens unterstützen:

| Vorlage | Beispiel | Vorschlag |
|---|---|---|
| Punktversion | `16.2.1` | Zahlenblöcke mit Punkten |
| Präfixversion | `V 3.0910.0` | Festes Präfix, optionaler Leerraum, Punktversion |
| Zusammengesetzte Version | `1.13/1.02` | Zwei Punktversionen mit `/` |

Aus einer einzelnen Beispielversion ist die Anzahl zukünftig möglicher Zahlenblöcke nicht sicher ableitbar. Deshalb die vorgeschlagene Anzahl anzeigen und eine Auswahl anbieten, zum Beispiel exakt drei oder drei bis vier Blöcke.

Festes Präfix nicht in den extrahierten Versionswert aufnehmen. Eine zusammengesetzte Angabe standardmäßig vollständig aufnehmen und diese Entscheidung anzeigen.

Beispielkandidat für COROS:

```regex
\bV\s*([0-9]+(?:\.[0-9]+){2,3})\b
```

Jeder Kandidat muss genau eine erfassende Klammergruppe haben. Hilfsgruppen sind nicht erfassend: `(?:...)`. Keine Verkürzung auf Teilversionen zulassen, wenn ein weiterer Punkt-Zahlenblock unmittelbar anschließt; hierfür passende Begrenzungen verwenden und testen.

Keinen Regex erzeugen, der die aktuelle Versionsnummer als feste Zeichenfolge festschreibt. Feste Texte dürfen nur Kontext oder Präfixe beschreiben.

Für zusammengesetzte Versionen müssen Grenzen verhindern, dass eine nicht unterstützte längere Angabe nur teilweise erkannt wird.

### Schritt 7: Expertenmodus

Für nicht unterstützte Formate oder komplizierte Quellen manuelle Eingabe eines vollständigen `version_pattern` anbieten.

- Muster wie normale Kandidaten validieren und testen.
- Kein stilles Umschalten auf eine unspezifische Zahlensuche.
- Genau eine erfassende Gruppe erforderlich.
- Leere Treffer und Muster, die ausschließlich die feste Beispielversion enthalten, dürfen nicht als automatisch verallgemeinert bezeichnet werden.

Der Expertenmodus darf komplexe Quellen ermöglichen, ist aber keine Zusage, dass beliebige Regex sicher oder performant sind.

## 5. Validierung und Vorschau

Kandidaten zunächst mit `re.compile()` validieren und Gruppenanzahl prüfen. Die tatsächliche Versionsauswertung muss anschließend die vorhandene Funktion `versions_from_text()` beziehungsweise `extract_versions()` nutzen.

Anzeigen:

- Profil-ID und Anzeigename.
- Hersteller und Modell.
- Konfigurierte URL, bei Weiterleitung zusätzlich finale URL.
- Verwendeter Zeichensatz.
- Ausgewählter Bereich.
- Erwarteter extrahierter Beispielwert ohne festes Präfix.
- Alle gefundenen eindeutigen Versionen.
- Zahl der Fundstellen und Zahl eindeutiger Werte getrennt.
- Generierter Regex in einem technischen Detailbereich.

Die konfigurierte URL bleibt standardmäßig die eingegebene Adresse. Wenn auf eine finale Adresse umgestellt werden soll, muss der Nutzer dies ausdrücklich bestätigen.

Vor Export prüfen:

1. Quelle erfolgreich abgerufen und dekodiert.
2. Beispiel-Fundstelle gewählt.
3. Regex gültig und genau eine Extraktionsgruppe.
4. Erwarteter Beispielwert unter den Ergebnissen.
5. Gewählte Fundstelle vom Kandidaten tatsächlich erfasst; nicht nur eine andere identische Version.
6. Keine leeren Extraktionswerte.
7. Tabellenmodus: keine Treffer außerhalb der bestätigten Zeile.
8. Nutzer bestätigt alle angezeigten Versionswerte.
9. Gesamtes Profil besteht die gemeinsame Profilvalidierung.

Ausgabe bei Erfolg:

```text
Auf der aktuellen Antwort erfolgreich getestet.
Nicht geprüft: zukünftige Seitenänderungen und zukünftige Versionsformate.
```

Nicht `dauerhaft verifiziert` oder vergleichbare Garantien anzeigen.

Erneuten Abruf und Prüfung vor Export anbieten. Scheitert der erneute Test, Export blockieren, bis eine erfolgreiche Prüfung erfolgt oder der Nutzer zum vorherigen erfolgreich getesteten Snapshot ausdrücklich zurückkehrt. In diesem Fall das Alter des Snapshots deutlich anzeigen.

## 6. Schutz vor problematischen Regex

Da Python-Regex bei ungünstigen Mustern sehr lange laufen können, sind alle Kandidatentests, insbesondere manuelle Muster, in einem separaten Prozess auszuführen.

- Testzeitlimit, beispielsweise drei Sekunden.
- Bei Überschreitung Prozess beenden und Kandidaten ablehnen.
- Bei Fehler oder Timeout keine Profildatei exportieren.
- Die Auswertung im Kindprozess verwendet den vorhandenen Extraktor.
- Plattformübergreifende Umsetzung mit der Standardbibliothek; keine ausschließlich Unix-spezifische Signal-Lösung.

Diese Absicherung des Assistenten ist keine vollständige Absicherung der regulären Prüfungen. Diese Einschränkung dokumentieren; eine allgemeine Absicherung aller Extraktionsläufe ist eine separate Erweiterung.

## 7. Profil-Export

Exportformat entspricht dem vorhandenen Schema (Version 2):

```json
{
  "schema_version": 2,
  "id": "coros.dura",
  "name": "COROS DURA – Firmware",
  "manufacturer": "COROS",
  "model": "DURA",
  "category": "firmware",
  "source_type": "text",
  "source_url": "https://example.org/releases",
  "version_pattern": "\\bV\\s*([0-9]+(?:\\.[0-9]+){2,3})\\b"
}
```

Die Beispieladresse ist ein Platzhalter, keine vorgegebene COROS-Quelle. Das exportierte Muster muss die im Dialog bestätigten Grenzen enthalten; der obige Regex illustriert lediglich das Schema.

Optional `encoding` exportieren, wenn explizit gewählt. Keine zusätzlichen Pflichtfelder und keine Zugangsdaten oder Seiteninhalte speichern.

Anforderungen:

- JSON mit `json.dump()`, UTF-8, `ensure_ascii=False`, Einrückung und abschließendem Zeilenumbruch.
- Regex nicht manuell für JSON escapen; Serialisierung übernimmt dies.
- Zielpfad innerhalb von `profiles_dir` validieren, auch bei Symlinks.
- Export erst nach Vorschau und ausdrücklichem Ja.
- Bereits vorhandene Datei nicht still überschreiben. Neue ID, explizites Überschreiben oder Abbruch anbieten.
- Datei atomar über eine temporäre Nachbardatei schreiben und anschließend ersetzen.
- Temporäre Dateien bei Fehlern bereinigen.
- Vorhandene Datei muss bei Schreibfehler vor Ersetzung erhalten bleiben.
- Bei gleichzeitigen Änderungen am Ziel seit Bestätigung erneute Bestätigung verlangen oder abbrechen.
- Profildatei nach Export erneut laden und validieren.

Nach Erfolg einen Einbindungsbeispieltext anzeigen:

```json
{
  "id": "mein-geraet",
  "profile": "coros.dura"
}
```

Hinweis anzeigen: Bei einem bestehenden Gerät lokale ID beibehalten. Keine automatische Änderung an `devices.json` oder `state.json`.

## 8. Interne Architektur

Dialog, Kandidatenerzeugung, Validierung und Export voneinander trennen. Beispielhafte Funktionsgrenzen:

```python
def run_profile_assistant(profiles_dir: Path) -> int:
    ...

def find_example_occurrences(content: str, example: str) -> list[Occurrence]:
    ...

def suggest_version_patterns(example: str, options: PatternOptions) -> list[PatternCandidate]:
    ...

def build_scoped_pattern(candidate: PatternCandidate, scope: ScopeSelection) -> str:
    ...

def test_pattern(content: str, pattern: str, timeout: float) -> TestResult:
    ...

def export_profile(profile: Mapping[str, Any], profiles_dir: Path) -> Path:
    ...
```

Datentypen sind interne Vorschläge, keine neuen JSON-Pflichtfelder. Kandidatenerzeugung möglichst als reine Funktionen implementieren. Die gemeinsame Quell- und Profilvalidierung wiederverwenden, nicht duplizieren.

## 9. Tests

Standardbibliothek verwenden: `unittest`, `unittest.mock`, `tempfile`. Keine Live-Webseiten oder externen Dienste voraussetzen.

### Modus und Dialog

- `--create-profile` schließt andere Modi aus.
- Erstellungsmodus funktioniert ohne `devices.json` und `state.json`.
- Standardprüfung bleibt unverändert.
- Fehlende, falsche und mehrfach vorkommende Beispielversionen.
- Fundstelle wird nicht automatisch gewählt.
- Abbruch und Eingabeende erzeugen keine finale Datei.
- Terminal-Steuerzeichen werden sicher dargestellt.

### Vorlagen und Grenzen

- Punktversion, Präfixversion und zusammengesetzte Version erkannt.
- Führende Nullen erhalten, etwa `3.0910.0`.
- Präfix nicht extrahiert.
- Zusammengesetzte Version vollständig erhalten.
- Andere zukünftige Werte desselben bestätigten Formats werden erkannt.
- Zusätzliche Zahlenblöcke werden nicht als verkürzte Teilversion übernommen.
- Feste Kontextzeichen mit Regex-Bedeutung sicher maskiert.
- Genau eine erfassende Gruppe.
- Unbekanntes Format verlangt Expertenmodus oder Korrektur.

### Bereichsauswahl

- Bestätigte Tabellenzeile erfasst, benachbarte Treiberversion ignoriert.
- Fehlende Version in Firmwarezeile führt nicht zur Version aus nächster Zeile.
- Mehrdeutiger, nachgelagerter oder durch Tags unterbrochener Anker wird nicht automatisch übernommen.
- Ganzquellenmodus zeigt sämtliche Treffer zur Bestätigung.
- Kandidat muss ausgewählte Fundstelle erfassen.

### Abruf und Kandidatentest

- HTTP 403, Timeout und Dekodierungsfehler sauber behandelt.
- Explizite Kodierung verwendet und exportiert.
- Antwortgrößenlimit und Weiterleitungslimit eingehalten.
- Unzulässiges Weiterleitungsprotokoll abgelehnt.
- Ungültiger Regex, falsche Gruppenanzahl und leere Ergebnisse abgelehnt.
- Regex-Testprozess mit Überschreitung des Zeitlimits beendet.
- Vorschau benutzt vorhandenen Extraktor.

### Export

- Exportiertes JSON lädt mit bestehendem Profilresolver.
- Regex-Backslashes bleiben nach JSON-Roundtrip korrekt.
- Dateiname entspricht Profil-ID.
- Pfadmanipulation und Symlink außerhalb des Verzeichnisses abgelehnt.
- Kein stilles Überschreiben.
- Schreibfehler beschädigt vorhandene Datei nicht.
- Temporäre Dateien bereinigt.
- `devices.json` und `state.json` bleiben unverändert.

## 10. Dokumentation

`specification.md` ergänzen um:

- Aufruf und Dialogschritte.
- Unterstützte Versionsformate.
- Unterschied Quellversion und installierter Geräteversion.
- Unterschied Ganzquellenmodus und Tabellenbereich.
- Expertenmodus.
- Keine KI und keine externen Datenübertragungen außer bestätigten Quellabrufen.
- Grenzen bei JavaScript, Anmeldung und HTTP-Sperren.
- Erfolgreicher Test gilt nur für die geprüfte Antwort.
- Export und Einbindung in `devices.json`.
- Hinweis auf Sicherheitsanforderungen vor einer Webbereitstellung.

## 11. Abnahmekriterien

1. Ein Anwender erstellt ein Profil für eine einfache unterstützte Textquelle ohne eigenen Regex.
2. Der Assistent erzeugt ein verallgemeinertes Muster, nicht nur einen Treffer für die feste Beispielversion.
3. Der Nutzer wählt Fundstelle und bestätigt die tatsächlichen Ergebnisse.
4. Ein zusammengesetzter Wert wie `1.13/1.02` bleibt vollständig erhalten.
5. Tabellenmodus ignoriert Versionen benachbarter Downloads in den Tests.
6. Exportierte Datei entspricht Schema-Version 2 mit Pflichtfeld `category` und ist im Projekt verwendbar.
7. Keine Änderungen an Geräteauswahl oder Zustand.
8. Keine KI, externen KI-Dienste oder zusätzlichen Python-Pakete.
9. Fehler und Abbruch hinterlassen keine unvollständige finale Profildatei.
10. Dokumentation und alle geforderten Tests vorhanden und erfolgreich.
