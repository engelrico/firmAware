#!/usr/bin/env python3
"""Tests für firmAware (firmaware.py) – nur Standardbibliothek (unittest)."""
from __future__ import annotations

import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import firmaware as fw


class TextExtractionTests(unittest.TestCase):
    def test_capture_group_is_used(self):
        device = {"version_pattern": r"RE200\(EU\)_V4_[0-9]+"}
        content = "Datei RE200(EU)_V4_200922 und RE200(EU)_V4_210916"
        self.assertEqual(
            fw.versions_from_text(device, content),
            {"RE200(EU)_V4_200922", "RE200(EU)_V4_210916"},
        )

    def test_without_group_full_match(self):
        device = {"version_pattern": r"v[0-9]+\.[0-9]+"}
        self.assertEqual(fw.versions_from_text(device, "v1.2 v3.4"), {"v1.2", "v3.4"})

    def test_html_entities_are_unescaped(self):
        device = {"version_pattern": r"FW&[0-9]+"}
        self.assertEqual(fw.versions_from_text(device, "FW&amp;7"), {"FW&7"})

    def test_two_groups_are_rejected(self):
        device = {"version_pattern": r"(a)(b)"}
        with self.assertRaisesRegex(ValueError, "höchstens eine Klammergruppe"):
            fw.versions_from_text(device, "ab")


class JsonExtractionTests(unittest.TestCase):
    DEVICE = {"items_key": "downloads", "version_key": "version"}

    def test_versions_are_collected_and_stripped(self):
        content = json.dumps({"downloads": [{"version": " 6.3.26 "}, {"version": "6.3.27"}]})
        self.assertEqual(fw.versions_from_json(self.DEVICE, content), {"6.3.26", "6.3.27"})

    def test_disabled_entries_are_skipped(self):
        content = json.dumps(
            {"downloads": [{"version": "a", "enabled": False}, {"version": "b", "enabled": True}]}
        )
        self.assertEqual(fw.versions_from_json(self.DEVICE, content), {"b"})

    def test_missing_version_fails(self):
        content = json.dumps({"downloads": [{"enabled": True}]})
        with self.assertRaisesRegex(ValueError, "Versionsfeld"):
            fw.versions_from_json(self.DEVICE, content)

    def test_non_list_fails(self):
        content = json.dumps({"downloads": {"version": "x"}})
        with self.assertRaisesRegex(ValueError, "JSON-Liste fehlt"):
            fw.versions_from_json(self.DEVICE, content)

    def test_non_object_entry_fails(self):
        content = json.dumps({"downloads": ["x"]})
        with self.assertRaisesRegex(ValueError, "Ungültiger JSON-Eintrag"):
            fw.versions_from_json(self.DEVICE, content)


class JsonValueExtractionTests(unittest.TestCase):
    def test_nested_path(self):
        device = {"value_path": ["data", "SHSW-1", "version"]}
        content = json.dumps({"data": {"SHSW-1": {"version": "20230913-112003/v1.14.0"}}})
        self.assertEqual(
            fw.versions_from_json_value(device, content), {"20230913-112003/v1.14.0"}
        )

    def test_invalid_value_fails(self):
        device = {"value_path": ["version"]}
        with self.assertRaisesRegex(ValueError, "Versionsfeld"):
            fw.versions_from_json_value(device, json.dumps({"version": "   "}))


class ExtractDispatchTests(unittest.TestCase):
    def test_unknown_source_type(self):
        with self.assertRaisesRegex(ValueError, "Unbekannter Quelltyp: html"):
            fw.extract_versions("html", "x", {})

    def test_empty_result_is_error(self):
        device = {"version_pattern": r"v[0-9]+"}
        with self.assertRaisesRegex(ValueError, "Keine Versionen gefunden"):
            fw.extract_versions("text", "keine Treffer", device)

    def test_sorted_list(self):
        device = {"version_pattern": r"v[0-9]+"}
        self.assertEqual(fw.extract_versions("text", "v2 v1 v2", device), ["v1", "v2"])


class CorosProfileTests(unittest.TestCase):
    PATTERN = r"\bV\s*([0-9]+(?:\.[0-9]+){2,3})\b"

    def test_extraction_from_sample(self):
        content = "V 3.0910.0\nV 3.0808.0\n"
        self.assertEqual(
            fw.extract_versions("text", content, {"version_pattern": self.PATTERN}),
            ["3.0808.0", "3.0910.0"],
        )


class CompareTests(unittest.TestCase):
    def test_first_run_has_no_new_versions(self):
        self.assertEqual(fw.compare_versions(["a", "b"], None), ([], []))

    def test_new_versions_are_detected(self):
        previous = {"known_versions": ["a", "b"]}
        known, new = fw.compare_versions(["a", "b", "c"], previous)
        self.assertEqual(known, ["a", "b"])
        self.assertEqual(new, ["c"])

    def test_invalid_stored_state_fails(self):
        with self.assertRaisesRegex(ValueError, "Ungültiger gespeicherter Versionsstand"):
            fw.compare_versions(["a"], {"known_versions": [1, 2]})


class StateStorageTests(unittest.TestCase):
    def test_roundtrip_and_parent_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "state.json"
            state = {"devices": {"x": {"known_versions": ["1"]}}}
            fw.save_state(path, state)
            self.assertEqual(fw.load_state(path), state)
            self.assertFalse(path.with_name(path.name + ".tmp").exists())

    def test_missing_state_starts_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(fw.load_state(Path(tmp) / "missing.json"), {"devices": {}})


class SourceValidationTests(unittest.TestCase):
    def test_valid_text_source(self):
        fw.validate_source_config(
            {
                "source_type": "text",
                "source_url": "https://example.com",
                "version_pattern": r"v[0-9]+",
            },
            "Gerät 'x'",
        )

    def test_unknown_source_type(self):
        with self.assertRaisesRegex(ValueError, "Unbekannter Quelltyp"):
            fw.validate_source_config(
                {"source_type": "html", "source_url": "https://example.com"}, "Gerät 'x'"
            )

    def test_invalid_url(self):
        with self.assertRaisesRegex(ValueError, "Ungültige Quell-URL"):
            fw.validate_source_config(
                {"source_type": "text", "source_url": "kein-url", "version_pattern": "x"},
                "Gerät 'x'",
            )

    def test_missing_pattern(self):
        with self.assertRaisesRegex(ValueError, "version_pattern"):
            fw.validate_source_config(
                {"source_type": "text", "source_url": "https://example.com"}, "Gerät 'x'"
            )

    def test_invalid_regex(self):
        with self.assertRaisesRegex(ValueError, "regulärer Ausdruck"):
            fw.validate_source_config(
                {"source_type": "text", "source_url": "https://example.com", "version_pattern": "("},
                "Gerät 'x'",
            )

    def test_two_groups_rejected(self):
        with self.assertRaisesRegex(ValueError, "Klammergruppe"):
            fw.validate_source_config(
                {
                    "source_type": "text",
                    "source_url": "https://example.com",
                    "version_pattern": "(a)(b)",
                },
                "Gerät 'x'",
            )

    def test_unknown_encoding(self):
        with self.assertRaisesRegex(ValueError, "Zeichensatz"):
            fw.validate_source_config(
                {
                    "source_type": "text",
                    "source_url": "https://example.com",
                    "version_pattern": "x",
                    "encoding": "nope-nope",
                },
                "Gerät 'x'",
            )

    def test_json_keys_required(self):
        with self.assertRaisesRegex(ValueError, "items_key"):
            fw.validate_source_config(
                {"source_type": "json", "source_url": "https://example.com", "version_key": "v"},
                "Gerät 'x'",
            )

    def test_json_value_path_required(self):
        with self.assertRaisesRegex(ValueError, "value_path"):
            fw.validate_source_config(
                {"source_type": "json_value", "source_url": "https://example.com", "value_path": []},
                "Gerät 'x'",
            )


class ProfileResolutionTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.profiles_dir = Path(self._tmp.name) / "profiles"
        self.profiles_dir.mkdir()

    def _write_profile(self, profile, filename=None):
        name = filename or f"{profile['id']}.json"
        (self.profiles_dir / name).write_text(
            json.dumps(profile, ensure_ascii=False), encoding="utf-8"
        )

    def _profile(self, **overrides):
        profile = {
            "schema_version": 1,
            "id": "coros.dura",
            "name": "COROS DURA – Firmware",
            "manufacturer": "COROS",
            "model": "DURA",
            "source_type": "text",
            "source_url": "https://example.com/coros",
            "version_pattern": r"\bV\s*([0-9]+(?:\.[0-9]+){2,3})\b",
        }
        profile.update(overrides)
        return profile

    def test_profile_is_merged(self):
        self._write_profile(self._profile())
        resolved = fw.resolve_device({"id": "coros-dura", "profile": "coros.dura"}, self.profiles_dir)
        self.assertEqual(resolved["id"], "coros-dura")
        self.assertEqual(resolved["source_url"], "https://example.com/coros")
        self.assertEqual(resolved["source_type"], "text")
        self.assertEqual(resolved["version_pattern"], r"\bV\s*([0-9]+(?:\.[0-9]+){2,3})\b")
        self.assertEqual(resolved["manufacturer"], "COROS")

    def test_local_name_overrides_profile_name(self):
        self._write_profile(self._profile())
        resolved = fw.resolve_device(
            {"id": "coros-dura", "profile": "coros.dura", "name": "Mein Gerät"}, self.profiles_dir
        )
        self.assertEqual(resolved["name"], "Mein Gerät")

    def test_profile_name_used_without_local_name(self):
        self._write_profile(self._profile())
        resolved = fw.resolve_device({"id": "coros-dura", "profile": "coros.dura"}, self.profiles_dir)
        self.assertEqual(resolved["name"], "COROS DURA – Firmware")

    def test_local_id_is_preserved(self):
        self._write_profile(self._profile())
        resolved = fw.resolve_device({"id": "meine-id", "profile": "coros.dura"}, self.profiles_dir)
        self.assertEqual(resolved["id"], "meine-id")
        self.assertEqual(resolved["profile"], "coros.dura")

    def test_input_device_is_not_mutated(self):
        device = {"id": "coros-dura", "profile": "coros.dura"}
        self._write_profile(self._profile())
        fw.resolve_device(device, self.profiles_dir)
        self.assertEqual(device, {"id": "coros-dura", "profile": "coros.dura"})

    def test_technical_field_not_allowed(self):
        with self.assertRaisesRegex(ValueError, "Unerlaubte Felder"):
            fw.resolve_device(
                {"id": "x", "profile": "coros.dura", "source_url": "https://example.com"},
                self.profiles_dir,
            )

    def test_missing_profile_file(self):
        with self.assertRaisesRegex(ValueError, "nicht lesbar"):
            fw.resolve_device({"id": "x", "profile": "coros.dura"}, self.profiles_dir)

    def test_invalid_json_profile(self):
        (self.profiles_dir / "coros.dura.json").write_text("{ nicht json", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Ungültiges JSON"):
            fw.resolve_device({"id": "x", "profile": "coros.dura"}, self.profiles_dir)

    def test_profile_not_object(self):
        (self.profiles_dir / "coros.dura.json").write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "kein JSON-Objekt"):
            fw.resolve_device({"id": "x", "profile": "coros.dura"}, self.profiles_dir)

    def test_unsupported_schema_version(self):
        self._write_profile(self._profile(schema_version=2))
        with self.assertRaisesRegex(ValueError, "Schema-Version"):
            fw.resolve_device({"id": "x", "profile": "coros.dura"}, self.profiles_dir)

    def test_boolean_schema_version_rejected(self):
        profile = self._profile()
        profile["schema_version"] = True
        self._write_profile(profile)
        with self.assertRaisesRegex(ValueError, "Schema-Version"):
            fw.resolve_device({"id": "x", "profile": "coros.dura"}, self.profiles_dir)

    def test_mismatched_profile_id(self):
        profile = self._profile()
        profile["id"] = "other"
        self._write_profile(profile, filename="coros.dura.json")
        with self.assertRaisesRegex(ValueError, "stimmt nicht"):
            fw.resolve_device({"id": "x", "profile": "coros.dura"}, self.profiles_dir)

    def test_invalid_profile_id_pattern(self):
        with self.assertRaisesRegex(ValueError, "Ungültige Profil-ID"):
            fw.resolve_device({"id": "x", "profile": "../evil"}, self.profiles_dir)

    def test_symlink_outside_profiles_dir_is_rejected(self):
        outside = Path(self._tmp.name) / "outside.json"
        outside.write_text(json.dumps(self._profile()), encoding="utf-8")
        (self.profiles_dir / "coros.dura.json").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "außerhalb"):
            fw.resolve_device({"id": "x", "profile": "coros.dura"}, self.profiles_dir)

    def test_invalid_profile_source_config(self):
        self._write_profile(self._profile(version_pattern="(a)(b)"))
        with self.assertRaisesRegex(ValueError, "Klammergruppe"):
            fw.resolve_device({"id": "x", "profile": "coros.dura"}, self.profiles_dir)

    def test_direct_entry_without_profiles_dir(self):
        missing = Path(self._tmp.name) / "does-not-exist"
        resolved = fw.resolve_devices(
            [
                {
                    "id": "direct",
                    "source_type": "text",
                    "source_url": "https://example.com/direct",
                    "version_pattern": r"v[0-9]+",
                }
            ],
            missing,
        )
        self.assertEqual(resolved[0]["id"], "direct")

    def test_mixed_entries_are_resolved(self):
        self._write_profile(self._profile())
        resolved = fw.resolve_devices(
            [
                {"id": "coros-dura", "profile": "coros.dura"},
                {
                    "id": "direct",
                    "name": "Direkt",
                    "source_type": "json_value",
                    "source_url": "https://example.com/direct",
                    "value_path": ["data", "version"],
                },
            ],
            self.profiles_dir,
        )
        self.assertEqual([item["id"] for item in resolved], ["coros-dura", "direct"])
        self.assertEqual(resolved[1]["value_path"], ["data", "version"])

    def test_two_devices_share_one_profile(self):
        self._write_profile(self._profile())
        resolved = fw.resolve_devices(
            [
                {"id": "gerät-a", "profile": "coros.dura"},
                {"id": "gerät-b", "profile": "coros.dura"},
            ],
            self.profiles_dir,
        )
        self.assertEqual(resolved[0]["id"], "gerät-a")
        self.assertEqual(resolved[1]["id"], "gerät-b")
        self.assertIsNot(resolved[0], resolved[1])


class CliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.devices_path = self.root / "devices.json"
        self.state_path = self.root / "state.json"
        self.profiles_dir = self.root / "profiles"
        self.profiles_dir.mkdir()

    def _write_devices(self, devices):
        self.devices_path.write_text(json.dumps({"devices": devices}), encoding="utf-8")

    def _write_profile(self, profile, filename=None):
        name = filename or f"{profile['id']}.json"
        (self.profiles_dir / name).write_text(
            json.dumps(profile, ensure_ascii=False), encoding="utf-8"
        )

    def _profile(self, **overrides):
        profile = {
            "schema_version": 1,
            "id": "coros.dura",
            "name": "COROS DURA – Firmware",
            "source_type": "text",
            "source_url": "https://example.com/coros",
            "version_pattern": r"\bV\s*([0-9]+(?:\.[0-9]+){2,3})\b",
        }
        profile.update(overrides)
        return profile

    def _args(self, extra=None):
        args = [
            "--devices",
            str(self.devices_path),
            "--state",
            str(self.state_path),
            "--profiles-dir",
            str(self.profiles_dir),
        ]
        return args + (extra or [])

    def _run(self, versions_by_url, extra=None):
        def fake_fetch(device):
            return versions_by_url[device["source_url"]]

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(fw, "fetch_source", fake_fetch):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = fw.main(self._args(extra))
        return code, out.getvalue(), err.getvalue()

    def _run_status(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(fw, "fetch_source", side_effect=AssertionError("kein Netz")):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = fw.main(self._args(["--status"]))
        return code, out.getvalue(), err.getvalue()

    def test_initial_then_unchanged_then_new(self):
        self._write_devices(
            [
                {
                    "id": "d1",
                    "name": "Gerät",
                    "source_type": "text",
                    "source_url": "https://example.com/u1",
                    "version_pattern": r"v[0-9]+",
                }
            ]
        )
        code, out, _ = self._run({"https://example.com/u1": "v1 v2"})
        self.assertEqual(code, 0)
        self.assertIn("Ausgangsstand (2): v1, v2", out)
        self.assertEqual(
            json.loads(self.state_path.read_text())["devices"]["d1"]["known_versions"], ["v1", "v2"]
        )

        code, out, _ = self._run({"https://example.com/u1": "v1 v2"})
        self.assertEqual(code, 0)
        self.assertIn("keine Änderung", out)

        code, out, _ = self._run({"https://example.com/u1": "v1 v2 v3"})
        self.assertEqual(code, 0)
        self.assertIn("neu: v3", out)
        self.assertEqual(
            json.loads(self.state_path.read_text())["devices"]["d1"]["known_versions"],
            ["v1", "v2", "v3"],
        )

    def test_one_failing_device_does_not_stop_others(self):
        self._write_devices(
            [
                {
                    "id": "bad",
                    "source_type": "text",
                    "source_url": "https://example.com/bad",
                    "version_pattern": r"v[0-9]+",
                },
                {
                    "id": "good",
                    "source_type": "text",
                    "source_url": "https://example.com/good",
                    "version_pattern": r"v[0-9]+",
                },
            ]
        )
        code, out, err = self._run(
            {"https://example.com/bad": "nichts", "https://example.com/good": "v1"}
        )
        self.assertEqual(code, 1)
        self.assertIn("Prüffehler:", err)
        self.assertIn("good", out)
        saved = json.loads(self.state_path.read_text())["devices"]
        self.assertIn("good", saved)
        self.assertNotIn("bad", saved)

    def test_duplicate_ids_are_config_error(self):
        self._write_devices(
            [
                {
                    "id": "dup",
                    "source_type": "text",
                    "source_url": "https://example.com/a",
                    "version_pattern": "x",
                },
                {
                    "id": "dup",
                    "source_type": "text",
                    "source_url": "https://example.com/b",
                    "version_pattern": "x",
                },
            ]
        )
        code, out, err = self._run({})
        self.assertEqual(code, 1)
        self.assertIn("Konfigurationsfehler:", err)
        self.assertFalse(self.state_path.exists())

    def test_missing_devices_file_is_config_error(self):
        code, out, err = self._run({})
        self.assertEqual(code, 1)
        self.assertIn("Konfigurationsfehler:", err)

    def test_status_shows_last_check_and_latest_version(self):
        self._write_devices(
            [
                {
                    "id": "d1",
                    "name": "Gerät",
                    "source_type": "text",
                    "source_url": "https://example.com/u1",
                    "version_pattern": r"v[0-9]+",
                }
            ]
        )
        self._run({"https://example.com/u1": "v1 v2"})
        code, out, err = self._run_status()
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("Letzte Version", out)
        self.assertIn("v2", out)
        stored = json.loads(self.state_path.read_text())["devices"]["d1"]["last_checked"]
        self.assertIn(stored[:10], out)

    def test_status_without_state_marks_unchecked(self):
        self._write_devices(
            [
                {
                    "id": "d1",
                    "name": "Gerät",
                    "source_type": "text",
                    "source_url": "https://example.com/u1",
                    "version_pattern": r"v[0-9]+",
                }
            ]
        )
        code, out, err = self._run_status()
        self.assertEqual(code, 0)
        self.assertIn("nie geprüft", out)
        self.assertFalse(self.state_path.exists())

    def test_check_and_status_are_mutually_exclusive(self):
        self._write_devices([])
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                fw.main(self._args(["--check", "--status"]))

    def test_profile_device_is_checked(self):
        self._write_profile(self._profile())
        self._write_devices([{"id": "coros-dura", "profile": "coros.dura"}])
        code, out, err = self._run({"https://example.com/coros": "V 3.0910.0 V 3.0808.0"})
        self.assertEqual(code, 0)
        self.assertIn("Ausgangsstand (2): 3.0808.0, 3.0910.0", out)
        self.assertEqual(
            json.loads(self.state_path.read_text())["devices"]["coros-dura"]["known_versions"],
            ["3.0808.0", "3.0910.0"],
        )

    def test_profile_device_reuses_existing_state(self):
        self.state_path.write_text(
            json.dumps(
                {
                    "devices": {
                        "coros-dura": {
                            "last_checked": "2026-10-01T10:00:00+02:00",
                            "known_versions": ["3.0808.0"],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        self._write_profile(self._profile())
        self._write_devices([{"id": "coros-dura", "profile": "coros.dura"}])
        code, out, _ = self._run({"https://example.com/coros": "V 3.0910.0 V 3.0808.0"})
        self.assertEqual(code, 0)
        self.assertIn("neu: 3.0910.0", out)

    def test_status_with_profiles_does_not_fetch(self):
        self._write_profile(self._profile())
        self._write_devices([{"id": "coros-dura", "profile": "coros.dura"}])
        code, out, err = self._run_status()
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("COROS DURA", out)
        self.assertIn("nie geprüft", out)

    def test_invalid_profile_prevents_check_and_state_change(self):
        self._write_profile(self._profile(schema_version=2))
        self._write_devices([{"id": "coros-dura", "profile": "coros.dura"}])
        code, out, err = self._run({"https://example.com/coros": "V 3.0910.0"})
        self.assertEqual(code, 1)
        self.assertIn("Konfigurationsfehler:", err)
        self.assertFalse(self.state_path.exists())

    def test_config_error_does_not_fetch(self):
        self._write_devices(
            [
                {
                    "id": "d1",
                    "source_type": "text",
                    "source_url": "kein-url",
                    "version_pattern": "x",
                }
            ]
        )
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(fw, "fetch_source", side_effect=AssertionError("kein Netz")):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = fw.main(self._args())
        self.assertEqual(code, 1)
        self.assertIn("Konfigurationsfehler:", err.getvalue())

    def test_custom_profiles_dir_is_used(self):
        other = self.root / "andere-profile"
        other.mkdir()
        (other / "coros.dura.json").write_text(
            json.dumps(self._profile(source_url="https://example.com/andere")), encoding="utf-8"
        )
        self._write_devices([{"id": "coros-dura", "profile": "coros.dura"}])
        out, err = io.StringIO(), io.StringIO()
        args = [
            "--devices",
            str(self.devices_path),
            "--state",
            str(self.state_path),
            "--profiles-dir",
            str(other),
        ]
        with mock.patch.object(fw, "fetch_source", return_value="V 3.0910.0"):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = fw.main(args)
        self.assertEqual(code, 0)
        self.assertIn("Ausgangsstand", out.getvalue())

    def test_default_profiles_dir_without_option(self):
        args = ["--devices", str(self.devices_path), "--state", str(self.state_path)]
        parsed = fw.parse_args(args)
        self.assertEqual(parsed.profiles_dir, Path(fw.__file__).resolve().parent / "profiles")

    def test_direct_config_without_profiles_dir(self):
        self._write_devices(
            [
                {
                    "id": "direct",
                    "name": "Direkt",
                    "source_type": "text",
                    "source_url": "https://example.com/direct",
                    "version_pattern": r"v[0-9]+",
                }
            ]
        )
        out, err = io.StringIO(), io.StringIO()
        args = ["--devices", str(self.devices_path), "--state", str(self.state_path)]
        with mock.patch.object(fw, "fetch_source", return_value="v1 v2"):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = fw.main(args)
        self.assertEqual(code, 0)
        self.assertIn("Ausgangsstand (2): v1, v2", out.getvalue())


class SuggestProfileIdTests(unittest.TestCase):
    def test_normalizes_and_lowercases(self):
        self.assertEqual(fw.suggest_profile_id("COROS", "DURA"), "coros.dura")

    def test_spaces_become_hyphens(self):
        self.assertEqual(fw.suggest_profile_id("Brother", "HL 3152CDW"), "brother.hl-3152cdw")

    def test_umlauts_are_transliterated(self):
        self.assertEqual(fw.suggest_profile_id("AVM", "FRITZ!Box 6660"), "avm.fritzbox-6660")

    def test_returns_empty_without_usable_characters(self):
        self.assertEqual(fw.suggest_profile_id("", "---"), "")


class AnalyzeExampleTests(unittest.TestCase):
    def test_point_version(self):
        shape = fw.analyze_example("16.2.1")
        self.assertEqual(shape, fw.ExampleShape(prefix="", left_blocks=3))

    def test_prefix_version(self):
        shape = fw.analyze_example("V 3.0910.0")
        self.assertEqual(shape, fw.ExampleShape(prefix="V ", left_blocks=3))

    def test_composite_version(self):
        shape = fw.analyze_example("1.13/1.02")
        self.assertEqual(shape, fw.ExampleShape(prefix="", left_blocks=2, right_blocks=2, separator="/"))

    def test_unsupported_format(self):
        self.assertIsNone(fw.analyze_example("keine Version"))
        self.assertIsNone(fw.analyze_example("1.2 und 3.4"))


class CandidatePatternTests(unittest.TestCase):
    def _extract(self, pattern, content):
        return fw.versions_from_text({"version_pattern": pattern}, content)

    def test_point_version_matches_future_values(self):
        candidate = fw.build_candidate(fw.ExampleShape("", 3), fw.PatternOptions(False))
        self.assertEqual(self._extract(candidate.pattern, "16.2.1 und 17.0.4"), {"16.2.1", "17.0.4"})

    def test_prefix_is_not_captured_and_leading_zeros_kept(self):
        shape = fw.analyze_example("V 3.0910.0")
        candidate = fw.build_candidate(shape, fw.PatternOptions(False))
        self.assertEqual(self._extract(candidate.pattern, "V 3.0910.0 V 3.0808.0"), {"3.0910.0", "3.0808.0"})

    def test_partial_longer_version_is_not_matched(self):
        candidate = fw.build_candidate(fw.analyze_example("V 3.0910.0"), fw.PatternOptions(False))
        self.assertEqual(self._extract(candidate.pattern, "V 3.0910.0.1"), set())

    def test_extra_block_option(self):
        candidate = fw.build_candidate(fw.analyze_example("16.2.1"), fw.PatternOptions(True))
        self.assertEqual(self._extract(candidate.pattern, "16.2.1 und 1.2.3.4"), {"16.2.1", "1.2.3.4"})

    def test_composite_is_kept_complete(self):
        candidate = fw.build_candidate(fw.analyze_example("1.13/1.02"), fw.PatternOptions(False))
        self.assertEqual(self._extract(candidate.pattern, "Version 1.13/1.02"), {"1.13/1.02"})

    def test_composite_does_not_match_longer_chain(self):
        candidate = fw.build_candidate(fw.analyze_example("1.13/1.02"), fw.PatternOptions(False))
        self.assertEqual(self._extract(candidate.pattern, "1.13/1.02/1.03"), set())

    def test_literal_prefix_with_regex_characters_is_escaped(self):
        candidate = fw.build_candidate(fw.analyze_example("V(x) 1.2.3"), fw.PatternOptions(False))
        self.assertIn(re.escape("V(x)"), candidate.pattern)

    def test_suggest_returns_two_variants(self):
        candidates = fw.suggest_version_patterns("16.2.1")
        self.assertEqual(len(candidates), 2)
        self.assertNotEqual(candidates[0].pattern, candidates[1].pattern)


class OccurrenceTests(unittest.TestCase):
    def test_finds_all_occurrences_with_context(self):
        occurrences = fw.find_example_occurrences("a V1 b V1 c", "V1", context=4)
        self.assertEqual([o.index for o in occurrences], [1, 2])
        self.assertTrue(occurrences[0].before.endswith("a "))
        self.assertTrue(occurrences[1].after.startswith(" c"))

    def test_empty_example_has_no_occurrences(self):
        self.assertEqual(fw.find_example_occurrences("abc", ""), [])

    def test_control_characters_are_sanitized(self):
        result = fw.sanitize_for_terminal("\x1b[31mrot\x1b[0m\nneu")
        self.assertNotIn("\x1b", result)
        self.assertNotIn("\n", result)


class PatternProcessTests(unittest.TestCase):
    def test_valid_pattern_runs_in_child_process(self):
        result = fw.test_pattern("V 1.2.3", r"\bV\s*([0-9]+(?:\.[0-9]+){2})\b")
        self.assertTrue(result.ok, result.message)
        self.assertEqual(result.versions, ["1.2.3"])

    def test_two_capture_groups_are_rejected(self):
        result = fw.test_pattern("ab", "(a)(b)")
        self.assertFalse(result.ok)
        self.assertIn("Klammergruppe", result.message)

    def test_invalid_regex_is_rejected(self):
        result = fw.test_pattern("ab", "(")
        self.assertFalse(result.ok)
        self.assertIn("regulärer Ausdruck", result.message)

    def test_empty_result_is_rejected(self):
        result = fw.test_pattern("abc", r"zzz([0-9]+)")
        self.assertFalse(result.ok)

    def test_timeout_terminates_child_process(self):
        result = fw.test_pattern("a" * 40 + "c", r"(a+)+b", timeout=1.0)
        self.assertFalse(result.ok)
        self.assertIn("Zeitlimit", result.message)


class ExportProfileTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.profiles_dir = Path(self._tmp.name) / "profiles"
        self.profiles_dir.mkdir()

    def _profile(self, **overrides):
        profile = {
            "schema_version": 1,
            "id": "coros.dura",
            "name": "COROS DURA – Firmware",
            "source_type": "text",
            "source_url": "https://example.com/coros",
            "version_pattern": r"\bV\s*([0-9]+(?:\.[0-9]+){2,3})\b",
        }
        profile.update(overrides)
        return profile

    def test_exports_and_reloads_with_resolver(self):
        path = fw.export_profile(self._profile(), self.profiles_dir)
        self.assertEqual(path.name, "coros.dura.json")
        reloaded = fw.load_profile("coros.dura", self.profiles_dir)
        self.assertEqual(reloaded["version_pattern"], r"\bV\s*([0-9]+(?:\.[0-9]+){2,3})\b")
        self.assertFalse(path.with_name(path.name + ".tmp").exists())

    def test_existing_file_is_not_overwritten_silently(self):
        path = fw.export_profile(self._profile(), self.profiles_dir)
        original = path.read_text(encoding="utf-8")
        with self.assertRaises(fw.ProfileExistsError):
            fw.export_profile(self._profile(name="Anders"), self.profiles_dir)
        self.assertEqual(path.read_text(encoding="utf-8"), original)

    def test_overwrite_replaces_atomically(self):
        path = fw.export_profile(self._profile(), self.profiles_dir)
        fw.export_profile(self._profile(name="Neu"), self.profiles_dir, overwrite=True)
        self.assertIn("Neu", path.read_text(encoding="utf-8"))
        self.assertFalse(path.with_name(path.name + ".tmp").exists())

    def test_write_error_keeps_existing_file_and_cleans_temp(self):
        path = fw.export_profile(self._profile(), self.profiles_dir)
        original = path.read_text(encoding="utf-8")
        with mock.patch("firmaware.json.dump", side_effect=OSError("boom")):
            with self.assertRaises(OSError):
                fw.export_profile(self._profile(name="Kaputt"), self.profiles_dir, overwrite=True)
        self.assertEqual(path.read_text(encoding="utf-8"), original)
        self.assertFalse(path.with_name(path.name + ".tmp").exists())

    def test_path_traversal_is_rejected(self):
        with self.assertRaises(ValueError):
            fw.export_profile(self._profile(id="../evil"), self.profiles_dir)

    def test_changed_target_requires_confirmation(self):
        fw.export_profile(self._profile(), self.profiles_dir)
        with self.assertRaises(fw.ProfileChangedError):
            fw.export_profile(
                self._profile(),
                self.profiles_dir,
                overwrite=True,
                expected_exists=False,
            )


class ProfileAssistantCliTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.profiles_dir = self.root / "profiles"

    def _document(self, text):
        return fw.FetchedDocument(
            text=text,
            status=200,
            final_url="https://example.com/releases",
            charset="utf-8",
            requested_url="https://example.com/releases",
            requested_encoding=None,
        )

    def test_create_profile_is_mutually_exclusive_with_status(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                fw.parse_args(["--create-profile", "--status"])

    def test_happy_path_exports_usable_profile(self):
        answers = [
            "COROS",                     # Hersteller
            "DURA",                      # Modell
            "",                          # Anzeigename (Vorschlag)
            "",                          # Profil-ID (Vorschlag coros.dura)
            "https://example.com/releases",
            "V 3.0910.0",                # Beispielversion
            "j",                         # Fundstelle bestätigen
            "1",                         # Kandidat wählen
            "n",                         # nicht erneut abrufen
            "j",                         # speichern
        ]
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(
            fw, "fetch_document", return_value=self._document("Firmware V 3.0910.0 und V 3.0808.0")
        ):
            with mock.patch("builtins.input", side_effect=answers):
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = fw.main(["--create-profile", "--profiles-dir", str(self.profiles_dir)])
        self.assertEqual(code, 0, err.getvalue())
        path = self.profiles_dir / "coros.dura.json"
        self.assertTrue(path.exists())
        data = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], 1)
        self.assertEqual(data["id"], "coros.dura")
        self.assertEqual(data["source_type"], "text")
        self.assertEqual(data["version_pattern"], r"\bV\s*([0-9]+(?:\.[0-9]+){2})(?![0-9.])")
        self.assertEqual(data["manufacturer"], "COROS")
        # Exportiertes JSON ist mit dem vorhandenen Resolver nutzbar.
        fw.load_profile("coros.dura", self.profiles_dir)

    def test_unsupported_format_can_be_corrected(self):
        content = "Firmware 1.2 und 3.4 sowie V 3.0910.0"
        answers = [
            "COROS", "DURA", "", "",
            "https://example.com/releases",
            "1.2 und 3.4",  # von den Vorlagen nicht unterstützt
            "j",            # Fundstelle bestätigen
            "w",            # erneut eingeben
            "V 3.0910.0",   # unterstütztes Format
            "j",            # Fundstelle bestätigen
            "1",            # Kandidat
            "n",            # nicht erneut abrufen
            "j",            # speichern
        ]
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(fw, "fetch_document", return_value=self._document(content)):
            with mock.patch("builtins.input", side_effect=answers):
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = fw.main(["--create-profile", "--profiles-dir", str(self.profiles_dir)])
        self.assertEqual(code, 0, err.getvalue() + out.getvalue())
        data = json.loads((self.profiles_dir / "coros.dura.json").read_text(encoding="utf-8"))
        self.assertIn("V", data["version_pattern"])

    def test_abort_creates_no_profile(self):
        answers = [
            "COROS", "DURA", "", "",
            "https://example.com/releases",
            "nicht vorhanden",   # Beispiel ohne Fundstelle
            "a",                  # abbrechen
        ]
        err = io.StringIO()
        with mock.patch.object(fw, "fetch_document", return_value=self._document("V 3.0910.0")):
            with mock.patch("builtins.input", side_effect=answers):
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                    code = fw.main(["--create-profile", "--profiles-dir", str(self.profiles_dir)])
        self.assertEqual(code, 1)
        self.assertFalse((self.profiles_dir / "coros.dura.json").exists())

    def test_eof_creates_no_profile(self):
        err = io.StringIO()
        with mock.patch("builtins.input", side_effect=EOFError):
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                code = fw.main(["--create-profile", "--profiles-dir", str(self.profiles_dir)])
        self.assertEqual(code, 1)
        self.assertIn("Eingabeende", err.getvalue())
        self.assertFalse((self.profiles_dir / "coros.dura.json").exists())

    def test_explicit_encoding_is_exported(self):
        answers = [
            "Shelly", "1", "", "",
            "https://example.com/fw",
            "z",              # Zeichensatz wählen
            "latin-1",        # Zeichensatz
            "",               # URL erneut (Vorschlag)
            "v1.2.3",         # Beispiel
            "j",              # Fundstelle
            "1",              # Kandidat
            "n",              # nicht erneut abrufen
            "j",              # speichern
        ]
        attempts = []

        def fake_fetch(url, encoding=None):
            attempts.append(encoding)
            if len(attempts) == 1:
                raise fw.FirmwareSourceError("Dekodierung mit 'utf-8' fehlgeschlagen")
            return fw.FetchedDocument(
                text="Firmware v1.2.3",
                status=200,
                final_url=url,
                charset=encoding or "utf-8",
                requested_url=url,
                requested_encoding=encoding,
            )

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(fw, "fetch_document", side_effect=fake_fetch):
            with mock.patch("builtins.input", side_effect=answers):
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = fw.main(["--create-profile", "--profiles-dir", str(self.profiles_dir)])
        self.assertEqual(code, 0, err.getvalue() + out.getvalue())
        data = json.loads((self.profiles_dir / "shelly.1.json").read_text(encoding="utf-8"))
        self.assertEqual(data["encoding"], "latin-1")


if __name__ == "__main__":
    sys.exit(unittest.main())
