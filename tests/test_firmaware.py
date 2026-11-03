#!/usr/bin/env python3
"""Tests for firmAware (firmaware.py) – standard library only (unittest)."""
from __future__ import annotations

import contextlib
import http.client
import io
import json
import re
import shutil
import subprocess
import smtplib
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import firmaware as fw
from firmaware_core import checking as fw_checking
from firmaware_core import extract as fw_extract
from firmaware_core import locking as fw_locking
from firmaware_core import service as fw_service
from firmaware_core.web import server as fw_web
from firmaware_core import sources as fw_sources


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

    def test_text_with_value_path_extracts_from_nested_path(self):
        device = {"value_path": ["article", "body"], "version_pattern": r"V\s*([0-9]+(?:\.[0-9]+)*)"}
        content = json.dumps({"article": {"body": "Download V1.2.3 here"}})
        self.assertEqual(fw.versions_from_text(device, content), {"1.2.3"})

    def test_text_with_value_path_unescapes_html_entities(self):
        device = {"value_path": ["article", "body"], "version_pattern": r"FW&([0-9]+)"}
        content = json.dumps({"article": {"body": "FW&amp;7 released"}})
        self.assertEqual(fw.versions_from_text(device, content), {"7"})

    def test_text_with_value_path_missing_value_fails(self):
        device = {"value_path": ["article", "body"], "version_pattern": r"x"}
        with self.assertRaisesRegex(ValueError, "Value at json path"):
            fw.versions_from_text(device, json.dumps({"article": {}}))

    def test_text_with_value_path_two_groups_rejected(self):
        device = {"value_path": ["article", "body"], "version_pattern": r"(a)(b)"}
        with self.assertRaisesRegex(ValueError, "capturing group"):
            fw.versions_from_text(device, json.dumps({"article": {"body": "ab"}}))


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
        with mock.patch.object(fw_extract, "fetch_source", side_effect=AssertionError("no network")):
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
        with mock.patch.object(fw_extract, "fetch_source", side_effect=AssertionError("no network")):
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

    def test_devices_template_is_valid(self):
        root = Path(__file__).resolve().parents[1]
        template = root / "devices.template.json"
        self.assertTrue(template.exists(), "devices.template.json must ship with the repo")
        config = json.loads(template.read_text(encoding="utf-8"))

        raw = fw.validate_config(config, {"devices": {}})
        self.assertGreaterEqual(len(raw), 1, "the template must show at least one entry")
        resolved = fw.resolve_devices(raw, root / "profiles")
        self.assertEqual(len(resolved), len(raw))
        # both entry forms must be covered: a profile binding and a direct source
        self.assertTrue(any("profile" in entry for entry in raw))
        self.assertTrue(any("source_type" in entry for entry in raw))
        for entry in resolved:
            self.assertTrue(entry["id"], "every template entry needs an ID")
            self.assertIn("source_type", entry)


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

    def test_text_source_with_value_path(self):
        fw.validate_source_config(
            {
                "source_type": "text",
                "source_url": "https://example.com",
                "version_pattern": r"v[0-9]+",
                "value_path": ["article", "body"],
            },
            "Device 'x'",
        )

    def test_text_source_invalid_value_path(self):
        for value_path in ([], "body", ["a", ""]):
            with self.subTest(value_path=value_path):
                with self.assertRaisesRegex(ValueError, "value_path"):
                    fw.validate_source_config(
                        {
                            "source_type": "text",
                            "source_url": "https://example.com",
                            "version_pattern": "x",
                            "value_path": value_path,
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
        with mock.patch.object(fw_extract, "fetch_source", fake_fetch):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = fw.main(self._args(extra))
        return code, out.getvalue(), err.getvalue()

    def _run_status(self):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(fw_extract, "fetch_source", side_effect=AssertionError("no network")):
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

    def test_missing_devices_file_hints_at_template(self):
        code, out, err = self._run({})
        self.assertEqual(code, 1)
        self.assertIn("No configuration yet: devices.json does not exist", err)
        self.assertIn("devices.template.json", err)
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
            sorted(json.loads(self.state_path.read_text())["profiles"]["coros.dura"]["discovered_at"]),
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
        with mock.patch.object(fw_extract, "fetch_source", side_effect=AssertionError("no network")):
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
        with mock.patch.object(fw_extract, "fetch_source", return_value="V 3.0910.0"):
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
        with mock.patch.object(fw_extract, "fetch_source", return_value="v1 v2"):
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
        with mock.patch.object(smtplib, "SMTP", return_value=smtp) as smtp_cls:
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
        with mock.patch.object(smtplib, "SMTP_SSL", return_value=smtp) as smtp_cls:
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
            smtplib, "SMTP", return_value=self.smtp
        )
        self.smtp_cls = self.smtp_patch.start()
        self.addCleanup(self.smtp_patch.stop)

    def _write_devices(self, devices):
        self.devices_path.write_text(json.dumps({"devices": devices}), encoding="utf-8")

    def _run(self, versions_by_url, extra=None):
        def fake_fetch(device):
            return versions_by_url[device["source_url"]]

        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(fw_extract, "fetch_source", fake_fetch):
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
        with mock.patch.object(fw_extract, "fetch_source", failing_fetch):
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
        with mock.patch.object(fw_extract, "fetch_source", side_effect=KeyboardInterrupt):
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


class SharedStorageTests(unittest.TestCase):
    """Locking and merge-on-write so cron and other processes do not clobber each other."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_lock_excludes_other_holders(self):
        path = self.root / "state.json"
        with fw_locking.file_lock(path):
            with self.assertRaises(TimeoutError):
                with fw_locking.file_lock(path, timeout=0.2):
                    pass
        with fw_locking.file_lock(path, timeout=0.2):
            pass  # released again

    def test_lock_waits_for_a_short_holder(self):
        path = self.root / "state.json"
        order = []

        def holder():
            with fw_locking.file_lock(path):
                order.append("first")
                threading.Event().wait(0.2)

        thread = threading.Thread(target=holder)
        thread.start()
        threading.Event().wait(0.05)
        with fw_locking.file_lock(path, timeout=5):
            order.append("second")
        thread.join()
        self.assertEqual(order, ["first", "second"])

    def test_update_state_keeps_records_written_in_between(self):
        path = self.root / "state.json"
        fw.update_state(path, {"a": {"last_checked": "t", "discovered_at": {"1": "t"}}})
        fw.update_state(path, {"b": {"last_checked": "t", "discovered_at": {"2": "t"}}})
        self.assertEqual(sorted(fw.load_state(path)["devices"]), ["a", "b"])

    def test_check_run_does_not_overwrite_concurrent_state_changes(self):
        devices = self.root / "devices.json"
        state = self.root / "state.json"
        devices.write_text(
            json.dumps(
                {
                    "devices": [
                        {
                            "id": "d1",
                            "source_type": "text",
                            "source_url": "https://example.com/u1",
                            "version_pattern": r"v[0-9]+",
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )

        def fake_fetch(device):
            # Another process (for example the web interface) saves a record meanwhile.
            fw.update_state(state, {"other": {"last_checked": "t", "discovered_at": {"9": "t"}}})
            return "v1"

        with mock.patch.object(fw_extract, "fetch_source", fake_fetch):
            with contextlib.redirect_stdout(io.StringIO()):
                code = fw.main(
                    ["--devices", str(devices), "--state", str(state), "--profiles-dir", str(self.root)]
                )
        self.assertEqual(code, 0)
        self.assertEqual(sorted(fw.load_state(state)["devices"]), ["d1", "other"])

    def test_devices_file_is_written_atomically(self):
        path = self.root / "devices.json"
        fw.save_devices_file(path, {"devices": [{"id": "x"}]})
        self.assertEqual(fw.load_devices_file(path), {"devices": [{"id": "x"}]})
        self.assertFalse(path.with_name("devices.json.tmp").exists())

    def test_missing_devices_file_is_empty_configuration(self):
        self.assertEqual(fw.load_devices_file(self.root / "none.json"), {"devices": []})


class ServiceTests(unittest.TestCase):
    """Entry management and checks as the web interface uses them."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.profiles_dir = root / "profiles"
        self.profiles_dir.mkdir()
        for profile_id, category in (("t.one", "firmware"), ("t.two", "software")):
            (self.profiles_dir / f"{profile_id}.json").write_text(
                json.dumps(
                    {
                        "schema_version": 2,
                        "id": profile_id,
                        "name": f"Profile {profile_id}",
                        "category": category,
                        "source_type": "text",
                        "source_url": f"https://example.com/{profile_id}",
                        "version_pattern": r"v[0-9]+(?:\.[0-9]+)*",
                    }
                ),
                encoding="utf-8",
            )
        (self.profiles_dir / "broken.json").write_text("{", encoding="utf-8")
        self.ws = fw_service.Workspace(root / "devices.json", root / "state.json", self.profiles_dir)
        fw.save_devices_file(
            self.ws.devices_path,
            {
                "devices": [
                    {"id": "a", "profile": "t.one", "name": "Alpha", "installed_version": "v1"},
                    {"id": "b", "profile": "t.one"},
                    {"id": "c", "profile": "t.two", "enabled": False},
                ]
            },
        )

    def ids(self):
        return [entry["id"] for entry in self.ws.entries()]

    def check(self, content, ids=None):
        with mock.patch.object(fw_extract, "fetch_source", return_value=content):
            return self.ws.check(ids)

    def test_entries_without_state_and_missing_file(self):
        views = {view["id"]: view for view in self.ws.entries()}
        self.assertEqual(views["a"]["name"], "Alpha")
        self.assertEqual(views["a"]["custom_name"], "Alpha")
        self.assertEqual(views["b"]["name"], "Profile t.one")
        self.assertIsNone(views["b"]["last_checked"])
        self.assertEqual(views["a"]["installed_state"], "unknown")
        self.assertIsNone(views["b"]["installed_state"])
        self.assertFalse(views["c"]["enabled"])
        empty = fw_service.Workspace(
            self.ws.devices_path.with_name("none.json"), self.ws.state_path, self.profiles_dir
        )
        self.assertEqual(empty.entries(), [])

    def test_invalid_entry_is_listed_with_error(self):
        fw.save_devices_file(self.ws.devices_path, {"devices": [{"id": "x", "profile": "missing"}]})
        view = self.ws.entries()[0]
        self.assertEqual(view["id"], "x")
        self.assertIn("Profile file not readable", view["error"])

    def test_check_records_state_and_reports_outdated(self):
        results = {r["id"]: r for r in self.check("v1 v2")}
        self.assertEqual(results["a"]["status"], "ok")
        self.assertTrue(results["a"]["summary"].startswith("Baseline (2): v1, v2"))
        self.assertIn("outdated: installed v1, latest v2", results["a"]["summary"])
        self.assertEqual(results["c"]["status"], "disabled")
        views = {view["id"]: view for view in self.ws.entries()}
        self.assertEqual(views["a"]["latest_version"], "v2")
        self.assertEqual(views["a"]["installed_state"], "outdated")
        self.assertIsNotNone(views["b"]["last_checked"])
        self.assertIsNone(views["c"]["last_checked"])
        again = {r["id"]: r for r in self.check("v1 v2 v3")}
        self.assertEqual(again["b"]["new_versions"], ["v3"])

    def test_check_selected_ids_and_progress(self):
        seen = []
        with mock.patch.object(fw_extract, "fetch_source", return_value="v1"):
            self.ws.check(["b"], progress=lambda done, total, result: seen.append((done, total, result["id"])))
        self.assertEqual(seen, [(1, 1, "b")])
        with self.assertRaises(fw_service.NotFoundError):
            self.ws.check(["nope"])

    def test_check_error_does_not_stop_others(self):
        def fake(device):
            if device["source_url"].endswith("t.two"):
                raise ValueError("boom")
            return "v1"

        fw.save_devices_file(
            self.ws.devices_path,
            {"devices": [{"id": "a", "profile": "t.two"}, {"id": "b", "profile": "t.one"}]},
        )
        with mock.patch.object(fw_extract, "fetch_source", fake):
            results = {r["id"]: r for r in self.ws.check()}
        self.assertEqual(results["a"]["status"], "error")
        self.assertEqual(results["a"]["summary"], "Check error: boom")
        self.assertEqual(results["b"]["status"], "ok")

    def test_entry_detail_has_history_newest_first(self):
        self.check("v1 v2 v10")
        detail = self.ws.entry("b")
        self.assertEqual([h["version"] for h in detail["history"]], ["v10", "v2", "v1"])
        with self.assertRaises(fw_service.NotFoundError):
            self.ws.entry("nope")

    def test_update_entry(self):
        self.ws.update_entry("b", {"name": " Beta ", "installed_version": "v5", "enabled": False})
        view = next(v for v in self.ws.entries() if v["id"] == "b")
        self.assertEqual((view["name"], view["installed_version"], view["enabled"]), ("Beta", "v5", False))
        self.ws.update_entry("b", {"name": "", "installed_version": ""})
        view = next(v for v in self.ws.entries() if v["id"] == "b")
        self.assertEqual(view["name"], "Profile t.one")
        self.assertIsNone(view["installed_version"])

    def test_update_entry_rejects_bad_input(self):
        for changes in ({"id": "z"}, {"enabled": "yes"}, {"installed_version": 5}, {"source_url": "x"}):
            with self.subTest(changes=changes):
                with self.assertRaises(fw_service.ServiceError):
                    self.ws.update_entry("b", changes)
        with self.assertRaises(fw_service.NotFoundError):
            self.ws.update_entry("nope", {"name": "x"})
        self.assertEqual(next(v for v in self.ws.entries() if v["id"] == "b")["enabled"], True)

    def test_mark_installed_latest(self):
        with self.assertRaises(fw_service.ConflictError):
            self.ws.mark_installed_latest("b")
        self.check("v1 v2 v10")
        self.assertEqual(self.ws.mark_installed_latest("b"), "v10")
        view = next(v for v in self.ws.entries() if v["id"] == "b")
        self.assertEqual((view["installed_version"], view["installed_state"]), ("v10", "current"))

    def test_add_generates_ids_from_names(self):
        self.ws.delete_entry("c")  # frees the default name of profile t.two
        self.assertEqual(self.ws.add_entry("t.two", "Delta Büro"), "delta-buro")
        self.assertEqual(self.ws.add_entry("t.two"), "profile-t.two")  # profile name as default
        self.assertEqual(self.ws.add_entry("t.two", "Profile t.two!"), "profile-t.two-2")
        self.assertEqual(self.ws.add_entry("t.one", "###"), "entry")
        self.assertEqual(self.ws.add_entry("t.one", "###!"), "entry-2")
        self.assertEqual(self.ids(), ["a", "b", "delta-buro", "profile-t.two", "profile-t.two-2", "entry", "entry-2"])
        views = {v["id"]: v for v in self.ws.entries()}
        self.assertEqual(views["delta-buro"]["name"], "Delta Büro")
        self.assertIsNone(views["profile-t.two"]["custom_name"])  # default name is not stored

    def test_add_checks_name_and_id(self):
        self.ws.add_entry("t.two", "Delta")
        with self.assertRaises(fw_service.ConflictError):
            self.ws.add_entry("t.two", " delta ")  # same name, any case and spacing
        with self.assertRaises(fw_service.ConflictError):
            self.ws.add_entry("t.one")  # default name "Profile t.one" is used by entry b
        for bad_id in ("D", "", "a b"):
            with self.assertRaises(fw_service.ServiceError):
                self.ws.add_entry("t.two", "Echo", bad_id)
        with self.assertRaises(fw_service.ConflictError):
            self.ws.add_entry("t.two", "Echo", "a")
        with self.assertRaises(fw_service.ServiceError):
            self.ws.add_entry("missing", "Foxtrot")
        with self.assertRaises(fw_service.ServiceError):
            self.ws.add_entry("t.two", 5)
        self.assertEqual(self.ws.add_entry("t.two", "Echo", "custom-id"), "custom-id")

    def test_duplicate_names_the_copy(self):
        self.assertEqual(self.ws.duplicate_entry("a"), "alpha-copy")
        self.assertEqual(self.ws.duplicate_entry("a"), "alpha-copy-2")
        self.assertEqual(self.ids(), ["a", "alpha-copy-2", "alpha-copy", "b", "c"])
        names = {v["id"]: v["name"] for v in self.ws.entries()}
        self.assertEqual((names["alpha-copy"], names["alpha-copy-2"]), ("Alpha (copy)", "Alpha (copy 2)"))
        self.assertEqual(self.ws.duplicate_entry("a", "Kitchen"), "kitchen")
        clone = next(v for v in self.ws.entries() if v["id"] == "kitchen")
        self.assertIsNone(clone["installed_version"])
        with self.assertRaises(fw_service.ConflictError):
            self.ws.duplicate_entry("a", "kitchen")
        with self.assertRaises(fw_service.NotFoundError):
            self.ws.duplicate_entry("nope")

    def test_rename_must_keep_names_unique(self):
        with self.assertRaises(fw_service.ConflictError):
            self.ws.update_entry("b", {"name": "alpha"})
        self.ws.update_entry("a", {"name": "ALPHA"})  # an entry may change its own spelling
        with self.assertRaises(fw_service.ConflictError):
            self.ws.update_entry("a", {"name": ""})  # falls back to the profile name, used by b
        self.assertEqual(next(v for v in self.ws.entries() if v["id"] == "a")["name"], "ALPHA")
        self.ws.update_entry("b", {"name": "Beta"})
        self.ws.update_entry("a", {"name": ""})  # the profile name is free now
        self.assertEqual(next(v for v in self.ws.entries() if v["id"] == "a")["name"], "Profile t.one")

    def test_existing_duplicate_names_do_not_block_other_edits(self):
        fw.save_devices_file(
            self.ws.devices_path,
            {"devices": [{"id": "x", "profile": "t.one"}, {"id": "y", "profile": "t.one"}]},
        )
        self.ws.update_entry("x", {"installed_version": "v1", "enabled": False})
        self.ws.delete_entry("y")
        self.assertEqual(self.ids(), ["x"])

    def test_order_and_delete(self):
        self.ws.add_entry("t.two", "Delta")
        self.ws.reorder(["delta", "c", "b", "a"])
        self.assertEqual(self.ids(), ["delta", "c", "b", "a"])
        for bad in (["a"], ["a", "a", "b", "c", "delta"], ["a", "b", "c", "delta", "zzz"]):
            with self.assertRaises(fw_service.ServiceError):
                self.ws.reorder(bad)
        self.ws.delete_entry("c")
        self.assertEqual(self.ids(), ["delta", "b", "a"])
        with self.assertRaises(fw_service.NotFoundError):
            self.ws.delete_entry("c")

    def test_profiles_list_skips_invalid_files(self):
        self.assertEqual([p["id"] for p in self.ws.profiles()], ["t.one", "t.two"])
        self.assertEqual(self.ws.profiles()[1]["category"], "software")

    def test_first_edit_creates_devices_file(self):
        ws = fw_service.Workspace(
            self.ws.devices_path.with_name("fresh.json"), self.ws.state_path, self.profiles_dir
        )
        ws.add_entry("t.one", "First")
        self.assertEqual([v["id"] for v in ws.entries()], ["first"])


class WebServerTests(unittest.TestCase):
    """The HTTP layer: routing, static files, and access control."""

    TOKEN = "t" * 24

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        profiles = root / "profiles"
        profiles.mkdir()
        (profiles / "t.one.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "id": "t.one",
                    "name": "Profile one",
                    "category": "firmware",
                    "source_type": "text",
                    "source_url": "https://example.com/one",
                    "version_pattern": r"v[0-9]+",
                }
            ),
            encoding="utf-8",
        )
        self.workspace = fw_service.Workspace(root / "devices.json", root / "state.json", profiles)
        fw.save_devices_file(
            self.workspace.devices_path,
            {"devices": [{"id": "a", "profile": "t.one", "name": "Alpha"}]},
        )

    def start(self, token=None):
        server = fw_web.WebServer(("127.0.0.1", 0), self.workspace, token)
        thread = threading.Thread(target=lambda: server.serve_forever(0.05), daemon=True)
        thread.start()
        self.addCleanup(lambda: (server.shutdown(), server.server_close(), thread.join(2)))
        return server

    def request(self, server, method, path, headers=None, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            data = response.read()
            return response, data
        finally:
            connection.close()

    def get_json(self, server, path, headers=None):
        response, data = self.request(server, "GET", path, headers)
        return response.status, json.loads(data)

    # --- reading -------------------------------------------------------

    def test_entries_and_detail(self):
        server = self.start()
        status, data = self.get_json(server, "/api/entries")
        self.assertEqual(status, 200)
        self.assertEqual([e["id"] for e in data["entries"]], ["a"])
        status, detail = self.get_json(server, "/api/entries/a")
        self.assertEqual((status, detail["name"], detail["history"]), (200, "Alpha", []))
        status, error = self.get_json(server, "/api/entries/nope")
        self.assertEqual(status, 404)
        self.assertIn("nope", error["error"])
        status, data = self.get_json(server, "/api/profiles")
        self.assertEqual([p["id"] for p in data["profiles"]], ["t.one"])

    def test_security_headers_on_every_response(self):
        server = self.start()
        for path in ("/api/entries", "/", "/missing"):
            response, _ = self.request(server, "GET", path)
            self.assertEqual(response.getheader("X-Content-Type-Options"), "nosniff", path)
            self.assertIn("default-src 'self'", response.getheader("Content-Security-Policy"), path)

    def test_static_files_are_served(self):
        server = self.start()
        for path, marker in (("/", b"<title>firmAware</title>"), ("/app.js", b"api("), ("/style.css", b"--bg")):
            response, data = self.request(server, "GET", path)
            self.assertEqual(response.status, 200, path)
            self.assertIn(marker, data, path)

    def test_only_listed_static_files_are_reachable(self):
        server = self.start()
        for path in ("/../firmaware.py", "/%2e%2e/firmaware.py", "/static/app.js", "/server.py", "/api"):
            response, _ = self.request(server, "GET", path)
            self.assertEqual(response.status, 404, path)

    # --- access control --------------------------------------------------

    def test_foreign_host_header_is_rejected_without_token(self):
        server = self.start()
        response, _ = self.request(server, "GET", "/api/entries", headers={"Host": "evil.example"})
        self.assertEqual(response.status, 403)
        port = server.server_address[1]
        for host in (f"localhost:{port}", f"127.0.0.1:{port}"):
            response, _ = self.request(server, "GET", "/api/entries", headers={"Host": host})
            self.assertEqual(response.status, 200, host)

    def test_changing_requests_need_the_page_header_and_same_origin(self):
        server = self.start()
        response, _ = self.request(server, "POST", "/api/entries", body=b"{}")
        self.assertEqual(response.status, 403)
        headers = {"X-Requested-With": "firmaware", "Origin": "http://evil.example"}
        response, _ = self.request(server, "POST", "/api/entries", headers=headers, body=b"{}")
        self.assertEqual(response.status, 403)
        headers = {"X-Requested-With": "firmaware"}
        response, _ = self.request(server, "POST", "/api/profiles", headers=headers, body=b"{}")
        self.assertEqual(response.status, 405)  # allowed through, but not a route for POST

    def test_token_is_required_when_configured(self):
        server = self.start(self.TOKEN)
        response, _ = self.request(server, "GET", "/api/entries")
        self.assertEqual(response.status, 401)
        response, _ = self.request(server, "GET", "/")
        self.assertEqual(response.status, 401)
        response, _ = self.request(server, "GET", "/api/entries", headers={"Authorization": "Bearer wrong"})
        self.assertEqual(response.status, 401)
        response, _ = self.request(
            server, "GET", "/api/entries", headers={"Authorization": f"Bearer {self.TOKEN}"}
        )
        self.assertEqual(response.status, 200)

    def test_token_in_url_sets_a_strict_cookie(self):
        server = self.start(self.TOKEN)
        response, _ = self.request(server, "GET", f"/?token={self.TOKEN}")
        self.assertEqual(response.status, 303)
        cookie = response.getheader("Set-Cookie")
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        value = cookie.split(";")[0]
        response, _ = self.request(server, "GET", "/api/entries", headers={"Cookie": value})
        self.assertEqual(response.status, 200)
        response, _ = self.request(server, "GET", "/?token=nope")
        self.assertEqual(response.status, 401)

    def test_network_binding_requires_a_token(self):
        with self.assertRaises(ValueError):
            fw_web.WebServer(("0.0.0.0", 0), self.workspace, None)
        server = fw_web.WebServer(("0.0.0.0", 0), self.workspace, self.TOKEN)
        server.server_close()

    def test_load_web_token(self):
        path = Path(self._tmp.name) / "web.json"
        self.assertIsNone(fw_web.load_web_token(path))
        path.write_text(json.dumps({"token": "short"}), encoding="utf-8")
        with self.assertRaises(ValueError):
            fw_web.load_web_token(path)
        path.write_text(json.dumps({"token": self.TOKEN}), encoding="utf-8")
        self.assertEqual(fw_web.load_web_token(path), self.TOKEN)

    def test_is_loopback(self):
        for host in ("127.0.0.1", "localhost", "::1"):
            self.assertTrue(fw_web.is_loopback(host), host)
        for host in ("0.0.0.0", "192.168.1.5", "example.com", "::"):
            self.assertFalse(fw_web.is_loopback(host), host)

    # --- changing entries ------------------------------------------------

    PAGE = {"X-Requested-With": "firmaware", "Content-Type": "application/json"}

    def send(self, server, method, path, payload=None, headers=None):
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        response, data = self.request(server, method, path, {**self.PAGE, **(headers or {})}, body)
        return response.status, json.loads(data) if data else None

    def test_update_entry(self):
        server = self.start()
        status, entry = self.send(
            server, "PATCH", "/api/entries/a", {"name": "Renamed", "installed_version": "v1", "enabled": False}
        )
        self.assertEqual(status, 200)
        self.assertEqual((entry["name"], entry["installed_version"], entry["enabled"]), ("Renamed", "v1", False))
        status, error = self.send(server, "PATCH", "/api/entries/a", {"enabled": "yes"})
        self.assertEqual(status, 400)
        self.assertIn("enabled", error["error"])
        self.assertEqual(self.send(server, "PATCH", "/api/entries/nope", {"name": "x"})[0], 404)
        self.assertEqual(self.send(server, "PATCH", "/api/entries/a", ["x"])[0], 400)

    def test_add_duplicate_order_and_delete(self):
        server = self.start()
        status, entry = self.send(server, "POST", "/api/entries", {"profile": "t.one", "id": "b", "name": "Beta"})
        self.assertEqual((status, entry["id"], entry["name"]), (201, "b", "Beta"))
        self.assertEqual(self.send(server, "POST", "/api/entries", {"profile": "t.one", "id": "b"})[0], 409)
        self.assertEqual(self.send(server, "POST", "/api/entries", {"profile": "t.one", "id": "B!"})[0], 400)
        self.assertEqual(self.send(server, "POST", "/api/entries", {"profile": "none", "id": "c"})[0], 400)
        self.assertEqual(self.send(server, "POST", "/api/entries", {})[0], 400)
        status, copy_ = self.send(server, "POST", "/api/entries/a/duplicate", {"id": "a2"})
        self.assertEqual((status, copy_["id"]), (201, "a2"))
        status, data = self.send(server, "PUT", "/api/order", {"ids": ["b", "a2", "a"]})
        self.assertEqual([e["id"] for e in data["entries"]], ["b", "a2", "a"])
        self.assertEqual(self.send(server, "PUT", "/api/order", {"ids": ["a"]})[0], 400)
        self.assertEqual(self.send(server, "DELETE", "/api/entries/a2")[0], 200)
        self.assertEqual(self.send(server, "DELETE", "/api/entries/a2")[0], 404)

    def test_names_instead_of_ids(self):
        server = self.start()
        status, entry = self.send(server, "POST", "/api/entries", {"profile": "t.one", "name": "Beta Reader"})
        self.assertEqual((status, entry["id"], entry["name"]), (201, "beta-reader", "Beta Reader"))
        status, error = self.send(server, "POST", "/api/entries", {"profile": "t.one", "name": "beta reader"})
        self.assertEqual(status, 409)
        self.assertIn("already used", error["error"])
        self.assertEqual(self.send(server, "POST", "/api/entries", {"profile": "t.one", "name": 5})[0], 400)
        status, copy_ = self.send(server, "POST", "/api/entries/a/duplicate", {})
        self.assertEqual((status, copy_["id"], copy_["name"]), (201, "alpha-copy", "Alpha (copy)"))
        status, copy_ = self.send(server, "POST", "/api/entries/a/duplicate", {"name": "Garage"})
        self.assertEqual((status, copy_["id"]), (201, "garage"))
        self.assertEqual(self.send(server, "POST", "/api/entries/a/duplicate", {"name": "garage"})[0], 409)
        status, error = self.send(server, "PATCH", "/api/entries/beta-reader", {"name": "ALPHA"})
        self.assertEqual(status, 409)

    def test_installed_latest_needs_known_versions(self):
        server = self.start()
        self.assertEqual(self.send(server, "POST", "/api/entries/a/installed-latest")[0], 409)
        self.run_check(server)
        status, entry = self.send(server, "POST", "/api/entries/a/installed-latest")
        self.assertEqual((status, entry["installed_version"], entry["installed_state"]), (200, "v2", "current"))

    def test_bad_bodies_are_rejected(self):
        server = self.start()
        status, _ = self.send(server, "PATCH", "/api/entries/a", headers={"Content-Type": "text/plain"}, payload={"name": "x"})
        self.assertEqual(status, 400)
        response, _ = self.request(server, "PATCH", "/api/entries/a", self.PAGE, b"{not json")
        self.assertEqual(response.status, 400)
        response, _ = self.request(server, "PATCH", "/api/entries/a", self.PAGE, b"x" * (64 * 1024 + 1))
        self.assertEqual(response.status, 413)

    # --- check jobs ------------------------------------------------------

    def run_check(self, server, ids=None, content="v1 v2"):
        with mock.patch.object(fw_extract, "fetch_source", return_value=content):
            status, data = self.send(server, "POST", "/api/checks", {} if ids is None else {"ids": ids})
            self.assertEqual(status, 202)
            return self.wait_for_job(server, data["job"]["id"])

    def wait_for_job(self, server, job_id):
        for _ in range(200):
            _, data = self.get_json(server, f"/api/checks/{job_id}")
            if data["job"]["status"] != "running":
                return data["job"]
            threading.Event().wait(0.02)
        self.fail("check job did not finish")

    def test_check_job_runs_and_updates_state(self):
        server = self.start()
        self.assertEqual(self.get_json(server, "/api/checks")[1], {"job": None})
        job = self.run_check(server)
        self.assertEqual((job["status"], job["done"], job["total"]), ("done", 1, 1))
        self.assertTrue(job["results"][0]["summary"].startswith("Baseline (2): v1, v2"))
        self.assertEqual(self.get_json(server, "/api/checks")[1]["job"]["id"], job["id"])
        _, entry = self.get_json(server, "/api/entries/a")
        self.assertEqual(entry["latest_version"], "v2")
        self.assertIsNotNone(entry["last_checked"])

    def test_check_job_for_selected_entry_and_unknown_id(self):
        server = self.start()
        self.assertEqual(self.send(server, "POST", "/api/checks", {"ids": ["nope"]})[0], 404)
        self.assertEqual(self.send(server, "POST", "/api/checks", {"ids": "a"})[0], 400)
        self.assertEqual(self.get_json(server, "/api/checks/999")[0], 404)
        job = self.run_check(server, ["a"])
        self.assertEqual([r["id"] for r in job["results"]], ["a"])

    def test_only_one_check_runs_at_a_time(self):
        server = self.start()
        gate = threading.Event()

        def slow_fetch(device):
            gate.wait(5)
            return "v1"

        with mock.patch.object(fw_extract, "fetch_source", slow_fetch):
            status, data = self.send(server, "POST", "/api/checks", {})
            self.assertEqual(status, 202)
            self.assertEqual(self.send(server, "POST", "/api/checks", {})[0], 409)
            gate.set()
            self.assertEqual(self.wait_for_job(server, data["job"]["id"])["status"], "done")
        job = self.run_check(server)  # a new job can start once the first one ended
        self.assertEqual(job["status"], "done")

    def test_check_job_reports_entry_errors(self):
        server = self.start()
        with mock.patch.object(fw_extract, "fetch_source", side_effect=ValueError("boom")):
            _, data = self.send(server, "POST", "/api/checks", {})
            job = self.wait_for_job(server, data["job"]["id"])
        self.assertEqual(job["status"], "done")
        self.assertEqual((job["results"][0]["status"], job["results"][0]["summary"]), ("error", "Check error: boom"))

    # --- command line ----------------------------------------------------

    def test_host_and_port_require_serve(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                fw.parse_args(["--host", "0.0.0.0"])
            with self.assertRaises(SystemExit):
                fw.parse_args(["--port", "9000", "--check"])
        args = fw.parse_args(["--serve", "--port", "9000"])
        self.assertTrue(args.serve)
        self.assertEqual(args.port, 9000)

    def test_serve_refuses_network_address_without_token(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = fw_web.serve(self.workspace, "0.0.0.0", 0, Path(self._tmp.name) / "none.json")
        self.assertEqual(code, 1)
        self.assertIn("token is required", err.getvalue())


class SharedSourceTests(unittest.TestCase):
    """Devices of one type (one profile) share their versions; only the installed version differs."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.profiles_dir = self.root / "profiles"
        self.profiles_dir.mkdir()
        (self.profiles_dir / "t.os.json").write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "id": "t.os",
                    "name": "Test Phone OS",
                    "category": "operating_system",
                    "source_type": "text",
                    "source_url": "https://example.com/os",
                    "version_pattern": r"v[0-9]+",
                }
            ),
            encoding="utf-8",
        )
        self.devices = self.root / "devices.json"
        self.state = self.root / "state.json"
        self.notify = self.root / "notify.json"
        self.notify.write_text(
            json.dumps(
                {"smtp_host": "h", "smtp_user": "u", "smtp_password": "p", "mail_to": ["me@example.com"]}
            ),
            encoding="utf-8",
        )
        self.smtp = mock.MagicMock()
        self.smtp.__enter__ = mock.MagicMock(return_value=self.smtp)
        self.smtp.__exit__ = mock.MagicMock(return_value=False)
        patcher = mock.patch.object(smtplib, "SMTP", return_value=self.smtp)
        patcher.start()
        self.addCleanup(patcher.stop)
        fw.save_devices_file(
            self.devices,
            {
                "devices": [
                    {"id": "phone-a", "profile": "t.os", "name": "Phone A", "installed_version": "v1"},
                    {"id": "phone-b", "profile": "t.os", "name": "Phone B", "installed_version": "v3"},
                ]
            },
        )

    def run_cli(self, content, extra=None):
        fetched = []

        def fake_fetch(device):
            fetched.append(device["source_url"])
            if isinstance(content, Exception):
                raise content
            return content

        args = [
            "--devices", str(self.devices), "--state", str(self.state),
            "--profiles-dir", str(self.profiles_dir), "--notify-config", str(self.notify),
        ] + (extra or [])
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(fw_extract, "fetch_source", fake_fetch):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = fw.main(args)
        return code, out.getvalue(), err.getvalue(), fetched

    def stored(self):
        return json.loads(self.state.read_text())

    def test_one_record_per_type_and_one_fetch(self):
        code, out, _, fetched = self.run_cli("v1 v2 v3")
        self.assertEqual(code, 0)
        self.assertEqual(len(fetched), 1)
        state = self.stored()
        self.assertEqual(sorted(state["profiles"]["t.os"]["discovered_at"]), ["v1", "v2", "v3"])
        self.assertEqual(state.get("devices", {}), {})
        lines = [line for line in out.splitlines() if line.startswith("Phone")]
        self.assertEqual(len(lines), 2)
        self.assertIn("outdated: installed v1, latest v3", next(l for l in lines if l.startswith("Phone A")))
        self.assertNotIn("outdated", next(l for l in lines if l.startswith("Phone B")))

    def test_a_new_version_is_reported_once_per_type(self):
        self.run_cli("v1 v2 v3")
        code, out, _, _ = self.run_cli("v1 v2 v3 v4", extra=["--notify"])
        self.assertEqual(code, 0)
        message = self.smtp.send_message.call_args[0][0]
        self.assertEqual(message["Subject"], "firmAware: 1 new version found, 2 outdated")
        body = message.get_content()
        self.assertEqual(body.count("new: v4"), 1)
        self.assertIn("Test Phone OS", body)
        self.assertIn("Phone A: installed v1", body)
        self.assertIn("Phone B: installed v3", body)

    def test_a_failing_source_is_one_warning(self):
        code, _, err, _ = self.run_cli(ValueError("boom"), extra=["--notify"])
        self.assertEqual(code, 1)
        self.assertEqual(err.count("Check error: boom"), 2)  # one line per device
        message = self.smtp.send_message.call_args[0][0]
        self.assertEqual(message["Subject"], "firmAware: 1 warning")
        self.assertEqual(message.get_content().count("boom"), 1)

    def test_second_device_gets_no_baseline_of_its_own(self):
        self.run_cli("v1 v2")
        fw.save_devices_file(
            self.devices,
            {"devices": [
                {"id": "phone-a", "profile": "t.os"},
                {"id": "phone-b", "profile": "t.os"},
                {"id": "phone-c", "profile": "t.os", "name": "Phone C", "installed_version": "v2"},
            ]},
        )
        _, out, _, _ = self.run_cli("v1 v2")
        line = next(l for l in out.splitlines() if l.startswith("Phone C"))
        self.assertIn("no changes", line)
        self.assertNotIn("Baseline", line)

    # --- migration of the former per-entry records ---------------------------

    def legacy_state(self):
        self.state.write_text(
            json.dumps(
                {
                    "devices": {
                        "phone-a": {"last_checked": "2026-10-01T10:00:00+02:00",
                                    "discovered_at": {"v1": "2026-09-01T10:00:00+02:00", "v2": "2026-10-01T10:00:00+02:00"}},
                        "phone-b": {"last_checked": "2026-10-05T10:00:00+02:00",
                                    "discovered_at": {"v1": "2026-08-01T10:00:00+02:00", "v3": "2026-10-05T10:00:00+02:00"}},
                        "removed-entry": {"last_checked": "t", "discovered_at": {"x": "t"}},
                    }
                }
            ),
            encoding="utf-8",
        )

    def test_migrate_state_merges_versions_and_keeps_other_records(self):
        self.legacy_state()
        state = json.loads(self.state.read_text())
        entries = json.loads(self.devices.read_text())["devices"]
        self.assertTrue(fw.migrate_state(state, entries))
        record = state["profiles"]["t.os"]
        self.assertEqual(sorted(record["discovered_at"]), ["v1", "v2", "v3"])
        self.assertEqual(record["discovered_at"]["v1"], "2026-08-01T10:00:00+02:00")  # earliest wins
        self.assertEqual(record["last_checked"], "2026-10-05T10:00:00+02:00")  # latest check
        self.assertEqual(list(state["devices"]), ["removed-entry"])
        self.assertFalse(fw.migrate_state(state, entries))  # nothing left to move

    def test_check_migrates_and_backs_up_once(self):
        self.legacy_state()
        original = self.state.read_text()
        code, out, _, _ = self.run_cli("v1 v2 v3")
        self.assertEqual(code, 0)
        self.assertIn("no changes", out)
        backup = self.state.with_name("state.json.bak")
        self.assertEqual(backup.read_text(), original)
        state = self.stored()
        self.assertEqual(sorted(state["profiles"]["t.os"]["discovered_at"]), ["v1", "v2", "v3"])
        self.assertEqual(list(state["devices"]), ["removed-entry"])
        self.run_cli("v1 v2 v3")
        self.assertEqual(backup.read_text(), original)  # an existing backup is never overwritten

    def test_status_shows_migrated_data_without_writing(self):
        self.legacy_state()
        original = self.state.read_text()
        code, out, _, fetched = self.run_cli("unused", extra=["--status"])
        self.assertEqual(code, 0)
        self.assertEqual(fetched, [])
        self.assertEqual(self.state.read_text(), original)
        self.assertFalse(self.state.with_name("state.json.bak").exists())
        self.assertIn("outdated", next(l for l in out.splitlines() if l.startswith("Phone A")))

    # --- service layer ---------------------------------------------------------

    def test_service_shares_history_and_checks_per_type(self):
        ws = fw_service.Workspace(self.devices, self.state, self.profiles_dir)
        with mock.patch.object(fw_extract, "fetch_source", return_value="v1 v2 v3") as fetch:
            results = ws.check()
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual([r["id"] for r in results], ["phone-a", "phone-b"])
        self.assertTrue(all(r["status"] == "ok" for r in results))
        views = {v["id"]: v for v in ws.entries()}
        self.assertEqual(views["phone-a"]["latest_version"], views["phone-b"]["latest_version"])
        self.assertEqual(views["phone-a"]["type_devices"], 2)
        self.assertEqual(views["phone-a"]["installed_state"], "outdated")
        self.assertEqual(views["phone-b"]["installed_state"], "current")
        self.assertEqual(
            [h["version"] for h in ws.entry("phone-b")["history"]],
            [h["version"] for h in ws.entry("phone-a")["history"]],
        )
        self.assertEqual(list(self.stored()["profiles"]), ["t.os"])

    def test_service_migrates_on_check_not_on_read(self):
        self.legacy_state()
        ws = fw_service.Workspace(self.devices, self.state, self.profiles_dir)
        original = self.state.read_text()
        views = {v["id"]: v for v in ws.entries()}
        self.assertEqual(views["phone-a"]["known_versions"], 3)
        self.assertEqual(self.state.read_text(), original)
        with mock.patch.object(fw_extract, "fetch_source", return_value="v1 v2 v3"):
            ws.check(["phone-a"])
        self.assertTrue(self.state.with_name("state.json.bak").exists())
        self.assertIn("profiles", self.stored())

    def test_service_error_applies_to_every_device_of_the_type(self):
        ws = fw_service.Workspace(self.devices, self.state, self.profiles_dir)
        with mock.patch.object(fw_extract, "fetch_source", side_effect=ValueError("boom")):
            results = ws.check()
        self.assertEqual([r["status"] for r in results], ["error", "error"])
        self.assertEqual({r["summary"] for r in results}, {"Check error: boom"})


class DeviceSelectionTests(unittest.TestCase):
    """--device accepts a name (any case) or an ID."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.devices, self.state = root / "devices.json", root / "state.json"
        self.profiles = root / "profiles"
        self.profiles.mkdir()
        entries = [
            {"id": "tolino-living", "name": "Tolino (living room)", "source_type": "text",
             "source_url": "https://example.com/t", "version_pattern": r"v[0-9]+"},
            {"id": "tolino-bed", "name": "Tolino (bedroom)", "source_type": "text",
             "source_url": "https://example.com/t", "version_pattern": r"v[0-9]+"},
            {"id": "twin-1", "name": "Twin", "source_type": "text",
             "source_url": "https://example.com/t", "version_pattern": r"v[0-9]+"},
            {"id": "twin-2", "name": "twin", "source_type": "text",
             "source_url": "https://example.com/t", "version_pattern": r"v[0-9]+"},
        ]
        fw.save_devices_file(self.devices, {"devices": entries})

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(fw_extract, "fetch_source", return_value="v1"):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = fw.main(
                    ["--devices", str(self.devices), "--state", str(self.state),
                     "--profiles-dir", str(self.profiles), *args]
                )
        return code, out.getvalue(), err.getvalue()

    def test_select_by_name_any_case_or_by_id(self):
        for selector in ("tolino (BEDROOM)", "Tolino (bedroom)", "tolino-bed"):
            with self.subTest(selector=selector):
                code, out, _ = self.run_cli("--check", "--device", selector)
                self.assertEqual(code, 0)
                self.assertIn("Tolino (bedroom)", out)
                self.assertNotIn("living room", out)

    def test_positional_name_and_status(self):
        code, out, _ = self.run_cli("--status", "Tolino (living room)")
        self.assertEqual(code, 0)
        self.assertIn("Tolino (living room)", out)
        self.assertNotIn("bedroom", out)
        self.assertNotIn("tolino-living", out)  # IDs are not shown

    def test_unknown_and_ambiguous_names(self):
        code, _, err = self.run_cli("--check", "--device", "Nokia")
        self.assertEqual(code, 1)
        self.assertIn("No entry 'Nokia'", err)
        code, _, err = self.run_cli("--check", "--device", "TWIN")
        self.assertEqual(code, 1)
        self.assertIn("twin-1", err)
        self.assertIn("twin-2", err)
        code, _, _ = self.run_cli("--check", "--device", "twin-2")  # the ID disambiguates
        self.assertEqual(code, 0)

    def test_disabled_entry_is_marked_in_status(self):
        data = json.loads(self.devices.read_text())
        data["devices"][0]["enabled"] = False
        fw.save_devices_file(self.devices, data)
        _, out, _ = self.run_cli("--status", "tolino-living")
        self.assertIn("Tolino (living room) [off]", out)


class SourceTypeRegistryTests(unittest.TestCase):
    def test_registry_defines_supported_types(self):
        self.assertEqual(fw.SUPPORTED_SOURCE_TYPES, frozenset(fw_sources.SOURCE_TYPES))
        for name, source_type in fw_sources.SOURCE_TYPES.items():
            self.assertEqual(source_type.name, name)

    def test_registered_type_is_validated_and_extracted(self):
        def validate(source, context):
            if "marker" not in source:
                raise ValueError(f"{context}: Required field 'marker' missing")

        def extract(device, content):
            return set(content.split())

        custom = fw_sources.SourceType("custom", validate, extract)
        source = {"source_type": "custom", "source_url": "https://example.com"}
        with mock.patch.dict(fw_sources.SOURCE_TYPES, {"custom": custom}):
            with self.assertRaisesRegex(ValueError, "marker"):
                fw.validate_source_config(source, "Entry 'x'")
            fw.validate_source_config({**source, "marker": 1}, "Entry 'x'")
            self.assertEqual(fw.extract_versions("custom", "1.0 1.1", source), ["1.0", "1.1"])


class CheckResultTests(unittest.TestCase):
    DEVICE = {"id": "x", "name": "X", "category": "firmware"}

    def check(self, versions, state):
        with mock.patch.object(fw_checking, "fetch_versions", return_value=versions):
            return fw.check_device(self.DEVICE, state)

    def test_baseline_has_no_new_versions(self):
        result = self.check(["1.0"], {"devices": {}})
        self.assertTrue(result.ok)
        self.assertIsNone(result.previous)
        self.assertEqual(result.new_versions, [])
        self.assertEqual(result.summary, "Baseline (1): 1.0")

    def test_new_version_is_reported_without_touching_state(self):
        state = {"devices": {"x": {"last_checked": "t", "discovered_at": {"1.0": "t"}}}}
        result = self.check(["1.0", "1.1"], state)
        self.assertEqual(result.new_versions, ["1.1"])
        self.assertEqual(result.summary, "new: 1.1")
        self.assertEqual(list(state["devices"]["x"]["discovered_at"]), ["1.0"])

    def test_fetch_error_is_captured(self):
        with mock.patch.object(fw_checking, "fetch_versions", side_effect=ValueError("boom")):
            result = fw.check_device(self.DEVICE, {"devices": {}})
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "boom")

    def test_record_check_keeps_known_timestamps(self):
        state = {"devices": {"x": {"last_checked": "t", "discovered_at": {"1.0": "old"}}}}
        result = self.check(["1.0", "1.1"], state)
        fw.record_check(state, self.DEVICE, result)
        discovered = state["devices"]["x"]["discovered_at"]
        self.assertEqual(discovered["1.0"], "old")
        self.assertIn("1.1", discovered)

    def test_process_device_leaves_state_unchanged_on_error(self):
        state = {"devices": {}}
        errors = []
        with mock.patch.object(fw_checking, "fetch_versions", side_effect=ValueError("boom")):
            with contextlib.redirect_stderr(io.StringIO()):
                ok = fw.process_device(self.DEVICE, state, 1, errors=errors, quiet=True)
        self.assertFalse(ok)
        self.assertEqual(state, {"devices": {}})
        self.assertEqual(errors, [{"name": "X", "error": "boom"}])


class InstalledVersionTests(unittest.TestCase):
    """The user-maintained installed version per entry and its comparison."""

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
        smtp_patch = mock.patch.object(smtplib, "SMTP", return_value=self.smtp)
        smtp_patch.start()
        self.addCleanup(smtp_patch.stop)

    def entry(self, entry_id, installed=None, url="https://example.com/u1"):
        entry = {
            "id": entry_id,
            "name": entry_id.title(),
            "category": "firmware",
            "source_type": "text",
            "source_url": url,
            "version_pattern": r"v[0-9]+",
        }
        if installed is not None:
            entry["installed_version"] = installed
        return entry

    def run_cli(self, content, extra=None):
        fetched = []

        def fake_fetch(device):
            fetched.append(device["source_url"])
            return content

        args = [
            "--devices", str(self.devices_path),
            "--state", str(self.state_path),
            "--profiles-dir", str(self.profiles_dir),
            "--notify-config", str(self.notify_path),
        ] + (extra or [])
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(fw_extract, "fetch_source", fake_fetch):
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = fw.main(args)
        self.assertEqual(code, 0, err.getvalue())
        return out.getvalue(), fetched

    def write_devices(self, *entries):
        self.devices_path.write_text(json.dumps({"devices": list(entries)}), encoding="utf-8")

    # --- validation ---

    def test_direct_entry_accepts_installed_version(self):
        resolved = fw.resolve_device(self.entry("a", " v1 "), self.profiles_dir)
        self.assertEqual(resolved["installed_version"], "v1")

    def test_invalid_installed_version_rejected(self):
        for value in ("", "  ", 5, None, ["v1"]):
            with self.subTest(value=value):
                entry = {**self.entry("a"), "installed_version": value}
                with self.assertRaisesRegex(ValueError, "installed_version"):
                    fw.resolve_device(entry, self.profiles_dir)

    def test_profile_based_entry_accepts_installed_version(self):
        profile = {
            "schema_version": 2, "id": "x.y", "name": "X", "category": "firmware",
            "source_type": "text", "source_url": "https://example.com/u1",
            "version_pattern": r"v[0-9]+",
        }
        (self.profiles_dir / "x.y.json").write_text(json.dumps(profile), encoding="utf-8")
        resolved = fw.resolve_device(
            {"id": "one", "profile": "x.y", "installed_version": "v3"}, self.profiles_dir
        )
        self.assertEqual(resolved["installed_version"], "v3")
        with self.assertRaisesRegex(ValueError, "installed_version"):
            fw.resolve_device(
                {"id": "one", "profile": "x.y", "installed_version": ""}, self.profiles_dir
            )

    def test_profile_must_not_contain_installed_version(self):
        profile = {
            "schema_version": 2, "id": "x.y", "name": "X", "category": "firmware",
            "source_type": "text", "source_url": "https://example.com/u1",
            "version_pattern": r"v[0-9]+", "installed_version": "v1",
        }
        with self.assertRaisesRegex(ValueError, "belongs to an entry"):
            fw.validate_profile(profile, "x.y", "ctx")

    # --- comparison ---

    def test_installed_status(self):
        discovered = {"1.9": "t", "1.10": "t"}
        status = fw_checking.installed_status
        self.assertEqual(status({}, discovered).state, "not set")
        self.assertEqual(status({"installed_version": "1.9"}, None).state, "unknown")
        self.assertEqual(status({"installed_version": "1.9"}, discovered).state, "outdated")
        self.assertEqual(status({"installed_version": "1.10"}, discovered).state, "current")
        self.assertEqual(status({"installed_version": "1.11"}, discovered).state, "current")
        legacy = {"1978074000": "t", "6.1.1978418030": "t"}
        self.assertEqual(
            status({"installed_version": "1978074000"}, legacy).state, "outdated"
        )
        result = status({"installed_version": "1.9"}, discovered)
        self.assertEqual((result.installed, result.latest), ("1.9", "1.10"))

    # --- check output, status, notification ---

    def test_two_devices_of_one_type_fetch_once_and_show_state(self):
        self.write_devices(self.entry("old", "v1"), self.entry("new", "v2"))
        self.run_cli("v1 v2")  # baseline
        out, fetched = self.run_cli("v1 v2")
        self.assertEqual(len(fetched), 1)
        old_line = next(line for line in out.splitlines() if line.startswith("Old"))
        new_line = next(line for line in out.splitlines() if line.startswith("New"))
        self.assertIn("outdated: installed v1, latest v2", old_line)
        self.assertNotIn("outdated", new_line)

    def test_quiet_suppresses_outdated_line(self):
        self.write_devices(self.entry("old", "v1"))
        self.run_cli("v1 v2")
        out, _ = self.run_cli("v1 v2", extra=["--quiet"])
        self.assertEqual(out, "")

    def test_status_shows_installed_and_state(self):
        self.write_devices(self.entry("old", "v1"), self.entry("new", "v2"), self.entry("none"))
        self.run_cli("v1 v2")
        out, _ = self.run_cli("v1 v2", extra=["--status"])
        header = next(line for line in out.splitlines() if line.startswith("Entry"))
        self.assertIn("Installed", header)
        self.assertIn("State", header)
        rows = {line.split()[0]: line for line in out.splitlines() if line.split()[:1] in (["Old"], ["New"], ["None"])}
        self.assertIn("outdated", rows["Old"])
        self.assertIn("current", rows["New"])
        self.assertNotIn("outdated", rows["None"])
        self.assertNotIn("current", rows["None"])
        self.assertNotIn("ID", header.split())

    def test_mail_lists_outdated_devices_with_new_version(self):
        self.write_devices(self.entry("old", "v1"), self.entry("new", "v2"))
        self.run_cli("v1")  # baseline
        self.run_cli("v1 v2", extra=["--notify"])
        self.smtp.send_message.assert_called_once()
        message = self.smtp.send_message.call_args[0][0]
        self.assertEqual(message["Subject"], "firmAware: 2 new versions found, 1 outdated")
        body = message.get_content()
        self.assertIn("Outdated devices", body)
        self.assertIn("Old: installed v1, latest v2", body)
        self.assertIn("installed: v1", body)

    def test_outdated_alone_sends_no_mail(self):
        self.write_devices(self.entry("old", "v1"))
        self.run_cli("v1 v2")  # baseline
        self.run_cli("v1 v2", extra=["--notify"])
        self.smtp.send_message.assert_not_called()

    def test_build_notification_without_outdated_is_unchanged(self):
        changes = [{"name": "X", "category": "firmware", "versions": ["1"]}]
        subject, body = fw.build_notification(changes, [])
        self.assertEqual(subject, "firmAware: 1 new version found")
        self.assertNotIn("Outdated", body)

    # --- helpers shared with the web interface ---

    def test_set_and_clear_installed_version(self):
        entry = {"id": "a"}
        fw_service.set_installed_version(entry, "  v3 ")
        self.assertEqual(entry["installed_version"], "v3")
        fw_service.set_installed_version(entry, "  ")
        self.assertNotIn("installed_version", entry)

    def test_mark_installed_latest(self):
        entry = {"id": "a"}
        self.assertIsNone(fw_service.mark_installed_latest(entry, {}))
        self.assertNotIn("installed_version", entry)
        latest = fw_service.mark_installed_latest(entry, {"1.9": "t", "1.10": "t"})
        self.assertEqual(latest, "1.10")
        self.assertEqual(entry["installed_version"], "1.10")

    def test_interactive_option_points_to_the_web_interface(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = fw.main(["--interactive"])
        self.assertEqual(code, 1)
        self.assertIn("--serve", err.getvalue())
        self.assertNotIn("--interactive", subprocess.run(
            [sys.executable, str(Path(__file__).resolve().parent.parent / "firmaware.py"), "--help"],
            capture_output=True, text=True, timeout=30, check=False,
        ).stdout)


class WebFrontendLogicTests(unittest.TestCase):
    """The pure JavaScript helpers, checked with Node.js when it is installed."""

    @unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
    def test_ordering_helpers(self):
        script = Path(__file__).resolve().parent / "web_order_test.mjs"
        result = subprocess.run(
            ["node", str(script)], capture_output=True, text=True, timeout=30, check=False
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    @unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
    def test_scripts_parse(self):
        static = Path(fw_web.__file__).resolve().parent / "static"
        for script in sorted(static.glob("*.js")):
            with self.subTest(script=script.name):
                result = subprocess.run(
                    ["node", "--check", "--input-type=module"],
                    input=script.read_text(encoding="utf-8"),
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)


class DocumentationConsistencyTests(unittest.TestCase):
    """Guard against drift between code and the single documentation sources."""

    ROOT = Path(__file__).resolve().parent.parent

    def read_doc(self, relative: str) -> str:
        return (self.ROOT / relative).read_text(encoding="utf-8")

    def test_every_source_type_is_documented(self):
        for name in ("requirements/specification.md", "README.md"):
            text = self.read_doc(name)
            for source_type in sorted(fw.SUPPORTED_SOURCE_TYPES):
                with self.subTest(doc=name, source_type=source_type):
                    self.assertIn(f"`{source_type}`", text)

    def test_every_cli_option_is_in_readme(self):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer), self.assertRaises(SystemExit):
            fw.parse_args(["--help"])
        options = set(re.findall(r"--[a-z][a-z-]*", buffer.getvalue())) - {"--help"}
        self.assertTrue(options)
        readme = self.read_doc("README.md")
        for option in sorted(options):
            with self.subTest(option=option):
                self.assertIn(f"`{option}", readme)


if __name__ == "__main__":
    sys.exit(unittest.main())
