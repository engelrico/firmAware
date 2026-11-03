# AGENTS.md

This document helps AI agents working on the firmAware project understand the codebase, conventions, and common tasks. For the detailed technical specification, see [`requirements/specification.md`](requirements/specification.md).

---

## Project Overview

**firmAware** checks public firmware/software sources for configured entries and reports previously unknown version labels in the terminal. Known versions are stored locally in `state.json`, so each run only highlights new entries.

- **Tech stack:** Python 3 standard library only (no pip dependencies, no database)
- **Entry point:** `firmaware.py` (thin wrapper); implementation in the `firmaware_core/` package
- **Configuration:** `devices.json` (personal selection) + `profiles/*.json` (reusable fetch/extraction logic)
- **State:** `state.json` (known versions + last check times, keyed by local entry ID)
- **Tests:** `tests/test_firmaware.py` (unittest, no network access)
- **Full specification:** `requirements/specification.md`

---

## Project Structure

```
firmAware/
├── firmaware.py              # command-line entry point (re-exports firmaware_core)
├── firmaware_core/           # implementation: fetch, sources, extract, profiles, state, checking,
│                             #   report, notify, service, locking, interactive, cli, web/ (+ constants, errors)
├── devices.template.json     # starting point: one example per entry form
├── devices.json              # personal entry selection (git-ignored)
├── profiles/                 # reusable device profiles (*.json)
│   └── *.json                # schema v2 with category field
├── state.template.json       # empty state template (reference)
├── state.json                # local runtime state (git-ignored, created on first --check)
├── tests/
│   └── test_firmaware.py     # automated tests (standard library only)
├── requirements/
│   └── specification.md      # detailed technical specification
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

Every profile in `profiles/*.json` must conform to schema v2 as defined in [`requirements/specification.md#46-entry-profiles-format-and-required-fields`](requirements/specification.md#46-entry-profiles-format-and-required-fields), which is the single reference for fields, allowed values, and source types. Non-negotiable: `schema_version` is `2`, `category` is present, and the `id` matches `[a-z0-9][a-z0-9._-]*` and the filename.

Profile-based entries in `devices.json` may contain **only** `id`, `profile`, `name`, `enabled`, and `installed_version`. Technical fields are a configuration error in profile-based entries.

### Version Sorting

The `version_sort_key` function compares versions **block by block as integers with zero padding** for shorter labels, so `27.1` > `27.0.1`, `26.7.1` > `18.7.10`, and `1.10` > `1.9`. A single numeric block of 100 000 000 or more is treated as a legacy label and ranked below multi-block labels: `6.1.1978418030` > `1978074000`. See [`requirements/specification.md#7-comparison-logic-and-initialization`](requirements/specification.md#7-comparison-logic-and-initialization).

### Wording (English Only)

- Use **"Entry"** not "Device" (in error messages, CLI output, comments)
- Error messages in English: `Configuration error:`, `Check error:`, `Storage error:`
- Status lines: `Baseline (N):`, `new:`, `no changes`

### CLI

The CLI options are documented once, in the [README](README.md#cli-options) (and by `python3 firmaware.py --help`). Behavior is specified in [`requirements/specification.md`](requirements/specification.md#3-project-files-and-invocation).

### Credentials

SMTP credentials for `--notify` live only in `notify.json` outside the
repository (`~/.config/firmaware/notify.json`, `chmod 600`). The file is
git-ignored. Never store credentials in `devices.json`, `profiles/*.json`, or
any committed file.

---

## Common Tasks

### Add a new profile

**Option A: Manual**

1. Create `profiles/<id>.json` with schema v2 and `category` field
2. Add `{ "id": "<local-id>", "profile": "<id>" }` to `devices.json`
3. Validate: `python3 -c "import firmaware as fw; fw.load_profile('<id>', Path('profiles'))"`

**Option B: Use the profileCreator agent**

The agent at `.opencode/agents/profileCreator.md` guides you through URL input, version number, manufacturer/model, and writes the profile plus `devices.json` entry.

`devices.json` is git-ignored. On a fresh clone it does not exist yet; copy
`devices.template.json` to `devices.json` before adding entries.

### Add a source type

Add the field validation and the extraction function to `firmaware_core/sources.py` and register a `SourceType` in `SOURCE_TYPES`. Nothing else in the code changes. Then document it in the specification (section 4.6) and the README; a test fails if either is missing.

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
6. **Tests must pass** after any change to `firmaware.py`, `firmaware_core/`, or `tests/test_firmaware.py`.
7. **All UI text, error messages, and documentation must be in English.** The web frontend (`firmaware_core/web/static/`) is vanilla HTML/CSS/JS: no build step, no framework, no third-party or external requests.
8. **`devices.json` and `state.json` are local files.** Both are git-ignored and must never be committed; the repo ships `devices.template.json` (one example per entry form) and `state.template.json` (empty `{"devices": {}}`).
9. **Documentation Sync:** Each fact lives in one place: schema, behavior, and output in `requirements/specification.md`; CLI options and user-facing usage in `README.md`. When changing `firmaware.py`, `firmaware_core/`, `profiles/*.json`, `devices.json`, `state.json`, or the CLI, update those two files. Update `AGENTS.md` and `CONTRIBUTING.md` only when conventions or workflow change; link to the specification instead of copying tables. All documentation must be in English.

---

## profileCreator Agent

Location: `.opencode/agents/profileCreator.md`

Purpose: Interactively create an entry profile for a manufacturer page and bind it in `devices.json`.

Steps:
1. Accept a release/source URL
2. Ask for the current version number
3. Derive or ask for manufacturer and model
4. Generate a valid profile ID
5. Create `profiles/<id>.json` (schema v2, `category: firmware`)
6. Add `{ "id": "<local-id>", "profile": "<id>" }` to `devices.json` if not present
7. Show a summary for confirmation

No network access is performed by the agent — the user supplies the version number.
