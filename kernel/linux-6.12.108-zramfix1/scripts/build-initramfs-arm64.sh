#!/usr/bin/env bash
set -euo pipefail

EXPECTED_RELEASE="6.12.108-eaidk310-zramfix1"
UIMAGE_NAME="initramfs-6.12.108-zramfix1"
DEFAULT_WSL_ROOT="/home/Fog/eaidk310-kernel"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
WSL_ROOT="$DEFAULT_WSL_ROOT"
STAGE_ROOT="$WSL_ROOT/stage-zramfix1"
CHROOT_DIR="$WSL_ROOT/chroot-trixie-arm64-zramfix1"
OUTPUT_DIR="$WSL_ROOT/initramfs-zramfix1"
PREFLIGHT_ONLY=0
CLEAN_CHROOT=0

fail() {
	printf 'INITRAMFS_GATE=FAIL: %s\n' "$*" >&2
	exit 1
}

usage() {
	printf 'usage: %s [--preflight-only] [--clean-chroot] [--stage-root PATH] [--chroot-dir PATH] [--output-dir PATH]\n' "$0"
}

while (($#)); do
	case "$1" in
		--preflight-only) PREFLIGHT_ONLY=1; shift ;;
		--clean-chroot) CLEAN_CHROOT=1; shift ;;
		--stage-root) STAGE_ROOT="${2:?missing value for --stage-root}"; shift 2 ;;
		--chroot-dir) CHROOT_DIR="${2:?missing value for --chroot-dir}"; shift 2 ;;
		--output-dir) OUTPUT_DIR="${2:?missing value for --output-dir}"; shift 2 ;;
		-h|--help) usage; exit 0 ;;
		*) fail "unknown argument: $1" ;;
	esac
done

for tool in debootstrap chroot cp dpkg-query dumpimage file mkimage python3 \
	qemu-aarch64-static realpath rsync sha256sum; do
	command -v "$tool" >/dev/null || fail "required tool is missing: $tool"
done
for package in debootstrap qemu-user-static binfmt-support initramfs-tools-core busybox zstd; do
	dpkg-query -W -f='${db:Status-Status}\n' "$package" 2>/dev/null | \
		grep -Fxq 'installed' || fail "required host package is not installed: $package"
done

[[ -r /proc/sys/fs/binfmt_misc/qemu-aarch64 ]] || \
	fail "qemu-aarch64 binfmt registration is missing"
grep -Fxq 'enabled' /proc/sys/fs/binfmt_misc/qemu-aarch64 || \
	fail "qemu-aarch64 binfmt registration is disabled"

STAGE_ROOT="$(realpath "$STAGE_ROOT")"
CHROOT_DIR="$(realpath -m "$CHROOT_DIR")"
OUTPUT_DIR="$(realpath -m "$OUTPUT_DIR")"
MODULE_DIR="$STAGE_ROOT/lib/modules/$EXPECTED_RELEASE"
IMAGE="$WSL_ROOT/build-zramfix1/arch/arm64/boot/Image"
DTB="$WSL_ROOT/build-zramfix1/arch/arm64/boot/dts/rockchip/rk3328-eaidk-310.dtb"

case "$STAGE_ROOT" in "$WSL_ROOT"/stage|"$WSL_ROOT"/stage-*) ;; *) fail "stage root is outside $WSL_ROOT" ;; esac
case "$CHROOT_DIR" in "$WSL_ROOT"/chroot-*) ;; *) fail "chroot path is outside the allowed $WSL_ROOT/chroot-* range" ;; esac
case "$OUTPUT_DIR" in "$WSL_ROOT"/initramfs|"$WSL_ROOT"/initramfs-*) ;; *) fail "output path is outside $WSL_ROOT" ;; esac

[[ -s "$IMAGE" ]] || fail "Task 5 Image is missing: $IMAGE"
[[ -s "$DTB" ]] || fail "Task 5 DTB is missing: $DTB"
[[ -s "$WSL_ROOT/build-zramfix1/.config" ]] || fail "Task 5 kernel config is missing"
[[ -s "$PROJECT_DIR/initramfs/initramfs.conf" ]] || fail "initramfs.conf is missing"
[[ -f "$PROJECT_DIR/initramfs/modules" ]] || fail "initramfs modules policy is missing"
python3 "$PROJECT_DIR/tools/kernel_artifacts.py" verify-early-deps \
	--module-dir "$MODULE_DIR" >/dev/null

while IFS= read -r requested_module; do
	requested_module="${requested_module%%#*}"
	requested_module="${requested_module//[[:space:]]/}"
	[[ -z "$requested_module" ]] && continue
	modinfo -b "$STAGE_ROOT" -k "$EXPECTED_RELEASE" -n "$requested_module" >/dev/null || \
		fail "explicit initramfs module is unavailable: $requested_module"
done < "$PROJECT_DIR/initramfs/modules"

printf 'INITRAMFS_PREFLIGHT_GATE=PASS\n'
((PREFLIGHT_ONLY)) && exit 0
((EUID == 0)) || fail "full ARM64 initramfs build must run as root"

policy_hash="$({
	printf '%s\n' 'debian=trixie' 'arch=arm64' "release=$EXPECTED_RELEASE" \
		'packages=initramfs-tools-core,kmod,busybox,zstd'
	sha256sum "$PROJECT_DIR/initramfs/initramfs.conf" "$PROJECT_DIR/initramfs/modules"
} | sha256sum | cut -d' ' -f1)"
metadata_path="$CHROOT_DIR/etc/eaidk310-chroot.sha256"

chroot_is_reusable=0
if ((CLEAN_CHROOT == 0)) && [[ -d "$CHROOT_DIR" ]] && \
	[[ -f "$CHROOT_DIR/etc/debian_version" ]] && \
	grep -Eq '^13([.]|$)' "$CHROOT_DIR/etc/debian_version" && \
	[[ -f "$metadata_path" ]] && [[ "$(<"$metadata_path")" == "$policy_hash" ]] && \
	[[ "$(chroot "$CHROOT_DIR" dpkg --print-architecture 2>/dev/null)" == "arm64" ]]; then
	chroot_is_reusable=1
fi

if ((chroot_is_reusable == 0)); then
	if [[ -e "$CHROOT_DIR" ]]; then
		rm -rf -- "$CHROOT_DIR"
	fi
	debootstrap --arch=arm64 --foreign --variant=minbase trixie "$CHROOT_DIR" \
		https://deb.debian.org/debian
	install -m 0755 /usr/bin/qemu-aarch64-static "$CHROOT_DIR/usr/bin/qemu-aarch64-static"
	chroot "$CHROOT_DIR" /debootstrap/debootstrap --second-stage
	printf '%s\n' \
		'deb https://deb.debian.org/debian trixie main' \
		'deb https://deb.debian.org/debian trixie-updates main' \
		'deb https://security.debian.org/debian-security trixie-security main' \
		> "$CHROOT_DIR/etc/apt/sources.list"
	cp --dereference /etc/resolv.conf "$CHROOT_DIR/etc/resolv.conf"
	chroot "$CHROOT_DIR" env DEBIAN_FRONTEND=noninteractive apt-get update
	chroot "$CHROOT_DIR" env DEBIAN_FRONTEND=noninteractive apt-get install -y \
		--no-install-recommends initramfs-tools-core kmod busybox zstd
	printf '%s\n' "$policy_hash" > "$metadata_path"
fi

chroot_release_dir="$CHROOT_DIR/lib/modules/$EXPECTED_RELEASE"
if [[ -e "$chroot_release_dir" ]]; then
	rm -rf -- "$chroot_release_dir"
fi
policy_dir="$CHROOT_DIR/etc/eaidk310-initramfs"
mkdir -p -- "$chroot_release_dir" "$policy_dir/conf.d" "$policy_dir/hooks" \
	"$policy_dir/scripts/init-bottom" "$policy_dir/scripts/init-premount" \
	"$policy_dir/scripts/init-top" "$policy_dir/scripts/local-bottom" \
	"$policy_dir/scripts/local-premount" "$policy_dir/scripts/local-top" \
	"$policy_dir/scripts/nfs-bottom" "$policy_dir/scripts/nfs-premount" \
	"$policy_dir/scripts/nfs-top" "$policy_dir/scripts/panic" "$CHROOT_DIR/boot"
rsync -a --delete --exclude build --exclude source \
	"$MODULE_DIR/" "$chroot_release_dir/"
install -m 0644 "$PROJECT_DIR/initramfs/initramfs.conf" \
	"$policy_dir/initramfs.conf"
install -m 0644 "$PROJECT_DIR/initramfs/modules" \
	"$policy_dir/modules"
install -m 0644 "$WSL_ROOT/build-zramfix1/.config" \
	"$CHROOT_DIR/boot/config-$EXPECTED_RELEASE"

raw_name="initrd.img-$EXPECTED_RELEASE"
uboot_name="uInitrd-$EXPECTED_RELEASE"
rm -f -- "$CHROOT_DIR/tmp/$raw_name"
chroot "$CHROOT_DIR" depmod "$EXPECTED_RELEASE"
chroot "$CHROOT_DIR" env SOURCE_DATE_EPOCH=1788352262 \
	mkinitramfs -d /etc/eaidk310-initramfs -o "/tmp/$raw_name" "$EXPECTED_RELEASE"

mkdir -p -- "$OUTPUT_DIR"
install -m 0644 "$CHROOT_DIR/tmp/$raw_name" "$OUTPUT_DIR/$raw_name"
SOURCE_DATE_EPOCH=1788352262 mkimage -A arm64 -O linux -T ramdisk -C none \
	-n "$UIMAGE_NAME" -d "$OUTPUT_DIR/$raw_name" "$OUTPUT_DIR/$uboot_name"

[[ -s "$OUTPUT_DIR/$raw_name" ]] || fail "raw initramfs was not created"
[[ -s "$OUTPUT_DIR/$uboot_name" ]] || fail "uInitrd was not created"
file "$OUTPUT_DIR/$raw_name"
dumpimage -l "$OUTPUT_DIR/$uboot_name"
printf 'INITRAMFS_GATE=PASS\n'
printf 'raw_initrd=%s\n' "$OUTPUT_DIR/$raw_name"
printf 'uinitrd=%s\n' "$OUTPUT_DIR/$uboot_name"
printf 'chroot_reused=%s\n' "$chroot_is_reusable"
