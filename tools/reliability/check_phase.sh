#!/usr/bin/env bash
# Phase check entry point: see tools/reliability/check_phase.py for what it runs.
#
#   bash tools/reliability/check_phase.sh
#
# Unit coverage always runs; native coverage runs when a kicad_rl_router build is
# present and is skipped (without an acceptance claim) when it is not.
#
#   bash tools/reliability/check_phase.sh --strict
#
# --strict is the acceptance mode: it requires the native group to execute with no
# skips and treats a load/provenance failure as an error.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

if [ -x "${REPO_ROOT}/.venv/bin/python" ]; then
    PYTHON="${REPO_ROOT}/.venv/bin/python"
else
    PYTHON="$(command -v python3)"
fi

exec "${PYTHON}" "${REPO_ROOT}/tools/reliability/check_phase.py" "$@"
