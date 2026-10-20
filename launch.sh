#!/bin/sh
cd -- "$(dirname -- "$0")" || exit 1
if [ -x .venv/bin/python ]; then
    exec .venv/bin/python crylocal.py gui "$@"
fi
exec python3 crylocal.py gui "$@"
