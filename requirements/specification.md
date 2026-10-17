# firmAware – Specification

Status: 17 October 2026  
Version: 0.5 (schema v2 with category, clustering, and single-entry check)

## 1. Goal and Scope

firmAware checks public firmware/software sources for configured entries and reports previously unknown version labels in the terminal. Known versions are stored locally in `state.json`, so each run only highlights new entries.

The application is operated locally by the user and requires no dedicated cloud service. It observes published version lists. It does **not** check the firmware installed on a device and does **not** confirm that an update is available for a specific device.

This specification describes the current state of `firmaware.py`, `devices.json`, `profiles/*.json`, `state.json`, and `tests/test_firmaware.py`. Entries can be configured directly or via reusable profiles. Future ideas are listed separately in section 12 and are not part of the implementation.

## 2. Technical Foundation

- Python 3; exclusively standard library, no pip dependencies.
- CLI without web interface or long-running background service.
- HTTP queries via `urllib.request`.
- JSON processing via `json`, regular expressions via `re`.
- Automated tests via `unittest` (standard library), without network access.
- Storage in local JSON files, no database.
- No firmware installation and no downloads of firmware files.
- Internet access to the configured sources is required.

## 3. Project Files and Invocation

| File | Purpose |
|------|---------|
| `firmaware.py` | Fetch, extraction, comparison, profile resolution, and storage |
| `devices.json` | Personal entry selection (direct or via profiles) |
| `profiles/*.json` | Reusable entry profiles (fetch and version extraction) |
| `state.json` | Known versions and last successful checks |
| `tests/test_firmaware.py` | Automated tests (standard library only) |
| `specification.md` | Project specification |

Standard invocation (equivalent to `--check`):

```bash
python3 firmaware.py
python3 firmaware.py --check
```

Show stored state without a network request:

```bash
python3 firmaware.py --status
```

Interactively create an entry profile:

```bash
python3 firmaware.py --create-profile --profiles-dir ./profiles
```

`--check`, `--status`, and `--create-profile` are mutually exclusive. `--profiles-dir PATH` applies to all modes and is the export directory in create mode. The create mode branches before loading `devices.json` and `state.json`; both files do not need to exist for that mode.

Custom file paths:

```bash
python3 firmaware.py --devices /path/devices.json --state /path/state.json
```

Custom profile directory:

```bash
python3 firmaware.py --devices ./devices.json --profiles-dir ./profiles --check
```

Without arguments, both JSON files are looked up relative to the Python file's directory – **not** relative to the current working directory. An explicitly relative `--profiles-dir`, however, is resolved relative to the current working directory. If the state file does not exist, an empty state is used. Configuration and state are read and written as UTF-8.

With `--device ID` (short form `-d ID`) or an ID as a positional argument after `--check`/`--status`, only the corresponding entry is checked or shown. If the ID does not exist, a configuration error occurs.

```bash
python3 firmaware.py --check --device nanostation-m5-xm
python3 firmaware.py --check nanostation-m5-xm
python3 firmaware.py --status nanostation-m5-xm
```

Automated tests:

```bash
python3 -m unittest discover -s tests -t .
```

The tests use `unittest` and require no network access; source fetching is replaced by test doubles.

## 4. Configuration: Entry Selection and Entry Profiles

The configuration is split into two levels:

- `devices.json` describes the **personal entry selection** and contains a local ID per entry plus an optional display name.
- `profiles/*.json` describe **reusable** fetch and version extraction for a source.

An entry is configured either directly (with all technical fields in `devices.json`) or profile-based (via a profile reference). Both forms may be mixed. The root object contains a list `devices`; every local entry ID must be unique. The implementation supports the source types `text`, `json`, and `json_value`.

### 4.1 Common Fields in devices.json

| Field | Required | Meaning |
|-------|----------|---------|
| `id` | Yes | Local entry ID; unique state key |
| `name` | No | Local display name; overrides the profile name for profile-based entries, otherwise the ID or profile name is shown |
| `profile` | No | Reference to an entry profile; present = profile-based entry |
| `source_type` | Direct only | `text`, `json`, or `json_value` |
| `source_url` | Direct only | URL of the source to fetch |

If `profile` is set, **only** the fields `id`, `profile`, and `name` may occur. Technical fields such as `source_url`, `source_type`, `encoding`, or `version_pattern` are a configuration error; there is no silent overriding or ignoring. Individual technical settings remain possible via direct entries.

Additional descriptive fields such as manufacturer, model, or hardware version are not evaluated.

### 4.2 Text Source: TP-Link RE200 V4 EU

```json
{
  "id": "tplink-re200-v4-eu",
  "name": "TP-Link RE200 V4 EU",
  "source_type": "text",
  "source_url": "https://www.tp-link.com/de/support/download/re200/v4/",
  "version_pattern": "(RE200\\(EU\\)_V4_[0-9]+)"
}
```

`version_pattern` is a Python-compatible regular expression. Backslashes must be escaped in JSON. The pattern may contain no or exactly one capture group:

- Without a capture group, the full match is stored.
- With one capture group, its content is stored.
- More than one capture group leads to a check error.

The retrieved text is processed with `html.unescape`. There is no HTML-to-text conversion, DOM evaluation, or JavaScript execution. A pattern must therefore work on the directly retrieved content.

Example recognized label: `RE200(EU)_V4_200922`.

### 4.3 JSON Source: NanoStation M5 XM

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

The response must be a JSON object with a list under `items_key`. Every list element must be an object. The field under `version_key` must be a non-empty string; leading and trailing whitespace is removed.

Entries with `enabled: false` are skipped. If `enabled` is missing, the entry is considered. A faulty considered entry leads to an error for the entire entry check; no partial list is stored.

The keys are direct field names, not JSONPath expressions. Nested paths are not supported.

Example response:

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

Only `version` and the optional activation status are currently evaluated. Publication date, release notes, download link, product lists, and channels are not processed. The product-specific URL provides the assignment to the XM entry profile; the program does not verify this assignment additionally.

### 4.4 JSON Single Value: Shelly 1 Gen1

```json
{
  "id": "shelly-1-gen1-stable",
  "name": "Shelly 1 Gen1 – stable firmware",
  "source_type": "json_value",
  "source_url": "https://api.shelly.cloud/files/firmware",
  "value_path": ["data", "SHSW-1", "version"]
}
```

The response must be a JSON object. `value_path` is a list of keys resolved successively from the root object. The resulting value must be a non-empty string; leading and trailing whitespace is removed. Exactly this one value forms the recognized version.

The keys are direct field names, not JSONPath expressions. A missing path or a value that is not a string leads to a check error. Unlike `json`, there is no list, no `enabled`, and no merging of multiple entries.

Example excerpt:

```json
{
  "data": {
    "SHSW-1": {
      "version": "20230913-112003/v1.14.0-gcb84623"
    }
  }
}
```

### 4.5 Complete Structure

Profile-based and direct entries may be mixed:

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
      "name": "My Repeater"
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

The second entry overrides the profile name locally, the third is a direct entry.

### 4.6 Entry Profiles: Format and Required Fields

Example `profiles/coros.dura.json`:

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

| Field | Required | Requirement |
|-------|----------|-------------|
| `schema_version` | Yes | Integer, exclusively `2`; boolean values not allowed |
| `id` | Yes | Valid profile ID; matches filename without `.json` |
| `name` | Yes | Non-empty string |
| `category` | Yes | `firmware`, `software`, or `operating_system` |
| `source_type` | Yes | `text`, `json`, or `json_value` |
| `source_url` | Yes | Non-empty HTTP/HTTPS URL with host |
| `encoding` | No | Non-empty, Python-supported charset designation |
| `manufacturer` | No | Non-empty string |
| `model` | No | Non-empty string |

Source-type-specific required fields:

| Source type | Required fields |
|-------------|-----------------|
| `text` | `version_pattern`: non-empty, valid regular expression |
| `json` | `items_key`, `version_key`: non-empty strings |
| `json_value` | `value_path`: non-empty list of non-empty strings |

For `version_pattern`: without a capture group, the full match is the version; with exactly one capture group, its content; more than one capture group is a configuration error. Regex validity and group count are checked before a network request.

Additional metadata may be retained. They may not replace required validations or change fetch behavior. Credentials and runtime states do not belong in profiles.

### 4.7 Profile IDs and Secure File Paths

Profile IDs must fully match the pattern `[a-z0-9][a-z0-9._-]*`. Disallowed are empty IDs, `/`, `\`, absolute paths, spaces, uppercase letters, and path specifications like `../coros.dura`.

A profile file is determined exclusively as `<profiles_dir>/<profile_id>.json`. The resolved file path must lie within the profile directory; symbolic links must not allow access outside this directory. Profile IDs are not import paths; there are no dynamic Python imports or program execution based on these IDs.

### 4.8 Profile Resolution and Merging

Before every check and status output, all entries are resolved and validated:

1. Check local entry ID.
2. Check profile ID.
3. Determine profile file.
4. Load UTF-8 JSON.
5. Validate profile structure, schema version, and required fields.
6. Check match of profile ID with filename.
7. Create flat entry configuration.
8. Take over local entry ID.
9. Take over optional local name.

Example of resolved configuration:

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

The result is usable with `fetch_source()`, `extract_versions()`, `process_device()`, and `print_status()`. Read entry and profile objects are not modified during merging.

### 4.9 Direct Configuration (Backwards Compatibility)

The previous direct configuration remains supported:

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

For direct entries: `id` is a non-empty string; `name` remains optional and otherwise the local ID is shown; technical fields are validated according to the same rules as profiles; `schema_version` is not required; a missing profile directory does not affect direct entries.

## 5. State: state.json

Initial state:

```json
{
  "devices": {}
}
```

Example after successful check:

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

- `last_checked`: Time of the last **successful** entry check, in local time zone with UTC offset and second precision. Not a timestamp of a failed attempt.
- `known_versions`: All version labels successfully recognized for this entry profile so far.
- Version labels are deduplicated and sorted lexicographically.
- The sorting is **not** a semantic version comparison.
- Versions disappeared from the source remain known.
- No longer configured entries are not automatically removed from the state.

The state file is managed by the program. Stored under `state["devices"][device["id"]]`, where `device["id"]` is always the **local** entry ID (not the profile ID). Profile ID and local entry ID are separate: the profile ID describes the shared source, the local ID is the state key. When switching an entry to a profile, with the same local ID the same stored version state is retained; no migration is needed. Multiple local entries may reference the same profile; local IDs must be unique. The state format itself remains unchanged.

If the state file is deleted or an entry ID is changed, a new initialization begins for the affected entry. When changing the source or extraction logic, a conscious handling of the previous state is required; there is no automatic migration.

## 6. Check Run Flow

1. Read CLI arguments.
2. Load configuration and existing state.
3. Check list/state structure and unique local entry IDs.
4. Resolve all entries: load profiles and validate profile and source configuration. All configuration errors are detected before any network access.
5. Process entries one after another.
6. Fetch source with a network timeout of 30 seconds.
7. Decode response based on its charset header or fallback UTF-8.
8. Extract versions according to source type.
9. Remove duplicate hits; treat zero hits as a check error.
10. Compare with stored state.
11. Output result and update entry state on success.
12. After processing all entries, save state.

HTTP headers:

```text
User-Agent: firmAware/0.1
Accept: application/json,text/html,*/*
```

There is no own retry logic and no browser automation.

## 7. Comparison Logic and Initialization

On the first successful fetch of an entry profile, all recognized versions are stored as the baseline and displayed. They are not reported as new firmware entries.

On subsequent successful fetches:

```text
New entries = currently recognized versions − previously known versions
Stored versions = previously known versions ∪ currently recognized versions
```

Example:

```text
Known:  6.3.24, 6.3.26
Current: 6.3.24, 6.3.26, 6.3.27
New:    6.3.27
```

`6.3.27` is only a hypothetical example version here.

A newly appearing older entry is also reported. Changes to release notes or download links with unchanged version label are not detected. An already known version is not reported again, even if it was temporarily removed from the source.

## 8. Output and Return Codes

Regular results go to stdout, errors to stderr. Output is compact, one line per entry, without blank lines. Entries are grouped by category (Firmware, Software, Operating System; unknown categories under "Other"). Entry name and status are column-aligned.

`--status` shows an additional `ID` column per row, so entries can be selected specifically with `--device ID`.

`--check` (default) shows one section per category and one line per entry:

```text
<Entry>  Baseline (N): v1, v2, ...   # first successful check
<Entry>  new: v3, v4                 # newly found versions
<Entry>  no changes                   # no new versions
```

`--status` outputs the stored state in a table:

```text
ID                            Entry                                    Last check           Last version
----------------------------  ---------------------------------------  -------------------  --------------------------------
<ID>                          <Entry>                                  2026-10-12 19:29:44  RE200(EU)_V4_210916
```

`Last version` is the highest detected version label using best-effort numeric sorting (block count as primary criterion, numeric block comparison within equal block counts; e. g. `6.1.1978418030` > `1978074000`, `1.10` > `1.9`). Not yet checked entries appear with `never checked` and `–`. No network request and no change to the state file.

Possible messages:

```text
Baseline (N): ...
new: ...
no changes
Check error: ...
Configuration error: ...
Storage error: ...
```

Configuration errors are detected before every network request and name local ID, profile ID, and possibly the field:

```text
Configuration error: Entry 'coros-dura', profile 'coros.dura': Required field 'version_pattern' missing
```

| Exit code | Meaning |
|-----------|---------|
| `0` | All configured checks and storage successful (also without new entries) or `--status` successful |
| `1` | Configuration, check, or storage error |

There is no separate exit code for found updates and no structured JSON output.

## 9. Error Handling and Storage

- A device error does not prevent checking other entries.
- On failed check, the previous state of this entry remains.
- Zero hits are not interpreted as "no updates".
- Successfully checked entries are stored even if other entries fail.
- Writing first goes to a neighboring `.tmp` file; then the target file is replaced with `os.replace`.
- The parent directory of the state file is created if needed.
- No file lock. Concurrent invocations using the same state file are not supported.
- Terminal message and storage do not form a transaction. After a storage error, the message may appear again on the next run.
- On a configuration error, an understandable message on `stderr`, exit code `1`, no network request, no change of `state.json`, and no uncontrolled traceback.

At least handled: missing or unreadable profile file; invalid JSON or profile not an object; unsupported schema version; invalid or mismatched profile ID; missing or incorrectly typed required fields; unknown source type; invalid regular expression or more than one capture group; unknown charset; invalid source URL; disallowed fields in profile-based entry; duplicate local entry IDs; file path outside profile directory.

The configuration check of the basic structure is intentionally simple. Profiles and technical source configurations are fully validated before fetching. Fetch errors remain entry-specific and do not prevent checking other correctly configured entries.

## 10. Current Limits and Security

- No automatic schedule and no running service.
- No push, email, or MQTT notifications.
- No collection of installed device firmware.
- No best-effort "higher/lower" version comparison for upgrade decisions; the displayed "Last version" is the highest detected label using best-effort numeric sort, not a guarantee that an update is available.
- No confirmation of device-specific update release.
- No automatic model, hardware, or region detection.
- No distinction between beta and stable unless the source itself is limited accordingly.
- No JavaScript execution, authentication, pagination, or bypassing of HTTP blocks (e. g. HTTP 403).
- No automatic firmware installation.
- No guaranteed long-term stability of the manufacturer endpoints used.
- URLs and search patterns are considered trusted local configuration and are not intended for unverified public input.
- Entry profiles are loaded only via secure profile IDs within the profile directory; symlinks must not lead outside the directory. Profiles contain no credentials and no runtime states.
- User-Agent, fetch frequency, and network access can influence the behavior of the manufacturer sources.

Live behavior: The Ubiquiti JSON structure was configured based on the response provided by the user. The original Ubiquiti HTML page provided no hits for the XM filename pattern in the terminal. For Shelly, the single value was successfully extracted via `data → SHSW-1 → version`. The TP-Link labels were successfully extracted with urlwatch; direct fetching by this Python prototype must be confirmed separately. The extraction and comparison logic is covered offline by `tests/test_firmaware.py`; a complete live integration test was not possible. The COROS-DURA profile does not change the known COROS HTTP-403 behavior and is not a solution for it.

## 11. Acceptance Criteria / Manual Tests

| Case | Expectation |
|------|-------------|
| First successful check | Versions stored as baseline, no update message |
| Same source on second run | No new firmware entries |
| Additional unknown version | One-time message and subsequent storage |
| Same entry on next run | No repeated message |
| Additional older version | Also reported as newly found entry |
| Duplicate hits | Stored and reported only once |
| Version disappears from source | Not removed from `known_versions` |
| Empty extraction | Check error, previous state retained |
| HTTP, decoding, or JSON error | Check error, next entry still checked |
| JSON entry with `enabled: false` | Skipped |
| Missing version field in active JSON entry | Entry check fails |
| `json_value` with valid path | Exactly one version stored as baseline |
| `json_value`: missing path or value not a string | Check error, previous state retained |
| Duplicate entry IDs | Configuration error |
| Missing state file | Empty state used, file created on storage |
| Invocation from another working directory | Default files next to script used |
| Changed entry ID | New entry initialized; old state record retained |
| Valid text profile | Technical fields correctly taken over |
| Valid JSON profile | Existing JSON extractor usable |
| Valid JSON-Value profile | Existing path extractor usable |
| Local ID differs from profile ID | Local ID remains state key |
| Local name present | Overrides profile name |
| No local name | Profile name used |
| Mixed profile-based and direct entries | Both formats resolved |
| Two entries with same profile | Each with own local ID |
| Switching to profile with same local ID | Stored version state retained |
| Technical fields in profile-based entry | Configuration error, no silent ignoring |
| Missing profile file / invalid JSON / not an object | Configuration error |
| Invalid profile ID / mismatched profile ID / schema version | Configuration error |
| Invalid source fields (type, URL, regex, charset) | Configuration error before fetch |
| Path manipulation / symlink outside profile directory | Access denied |
| `--profiles-dir` | Chosen directory used |
| `--status` with profiles | No network request |
| One invalid profile | No check and no state change |
| Direct configuration without profile directory | Works |
| COROS extraction from sample data | `["3.0808.0", "3.0910.0"]` |
| `--device <id>` | Only this one entry is checked/shown |
| `--device <id>` not existent | Configuration error, exit code 1 |
| `--status` ID column | Each row shows the entry ID |
| Clustering by category | Entries grouped under Firmware, Software, Operating System, Other |
| Highest version shown | `max(versions, key=version_sort_key)` instead of last sorted element |

## 12. Possible Next Steps – Not Implemented

Priority and scope are open:

1. Further improvements of error diagnostics for fetch problems.
2. Release date and release notes link in messages.
3. Optional separate initialization and check mode.
4. A simple schedule outside the script.
5. A notification channel.
6. Entry-specific collection of installed versions.
7. Manufacturer-specific semantic version comparison.
8. More entry profiles, for example for additional models.

Automated tests with stored example responses are now implemented in `tests/test_firmaware.py` (see sections 3 and 11) and are therefore no longer listed as an open point. Reusable entry profiles were implemented with version 0.2.

Web interface, Docker package, and Home Assistant integration are possible later extensions, not a prerequisite for the current prototype.

## 13. Profile Wizard (`--create-profile`)

The profile wizard creates a text/HTML entry profile dialog-driven, without requiring knowledge of the JSON schema or regular expressions. It works exclusively rule-based: **no AI, no external AI services, no additional Python dependencies**. The only data transfer is the source fetch triggered by the user.

### 13.1 Invocation and Flow

```bash
python3 firmaware.py --create-profile --profiles-dir ./profiles
```

Dialog steps:

1. **Describe entry:** manufacturer, model, display name (suggestion `<Manufacturer> <Model> – Firmware`), profile ID, and category (`firmware`, `software`, or `operating_system`; suggestion: `firmware`). The ID suggestion is lowercased, normalized to ASCII, and cleaned of disallowed characters; the user must confirm or change the suggestion. If no usable suggestion is possible, the ID is requested manually. The final ID must fully match `[a-z0-9][a-z0-9._-]*`.
2. **Fetch source:** enter URL. Only HTTP/HTTPS with host, no credentials, no control characters; a fragment is removed before fetching. Fetch using the same logic as regular checks (30 s timeout, fixed headers), additionally with size limit (5 MiB) and validation of every redirect target. On decoding/HTTP error, the URL can be corrected or an explicit Python charset can be chosen.
3. **Capture sample version:** a version label visible on the page (max. 100 characters). This means the **source version** (published firmware label), **not** the installed device version.
4. **Select occurrence:** all exact occurrences are shown with limited context (150 characters each). With multiple occurrences, an explicit selection is needed (pagination with many hits); the first occurrence is never selected automatically. Control characters from foreign content are removed before output.
5. **Suggest version format:** from a fixed template collection. Supported: dotted version (`16.2.1`), prefix version (`V 3.0910.0`), and composite version (`1.13/1.02`). From a single sample version, the future block count cannot be derived with certainty, so two variants are offered: exact block count and "one more block". Each candidate has exactly one capturing group; non-capturing helper groups are not used. Leading zeros are retained, a fixed prefix is not included in the value.
6. **Validation and preview:** each candidate is compiled, checked for exactly one group, and tested with the existing extractor. The preview shows ID, name, manufacturer/model, source URL (and final URL on redirect), charset, scope, expected value, all unique versions, number of occurrences separated from number of unique values, and the regex. Export requires explicit confirmation; optionally a fresh fetch and test is done beforehand.
7. **Export:** atomic via temporary neighbor file, only within the profile directory, no silent overwriting.

Exit codes: `0` profile exported, `1` technical error or unsupported source or end of input before completion, `130` abort via `Ctrl+C`. On abort or error, no final profile file is created and no existing file is modified. A newly created empty export directory may remain.

### 13.2 Difference Between Full Source Mode and Table Row Scope

This scope covers the **full source mode**: the version pattern applies to the entire `html.unescape()`-normalized content, and all hits are shown for confirmation. The **table row mode** (limiting the pattern to a selected HTML table row via a text anchor) described in the original requirement is **not** included. Likewise, the **expert mode** (manual entry of a complete `version_pattern`) is **not** included. Formats outside the fixed templates are therefore rejected with a hint to correct. An extension by table row scope and expert mode remains possible; the profile schema must remain unchanged for that (the scope would be integrated into `version_pattern`, no new `selector` property).

### 13.3 Protection Against Problematic Regex

All candidate tests run in a **separate process** with a time limit (3 seconds, `multiprocessing` with `spawn` context, cross-platform without Unix signals). On timeout, the process is terminated and the candidate is rejected; no profile file is exported. This protection applies only to the wizard, not to regular check runs.

### 13.4 Limits

- No AI and no automatic semantic evaluation.
- No web interface, DOM selection, JavaScript execution, or browser automation.
- No login, cookies, or circumvention of HTTP blocks (e. g. HTTP 403).
- No automatic changes to `devices.json` or `state.json`.
- No general-purpose HTML parser; the version must be in the directly retrieved content.
- A prefix with digits (e. g. `RE200(EU)_V4_`) is not recognized by the templates.
- The wizard is a local CLI fetch and **not** intended as a publicly reachable URL fetch service. A later web interface requires a separate SSRF security requirement.

### 13.5 Test Result and Export

After success, the wizard explicitly states that only the current response was successfully tested and that future page changes or version formats are not checked. No guarantees such as "permanently verified" are given.

The exported profile corresponds to schema version 2 and is usable with the existing resolver. Example:

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

The example address is a placeholder, not a predefined COROS source. The exported pattern must contain the limits confirmed in the dialog; the above regex merely illustrates the schema. `encoding` is exported only if the user explicitly chose a charset. After export, the file is reloaded and validated. For integration, the wizard shows an example for `devices.json`; with an existing entry, the local ID remains. `devices.json` and `state.json` are never modified by the wizard.
