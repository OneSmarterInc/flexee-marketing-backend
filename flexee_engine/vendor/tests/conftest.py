"""Bind the golden suite to the VENDORED engine.

The step goldens use bare top-level imports (`from engine import ...`, `from
faculty import ...`). Prepend the vendored engine dir (this file's parent's
parent = flexee_engine/vendor/) to sys.path so those imports resolve to the
single verified engine under vendor/ — the same modules the running app uses —
rather than any stray copy. This is what makes the goldens a real guard: a bad
edit to vendor/*.py breaks these tests.
"""
import os
import sys

_VENDOR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _VENDOR not in sys.path:
    sys.path.insert(0, _VENDOR)
