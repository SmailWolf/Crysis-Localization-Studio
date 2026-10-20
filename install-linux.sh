#!/bin/sh
set -eu
cd -- "$(dirname -- "$0")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3.10 or newer is required. Install Python 3 and its venv/pip support, then retry."
    exit 1
fi
python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10 or newer is required.")
PY

python3 -m venv .venv || {
    echo "Could not create a virtual environment. Install your system's Python venv package and retry."
    exit 1
}
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
echo "CryLocal is ready. Start it with ./launch.sh"
