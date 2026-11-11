#!/usr/bin/env bash
# Regenerate the hermetic test deps (no system pytest install needed).
set -euo pipefail
cd "$(dirname "$0")"
rm -rf vendor vendor_pkgs
python3 -m pip download -q -d vendor --only-binary=:all: pytest
python3 - <<'PY'
import zipfile, glob
for w in glob.glob("vendor/*.whl"):
    with zipfile.ZipFile(w) as z: z.extractall("vendor_pkgs")
PY
echo "vendor ready: $(ls vendor | wc -l) wheels"
