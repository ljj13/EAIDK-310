#!/usr/bin/env bash
set -euo pipefail

EXPECTED_VERSION="6.12.108"
EXPECTED_RELEASE="6.12.108-eaidk310-zramfix1"
BOARD_DTB="rk3328-eaidk-310.dtb"
DEFAULT_WSL_ROOT="/home/Fog/eaidk310-kernel"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
WSL_ROOT="$DEFAULT_WSL_ROOT"
SOURCE_DIR="$WSL_ROOT/src/linux-$EXPECTED_VERSION"
BUILD_DIR="$WSL_ROOT/build-zramfix1"
STAGE_ROOT="$WSL_ROOT/stage-zramfix1"
LOG_PATH="$PROJECT_DIR/analysis/build-kernel.log"
METADATA_PATH="$PROJECT_DIR/analysis/build-metadata.json"
CLEAN=0

fail() {
	printf 'BUILD_GATE=FAIL: %s\n' "$*" >&2
	exit 1
}

while (($#)); do
	case "$1" in
		--clean) CLEAN=1; shift ;;
		--source-dir) SOURCE_DIR="${2:?missing value for --source-dir}"; shift 2 ;;
		--build-dir) BUILD_DIR="${2:?missing value for --build-dir}"; shift 2 ;;
		--stage-root) STAGE_ROOT="${2:?missing value for --stage-root}"; shift 2 ;;
		--log) LOG_PATH="${2:?missing value for --log}"; shift 2 ;;
		--metadata) METADATA_PATH="${2:?missing value for --metadata}"; shift 2 ;;
		-h|--help)
			printf 'usage: %s [--clean] [--source-dir PATH] [--build-dir PATH] [--stage-root PATH] [--log PATH] [--metadata PATH]\n' "$0"
			exit 0
			;;
		*) fail "unknown argument: $1" ;;
	esac
done

for tool in python3 make nproc depmod modinfo file realpath tee find; do
	command -v "$tool" >/dev/null || fail "required tool is missing: $tool"
done
[[ -f "$SOURCE_DIR/Makefile" ]] || fail "not a Linux source tree: $SOURCE_DIR"

SOURCE_DIR="$(realpath "$SOURCE_DIR")"
BUILD_DIR="$(realpath -m "$BUILD_DIR")"
STAGE_ROOT="$(realpath -m "$STAGE_ROOT")"

case "$SOURCE_DIR" in
	"$WSL_ROOT"/src/*) ;;
	*) fail "source directory must remain below $WSL_ROOT/src" ;;
esac
case "$BUILD_DIR" in
	"$WSL_ROOT"/build|"$WSL_ROOT"/build-*) ;;
	*) fail "build directory must remain below $WSL_ROOT" ;;
esac
case "$STAGE_ROOT" in
	"$WSL_ROOT"/stage|"$WSL_ROOT"/stage-*) ;;
	*) fail "stage directory must remain below $WSL_ROOT" ;;
esac
[[ "$BUILD_DIR" != "$SOURCE_DIR" ]] || fail "build directory must differ from source"
[[ "$STAGE_ROOT" != "$SOURCE_DIR" ]] || fail "stage directory must differ from source"

if ((CLEAN)); then
	rm -rf -- "$BUILD_DIR" "$STAGE_ROOT"
fi
mkdir -p -- "$BUILD_DIR" "$STAGE_ROOT" "$(dirname -- "$LOG_PATH")" \
	"$(dirname -- "$METADATA_PATH")"

export ARCH=arm64
export CROSS_COMPILE=aarch64-linux-gnu-
export KBUILD_BUILD_VERSION=1
export KBUILD_BUILD_USER=Fog
export KBUILD_BUILD_HOST=eaidk310-wsl
SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-$(stat -c %Y "$SOURCE_DIR/Makefile")}"
export SOURCE_DATE_EPOCH
export KBUILD_BUILD_TIMESTAMP="@${SOURCE_DATE_EPOCH}"
JOBS="$(nproc)"

if [[ -d "$WSL_ROOT/tools/dtschema-venv/bin" ]]; then
	export PATH="$WSL_ROOT/tools/dtschema-venv/bin:$PATH"
fi
command -v dt-validate >/dev/null || fail "dtschema tools are missing"

require_nonempty() {
	[[ -s "$1" ]] || fail "required build output is missing or empty: $1"
}

verify_module_if_modular() {
	local symbol="$1"
	local module="$2"
	local value
	value="$(sed -n "s/^${symbol}=//p" "$BUILD_DIR/.config")"
	if [[ "$value" == "m" ]]; then
		local module_path
		module_path="$(modinfo -b "$STAGE_ROOT" -k "$EXPECTED_RELEASE" -n "$module")"
		require_nonempty "$module_path"
		file "$module_path" | grep -Fq 'ARM aarch64' || \
			fail "module is not ARM aarch64: $module_path"
	fi
}

run_build() {
	local started_at elapsed_seconds module_count
	started_at="$(date +%s)"

	make -C "$SOURCE_DIR" O="$BUILD_DIR" olddefconfig
	make -C "$SOURCE_DIR" O="$BUILD_DIR" syncconfig
	python3 "$PROJECT_DIR/tools/kernel_artifacts.py" check-config \
		--config "$BUILD_DIR/.config"
	python3 -m unittest discover -s "$PROJECT_DIR/tests" \
		-p 'test_config_invariants.py' -v
	python3 -m unittest discover -s "$PROJECT_DIR/tests" \
		-p 'test_dts_invariants.py' -v

	local kernel_release
	kernel_release="$(make -s -C "$SOURCE_DIR" O="$BUILD_DIR" kernelrelease)"
	[[ "$kernel_release" == "$EXPECTED_RELEASE" ]] || \
		fail "kernel release mismatch: expected $EXPECTED_RELEASE, got $kernel_release"

	make -C "$SOURCE_DIR" O="$BUILD_DIR" -j"$JOBS" \
		Image modules "rockchip/$BOARD_DTB"
	make -C "$SOURCE_DIR" O="$BUILD_DIR" CHECK_DTBS=y \
		"rockchip/$BOARD_DTB"
	make -C "$SOURCE_DIR" O="$BUILD_DIR" \
		INSTALL_MOD_PATH="$STAGE_ROOT" modules_install
	depmod -b "$STAGE_ROOT" "$EXPECTED_RELEASE"

	local image="$BUILD_DIR/arch/arm64/boot/Image"
	local dtb="$BUILD_DIR/arch/arm64/boot/dts/rockchip/$BOARD_DTB"
	local module_dir="$STAGE_ROOT/lib/modules/$EXPECTED_RELEASE"
	require_nonempty "$image"
	require_nonempty "$dtb"
	require_nonempty "$BUILD_DIR/System.map"
	require_nonempty "$BUILD_DIR/Module.symvers"
	require_nonempty "$BUILD_DIR/.config"
	require_nonempty "$module_dir/modules.dep"
	require_nonempty "$module_dir/modules.builtin"
	require_nonempty "$module_dir/modules.order"
	file "$image" | grep -Eq 'ARM64|ARM aarch64' || fail "Image is not ARM64"

	verify_module_if_modular CONFIG_DWMAC_ROCKCHIP dwmac-rk
	verify_module_if_modular CONFIG_DRM_LIMA lima
	verify_module_if_modular CONFIG_BRCMFMAC brcmfmac
	verify_module_if_modular CONFIG_BT_HCIUART hci_uart
	verify_module_if_modular CONFIG_FB_TFT_ST7789V fb_st7789v

	elapsed_seconds="$(( $(date +%s) - started_at ))"
	module_count="$(find "$module_dir" -type f -name '*.ko*' -print | wc -l)"
	export META_ELAPSED="$elapsed_seconds" META_JOBS="$JOBS" META_MODULES="$module_count"
	export META_SOURCE="$SOURCE_DIR" META_BUILD="$BUILD_DIR" META_STAGE="$STAGE_ROOT"
	export META_EPOCH="$SOURCE_DATE_EPOCH" META_RELEASE="$kernel_release"
	python3 - "$METADATA_PATH" <<'PY'
import json
import os
import pathlib
import sys

metadata = {
    "gate": "PASS",
    "kernel_release": os.environ["META_RELEASE"],
    "source_dir": os.environ["META_SOURCE"],
    "build_dir": os.environ["META_BUILD"],
    "stage_root": os.environ["META_STAGE"],
    "source_date_epoch": int(os.environ["META_EPOCH"]),
    "maximum_parallel_jobs": int(os.environ["META_JOBS"]),
    "elapsed_seconds": int(os.environ["META_ELAPSED"]),
    "installed_module_files": int(os.environ["META_MODULES"]),
}
pathlib.Path(sys.argv[1]).write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
PY

	printf 'BUILD_GATE=PASS\n'
	printf 'kernel_release=%s\n' "$kernel_release"
	printf 'image=%s\n' "$image"
	printf 'dtb=%s\n' "$dtb"
	printf 'module_dir=%s\n' "$module_dir"
	printf 'jobs=%s\n' "$JOBS"
	printf 'elapsed_seconds=%s\n' "$elapsed_seconds"
}

if ! run_build 2>&1 | tee "$LOG_PATH"; then
	fail "kernel build pipeline failed; see $LOG_PATH"
fi
