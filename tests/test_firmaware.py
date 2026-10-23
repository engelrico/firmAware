#!/usr/bin/env python3
"""Tests for firmAware (firmaware.py) – standard library only (unittest)."""
from __future__ import annotations

import contextlib
import io
import json
import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import firmaware as fw


class TextExtractionTests(unittest.TestCase):
    def test_capture_group_is_used(self):
        device = {"version_pattern": r"RE200\(EU\)_V4_[0-9]+"}
        content = "File RE200(EU)_V4_200922 and RE200(EU)_V4_210916"
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
        with self.assertRaisesRegex(ValueError, "capturing group"):
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
        with self.assertRaisesRegex(ValueError, "Version field"):
            fw.versions_from_json(self.DEVICE, content)

    def test_non_list_fails(self):
        content = json.dumps({"downloads": {"version": "x"}})
        with self.assertRaisesRegex(ValueError, "Expected JSON list is missing"):
            fw.versions_from_json(self.DEVICE, content)

    def test_non_object_entry_fails(self):
        content = json.dumps({"downloads": ["x"]})
        with self.assertRaisesRegex(ValueError, "Invalid JSON entry"):
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
        with self.assertRaisesRegex(ValueError, "Version field"):
            fw.versions_from_json_value(device, json.dumps({"version": "   "}))


class ExtractDispatchTests(unittest.TestCase):
    def test_unknown_source_type(self):
        with self.assertRaisesRegex(ValueError, "Unknown source type: html"):
            fw.extract_versions("html", "x", {})

    def test_empty_result_is_error(self):
        device = {"version_pattern": r"v[0-9]+"}
        with self.assertRaisesRegex(ValueError, "No versions found; stored state remains unchanged"):
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
        previous = {"discovered_at": {"a": "2026-01-01T00:00:00", "b": "2026-01-01T00:00:00"}}
        known, new = fw.compare_versions(["a", "b", "c"], previous)
        self.assertEqual(sorted(known), ["a", "b"])
        self.assertEqual(new, ["c"])

    def test_invalid_stored_state_fails(self):
        with self.assertRaisesRegex(ValueError, "Invalid stored version state"):
            fw.compare_versions(["a"], {"discovered_at": {1: "x", 2: "y"}})


class StateStorageTests(unittest.TestCase):
    def test_roundtrip_and_parent_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "state.json"
            state = {"devices": {"x": {"discovered_at": {"1": "2026-01-01T00:00:00"}}}}
            fw.save_state(path, state)
            self.assertEqual(fw.load_state(path), state)
            self.assertFalse(path.with_name(path.name + ".tmp").exists())

    def test_missing_state_starts_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(fw.load_state(Path(tmp) / "missing.json"), {"devices": {}})

    def test_status_without_state_prints_hint(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        devices = root / "devices.json"
        devices.write_text(
            json.dumps(
                {
                    "devices": [
                        {
                            "id": "d1",
                            "name": "Entry",
                            "source_type": "text",
                            "source_url": "https://example.com/u1",
                            "version_pattern": r"v[0-9]+",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(fw, "fetch_source", side_effect=AssertionError("no network")):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = fw.main(
                    [
                        "--status",
                        "--devices", str(devices),
                        "--state", str(root / "missing-state.json"),
                    ]
                )
        self.assertEqual(code, 0)
        self.assertIn("No state yet", err.getvalue())
        self.assertIn("Run --check first", err.getvalue())

    def test_status_with_state_prints_no_hint(self):
        root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        devices = root / "devices.json"
        devices.write_text(
            json.dumps(
                {
                    "devices": [
                        {
                            "id": "d1",
                            "name": "Entry",
                            "source_type": "text",
                            "source_url": "https://example.com/u1",
                            "version_pattern": r"v[0-9]+",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        state = root / "state.json"
        state.write_text(json.dumps({"devices": {}}), encoding="utf-8")
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(fw, "fetch_source", side_effect=AssertionError("no network")):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = fw.main(
                    [
                        "--status",
                        "--devices", str(devices),
                        "--state", str(state),
                    ]
                )
        self.assertEqual(code, 0)
        self.assertNotIn("No state yet", err.getvalue())

    def test_state_template_is_empty(self):
        template = Path(__file__).resolve().parents[1] / "state.template.json"
        self.assertTrue(template.exists(), "state.template.json must ship with the repo")
        self.assertEqual(json.loads(template.read_text(encoding="utf-8")), {"devices": {}})


class SourceValidationTests(unittest.TestCase):
    def test_valid_text_source(self):
        fw.validate_source_config(
            {
                "source_type": "text",
                "source_url": "https://example.com",
                "version_pattern": r"v[0-9]+",
            },
            "Device 'x'",
        )

    def test_unknown_source_type(self):
        with self.assertRaisesRegex(ValueError, "Unknown source type"):
            fw.validate_source_config(
                {"source_type": "html", "source_url": "https://example.com"}, "Device 'x'"
            )

    def test_invalid_url(self):
        with self.assertRaisesRegex(ValueError, "Invalid source URL"):
            fw.validate_source_config(
                {"source_type": "text", "source_url": "kein-url", "version_pattern": "x"},
                "Device 'x'",
            )

    def test_missing_pattern(self):
        with self.assertRaisesRegex(ValueError, "version_pattern"):
            fw.validate_source_config(
                {"source_type": "text", "source_url": "https://example.com"}, "Device 'x'"
            )

    def test_invalid_regex(self):
        with self.assertRaisesRegex(ValueError, "Invalid regular expression"):
            fw.validate_source_config(
                {"source_type": "text", "source_url": "https://example.com", "version_pattern": "("},
                "Device 'x'",
            )

    def test_two_groups_rejected(self):
        with self.assertRaisesRegex(ValueError, "capturing group"):
            fw.validate_source_config(
                {
                    "source_type": "text",
                    "source_url": "https://example.com",
                    "version_pattern": "(a)(b)",
                },
                "Device 'x'",
            )

    def test_unknown_encoding(self):
        with self.assertRaisesRegex(ValueError, "Unknown charset"):
            fw.validate_source_config(
                {
                    "source_type": "text",
                    "source_url": "https://example.com",
                    "version_pattern": "x",
                    "encoding": "nope-nope",
                },
                "Device 'x'",
            )

    def test_json_keys_required(self):
        with self.assertRaisesRegex(ValueError, "items_key"):
            fw.validate_source_config(
                {"source_type": "json", "source_url": "https://example.com", "version_key": "v"},
                "Device 'x'",
            )

    def test_json_value_path_required(self):
        with self.assertRaisesRegex(ValueError, "value_path"):
            fw.validate_source_config(
                {"source_type": "json_value", "source_url": "https://example.com", "value_path": []},
                "Device 'x'",
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
            "schema_version": 2,
            "id": "coros.dura",
            "name": "COROS DURA – Firmware",
            "manufacturer": "COROS",
            "model": "DURA",
            "category": "firmware",
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
        self.assertEqual(resolved["category"], "firmware")

    def test_local_name_overrides_profile_name(self):
        self._write_profile(self._profile())
        resolved = fw.resolve_device(
            {"id": "coros-dura", "profile": "coros.dura", "name": "My Device"}, self.profiles_dir
        )
        self.assertEqual(resolved["name"], "My Device")

    def test_profile_name_used_without_local_name(self):
        self._write_profile(self._profile())
        resolved = fw.resolve_device({"id": "coros-dura", "profile": "coros.dura"}, self.profiles_dir)
        self.assertEqual(resolved["name"], "COROS DURA – Firmware")

    def test_local_id_is_preserved(self):
        self._write_profile(self._profile())
        resolved = fw.resolve_device({"id": "my-id", "profile": "coros.dura"}, self.profiles_dir)
        self.assertEqual(resolved["id"], "my-id")
        self.assertEqual(resolved["profile"], "coros.dura")

    def test_input_device_is_not_mutated(self):
        device = {"id": "coros-dura", "profile": "coros.dura"}
        self._write_profile(self._profile())
        fw.resolve_device(device, self.profiles_dir)
        self.assertEqual(device, {"id": "coros-dura", "profile": "coros.dura"})

    def test_technical_field_not_allowed(self):
        with self.assertRaisesRegex(ValueError, "Disallowed fields"):
            fw.resolve_device(
                {"id": "x", "profile": "coros.dura", "source_url": "https://example.com"},
                self.profiles_dir,
            )

    def test_missing_profile_file(self):
        with self.assertRaisesRegex(ValueError, "not readable"):
            fw.resolve_device({"id": "x", "profile": "coros.dura"}, self.profiles_dir)

    def test_invalid_json_profile(self):
        (self.profiles_dir / "coros.dura.json").write_text("{ nicht json", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "Invalid JSON"):
            fw.resolve_device({"id": "x", "profile": "coros.dura"}, self.profiles_dir)

    def test_profile_not_object(self):
        (self.profiles_dir / "coros.dura.json").write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "not a JSON object"):
            fw.resolve_device({"id": "x", "profile": "coros.dura"}, self.profiles_dir)

    def test_unsupported_schema_version(self):
        self._write_profile(self._profile(schema_version=1))
        with self.assertRaisesRegex(ValueError, "schema version"):
            fw.resolve_device({"id": "x", "profile": "coros.dura"}, self.profiles_dir)

    def test_boolean_schema_version_rejected(self):
        profile = self._profile()
        profile["schema_version"] = True
        self._write_profile(profile)
        with self.assertRaisesRegex(ValueError, "schema version"):
            fw.resolve_device({"id": "x", "profile": "coros.dura"}, self.profiles_dir)

    def test_mismatched_profile_id(self):
        profile = self._profile()
        profile["id"] = "other"
        self._write_profile(profile, filename="coros.dura.json")
        with self.assertRaisesRegex(ValueError, "does not match"):
            fw.resolve_device({"id": "x", "profile": "coros.dura"}, self.profiles_dir)

    def test_invalid_profile_id_pattern(self):
        with self.assertRaisesRegex(ValueError, "Invalid profile ID"):
            fw.resolve_device({"id": "x", "profile": "../evil"}, self.profiles_dir)

    def test_symlink_outside_profiles_dir_is_rejected(self):
        outside = Path(self._tmp.name) / "outside.json"
        outside.write_text(json.dumps(self._profile()), encoding="utf-8")
        (self.profiles_dir / "coros.dura.json").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "outside"):
            fw.resolve_device({"id": "x", "profile": "coros.dura"}, self.profiles_dir)

    def test_invalid_profile_source_config(self):
        self._write_profile(self._profile(version_pattern="(a)(b)"))
        with self.assertRaisesRegex(ValueError, "capturing group"):
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
                    "name": "Direct",
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
                {"id": "device-a", "profile": "coros.dura"},
                {"id": "device-b", "profile": "coros.dura"},
            ],
            self.profiles_dir,
        )
        self.assertEqual(resolved[0]["id"], "device-a")
        self.assertEqual(resolved[1]["id"], "device-b")
        self.assertIsNot(resolved[0], resolved[1])

    def test_missing_category_is_rejected(self):
        self._write_profile(self._profile(category=None))
        del self._profile()["category"]  # noqa: B018 – just to keep linters quiet, replaced below
        profile = {k: v for k, v in self._profile().items() if k != "category"}
        self._write_profile(profile)
        with self.assertRaisesRegex(ValueError, "category"):
            fw.resolve_device({"id": "x", "profile": "coros.dura"}, self.profiles_dir)


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
            "schema_version": 2,
            "id": "coros.dura",
            "name": "COROS DURA – Firmware",
            "source_type": "text",
            "source_url": "https://example.com/coros",
            "version_pattern": r"\bV\s*([0-9]+(?:\.[0-9]+){2,3})\b",
            "category": "firmware",
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
        with mock.patch.object(fw, "fetch_source", side_effect=AssertionError("no network")):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = fw.main(self._args(["--status"]))
        return code, out.getvalue(), err.getvalue()

    def test_initial_then_unchanged_then_new(self):
        self._write_devices(
            [
                {
                    "id": "d1",
                    "name": "Device",
                    "source_type": "text",
                    "source_url": "https://example.com/u1",
                    "version_pattern": r"v[0-9]+",
                }
            ]
        )
        code, out, _ = self._run({"https://example.com/u1": "v1 v2"})
        self.assertEqual(code, 0)
        self.assertIn("Baseline (2): v1, v2", out)
        self.assertEqual(
            sorted(json.loads(self.state_path.read_text())["devices"]["d1"]["discovered_at"]),
            ["v1", "v2"],
        )

        code, out, _ = self._run({"https://example.com/u1": "v1 v2"})
        self.assertEqual(code, 0)
        self.assertIn("no changes", out)

        code, out, _ = self._run({"https://example.com/u1": "v1 v2 v3"})
        self.assertEqual(code, 0)
        self.assertIn("new: v3", out)
        self.assertEqual(
            sorted(json.loads(self.state_path.read_text())["devices"]["d1"]["discovered_at"]),
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
            {"https://example.com/bad": "nothing", "https://example.com/good": "v1"}
        )
        self.assertEqual(code, 1)
        self.assertIn("Check error:", err)
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
        self.assertIn("Configuration error:", err)
        self.assertFalse(self.state_path.exists())

    def test_missing_devices_file_is_config_error(self):
        code, out, err = self._run({})
        self.assertEqual(code, 1)
        self.assertIn("Configuration error:", err)
        self.assertFalse(self.state_path.exists())

    def test_status_shows_last_check_and_latest_version(self):
        self._write_devices(
            [
                {
                    "id": "d1",
                    "name": "Device",
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
        self.assertIn("Last version", out)
        self.assertIn("v2", out)
        stored = json.loads(self.state_path.read_text())["devices"]["d1"]["last_checked"]
        self.assertIn(stored[:10], out)

    def test_status_without_state_marks_unchecked(self):
        self._write_devices(
            [
                {
                    "id": "d1",
                    "name": "Device",
                    "source_type": "text",
                    "source_url": "https://example.com/u1",
                    "version_pattern": r"v[0-9]+",
                }
            ]
        )
        code, out, err = self._run_status()
        self.assertEqual(code, 0)
        self.assertIn("never checked", out)
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
        self.assertIn("Baseline (2): 3.0808.0, 3.0910.0", out)
        self.assertEqual(
            sorted(json.loads(self.state_path.read_text())["devices"]["coros-dura"]["discovered_at"]),
            ["3.0808.0", "3.0910.0"],
        )

    def test_profile_device_reuses_existing_state(self):
        self.state_path.write_text(
            json.dumps(
                {
                    "devices": {
                        "coros-dura": {
                            "last_checked": "2026-10-01T10:00:00+02:00",
                            "discovered_at": {"3.0808.0": "2026-10-01T10:00:00+02:00"},
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
        self.assertIn("new: 3.0910.0", out)

    def test_status_with_profiles_does_not_fetch(self):
        self._write_profile(self._profile())
        self._write_devices([{"id": "coros-dura", "profile": "coros.dura"}])
        self.state_path.write_text(json.dumps({"devices": {}}), encoding="utf-8")
        code, out, err = self._run_status()
        self.assertEqual(code, 0)
        self.assertEqual(err, "")
        self.assertIn("COROS DURA", out)
        self.assertIn("never checked", out)

    def test_invalid_profile_prevents_check_and_state_change(self):
        self._write_profile(self._profile(schema_version=1))
        self._write_devices([{"id": "coros-dura", "profile": "coros.dura"}])
        code, out, err = self._run({"https://example.com/coros": "V 3.0910.0"})
        self.assertEqual(code, 1)
        self.assertIn("Configuration error:", err)
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
        with mock.patch.object(fw, "fetch_source", side_effect=AssertionError("no network")):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = fw.main(self._args())
        self.assertEqual(code, 1)
        self.assertIn("Configuration error:", err.getvalue())

    def test_custom_profiles_dir_is_used(self):
        other = self.root / "other-profiles"
        other.mkdir()
        (other / "coros.dura.json").write_text(
            json.dumps(self._profile(source_url="https://example.com/other")), encoding="utf-8"
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
        self.assertIn("Baseline", out.getvalue())

    def test_default_profiles_dir_without_option(self):
        args = ["--devices", str(self.devices_path), "--state", str(self.state_path)]
        parsed = fw.parse_args(args)
        self.assertEqual(parsed.profiles_dir, Path(fw.__file__).resolve().parent / "profiles")

    def test_direct_config_without_profiles_dir(self):
        self._write_devices(
            [
                {
                    "id": "direct",
                    "name": "Direct",
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
        self.assertIn("Baseline (2): v1, v2", out.getvalue())


class VersionSortKeyTests(unittest.TestCase):
    def test_numeric_blocks_are_compared_as_integers(self):
        self.assertLess(fw.version_sort_key("1.9"), fw.version_sort_key("1.10"))
        self.assertLess(fw.version_sort_key("1.2"), fw.version_sort_key("1.10.0"))
        self.assertEqual(fw.version_sort_key("1.10"), fw.version_sort_key("1.10"))

    def test_date_like_versions_sort_chronologically(self):
        self.assertLess(
            fw.version_sort_key("2013-12-18"), fw.version_sort_key("2014-01-07")
        )
        self.assertLess(
            fw.version_sort_key("2025-05-06"), fw.version_sort_key("2026-10-06")
        )

    def test_composite_versions_sort_by_blocks(self):
        self.assertLess(fw.version_sort_key("1.13/1.02"), fw.version_sort_key("1.13/1.03"))

    def test_suffixes_are_handled_best_effort(self):
        self.assertLess(fw.version_sort_key("6.5.0a"), fw.version_sort_key("6.5.0c"))

    def test_empty_string_sorts_lowest(self):
        self.assertLess(fw.version_sort_key(""), fw.version_sort_key("1.0"))

    def test_more_blocks_sort_higher(self):
        self.assertLess(
            fw.version_sort_key("1978074000"), fw.version_sort_key("6.1.1978418030")
        )

    def test_shorter_label_with_larger_value_wins(self):
        # iOS: 27.1 is newer than 27.0.1 although it has fewer blocks.
        self.assertLess(fw.version_sort_key("27.0.1"), fw.version_sort_key("27.1"))
        self.assertLess(fw.version_sort_key("18.7.10"), fw.version_sort_key("27.1"))
        self.assertLess(fw.version_sort_key("18.7.10"), fw.version_sort_key("27"))

    def test_single_huge_block_is_ranked_below_multi_block(self):
        # Philips Hue legacy labels are single huge numeric blocks.
        self.assertLess(
            fw.version_sort_key("196606010"), fw.version_sort_key("6.3.1978545030")
        )


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.notify_path = self.root / "notify.json"
        self.notify_path.write_text(
            json.dumps(
                {
                    "smtp_host": "smtp.example.com",
                    "smtp_user": "me@example.com",
                    "smtp_password": "secret",
                    "mail_to": ["me@example.com"],
                }
            ),
            encoding="utf-8",
        )

    def _args(self, extra=None):
        args = [
            "--devices",
            str(self.root / "devices.json"),
            "--state",
            str(self.root / "state.json"),
            "--profiles-dir",
            str(self.root / "profiles"),
            "--notify-config",
            str(self.notify_path),
        ]
        return args + (extra or [])

    def _write_devices(self, devices):
        (self.root / "devices.json").write_text(
            json.dumps({"devices": devices}), encoding="utf-8"
        )
        (self.root / "profiles").mkdir(exist_ok=True)

    def test_load_notify_config_defaults(self):
        config = fw.load_notify_config(self.notify_path)
        self.assertEqual(config["smtp_host"], "smtp.example.com")
        self.assertEqual(config["smtp_security"], "starttls")
        self.assertEqual(config["mail_from"], "me@example.com")
        self.assertEqual(config["mail_to"], ["me@example.com"])

    def test_load_notify_config_accepts_single_recipient_string(self):
        self.notify_path.write_text(
            json.dumps(
                {"smtp_host": "h", "smtp_user": "u", "mail_to": "me@example.com"}
            ),
            encoding="utf-8",
        )
        self.assertEqual(fw.load_notify_config(self.notify_path)["mail_to"], ["me@example.com"])

    def test_load_notify_config_missing_field(self):
        self.notify_path.write_text(
            json.dumps({"smtp_host": "h", "smtp_user": "u"}), encoding="utf-8"
        )
        with self.assertRaises(ValueError) as ctx:
            fw.load_notify_config(self.notify_path)
        self.assertIn("mail_to", str(ctx.exception))

    def test_load_notify_config_empty_recipients(self):
        self.notify_path.write_text(
            json.dumps({"smtp_host": "h", "smtp_user": "u", "mail_to": []}),
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            fw.load_notify_config(self.notify_path)

    def test_load_notify_config_invalid_security(self):
        self.notify_path.write_text(
            json.dumps(
                {
                    "smtp_host": "h",
                    "smtp_user": "u",
                    "mail_to": ["a@example.com"],
                    "smtp_security": "carrier-pigeon",
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            fw.load_notify_config(self.notify_path)

    def test_load_notify_config_invalid_port(self):
        self.notify_path.write_text(
            json.dumps(
                {
                    "smtp_host": "h",
                    "smtp_user": "u",
                    "mail_to": ["a@example.com"],
                    "smtp_port": "not-a-number",
                }
            ),
            encoding="utf-8",
        )
        with self.assertRaises(ValueError):
            fw.load_notify_config(self.notify_path)

    def test_load_notify_config_must_be_object(self):
        self.notify_path.write_text(json.dumps(["a", "b"]), encoding="utf-8")
        with self.assertRaises(ValueError):
            fw.load_notify_config(self.notify_path)

    def test_build_notification_groups_by_category(self):
        changes = [
            {
                "name": "COROS DURA",
                "category": "firmware",
                "versions": ["3.2008.0", "3.2007.0"],
            }
        ]
        subject, body = fw.build_notification(changes, [])
        self.assertEqual(subject, "firmAware: 2 new versions found")
        self.assertIn("New versions", body)
        self.assertIn("firmware", body)
        self.assertIn("COROS DURA", body)
        self.assertIn("new: 3.2008.0", body)

    def test_build_notification_single_version(self):
        subject, _ = fw.build_notification(
            [{"name": "X", "category": "firmware", "versions": ["1.0"]}], []
        )
        self.assertEqual(subject, "firmAware: 1 new version found")

    def test_build_notification_warnings_only(self):
        subject, body = fw.build_notification(
            [], [{"name": "COROS DURA", "error": "timeout"}]
        )
        self.assertEqual(subject, "firmAware: 1 warning")
        self.assertIn("Warnings", body)
        self.assertIn("COROS DURA: timeout", body)

    def test_build_notification_changes_and_warnings(self):
        subject, body = fw.build_notification(
            [{"name": "X", "category": "firmware", "versions": ["1.0"]}],
            [{"name": "Y", "error": "boom"}],
        )
        self.assertEqual(subject, "firmAware: 1 new version found, 1 warning")
        self.assertIn("New versions", body)
        self.assertIn("Warnings", body)

    def test_send_email_uses_starttls_and_login(self):
        config = fw.load_notify_config(self.notify_path)
        smtp = mock.MagicMock()
        smtp.__enter__ = mock.MagicMock(return_value=smtp)
        smtp.__exit__ = mock.MagicMock(return_value=False)
        with mock.patch.object(fw.smtplib, "SMTP", return_value=smtp) as smtp_cls:
            fw.send_email(config, "Subject line", "Body text")
        smtp_cls.assert_called_once_with("smtp.example.com", 587, timeout=fw.REQUEST_TIMEOUT_SECONDS)
        smtp.starttls.assert_called_once()
        smtp.login.assert_called_once_with("me@example.com", "secret")
        smtp.send_message.assert_called_once()
        message = smtp.send_message.call_args[0][0]
        self.assertEqual(message["Subject"], "Subject line")
        self.assertEqual(message["To"], "me@example.com")

    def test_send_email_ssl_defaults_to_port_465(self):
        self.notify_path.write_text(
            json.dumps(
                {
                    "smtp_host": "smtp.example.com",
                    "smtp_user": "me@example.com",
                    "mail_to": ["me@example.com"],
                    "smtp_security": "ssl",
                }
            ),
            encoding="utf-8",
        )
        config = fw.load_notify_config(self.notify_path)
        smtp = mock.MagicMock()
        smtp.__enter__ = mock.MagicMock(return_value=smtp)
        smtp.__exit__ = mock.MagicMock(return_value=False)
        with mock.patch.object(fw.smtplib, "SMTP_SSL", return_value=smtp) as smtp_cls:
            fw.send_email(config, "S", "B")
        smtp_cls.assert_called_once_with("smtp.example.com", 465, timeout=fw.REQUEST_TIMEOUT_SECONDS)
        smtp.starttls.assert_not_called()

    def _fake_smtp(self):
        smtp = mock.MagicMock()
        smtp.__enter__ = mock.MagicMock(return_value=smtp)
        smtp.__exit__ = mock.MagicMock(return_value=False)
        return smtp


class NotificationCliTests(unittest.TestCase):
    """Check-mode wiring: changes and failures produce a mail when --notify is on."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.devices_path = self.root / "devices.json"
        self.state_path = self.root / "state.json"
        self.profiles_dir = self.root / "profiles"
        self.profiles_dir.mkdir()
        self.notify_path = self.root / "notify.json"
        self.notify_path.write_text(
            json.dumps(
                {
                    "smtp_host": "smtp.example.com",
                    "smtp_user": "me@example.com",
                    "smtp_password": "secret",
                    "mail_to": ["me@example.com"],
                }
            ),
            encoding="utf-8",
        )
        self.smtp = mock.MagicMock()
        self.smtp.__enter__ = mock.MagicMock(return_value=self.smtp)
        self.smtp.__exit__ = mock.MagicMock(return_value=False)
        self.smtp_patch = mock.patch.object(
            fw.smtplib, "SMTP", return_value=self.smtp
        )
        self.smtp_cls = self.smtp_patch.start()
        self.addCleanup(self.smtp_patch.stop)

    def _write_devices(self, devices):
        self.devices_path.write_text(json.dumps({"devices": devices}), encoding="utf-8")

    def _run(self, versions_by_url, extra=None):
        def fake_fetch(device):
            return versions_by_url[device["source_url"]]

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(fw, "fetch_source", fake_fetch):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = fw.main(self._args(extra))
        return code, out.getvalue(), err.getvalue()

    def _args(self, extra=None):
        args = [
            "--devices",
            str(self.devices_path),
            "--state",
            str(self.state_path),
            "--profiles-dir",
            str(self.profiles_dir),
            "--notify-config",
            str(self.notify_path),
        ]
        return args + (extra or [])

    def test_notify_sends_mail_on_new_version(self):
        self._write_devices(
            [
                {
                    "id": "d1",
                    "name": "Entry",
                    "source_type": "text",
                    "source_url": "https://example.com/u1",
                    "version_pattern": r"v[0-9]+",
                }
            ]
        )
        self._run({"https://example.com/u1": "v1"})
        code, out, _ = self._run({"https://example.com/u1": "v1 v2"}, extra=["--notify"])
        self.assertEqual(code, 0)
        self.smtp.send_message.assert_called_once()
        message = self.smtp.send_message.call_args[0][0]
        self.assertEqual(message["Subject"], "firmAware: 1 new version found")

    def test_no_notify_flag_sends_nothing(self):
        self._write_devices(
            [
                {
                    "id": "d1",
                    "name": "Entry",
                    "source_type": "text",
                    "source_url": "https://example.com/u1",
                    "version_pattern": r"v[0-9]+",
                }
            ]
        )
        code, out, _ = self._run({"https://example.com/u1": "v1 v2"})
        self.assertEqual(code, 0)
        self.smtp.send_message.assert_not_called()

    def test_no_mail_without_changes(self):
        self._write_devices(
            [
                {
                    "id": "d1",
                    "name": "Entry",
                    "source_type": "text",
                    "source_url": "https://example.com/u1",
                    "version_pattern": r"v[0-9]+",
                }
            ]
        )
        self._run({"https://example.com/u1": "v1"})
        self.smtp.send_message.reset_mock()
        code, _, _ = self._run({"https://example.com/u1": "v1"})
        self.assertEqual(code, 0)
        self.smtp.send_message.assert_not_called()

    def test_check_failure_sends_warning_mail(self):
        def failing_fetch(device):
            raise ValueError("network down")

        self._write_devices(
            [
                {
                    "id": "d1",
                    "name": "Entry",
                    "source_type": "text",
                    "source_url": "https://example.com/u1",
                    "version_pattern": r"v[0-9]+",
                }
            ]
        )
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(fw, "fetch_source", failing_fetch):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = fw.main(self._args(["--notify"]))
        self.assertEqual(code, 1)
        self.smtp.send_message.assert_called_once()
        message = self.smtp.send_message.call_args[0][0]
        self.assertIn("warning", message["Subject"])
        self.assertIn("network down", message.get_content())

    def test_config_error_sends_failure_mail(self):
        self.devices_path.write_text("not valid json", encoding="utf-8")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = fw.main(self._args(["--notify"]))
        self.assertEqual(code, 1)
        self.smtp.send_message.assert_called_once()
        message = self.smtp.send_message.call_args[0][0]
        self.assertEqual(message["Subject"], "firmAware: check failed")
        self.assertIn("Configuration error", message.get_content())

    def test_keyboard_interrupt_sends_nothing(self):
        self._write_devices(
            [
                {
                    "id": "d1",
                    "name": "Entry",
                    "source_type": "text",
                    "source_url": "https://example.com/u1",
                    "version_pattern": r"v[0-9]+",
                }
            ]
        )
        with mock.patch.object(fw, "fetch_source", side_effect=KeyboardInterrupt):
            with contextlib.redirect_stdout(io.StringIO()):
                with contextlib.redirect_stderr(io.StringIO()):
                    code = fw.main(self._args(["--notify"]))
        self.assertEqual(code, 130)
        self.smtp.send_message.assert_not_called()

    def test_smtp_failure_does_not_crash(self):
        self.smtp.send_message.side_effect = OSError("smtp unreachable")
        self._write_devices(
            [
                {
                    "id": "d1",
                    "name": "Entry",
                    "source_type": "text",
                    "source_url": "https://example.com/u1",
                    "version_pattern": r"v[0-9]+",
                }
            ]
        )
        self._run({"https://example.com/u1": "v1"})
        code, _, err = self._run(
            {"https://example.com/u1": "v1 v2"}, extra=["--notify"]
        )
        self.assertEqual(code, 0)
        self.assertIn("Notification error", err)

    def test_quiet_suppresses_normal_output(self):
        self._write_devices(
            [
                {
                    "id": "d1",
                    "name": "Entry",
                    "source_type": "text",
                    "source_url": "https://example.com/u1",
                    "version_pattern": r"v[0-9]+",
                }
            ]
        )
        self._run({"https://example.com/u1": "v1"})
        code, out, _ = self._run(
            {"https://example.com/u1": "v1 v2"}, extra=["--quiet", "--notify"]
        )
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "")
        # the mail was still sent
        self.smtp.send_message.assert_called_once()


if __name__ == "__main__":
    sys.exit(unittest.main())
