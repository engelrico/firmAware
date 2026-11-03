"""Shared constants: limits, source types, profile fields, and categories."""
from __future__ import annotations

import re


DESCRIPTION = "firmAware – Check firmware sources for new version labels."
USER_AGENT = "firmAware/0.1"
ACCEPT_HEADER = "application/json,text/html,*/*"
REQUEST_TIMEOUT_SECONDS = 30
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
PATTERN_TEST_TIMEOUT_SECONDS = 3.0
CONTEXT_CHARS = 150
MAX_EXAMPLE_LENGTH = 100
PROFILE_SCHEMA_VERSION = 2
PROFILE_ID_PATTERN = re.compile(r"[a-z0-9][a-z0-9._-]*")
PROFILE_ENTRY_FIELDS = frozenset({"id", "profile", "name", "enabled"})
PROFILE_CATEGORIES = frozenset({"firmware", "software", "operating_system"})
CATEGORY_DISPLAY_NAMES = {
    "firmware": "Firmware",
    "software": "Software",
    "operating_system": "Operating System",
}
CATEGORY_ORDER = ("firmware", "software", "operating_system")
BINARY_MEDIA_TYPES = frozenset({"image", "audio", "video", "font"})
BINARY_SUBTYPES = frozenset({"octet-stream", "pdf", "zip", "gzip"})
