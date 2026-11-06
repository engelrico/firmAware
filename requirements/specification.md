# firmAware – Specification

Status: 17 October 2026  
Version: 0.5 (schema v2 with category, clustering, and single-entry check)

## 1. Goal and Scope

firmAware checks public firmware/software sources for configured entries and reports previously unknown version labels in the terminal. Known versions are stored locally in `state.json`, so each run only highlights new entries.

The application is operated locally by the user and requires no dedicated cloud service. It observes published version lists. It does **not** check the firmware installed on a device and does **not** confirm that an update is available for a specific device.

This specification describes the current state of `firmaware.py`, `firmaware_core/`, `devices.json`, `profiles/*.json`, `state.json`, and `tests/test_firmaware.py`. Entries can be configured directly or via reusable profiles. Future ideas are listed separately in section 12 and are not part of the implementation.

## 2. Technical Foundation

- Python 3; exclusively standard library, no pip dependencies.
- CLI for scheduled runs; an optional web interface (`--serve`, section 14) runs as a foreground server. No scheduling inside the program.
- HTTP queries via `urllib.request`.
- JSON processing via `json`, regular expressions via `re`.
- Automated tests via `unittest` (standard library), without network access.
- Storage in local JSON files, no database.
- No firmware installation and no downloads of firmware files.
- Internet access to the configured sources is required.

## 3. Project Files and Invocation

| File | Purpose |
|------|---------|
| `firmaware.py` | Command-line entry point; re-exports the `firmaware_core` package |
| `firmaware_core/` | Implementation: `fetch`, `sources` (source type registry), `extract`, `profiles`, `state`, `checking`, `report`, `notify`, `service` (entry management and checks without output), `locking`, `cli`, `web` (HTTP server and static frontend), plus `constants` and `errors` |
| `devices.json` | Personal entry selection (direct or via profiles) |
| `devices.template.json` | Tracked starting point with one example per entry form |
| `profiles/*.json` | Reusable entry profiles (fetch and version extraction) |
| `state.json` | Known versions and last successful checks |
| `state.template.json` | Tracked empty state structure |
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

Manage entries, run checks, and see the status in a browser (section 14):

```bash
python3 firmaware.py --serve
```

The former `--interactive` terminal mode was replaced by the web interface. `--interactive` and `-i` remain only as hidden options that print a pointer to `--serve` on `stderr` and exit with code `1`.

`--check`, `--status`, and `--serve` are mutually exclusive. `--profiles-dir PATH` applies to all three modes.

Custom file paths:

```bash
python3 firmaware.py --devices /path/devices.json --state /path/state.json
```

Custom profile directory:

```bash
python3 firmaware.py --devices ./devices.json --profiles-dir ./profiles --check
```

Without arguments, both JSON files are looked up relative to the Python file's directory – **not** relative to the current working directory. An explicitly relative `--profiles-dir`, however, is resolved relative to the current working directory. If the state file does not exist, an empty state is used. Configuration and state are read and written as UTF-8.

With `--device NAME` (short form `-d NAME`) or a name as a positional argument after `--check`/`--status`, only the corresponding entry is checked or shown. The argument is matched first against the entry IDs (exact), then against the display names (case-insensitive, surrounding spaces ignored). Nothing found is a configuration error (`No entry 'X'`); several entries with the same name are a configuration error that lists their IDs, and the ID selects one of them.

```bash
python3 firmaware.py --check --device "NanoStation M5 XM"
python3 firmaware.py --check "NanoStation M5 XM"
python3 firmaware.py --status "NanoStation M5 XM"
```

With `--notify`, the check additionally sends a plain-text e-mail when new
versions were found or the run could not complete. `--notify-config PATH`
points to the SMTP configuration (default
`~/.config/firmaware/notify.json`). `--quiet` suppresses the normal output
(so cron logs stay small) but still writes errors to `stderr`. `--quiet`
applies to `--check` and `--status`.

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

**Local and private:** `devices.json` is per-user configuration and is git-ignored; it is never committed. The repository ships `devices.template.json` with one example per entry form: one profile-based entry and one direct `text` entry with a placeholder URL. On a missing configuration the program prints `No configuration yet: devices.json does not exist. Copy devices.template.json to devices.json and add your entries, or run --serve to create it in the web interface.` to `stderr` and exits with code `1`. The web interface starts with an empty list when the file is missing and creates it on the first change.

### 4.1 Common Fields in devices.json

| Field | Required | Meaning |
|-------|----------|---------|
| `id` | Yes | Technical entry ID; unique, stable, and never shown in the web interface. The web interface generates it from the entry name (`[a-z0-9._-]`, suffix `-2`, `-3`, ... if taken). It keys the record of a direct entry in `state.json` and identifies the entry in the web API and for `--device`. Renaming an entry does not change it |
| `name` | No | Local display name; overrides the profile name for profile-based entries, otherwise the ID or profile name is shown |
| `profile` | No | Reference to an entry profile; present = profile-based entry |
| `enabled` | No | Boolean; `false` skips the entry during `--check`; default `true` |
| `installed_version` | No | Non-empty string; the version label installed on this device, maintained by the user (see 4.10) |
| `device_url` | No | Address of the device itself in the network, shown as a link in the web interface. `http` or `https` with a host (a value without scheme such as `192.168.1.20` is stored as `http://192.168.1.20`), no credentials, at most 2048 characters; `javascript:` and other schemes are a configuration error. Not allowed in a profile file; not copied when an entry is duplicated |
| `source_type` | Direct only | `text`, `json`, or `json_value` |
| `source_url` | Direct only | URL of the source to fetch |

If `profile` is set, **only** the fields `id`, `profile`, `name`, `enabled`, `installed_version`, and `device_url` may occur. Technical fields such as `source_url`, `source_type`, `encoding`, or `version_pattern` are a configuration error; there is no silent overriding or ignoring. Individual technical settings remain possible via direct entries.

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
| `source_page` | No | `http`/`https` URL of the human-readable page for the firmware source (release notes, download page). Used only as a link in the web interface, never fetched; without it the link uses `source_url` |
| `manufacturer` | No | Non-empty string |
| `model` | No | Non-empty string |

Source-type-specific required fields:

| Source type | Required fields |
|-------------|-----------------|
| `text` | `version_pattern`: non-empty, valid regular expression; optional `value_path`: non-empty list of non-empty strings (the source is then a JSON document and the pattern is applied to the string at that path) |
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
9. Take over optional local name, `enabled`, and `installed_version`.

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

The result is usable with `fetch_source()`, `extract_versions()`, `check_device()` (returns a `CheckResult` without output or state changes), `record_check()`, `process_device()`, and `print_status()`. Read entry and profile objects are not modified during merging.

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

### 4.10 Installed Version per Device

A profile describes a device **type**; an entry describes one concrete **device**. Several entries may reference the same profile (for example two e-readers of the same model) with different local IDs and names. Each entry can record the version installed on that device in the optional field `installed_version`:

```json
{ "id": "tolino-living-room", "profile": "tolino.shine-4", "name": "Tolino (living room)", "installed_version": "5.1.2" }
```

- The field is user-maintained configuration in `devices.json`; the program writes it only on request from the web interface, which accepts only versions known for the source. A value written by hand in the file may be any non-empty string. `state.json` is unchanged.
- It is allowed on profile-based and direct entries and is a configuration error inside a profile file or if it is not a non-empty string.
- **Comparison:** the latest known version is `max(discovered_at, key=version_sort_key)`. The installed version is `current` if its sort key is greater than or equal to the latest one, `outdated` if lower, `unknown` if nothing has been discovered yet, and not set if the field is missing.
- Sources that publish several release tracks (for example Apple iOS 18.x and 26.x) report a device on an older track as `outdated`, because the comparison uses the highest label only.
- Entries of the same type share one check per run: the source is fetched, compared, and recorded once, and every entry reports the shared result and compares its own installed version. Sources shared by different profiles or direct entries still share one fetch per run (cache key: source URL and encoding); failed fetches are not cached.

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
      "discovered_at": {
        "6.3.24": "2026-09-15T10:00:00+02:00",
        "6.3.26": "2026-10-11T18:00:00+02:00"
      }
    },
    "shelly-1-gen1-stable": {
      "last_checked": "2026-10-11T18:00:05+02:00",
      "discovered_at": {
        "20230913-112003/v1.14.0-gcb84623": "2026-10-11T18:00:05+02:00"
      }
    }
  }
}
```

Profile-based entries share one record per device type under `profiles`, keyed by the profile ID; direct entries (without a profile) are their own source and stay under `devices`, keyed by the local entry ID:

```json
{
  "devices": {
    "my-router": { "last_checked": "2026-10-11T18:00:00+02:00", "discovered_at": { "2.10": "2026-10-11T18:00:00+02:00" } }
  },
  "profiles": {
    "apple.ios": {
      "last_checked": "2026-10-11T18:00:05+02:00",
      "discovered_at": { "26.0.1": "2026-09-20T10:00:00+02:00", "26.1": "2026-10-11T18:00:05+02:00" }
    }
  }
}
```

The versions belong to the device type, only `installed_version` belongs to a device: two iPhones with the profile `apple.ios` share one version list, one baseline, and one "new" message, and each compares its own installed version against it. The `profiles` section is optional; a state without it is valid.

- `last_checked`: Time of the last **successful** check of the source, in local time zone with UTC offset and second precision. Not a timestamp of a failed attempt.
- `discovered_at`: Mapping of every recognized version label to the timestamp when it was first discovered by a check.
- Versions disappeared from the source remain known.
- No longer configured entries are not automatically removed from the state.

**Local and private:** `state.json` is per-user runtime state. It is git-ignored and never committed; the repository ships `state.template.json` with the empty structure `{"devices": {}}`. A missing state file is treated as empty and created by the first `--check` run. `--status` prints `No state yet: state.json does not exist. Run --check first.` to `stderr` when the state file does not exist yet (also with `--device` and `--quiet`).

**Migration:** A legacy state file containing `known_versions` (a list) is automatically migrated to `discovered_at` on the first load; the `last_checked` timestamp is used as the discovery time for all existing versions.

**Migration to per-profile records:** States written before the `profiles` section hold one record per profile-based entry under `devices`. On the next `--check` (and on the first check in the web interface) these records are folded into one record per profile: the versions of all entries are united, the earliest discovery time of each version wins, and the latest `last_checked` is kept. The folded entry records are removed, records of entries that no longer exist stay untouched. Before the first rewrite the old file is copied to `<state file>.bak`; an existing backup is never overwritten. `--status` and reading in the web interface apply the same folding in memory without writing the file.

The state file is managed by the program. The record of an entry is found by `source_key()`: `state["profiles"][<profile ID>]` for profile-based entries, `state["devices"][<local entry ID>]` for direct entries. Multiple local entries may reference the same profile and then share the record; local IDs must be unique. Switching a direct entry to a profile starts from the profile's record (or a new baseline); the old entry record is kept but no longer used.

If the state file is deleted, or the ID of a direct entry is changed, a new initialization begins for the affected source. Renaming the ID of a profile-based entry keeps the versions, because they belong to the profile. When changing the source or extraction logic, a conscious handling of the previous state is required; there is no automatic migration.

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

`--status` shows no ID; entries are addressed by name (`--device NAME`). A disabled entry is marked `[off]` after its name.

`--check` (default) shows one section per category and one line per entry:

```text
<Entry>  Baseline (N): v1, v2, ...   # first successful check
<Entry>  new: v3, v4                 # newly found versions
<Entry>  no changes                   # no new versions
<Entry>  no changes  outdated: installed 5.1.2, latest 5.2.0   # installed version is behind
```

Devices of one type print the same summary (`Baseline`, `new:`, `no changes`, or the error) because they share the check; only the `outdated:` suffix differs per device.

The `outdated:` suffix follows any of the three summaries and appears only for entries with `installed_version` that is behind the latest known version. It is suppressed by `--quiet`.

`--status` outputs the stored state in a table:

```text
ID      Entry    Last check           Last version         First seen           Installed  State
------  -------  -------------------  -------------------  -------------------  ---------  --------
<ID>    <Entry>  2026-10-12 19:29:44  RE200(EU)_V4_210916  2026-10-12 19:29:44  –          –
```

`Installed` shows `installed_version` and `State` shows `current`, `outdated`, or `unknown`; both show `–` when no installed version is set.

`Last version` is the highest detected version label using best-effort numeric sorting: numeric blocks are compared position by position as integers and shorter labels are zero-padded on the right, so `27.1` > `27.0.1`, `26.7.1` > `18.7.10`, and `1.10` > `1.9`. A single numeric block of 100 000 000 or more is treated as a legacy label and ranked below multi-block labels (`6.1.1978418030` > `1978074000`). `First seen` shows when that version was first discovered by a check. Not yet checked entries appear with `never checked` and `–`. No network request and no change to the state file.

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
| `130` | Abort via `Ctrl+C`; caught cleanly, no notification |

There is no separate exit code for found updates and no structured JSON output.

## 9. Error Handling and Storage

- A device error does not prevent checking other entries.
- On failed check, the previous state of this entry remains.
- Zero hits are not interpreted as "no updates".
- Successfully checked entries are stored even if other entries fail.
- Writing first goes to a neighboring `.tmp` file; then the target file is replaced with `os.replace`.
- The parent directory of the state file is created if needed.
- The state is saved under an advisory file lock (`<state file>.lock`, via `fcntl.flock`, skipped where `fcntl` is unavailable) and merged per entry: the file is read again, only the entry records changed by this run are replaced, then it is written atomically. A concurrent process (for example another `--check` or the web interface) therefore does not lose its records. Two processes checking the same entry at the same time are not coordinated; the last write wins. `devices.json` is also written atomically (`.tmp` and `os.replace`). A lock that cannot be acquired within 30 seconds is a storage error.
- Terminal message and storage do not form a transaction. After a storage error, the message may appear again on the next run.
- On a configuration error, an understandable message on `stderr`, exit code `1`, no network request, no change of `state.json`, and no uncontrolled traceback.

At least handled: missing or unreadable profile file; invalid JSON or profile not an object; unsupported schema version; invalid or mismatched profile ID; missing or incorrectly typed required fields; unknown source type; invalid regular expression or more than one capture group; unknown charset; invalid source URL; disallowed fields in profile-based entry; duplicate local entry IDs; file path outside profile directory.

The configuration check of the basic structure is intentionally simple. Profiles and technical source configurations are fully validated before fetching. Fetch errors remain entry-specific and do not prevent checking other correctly configured entries.

## 10. Current Limits and Security

- No automatic schedule and no running service (scheduling is left to cron or a similar tool).
- Email notification is opt-in via `--notify` and needs an external SMTP relay; no push or MQTT notifications.
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

Live behavior: The Ubiquiti JSON structure was configured based on the response provided by the user. The original Ubiquiti HTML page provided no hits for the XM filename pattern in the terminal. For Shelly, the single value was successfully extracted via `data → SHSW-1 → version`. The TP-Link labels were successfully extracted with urlwatch; direct fetching by this Python prototype must be confirmed separately. The extraction and comparison logic is covered offline by `tests/test_firmaware.py`; a complete live integration test was not possible. The COROS DURA and Thermomix TM5 profiles read the Zendesk help center JSON API (`text` with `value_path`) instead of the HTML article page, which answered with HTTP 403 on some hosts; with the API URLs both entries were checked successfully on macOS and on a Raspberry Pi.

## 11. Acceptance Criteria / Manual Tests

| Case | Expectation |
|------|-------------|
| First successful check | Versions stored as baseline, no update message |
| Same source on second run | No new firmware entries |
| Additional unknown version | One-time message and subsequent storage |
| Same entry on next run | No repeated message |
| Additional older version | Also reported as newly found entry |
| Duplicate hits | Stored and reported only once |
| Version disappears from source | Not removed from `discovered_at` |
| Empty extraction | Check error, previous state retained |
| HTTP, decoding, or JSON error | Check error, next entry still checked |
| JSON entry with `enabled: false` | Skipped |
| Missing version field in active JSON entry | Entry check fails |
| `json_value` with valid path | Exactly one version stored as baseline |
| `json_value`: missing path or value not a string | Check error, previous state retained |
| `text` with `value_path` and valid path | Versions matched by `version_pattern` in the string at that path (after `html.unescape`) stored as for `text` |
| `text` with `value_path`: missing path or value not a non-empty string | Check error, previous state retained |
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
| `--device <name>` | Only this one entry is checked/shown; name matching ignores case |
| `--device <id>` | An exact ID selects its entry as well |
| `--device <name>` unknown or ambiguous | Configuration error, exit code 1; an ambiguous name lists the IDs |
| `--status` | No ID column; disabled entries are marked `[off]` |
| Entry names in the web interface | Adding, duplicating, and renaming reject a name already used by another entry (case-insensitive) |
| Clustering by category | Entries grouped under Firmware, Software, Operating System, Other |
| Highest version shown | `max(versions, key=version_sort_key)` instead of last sorted element |
| `installed_version` valid / invalid | Accepted (trimmed) / configuration error; not allowed in a profile file |
| Installed older / equal / newer than latest | `outdated` / `current` / `current` |
| Two entries of one type | One fetch per run; each shows its own state |
| Only outdated entries, nothing new | No mail |
| Outdated entry together with a new version | Mail with "Outdated devices" section |

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

Docker package and Home Assistant integration are possible later extensions, not a prerequisite for the current prototype.

## 13. Notification (`--notify`)

With `--notify`, a completed or aborted `--check` can additionally send a
plain-text e-mail. The feature is intended for unattended runs (cron, NAS,
Raspberry Pi). It uses only the standard library (`smtplib`,
`email.message.EmailMessage`).

### Trigger

| Situation | Mail subject |
|-----------|--------------|
| New versions found | `firmAware: <n> new version(s) found` |
| Some entries failed, run finished | `firmAware: <n> warning(s)` |
| Configuration or storage error | `firmAware: check failed` |
| Unexpected exception | `firmAware: check failed` |

- A mail is sent if the run found new versions **or** recorded at least one
  entry error. A run without changes and without errors sends nothing.
- Entries whose `installed_version` is behind the latest known version are
  listed in an "Outdated devices" section, and the subject gets an
  `<n> outdated` part. They never trigger a mail by themselves, so a device
  that stays outdated does not cause a mail on every run. Under "New versions",
  each new version is listed once per device type (named after the profile), followed by the installed version of every device of that type. Errors are counted and listed once per source, too.
- A manual abort via `Ctrl+C` is caught cleanly: a short notice is printed to
  `stderr` and the process exits with code `130`. It never triggers a
  notification.
- The failure notification for aborted runs is sent from a top-level error
  handler around the whole run (not for `--serve`).
- A notification failure (broken config, unreachable SMTP) is printed to
  `stderr` as `Notification error: ...` and never changes the exit code or
  crashes the check.

### SMTP configuration

The configuration is read from `--notify-config PATH` (default
`~/.config/firmaware/notify.json`) as a JSON object:

| Field | Required | Meaning |
|-------|----------|---------|
| `smtp_host` | Yes | SMTP server hostname |
| `smtp_user` | Yes | Login user (may be empty string for unauthenticated relay) |
| `mail_to` | Yes | Recipient address or list of addresses |
| `smtp_password` | No | Login password; sent only if user and password are non-empty |
| `smtp_port` | No | Port; default `587` for `starttls`, `465` for `ssl` |
| `smtp_security` | No | `starttls` (default), `ssl`, or `none` |
| `mail_from` | No | From address; defaults to `smtp_user` |

Credentials are stored **outside** the repository. `notify.json` is listed in
`.gitignore` and must be protected with filesystem permissions
(`chmod 600`). No credentials may be stored in `devices.json`, `profiles/`, or
any committed file.

### Exit codes

Notification does not change the exit-code semantics: `0` for a successful run
(even if a mail was sent), `1` for a configuration, check, or storage error.

## 14. Web Interface (`--serve`)

`python3 firmaware.py --serve [--host ADDRESS] [--port PORT] [--web-config PATH]` runs a foreground HTTP server (standard library `http.server`, threaded) with a JSON API and a static single-page frontend (`firmaware_core/web/`). It uses the same configuration and state files as the command line and the operations of `firmaware_core/service.py` (`Workspace`). `--serve` is mutually exclusive with `--check` and `--status`; `--host` and `--port` require `--serve`. Defaults: `127.0.0.1`, port `8765`.

### Frontend
Vanilla HTML, CSS, and JavaScript without build step, framework, or external request (`index.html`, `app.js`, `style.css`, `favicon.svg`). The start page is the `--status` table: entries grouped by category with the columns `ID`, `Entry`, `Last check`, `Last version`, `First seen`, `Installed`, `State`, read from the stored state without any network request. Search, a filter (all, outdated, disabled), light and dark theme following the system, and a card layout on narrow screens.

- **Check** (like `--check`): "Check all" and "Check now" per entry start a background job. A result panel shows the per-entry lines the command line prints (`Baseline (N): ...`, `new: ...`, `no changes`, `outdated: installed X, latest Y`, `Check error: ...`, `[disabled]`) with a progress bar; the table refreshes when the job ends. A reloaded page resumes the latest job. The web check never sends mail; scheduled mail stays with `--check --notify`.
- **Edit** (replaces the former `--interactive` mode): clicking an entry opens a details panel with the version history and these actions: edit the name (empty restores the profile name), change the device type of a profile-based entry (a list of the valid profiles; the entry then follows that profile's source and version list, keeps its installed version, and shows the new profile's name unless it has a local name; the new shown name must be free; direct entries cannot be switched), choose the installed version from a list of the versions known for the device type (newest first; no free text; "Not set" clears it; the list is empty until a check has found versions), mark it as updated to the latest known version, remove a wrongly detected version from the version history ("Remove" next to the version, two clicks; the history is shared by all devices of the type), enable or disable, move up or down within its category, duplicate under a new name (default `<name> (copy)`; the copy has no installed version), and delete (two clicks). Rows can also be reordered by drag and drop while no filter or search is active.
- **Links:** every entry shows a "Source" link (the profile's `source_page`, else its `source_url`) and, if `device_url` is set, a "Device" link, in the table (column "Links") and in the details panel. The device URL is edited in the panel (a value without scheme gets `http://`; empty clears it). Links open in a new tab with `rel="noopener noreferrer"` and are only rendered for `http`/`https` addresses.
- **Add:** the "Add entry" dialog lists the valid profiles (searchable, with a badge for types already in use) and creates a profile-based entry with the name you give it (prefilled with the profile name; every name can be used once) and a generated ID; the details panel of the new entry opens afterwards.
- **Accessibility and layout:** native `<dialog>` for the add form, a focus-trapped details panel closed with `Escape`, keyboard-operable controls, `aria-live` regions for progress and messages, larger touch targets on touch screens, and no external fonts or requests. Entries from invalid configuration stay visible with their error.

### API (JSON, under `/api`)
| Request | Result |
|---------|--------|
| `GET /api/entries` | `{"entries": [...]}` with `id`, `name`, `custom_name`, `category`, `kind` (`profile` or `direct`), `profile`, `enabled`, `source_url`, `source_page` (link target for the firmware source), `device_url` (or `null`; both are `null` for an entry with a configuration `error`), `source_type`, `type_devices`, `last_checked`, `latest_version`, `first_seen`, `known_versions`, `installed_version`, `installed_state` (`current`, `outdated`, `unknown`, or `null`), `error` |
| `GET /api/entries/{id}` | the same fields plus `history` (version, `discovered_at`, `newest`), newest version first |
| `GET /api/profiles` | `{"profiles": [...]}` with `id`, `name`, `category`, `manufacturer`, `model` of every valid profile |
| `PATCH /api/entries/{id}` | body with any of `profile` (profile-based entries only; must be a valid profile ID, otherwise `400`; `409` if the entry would then show a name another entry already uses), `name` (empty removes the local name; `409` if another entry already shows that name), `enabled`, `device_url` (empty clears; normalized as described in 4.1; invalid gives `400`), `installed_version` (empty clears; a new value must be a version known for the entry's source, otherwise `400`; an unchanged value is accepted); returns the entry |
| `POST /api/entries/{id}/installed-latest` | sets `installed_version` to the highest known version; `409` if none is known yet |
| `POST /api/entries` | `{"profile", "name"?, "id"?}` adds a profile-based entry at the end (`201`); the name defaults to the profile name, the ID is generated from the name unless given; `409` for a name or ID already used |
| `POST /api/entries/{id}/forget-version` | `{"version"}` removes a version from the known versions of the entry's source (shared by all devices of that type); returns the entry; `404` if the version is not in the history, `400` without a version. Installed versions are left as they are; a source left without any version is dropped, so its next check is a baseline; a version that the source still lists is found again by the next check |
| `POST /api/entries/{id}/duplicate` | `{"name"?, "id"?}` copies the entry below the original without `installed_version` (`201`); the default name is `<name> (copy)`, then `(copy 2)`, ...; `409` for a name or ID already used |
| `DELETE /api/entries/{id}` | removes the entry; its record in `state.json` is kept |
| `PUT /api/order` | `{"ids": [...]}` must list every entry exactly once; returns the new list |
| `POST /api/checks` | `{"ids"?: [...]}` starts a check of all enabled entries or the given IDs and returns `202` with the job; `409` while another check runs |
| `GET /api/checks`, `GET /api/checks/{job}` | the latest job (or `null`) and a job by ID: `status` (`running`, `done`, `failed`), `done`, `total`, `results` (`id`, `name`, `status` of `ok`, `error`, `disabled`, `summary`, `new_versions`, `error`), `error` |

Errors are `{"error": "<message>"}` with status `400`, `401`, `403`, `404`, `405`, `409`, `413`, or `500`. An entry that cannot be resolved (for example a missing profile) is listed with its `error` text instead of being dropped.

### Access control
- The server binds to `127.0.0.1` by default. Any other address needs an access token: `--web-config PATH` (default `~/.config/firmaware/web.json`, `{"token": "<at least 16 characters>"}`, outside the repository, `chmod 600`); without it the server refuses to start and prints how to create the file.
- With a token, every request must carry it as `Authorization: Bearer <token>` or as the `firmaware_token` cookie. Opening `/?token=<token>` once sets the cookie (`HttpOnly`, `SameSite=Strict`) and redirects. Tokens are compared in constant time.
- Without a token (loopback only) the `Host` header must be `localhost`, `127.0.0.1`, or `[::1]` with the server port, which blocks DNS rebinding.
- Requests other than `GET` must send `X-Requested-With: firmaware` and, if an `Origin` header is present, it must match the `Host`. The server sends no CORS headers.
- Only the files of the flat `static/` directory (`.html`, `.js`, `.css`, `.svg`) are served, by exact name and without directory access. Request bodies are limited to 64 KiB. Responses carry `Content-Security-Policy: default-src 'self'`, `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, and `Referrer-Policy: no-referrer`.
- Transport is plain HTTP. For access from other machines use an SSH tunnel or a reverse proxy with TLS.
