"""Shared test setup: puts the repo root on sys.path so agy_subagent_py imports from any cwd."""

import os
import sys

TESTS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(TESTS))
FIXTURES = os.path.join(TESTS, "fixtures")
for p in (REPO, FIXTURES):
    if p not in sys.path:
        sys.path.insert(0, p)
