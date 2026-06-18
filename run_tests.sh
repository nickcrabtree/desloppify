#!/usr/bin/env bash
# Canonical test runner for the desloppify fork. Mirrors the pattern used by
# sibling repos: activate the project's conda env, then run pytest.
#
# This repo is editable-installed (`pip install -e .`) into the conda env below,
# so `desloppify` and its deps import directly from this working tree — no
# PYTHONPATH juggling needed. Test paths are configured in pyproject.toml
# ([tool.pytest.ini_options].testpaths), so a bare invocation runs the full
# suite; pass args/paths to narrow it, e.g.:
#
#   ./run_tests.sh                                   # full suite
#   ./run_tests.sh -q desloppify/tests/detectors     # one directory
#   ./run_tests.sh -k orphaned                        # by keyword
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONDA_ENV="aro_desloppify_20260228T061951Z"

if ! command -v conda >/dev/null 2>&1; then
  echo "ERROR: conda not found on PATH" >&2
  exit 1
fi

CONDA_BASE="$(conda info --base)"
# shellcheck disable=SC1090
. "${CONDA_BASE}/etc/profile.d/conda.sh"

conda activate "${CONDA_ENV}"

if ! python -c "import pytest" >/dev/null 2>&1; then
  echo "ERROR: pytest not installed in '${CONDA_ENV}'." >&2
  echo "       Install the dev tools:  pip install pytest mypy ruff import-linter" >&2
  exit 1
fi

cd "${REPO_DIR}"
exec python -m pytest "$@"
