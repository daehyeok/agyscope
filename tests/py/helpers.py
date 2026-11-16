"""Shared test paths. pytest (pyproject.toml) puts tests/py and the fixtures on sys.path."""

import os

TESTS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(TESTS))
FIXTURES = os.path.join(TESTS, "fixtures")
