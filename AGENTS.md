# AGENTS.md

This document helps AI agents working on the firmAware project understand the codebase, conventions, and common tasks.

---

## Project Overview

**firmAware** checks public firmware/software sources for configured devices and reports previously unknown version labels in the terminal. Known versions are stored locally in `state.json`, so each run only highlights new entries.

- **Tech stack:** Python 3 standard library only (no pip dependencies, no database)
- **Entry point:** `firmaware.py`
- **Configuration:** `devices.json` (personal selection) + `profiles/*.json` (reusable fetch/extraction logic)
- **State:** `state.json` (known versions + last check times, keyed by local entry ID)
- **Tests:** `tests/test_firmaware.py` (unittest, no network access)

---

## Project Structure

```
firmAware/
├── firmaware.py              # fetch, extraction, comparison, profiles, wizard, CLI
├── devices.json              # personal device selection (id + profile or direct config)
├── profiles/                 # reusable device profiles (*.json)
│   └── *.json                # schema v2 with category field
├── state.json                # known versions and last checks
├── tests/
│   └── test_firmaware.py     # automated tests (standard library only)
├── requirements/
│   ├── specification.md
│   ├── firmAware-requirement-geraeteprofile.md
│   └── firmAware-requirement-profilassistent.md
├── .opencode/
│   └── agents/
│       └── profileCreator.md # agent for interactive profile creation
├── AGENTS.md                 # this file
├── CONTRIBUTING.md           # guidelines for human contributors
├── README.md
└── LICENSE
```

---

## Conventions & Rules

### Profile Schema (version 2)

Every profile in `profiles/*.json` must have:

| Field | Required | Allowed values / notes |
|-------|----------|------------------------|
| `schema_version` | Yes | Must be `2` |
| `id` | Yes | Must match `[a-z0-9][a-z0-9._-]*`, must match filename without `.json` |
| `name` | Yes | Non-empty string |
| `category` | Yes | One of: `firmware`, `software`, `operating_system` |
| `source_type` | Yes | `text`, `json`, or `json_value` |
| `source_url` | Yes | Valid HTTP/HTTPS URL without credentials |
| `encoding` | No | Valid Python charset name |
| `manufacturer` | No | Non-empty string |
| `model` | No | Non-empty string |

Profile-based entries in `devices.json` may contain **only** `id`, `profile`, and `name`. Technical fields (`source_url`, `source_type`, etc.) are a configuration error in profile-based entries.

### Version Sorting

The `version_sort_key` function compares versions by **block count first** (more numeric blocks = newer), then numerically per block. Example: `6.1.1978418030` (3 blocks) > `1978074000` (1 block); `1.10` > `1.9`.

### Wording (English Only)

- Use **"Entry"** not "Device" (in error messages, CLI output, comments)
- Error messages in English: `Configuration error:`, `Check error:`, `Storage error:`
- Status lines: `Baseline (N):`, `new:`, `no changes`

### CLI

| Option | Meaning |
|--------|---------|
| `--check` | Check sources and update state (default) |
| `--status` | Show stored state only, no network request |
| `--create-profile` | Interactively create and export a device profile |
| `--device ID`, `-d ID` | Check or show only the entry with this ID |
| `--devices PATH` | Path to `devices.json` |
| `--state PATH` | Path to `state.json` |
| `--profiles-dir PATH` | Directory with device profiles |

`--device ID` can also be passed as a positional argument after `--check` or `--status` (e.g. `python3 firmaware.py --check nanostation-m5-xm`).

---

## Common Tasks

### Add a new profile

**Option A: Manual**

1. Create `profiles/<id>.json` with schema v2 and `category` field
2. Add `{ "id": "<local-id>", "profile": "<id>" }` to `devices.json`
3. Validate: `python3 -c "import firmaware as fw; fw.load_profile('<id>', Path('profiles'))"`

**Option B: Use the profileCreator agent**

The agent at `.opencode/agents/profileCreator.md` guides you through URL input, version number, manufacturer/model, and writes the profile plus `devices.json` entry.

### Check a single entry

```bash
python3 firmaware.py --check --device nanostation-m5-xm
# or positional shortcut:
python3 firmaware.py --check nanostation-m5-xm
```

### Show status for a single entry

```bash
python3 firmaware.py --status --device nanostation-m5-xm
```

### Run tests

```bash
python3 -m unittest discover -s tests -t .
```

### Output grouping

`--status` and `--check` group entries by category: Firmware, Software, Operating System. Unknown/missing categories appear under "Other". Each `--status` row shows an `ID` column.

---

## Rules for Agents

1. **No network fetches unless explicitly requested.** The user provides version numbers manually when creating profiles.
2. **Profile schema v2 is mandatory.** Do not create or modify profiles with `schema_version: 1`.
3. **Always validate JSON** before writing profile files.
4. **Never modify `devices.json` or `state.json` without explicit user confirmation.**
5. **Profile IDs must match `[a-z0-9][a-z0-9._-]*`.** No uppercase, no slashes, no spaces.
6. **Tests must pass** after any change to `firmaware.py` or `tests/test_firmaware.py`.
7. **All UI text, error messages, and documentation must be in English.**

---

## profileCreator Agent

Location: `.opencode/agents/profileCreator.md`

Purpose: Interactively create a device profile for a manufacturer page and bind it in `devices.json`.

Steps:
1. Accept a release/source URL
2. Ask for the current version number
3. Derive or ask for manufacturer and model
4. Generate a valid profile ID
5. Create `profiles/<id>.json` (schema v2, `category: firmware`)
6. Add `{ "id": "<local-id>", "profile": "<id>" }` to `devices.json` if not present
7. Show a summary for confirmation

No network access is performed by the agent — the user supplies the version number.
