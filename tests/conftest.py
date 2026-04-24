"""Shared fixtures. Keep Qt out of tests that don't need it — the Qt
import takes ~600 ms and most of our unit-testable logic is pure."""
import os
import sys
from pathlib import Path

# Make the package importable when running from repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Run headless for the rare Qt-requiring test.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
