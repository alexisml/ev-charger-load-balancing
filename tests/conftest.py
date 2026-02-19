"""pytest configuration — add the tests/ directory to sys.path so that
tests/load_balancer_core.py is importable as ``load_balancer_core``."""

import sys
import os

sys.path.insert(0, os.path.dirname(__file__))
