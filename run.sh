#!/usr/bin/env bash
#
# Run Photo Dupe straight from this folder, without installing it.
# Creates a local .venv on first use.
#
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

if [[ ! -x .venv/bin/python ]]; then
    echo "==> First run: creating .venv and installing dependencies"
    python3 -m venv .venv
    .venv/bin/python -m pip install --quiet --upgrade pip
    .venv/bin/python -m pip install --quiet -r requirements.txt
fi

exec .venv/bin/python -m photodupe "$@"
