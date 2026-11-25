#!/usr/bin/env bash
# POSIX wrapper for the CL4R1T4S drift watch. Kept as a .sh because the
# dashboard job handler on macOS/Linux executes the file directly.
#
# The real work lives in cl4r1t4s_probe_regression_cron.py so Windows can run
# it with sys.executable.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"

exec "$PY" "$DIR/cl4r1t4s_probe_regression_cron.py" "$@"
