#!/usr/bin/env bash
# POSIX wrapper for the weekly coverage probe.
#
# The real work lives in coverage_probe_cron.py so Windows can run it with
# sys.executable.
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="${PYTHON:-python3}"

exec "$PY" "$DIR/coverage_probe_cron.py" "$@"
