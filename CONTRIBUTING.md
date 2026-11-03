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

   `devices.json` is git-ignored. On a fresh clone it does not exist yet; copy
   `devices.template.json` to `devices.json` first.

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

Required and optional fields, allowed values, and source types are defined in [`requirements/specification.md#46-entry-profiles-format-and-required-fields`](requirements/specification.md#46-entry-profiles-format-and-required-fields). Do not copy them into other documents.

### Rules

- **No credentials** in profiles (no usernames, passwords, or API keys)
- **No runtime state** in profiles (profiles are static configuration only)
- **Symlinks must not lead outside** the profiles directory
- **Profile IDs are not import paths** — no dynamic Python imports based on IDs

---

## CLI Options

See the [README](README.md#cli-options) or run `python3 firmaware.py --help`.

**Credentials:** SMTP credentials for `--notify` live only in `notify.json`
outside the repository (`~/.config/firmaware/notify.json`, `chmod 600`). The
file is git-ignored. Never store credentials in `devices.json`,
`profiles/*.json`, or any committed file.

---

## Error Handling

Error handling, exit codes, storage behavior, and notification failures are specified in [`requirements/specification.md`](requirements/specification.md#9-error-handling-and-storage) and [section 13](requirements/specification.md#13-notification---notify). Configuration errors must be detected before any network request.

---

## Documentation Sync Guardrail

Each fact has one home:

| Fact | Where |
|------|-------|
| Profile schema, source types, behavior, output, errors | `requirements/specification.md` |
| CLI options and user-facing usage | `README.md` |
| Conventions and working agreements | `AGENTS.md`, `CONTRIBUTING.md` (link, do not copy) |

When changing `firmaware.py`, `profiles/*.json`, `devices.json`, `state.json`, or the CLI, update the specification and the README. All documentation must be in English.

Before committing, verify:
- The specification reflects any schema, CLI, output, or comparison changes
- The README lists every CLI option (a test checks this)
- All documentation text is in English (no German)
- All profile examples show `schema_version: 2` and the `category` field

A dedicated [`docGuardian`](.opencode/agents/docGuardian.md) agent under `.opencode/agents/` helps verify documentation consistency before commits.

---

## Questions?

Open an issue on GitHub or contact the repository maintainer.
