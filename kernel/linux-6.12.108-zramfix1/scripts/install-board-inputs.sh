#!/usr/bin/env bash
set -euo pipefail

EXPECTED_VERSION="6.12.108"
DEFAULT_SOURCE_DIR="/home/Fog/eaidk310-kernel/src/linux-6.12.108"
DEFAULT_BUILD_DIR="/home/Fog/eaidk310-kernel/build-zramfix1"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
SOURCE_DIR="${1:-$DEFAULT_SOURCE_DIR}"
BUILD_DIR="${2:-$DEFAULT_BUILD_DIR}"
BOARD_DTS="$PROJECT_DIR/dts/rk3328-eaidk-310.dts"
BOARD_CONFIG="$PROJECT_DIR/config/eaidk310-6.12.108-zramfix1.config"
REGISTRATION_PATCH="$PROJECT_DIR/patches/0001-arm64-dts-rockchip-register-eaidk310.patch"
DTS_DESTINATION="$SOURCE_DIR/arch/arm64/boot/dts/rockchip/rk3328-eaidk-310.dts"

fail() {
	printf 'INSTALL_BOARD_INPUTS_GATE=FAIL: %s\n' "$*" >&2
	exit 1
}

[[ -f "$BOARD_DTS" ]] || fail "missing maintained board DTS: $BOARD_DTS"
[[ -f "$BOARD_CONFIG" ]] || fail "missing reviewed kernel config: $BOARD_CONFIG"
[[ -f "$REGISTRATION_PATCH" ]] || fail "missing registration patch: $REGISTRATION_PATCH"
[[ -f "$SOURCE_DIR/Makefile" ]] || fail "not a Linux source tree: $SOURCE_DIR"

SOURCE_DIR="$(realpath "$SOURCE_DIR")"
DTS_DESTINATION="$SOURCE_DIR/arch/arm64/boot/dts/rockchip/rk3328-eaidk-310.dts"
case "$SOURCE_DIR" in
	/home/Fog/eaidk310-kernel/src/*) ;;
	*) fail "source tree must remain below /home/Fog/eaidk310-kernel/src" ;;
esac

BUILD_DIR="$(realpath -m "$BUILD_DIR")"
case "$BUILD_DIR" in
	/home/Fog/eaidk310-kernel/build-zramfix1|/home/Fog/eaidk310-kernel/build-zramfix1-*) ;;
	*) fail "build directory must remain below /home/Fog/eaidk310-kernel" ;;
esac
install -d -m 0755 "$BUILD_DIR"
CONFIG_DESTINATION="$BUILD_DIR/.config"

actual_version="$(make -s -C "$SOURCE_DIR" kernelversion)"
[[ "$actual_version" == "$EXPECTED_VERSION" ]] || \
	fail "kernel version mismatch: expected $EXPECTED_VERSION, got $actual_version"

if [[ -e "$CONFIG_DESTINATION" ]]; then
	cmp -s "$BOARD_CONFIG" "$CONFIG_DESTINATION" || \
		fail "existing build config differs from the reviewed config"
else
	install -m 0644 "$BOARD_CONFIG" "$CONFIG_DESTINATION"
fi

if [[ -e "$DTS_DESTINATION" ]]; then
	cmp -s "$BOARD_DTS" "$DTS_DESTINATION" || \
		fail "existing DTS destination differs from the maintained source"
else
	install -m 0644 "$BOARD_DTS" "$DTS_DESTINATION"
fi

if patch --batch --forward --dry-run -p1 -d "$SOURCE_DIR" < "$REGISTRATION_PATCH" >/dev/null; then
	(
		cd "$SOURCE_DIR"
		patch --batch --forward -p1 < "$REGISTRATION_PATCH"
	)
elif patch --batch --reverse --dry-run -p1 -d "$SOURCE_DIR" < "$REGISTRATION_PATCH" >/dev/null; then
	:
else
	fail "registration patch is neither cleanly applicable nor already applied"
fi

cmp -s "$BOARD_DTS" "$DTS_DESTINATION" || fail "installed DTS verification failed"
cmp -s "$BOARD_CONFIG" "$CONFIG_DESTINATION" || fail "installed config verification failed"
grep -Fqx 'dtb-$(CONFIG_ARCH_ROCKCHIP) += rk3328-eaidk-310.dtb' \
	"$SOURCE_DIR/arch/arm64/boot/dts/rockchip/Makefile" || \
	fail "DTB Makefile registration is missing"
grep -Fq 'const: openailab,eaidk-310' \
	"$SOURCE_DIR/Documentation/devicetree/bindings/arm/rockchip.yaml" || \
	fail "root compatible registration is missing"
grep -Fq '"^openailab,.*":' \
	"$SOURCE_DIR/Documentation/devicetree/bindings/vendor-prefixes.yaml" || \
	fail "upstream Open AI Lab vendor prefix is missing"

printf 'INSTALL_BOARD_INPUTS_GATE=PASS\n'
printf 'source_dir=%s\n' "$SOURCE_DIR"
printf 'build_dir=%s\n' "$BUILD_DIR"
printf 'board_dts=%s\n' "$DTS_DESTINATION"
printf 'build_config=%s\n' "$CONFIG_DESTINATION"
