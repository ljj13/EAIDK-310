#!/usr/bin/env bash
# Thin wrapper: tools/release-kernel.py is the pipeline engine.
# From Windows: wsl.exe -u root -e bash -c "cd <repo> && ./tools/release-kernel.sh <version>"
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec python3 "$SCRIPT_DIR/release-kernel.py" "$@"
