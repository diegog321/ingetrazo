#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Day-to-day commands for the experimental fork, run from anywhere in the
# checkout. A thin wrapper over what CONTRIBUTING.md and ci.yml already do,
# so a fresh machine goes from clone to a running IngeTrazo in one step.
#
#   scripts/fork/dev.sh setup        # venv + requirements (once, or after they change)
#   scripts/fork/dev.sh run [args]   # start IngeTrazo from source
#   scripts/fork/dev.sh check        # main.py --check: report missing pieces
#   scripts/fork/dev.sh test [args]  # the fast suite, batched like CI
#   scripts/fork/dev.sh appimage     # build dist/IngeTrazo-*.AppImage (Linux)
#   scripts/fork/dev.sh sync         # merge upstream main into this branch
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
VENV="${INGETRAZO_VENV:-$ROOT/venv}"
PY="$VENV/bin/python"
UPSTREAM_URL="https://github.com/ingelibre/ingetrazo.git"

need_venv() {
    if [[ ! -x "$PY" ]]; then
        echo "No virtualenv at $VENV — run: scripts/fork/dev.sh setup" >&2
        exit 1
    fi
}

cmd="${1:-help}"
shift || true
cd "$ROOT"

case "$cmd" in
    setup)
        python3 -c 'import sys; sys.exit(sys.version_info < (3, 12))' || {
            echo "IngeTrazo needs Python 3.12+ (found $(python3 --version))" >&2
            exit 1
        }
        [[ -x "$PY" ]] || python3 -m venv "$VENV"
        "$PY" -m pip install --quiet --upgrade pip
        "$PY" -m pip install --quiet -r requirements.txt
        "$PY" -c "import PySide6, numpy, openskp; print('deps OK, PySide6', PySide6.__version__)"
        ;;
    run)
        need_venv
        exec "$PY" main.py "$@"
        ;;
    check)
        need_venv
        exec "$PY" main.py --check
        ;;
    test)
        need_venv
        export QT_QPA_PLATFORM="${QT_QPA_PLATFORM:-offscreen}"
        if [[ $# -eq 0 ]]; then
            set -- -m "not slow" tests
        fi
        exec "$PY" scripts/pytest_batches.py "$@"
        ;;
    appimage)
        need_venv
        "$PY" -m pip install --quiet pyinstaller
        PYTHON="$PY" exec packaging/build-appimage.sh dist
        ;;
    sync)
        git remote get-url upstream >/dev/null 2>&1 \
            || git remote add upstream "$UPSTREAM_URL"
        git fetch upstream main
        git merge --no-edit upstream/main
        ;;
    *)
        sed -n '7,12p' "$0" | sed 's/^# \{0,1\}//'
        ;;
esac
