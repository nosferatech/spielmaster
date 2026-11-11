#!/usr/bin/env bash
# Run the test suite hermetically (no system pytest needed).
cd "$(dirname "$0")"
PYTHONPATH="$PWD/vendor_pkgs" python3 -m pytest "$@"
