#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["nltk"]
# ///

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from commit_guard import commit_guard_main

sys.exit(commit_guard_main())
