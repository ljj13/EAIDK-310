#!/usr/bin/env bash
set -euo pipefail

EXPECTED_VERSION="6.12.108"
EXPECTED_GCC_TARGET="aarch64-linux-gnu"
DEFAULT_WSL_ROOT="/home/Fog/eaidk310-kernel"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
LOCK_PATH="$PROJECT_DIR/source-lock.json"
WSL_ROOT="$DEFAULT_WSL_ROOT"
SOURCE_DIR="$WSL_ROOT/src/linux-$EXPECTED_VERSION"
BUILD_DIR="$WSL_ROOT/build-zramfix1"
ARCHIVE="$WSL_ROOT/downloads/linux-$EXPECTED_VERSION.tar.xz"
SIGNATURE="$WSL_ROOT/downloads/linux-$EXPECTED_VERSION.tar.sign"
REPORT_PATH="$PROJECT_DIR/analysis/verify-inputs.json"

fail() {
	printf 'VERIFY_INPUTS_GATE=FAIL: %s\n' "$*" >&2
	exit 1
}

while (($#)); do
	case "$1" in
		--source-dir) SOURCE_DIR="${2:?missing value for --source-dir}"; shift 2 ;;
		--build-dir) BUILD_DIR="${2:?missing value for --build-dir}"; shift 2 ;;
		--archive) ARCHIVE="${2:?missing value for --archive}"; shift 2 ;;
		--signature) SIGNATURE="${2:?missing value for --signature}"; shift 2 ;;
		--report) REPORT_PATH="${2:?missing value for --report}"; shift 2 ;;
		-h|--help)
			printf 'usage: %s [--source-dir PATH] [--build-dir PATH] [--archive PATH] [--signature PATH] [--report PATH]\n' "$0"
			exit 0
			;;
		*) fail "unknown argument: $1" ;;
	esac
done

for tool in python3 sha256sum xz gpg make aarch64-linux-gnu-gcc \
	aarch64-linux-gnu-ld dtc pahole depmod file realpath; do
	command -v "$tool" >/dev/null || fail "required tool is missing: $tool"
done

[[ -f "$LOCK_PATH" ]] || fail "source lock is missing: $LOCK_PATH"
[[ -f "$ARCHIVE" ]] || fail "source archive is missing: $ARCHIVE"
[[ -f "$SIGNATURE" ]] || fail "detached signature is missing: $SIGNATURE"
[[ -f "$SOURCE_DIR/Makefile" ]] || fail "not a Linux source tree: $SOURCE_DIR"

SOURCE_DIR="$(realpath "$SOURCE_DIR")"
BUILD_DIR="$(realpath -m "$BUILD_DIR")"
ARCHIVE="$(realpath "$ARCHIVE")"
SIGNATURE="$(realpath "$SIGNATURE")"

case "$SOURCE_DIR" in
	"$WSL_ROOT"/src/*) ;;
	*) fail "source directory must remain below $WSL_ROOT/src" ;;
esac
case "$BUILD_DIR" in
	"$WSL_ROOT"/build|"$WSL_ROOT"/build-*) ;;
	*) fail "build directory must remain below $WSL_ROOT" ;;
esac

python3 "$PROJECT_DIR/tools/kernel_artifacts.py" verify-lock "$LOCK_PATH" >/dev/null

expected_archive_sha256="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["archive_sha256"])' "$LOCK_PATH")"
actual_archive_sha256="$(sha256sum "$ARCHIVE" | awk '{print $1}')"
[[ "$actual_archive_sha256" == "$expected_archive_sha256" ]] || \
	fail "archive SHA-256 mismatch"

if ! signature_status="$(xz -cd "$ARCHIVE" | \
	gpg --batch --status-fd=1 --verify "$SIGNATURE" - 2>&1)"; then
	printf '%s\n' "$signature_status" >&2
	fail "detached signature verification failed"
fi
validsig_fingerprint="$(printf '%s\n' "$signature_status" | \
	awk '$1 == "[GNUPG:]" && $2 == "VALIDSIG" {print $3; exit}')"
[[ -n "$validsig_fingerprint" ]] || fail "GPG did not emit a VALIDSIG record"

kernel_version="$(make -s -C "$SOURCE_DIR" kernelversion)"
[[ "$kernel_version" == "$EXPECTED_VERSION" ]] || \
	fail "kernel version mismatch: expected $EXPECTED_VERSION, got $kernel_version"
gcc_target="$(aarch64-linux-gnu-gcc -dumpmachine)"
[[ "$gcc_target" == "$EXPECTED_GCC_TARGET" ]] || \
	fail "GCC target mismatch: expected $EXPECTED_GCC_TARGET, got $gcc_target"

mkdir -p -- "$WSL_ROOT/tmp" "$(dirname -- "$REPORT_PATH")"
TEMP_DIR="$(mktemp -d "$WSL_ROOT/tmp/verify-inputs.XXXXXX")"
trap 'rm -rf -- "$TEMP_DIR"' EXIT
OBSERVATIONS="$TEMP_DIR/observations.json"

export OBS_SOURCE_DIR="$SOURCE_DIR"
export OBS_BUILD_DIR="$BUILD_DIR"
export OBS_KERNEL_VERSION="$kernel_version"
export OBS_GCC_TARGET="$gcc_target"
export OBS_VALIDSIG="$validsig_fingerprint"
export OBS_GCC_VERSION="$(aarch64-linux-gnu-gcc -dumpfullversion -dumpversion)"
export OBS_BINUTILS_VERSION="$(aarch64-linux-gnu-ld --version | sed -n '1p')"
export OBS_MAKE_VERSION="$(make --version | sed -n '1p')"
export OBS_DTC_VERSION="$(dtc --version)"
export OBS_PAHOLE_VERSION="$(pahole --version)"
export OBS_DEPMOD_VERSION="$(depmod --version | sed -n '1p')"
export OBS_FILE_VERSION="$(file --version | sed -n '1p')"
export OBS_PYTHON_VERSION="$(python3 --version)"
export OBS_GPG_VERSION="$(gpg --version | sed -n '1p')"
export OBS_XZ_VERSION="$(xz --version 2>&1 | sed -n '1p')"

python3 - "$OBSERVATIONS" <<'PY'
import json
import os
import pathlib
import sys

report = {
    "source_dir": os.environ["OBS_SOURCE_DIR"],
    "build_dir": os.environ["OBS_BUILD_DIR"],
    "kernel_version": os.environ["OBS_KERNEL_VERSION"],
    "gcc_target": os.environ["OBS_GCC_TARGET"],
    "validsig_fingerprint": os.environ["OBS_VALIDSIG"],
    "tool_versions": {
        "gcc": os.environ["OBS_GCC_VERSION"],
        "binutils": os.environ["OBS_BINUTILS_VERSION"],
        "make": os.environ["OBS_MAKE_VERSION"],
        "dtc": os.environ["OBS_DTC_VERSION"],
        "pahole": os.environ["OBS_PAHOLE_VERSION"],
        "depmod": os.environ["OBS_DEPMOD_VERSION"],
        "file": os.environ["OBS_FILE_VERSION"],
        "python": os.environ["OBS_PYTHON_VERSION"],
        "gpg": os.environ["OBS_GPG_VERSION"],
        "xz": os.environ["OBS_XZ_VERSION"],
    },
}
pathlib.Path(sys.argv[1]).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
PY

python3 "$PROJECT_DIR/tools/kernel_artifacts.py" input-report \
	--lock "$LOCK_PATH" \
	--project-root "$PROJECT_DIR" \
	--archive "$ARCHIVE" \
	--observations-json "$OBSERVATIONS" \
	--output "$REPORT_PATH" >/dev/null

printf 'VERIFY_INPUTS_GATE=PASS\n'
printf 'report=%s\n' "$REPORT_PATH"
printf 'archive_sha256=%s\n' "$actual_archive_sha256"
printf 'validsig_fingerprint=%s\n' "$validsig_fingerprint"
printf 'kernel_version=%s\n' "$kernel_version"
printf 'gcc_target=%s\n' "$gcc_target"
