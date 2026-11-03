#!/usr/bin/env python3
"""firmAware – Check firmware sources for new version labels.

Checks the sources configured in ``devices.json`` and reports previously
unknown version labels. Known versions are stored in ``state.json``.
Entries can be configured directly or via reusable profiles in
``profiles/*.json``. Only the Python standard library is required; see
``requirements/specification.md`` for details.

This file is the command-line entry point. The implementation lives in the
``firmaware_core`` package and is re-exported here.
"""
from __future__ import annotations

import sys

from firmaware_core.constants import *  # noqa: F401,F403
from firmaware_core.errors import *  # noqa: F401,F403
from firmaware_core.state import *  # noqa: F401,F403
from firmaware_core.fetch import *  # noqa: F401,F403
from firmaware_core.sources import *  # noqa: F401,F403
from firmaware_core.extract import *  # noqa: F401,F403
from firmaware_core.notify import *  # noqa: F401,F403
from firmaware_core.profiles import *  # noqa: F401,F403
from firmaware_core.checking import *  # noqa: F401,F403
from firmaware_core.report import *  # noqa: F401,F403
from firmaware_core.interactive import *  # noqa: F401,F403
from firmaware_core.cli import *  # noqa: F401,F403


if __name__ == "__main__":
    sys.exit(main())  # noqa: F405
