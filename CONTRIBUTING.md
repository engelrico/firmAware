# CONTRIBUTING.md

Guidelines for contributing to firmAware.

---

## Getting Started

firmAware runs locally and requires **no dedicated cloud service**. It relies **exclusively on the Python standard library** — there are no pip dependencies and no database.

For the detailed technical specification, see [`requirements/specification.md`](requirements/specification.md).

### Local Setup

```bash
# Clone the repository
git clone https://github.com/engelrico/firmAware.git
cd firmAware

# Run the checks
python3 firmaware.py --check

# Show stored state without network
python3 firmaware.py --status

# Run the test suite
python3 -m unittest discover -s tests -t .
```

Python 3.14+ is recommended (tested with Python 3.14).

---

## How to Contribute

### 1. Fork and Branch

Fork the repository on GitHub, then create a feature branch:

```bash
git checkout -b feature/your-feature-name
```

### 2. Make Your Changes

Follow the conventions described in [`AGENTS.md`](AGENTS.md):

- Profile schema v2 with `category` field (`firmware`, `software`, `operating_system`)
- Profile IDs matching `[a-z0-9][a-z0-9._-]*`
- English wording for all UI text, error messages, and documentation
- "Entry" instead of "Device" in messages

### 3. Add or Update Profiles

When adding a new profile to `profiles/*.json`:

1. Use `schema_version: 2`
2. Include the required `category` field
3. Set `manufacturer` and `model` where known
4. Validate with the profile resolver:
   ```bash
   python3 -c "import firmaware as fw; from pathlib import Path; fw.load_profile('<id>', Path('profiles'))"
   ```
5. Add the entry to `devices.json`:
   ```json
   { "id": "<local-id>", "profile": "<id>" }
   ```

### 4. Test

Run the full test suite:

```bash
python3 -m unittest discover -s tests -t .
```

All tests must pass. If you change `firmaware.py`, add or update tests in `tests/test_firmaware.py` accordingly.

### 5. Commit and Push

Use descriptive commit messages:

```bash
git commit -m "Add Nintendo Switch profile using THZoria/NX_Firmware releases"
git push origin your-branch-name
```

Then open a Pull Request on GitHub.

---

## Profile Guidelines

### Required Fields

The full field reference is in [`requirements/specification.md#46-entry-profiles-format-and-required-fields`](requirements/specification.md#46-entry-profiles-format-and-required-fields).

| Field | Required | Description |
|-------|----------|-------------|
| `schema_version` | Yes | Must be `2` |
| `id` | Yes | Profile ID, matches filename without `.json`, pattern `[a-z0-9][a-z0-9._-]*` |
| `name` | Yes | Human-readable display name |
| `category` | Yes | `firmware`, `software`, or `operating_system` |
| `source_type` | Yes | `text`, `json`, or `json_value` |
| `source_url` | Yes | HTTP/HTTPS URL without credentials |

### Optional Fields

| Field | Description |
|-------|-------------|
| `encoding` | Python charset name (e. g. `utf-8`, `cp1252`, `latin-1`) |
| `manufacturer` | Device manufacturer |
| `model` | Device model |

### Rules

- **No credentials** in profiles (no usernames, passwords, or API keys)
- **No runtime state** in profiles (profiles are static configuration only)
- **Symlinks must not lead outside** the profiles directory
- **Profile IDs are not import paths** — no dynamic Python imports based on IDs

### Source Types

The full source type reference is in [`requirements/specification.md`](requirements/specification.md):

| `source_type` | Required fields | Description |
|---------------|-----------------|-------------|
| `text` | `version_pattern` | Regex on HTML/text (after `html.unescape`) |
| `json` | `items_key`, `version_key` | Versions from a JSON list |
| `json_value` | `value_path` | Single value via a nested JSON path |

---

## CLI Options

| Option | Meaning |
|--------|---------|
| `--check` | Check sources and update state (default) |
| `--status` | Show stored state only, no network request |
| `--create-profile` | Interactively create and export an entry profile |
| `--interactive`, `-i` | Interactively manage entries (check, history, reorder, edit, duplicate, delete, disable) |
| `--device ID`, `-d ID` | Check or show only the entry with this ID |
| `--devices PATH` | Path to `devices.json` |
| `--state PATH` | Path to `state.json` |
| `--profiles-dir PATH` | Directory with device profiles |

`--device ID` also works as a positional argument: `python3 firmaware.py --check nanostation-m5-xm`.

---

## Error Handling

- Configuration errors are detected **before** any network request
- On configuration error: message on `stderr`, exit code `1`, no network access, no changes to `state.json`
- Device errors (fetch/extraction failures) do not stop other devices from being checked
- State is written atomically via a `.tmp` file
- No file locking — concurrent invocations with the same state file are not supported

---

## Documentation Sync Guardrail

When changing `firmaware.py`, `profiles/*.json`, `devices.json`, `state.json`, `README.md`, or `requirements/*.md`, you must also update `requirements/specification.md`, `AGENTS.md`, and `CONTRIBUTING.md` to keep them consistent. All documentation must be in English.

Before committing, verify:
- [`requirements/specification.md`](requirements/specification.md) reflects any schema, CLI, output, or comparison changes
- [`AGENTS.md`](AGENTS.md) reflects any CLI, profile schema, or output grouping changes
- All documentation text is in English (no German)
- All profile examples show `schema_version: 2` and the `category` field

A dedicated [`docGuardian`](.opencode/agents/docGuardian.md) agent is available under `.opencode/agents/` to help verify documentation consistency before commits.

---

## Questions?

Open an issue on GitHub or contact the repository maintainer.
