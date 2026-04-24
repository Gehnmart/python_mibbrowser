#!/usr/bin/env bash
# Run every analyser in one go. Exit non-zero if any of them fail.
set -e
cd "$(dirname "$0")"
PY="${PYTHON:-.venv/bin/python}"

echo "=== ruff"
"$PY" -m ruff check pymibbrowser/ tests/

echo "=== mypy"
"$PY" -m mypy pymibbrowser/

echo "=== bandit"
"$PY" -m bandit -q -c pyproject.toml -r pymibbrowser/

echo "=== pytest"
QT_QPA_PLATFORM=offscreen "$PY" -m pytest

echo "All checks passed."
