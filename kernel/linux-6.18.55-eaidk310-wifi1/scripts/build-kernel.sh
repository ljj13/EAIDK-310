#!/usr/bin/env bash
set -euo pipefail

EXPECTED_VERSION="6.18.55"
EXPECTED_RELEASE="6.18.55-eaidk310-wifi1"
BOARD_DTB="rk3328-eaidk-310.dtb"
DEFAULT_WSL_ROOT="/home/Fog/eaidk310-kernel"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
WSL_ROOT="$DEFAULT_WSL_ROOT"
SOURCE_DIR="$WSL_ROOT/src/linux-$EXPECTED_VERSION"
BUILD_DIR="$WSL_ROOT/build-zramfix1-6.18.55"
STAGE_ROOT="$WSL_ROOT/stage-zramfix1-6.18.55"
LOG_PATH="$PROJECT_DIR/analysis/build-kernel.log"
METADATA_PATH="$PROJECT_DIR/analysis/build-metadata.json"
CLEAN=0
CONFIG_ONLY=0
SEED_CONFIG="$PROJECT_DIR/config/eaidk310-6.18.55-wifi1.config"

fail() {
	printf 'BUILD_GATE=FAIL: %s\n' "$*" >&2
	exit 1
}

while (($#)); do
	case "$1" in
		--clean) CLEAN=1; shift ;;
		--config-only) CONFIG_ONLY=1; shift ;;
		--source-dir) SOURCE_DIR="${2:?missing value for --source-dir}"; shift 2 ;;
		--build-dir) BUILD_DIR="${2:?missing value for --build-dir}"; shift 2 ;;
		--stage-root) STAGE_ROOT="${2:?missing value for --stage-root}"; shift 2 ;;
		--log) LOG_PATH="${2:?missing value for --log}"; shift 2 ;;
		--metadata) METADATA_PATH="${2:?missing value for --metadata}"; shift 2 ;;
		-h|--help)
			printf 'usage: %s [--clean] [--config-only] [--source-dir PATH] [--build-dir PATH] [--stage-root PATH] [--log PATH] [--metadata PATH]\n' "$0"
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

# Seed-config self-healing: the build directory is DERIVED state.  Whatever
# the caller's clean/install order was, olddefconfig must never consume a
# missing or non-EAIDK config.  Reinstall the reviewed seed and prove its
# identity before anything else touches it.
ensure_seed_config() {
	[[ -f "$SEED_CONFIG" ]] || fail "reviewed seed config is missing: $SEED_CONFIG"
	if [[ ! -s "$BUILD_DIR/.config" ]]; then
		install -m 0644 "$SEED_CONFIG" "$BUILD_DIR/.config"
		printf 'SEED_CONFIG=reinstalled into %s (build directory had no config)\n' "$BUILD_DIR"
	fi
	python3 "$PROJECT_DIR/tools/kernel_artifacts.py" check-config \
		--config "$BUILD_DIR/.config" --mode seed || \
		fail "seed config gate failed: build directory does not hold the reviewed EAIDK310 config"
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

	# Order-independent by design: seed config is (re)installed and verified
	# no matter what ran before this script, so --clean can never leave
	# olddefconfig without the reviewed EAIDK310 configuration.
	[[ -f "$SOURCE_DIR/arch/arm64/boot/dts/rockchip/rk3328-eaidk-310.dts" ]] || \
		fail "board DTS missing from source tree; run scripts/install-board-inputs.sh first"
	ensure_seed_config

	make -C "$SOURCE_DIR" O="$BUILD_DIR" olddefconfig || fail "olddefconfig failed"
	make -C "$SOURCE_DIR" O="$BUILD_DIR" syncconfig || fail "syncconfig failed"
	python3 "$PROJECT_DIR/tools/kernel_artifacts.py" check-config \
		--config "$BUILD_DIR/.config" || fail "final config gate failed"
	python3 -m unittest discover -s "$PROJECT_DIR/tests" \
		-p 'test_config_invariants.py' -v || fail "config invariant tests failed"
	python3 -m unittest discover -s "$PROJECT_DIR/tests" \
		-p 'test_dts_invariants.py' -v || fail "dts invariant tests failed"

	local kernel_release
	kernel_release="$(make -s -C "$SOURCE_DIR" O="$BUILD_DIR" kernelrelease)" || \
		fail "kernelrelease query failed"
	[[ "$kernel_release" == "$EXPECTED_RELEASE" ]] || \
		fail "kernel release mismatch: expected $EXPECTED_RELEASE, got $kernel_release"

	if ((CONFIG_ONLY)); then
		printf 'CONFIG_ONLY_GATE=PASS\n'
		printf 'kernel_release=%s\n' "$kernel_release"
		return 0
	fi

	make -C "$SOURCE_DIR" O="$BUILD_DIR" -j"$JOBS" \
		Image modules "rockchip/$BOARD_DTB" || fail "kernel compile failed"
	make -C "$SOURCE_DIR" O="$BUILD_DIR" CHECK_DTBS=y \
		"rockchip/$BOARD_DTB" || fail "dtbs validation failed"
	make -C "$SOURCE_DIR" O="$BUILD_DIR" \
		INSTALL_MOD_PATH="$STAGE_ROOT" modules_install || fail "modules_install failed"
	depmod -b "$STAGE_ROOT" "$EXPECTED_RELEASE" || fail "depmod failed"

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

# Top-level pipeline (NOT a conditional): every critical step inside run_build
# carries an explicit `|| fail`, and PIPESTATUS is checked explicitly, so the
# gate chain can never be silently skipped the way a conditional-wrapped
# function would suppress `set -e`.
run_build 2>&1 | tee "$LOG_PATH"
build_status=${PIPESTATUS[0]}
((build_status == 0)) || fail "kernel build pipeline failed (exit $build_status); see $LOG_PATH"
