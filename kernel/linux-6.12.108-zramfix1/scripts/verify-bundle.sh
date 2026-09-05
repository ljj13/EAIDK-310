#!/usr/bin/env bash
set -euo pipefail

EXPECTED_RELEASE="6.12.108-eaidk310-zramfix1"
BUNDLE_NAME="eaidk310-linux-$EXPECTED_RELEASE"
DEFAULT_WSL_ROOT="/home/Fog/eaidk310-kernel"
APPEND_LINE="root=UUID=bd2a6dbf-c55f-4d5e-8738-797e581ee7c9 rootwait rootfstype=ext4 net.ifnames=0 earlycon console=ttyS2,1500000n8 console=tty1 consoleblank=0 loglevel=4"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
WSL_ROOT="$DEFAULT_WSL_ROOT"
BUNDLE_ROOT="$WSL_ROOT/artifacts/$BUNDLE_NAME"
ARCHIVE="$PROJECT_DIR/artifacts/$BUNDLE_NAME.tar.zst"

fail() {
	printf 'BUNDLE_GATE=FAIL: %s\n' "$*" >&2
	exit 1
}

usage() {
	printf 'usage: %s [--bundle-root PATH] [--archive PATH]\n' "$0"
}

while (($#)); do
	case "$1" in
		--bundle-root) BUNDLE_ROOT="${2:?missing value for --bundle-root}"; shift 2 ;;
		--archive) ARCHIVE="${2:?missing value for --archive}"; shift 2 ;;
		-h|--help) usage; exit 0 ;;
		*) fail "unknown argument: $1" ;;
	esac
done

for tool in cmp dumpimage fdtget file find mktemp python3 readlink \
	realpath sha256sum tar unmkinitramfs zstd; do
	command -v "$tool" >/dev/null || fail "required tool is missing: $tool"
done

BUNDLE_ROOT="$(realpath "$BUNDLE_ROOT")"
ARCHIVE="$(realpath "$ARCHIVE")"
case "$BUNDLE_ROOT" in "$WSL_ROOT"/artifacts/*) ;; *) fail "bundle root must remain below $WSL_ROOT/artifacts" ;; esac
[[ "$(basename -- "$BUNDLE_ROOT")" == "$BUNDLE_NAME" ]] || \
	fail "unexpected bundle directory name"
[[ -s "$ARCHIVE" ]] || fail "bundle archive is missing or empty"
[[ -s "$ARCHIVE.sha256" ]] || fail "bundle archive SHA-256 file is missing"

python3 "$PROJECT_DIR/tools/kernel_artifacts.py" verify-bundle-layout \
	--root "$BUNDLE_ROOT" --manifest "$BUNDLE_ROOT/manifest.json" >/dev/null

special_paths="$(find "$BUNDLE_ROOT" \( -type b -o -type c -o -type p -o -type s -o -type l \) -print)"
[[ -z "$special_paths" ]] || fail "bundle contains special files or symlinks: $special_paths"

image="$BUNDLE_ROOT/boot/Image-$EXPECTED_RELEASE"
dtb="$BUNDLE_ROOT/boot/dtb/rockchip/rk3328-eaidk-310-6.12.108.dtb"
uinitrd="$BUNDLE_ROOT/boot/uInitrd-$EXPECTED_RELEASE"
file "$image" | grep -Eq 'ARM64|ARM aarch64' || fail "bundle Image is not ARM64"
[[ "$(fdtget -t s "$dtb" / model)" == "EAIDK-310 build by lcy v2" ]] || \
	fail "bundle DTB model mismatch"
fdtget -t s "$dtb" / compatible | grep -Fqw 'openailab,eaidk-310' || \
	fail "bundle DTB compatible mismatch"

uimage_info="$(dumpimage -l "$uinitrd")"
grep -Fq 'Image Name:   initramfs-6.12.108-zramfix1' <<<"$uimage_info" || \
	fail "uInitrd name mismatch"
grep -Fq 'Image Type:   AArch64 Linux RAMDisk Image (uncompressed)' <<<"$uimage_info" || \
	fail "uInitrd type mismatch"
uimage_epoch="$(python3 - "$uinitrd" <<'PY'
import pathlib
import struct
import sys

header = pathlib.Path(sys.argv[1]).read_bytes()[:12]
if len(header) != 12:
    raise SystemExit("short uInitrd header")
print(struct.unpack(">I", header[8:12])[0])
PY
)"
[[ "$uimage_epoch" == 1788352262 ]] || fail "uInitrd timestamp is not reproducible"

verify_root="$(mktemp -d "$WSL_ROOT/task6-verify.XXXXXX")"
trap 'rm -rf -- "$verify_root"' EXIT
mkdir -p -- "$verify_root/initramfs" "$verify_root/archive"
dumpimage -T ramdisk -p 0 -o "$verify_root/initrd.img" "$uinitrd" >/dev/null
unmkinitramfs "$verify_root/initrd.img" "$verify_root/initramfs"

initramfs_root="$verify_root/initramfs"
[[ -s "$initramfs_root/init" ]] || fail "initramfs /init is missing"
[[ -s "$initramfs_root/usr/bin/busybox" ]] || fail "ARM64 busybox is missing"
[[ -e "$initramfs_root/usr/sbin/modprobe" ]] || fail "ARM64 modprobe is missing"
[[ -s "$initramfs_root/usr/bin/kmod" ]] || fail "ARM64 kmod is missing"
file "$initramfs_root/usr/bin/busybox" | grep -Fq 'ARM aarch64' || \
	fail "initramfs busybox is not ARM aarch64"
file "$initramfs_root/usr/bin/kmod" | grep -Fq 'ARM aarch64' || \
	fail "initramfs kmod is not ARM aarch64"
initramfs_modules="$initramfs_root/usr/lib/modules/$EXPECTED_RELEASE"
[[ -s "$initramfs_modules/modules.dep" ]] || fail "initramfs modules.dep is missing"
[[ -s "$initramfs_modules/modules.builtin" ]] || fail "initramfs modules.builtin is missing"

while IFS= read -r requested_module; do
	requested_module="${requested_module%%#*}"
	requested_module="${requested_module//[[:space:]]/}"
	[[ -z "$requested_module" ]] && continue
	find "$initramfs_modules" -type f -name "${requested_module//-/_}.ko*" -print -quit | \
		grep -q . || fail "explicit module is absent from initramfs: $requested_module"
done < "$PROJECT_DIR/initramfs/modules"

initramfs_devices="$(find "$initramfs_root" \( -type b -o -type c \) -print)"
[[ -z "$initramfs_devices" ]] || fail "initramfs contains device nodes: $initramfs_devices"

expected_entry="$verify_root/extlinux-entry.conf"
python3 "$PROJECT_DIR/tools/kernel_artifacts.py" render-extlinux \
	--append-line "$APPEND_LINE" --output "$expected_entry" >/dev/null
cmp -s "$expected_entry" "$BUNDLE_ROOT/deploy/extlinux-entry.conf" || \
	fail "extlinux test entry mismatch"

(
	cd "$(dirname -- "$ARCHIVE")"
	sha256sum -c "$(basename -- "$ARCHIVE.sha256")"
) >/dev/null
zstd -dc "$ARCHIVE" | tar -xf - -C "$verify_root/archive"
archive_bundle="$verify_root/archive/$BUNDLE_NAME"
python3 "$PROJECT_DIR/tools/kernel_artifacts.py" verify-bundle-layout \
	--root "$archive_bundle" --manifest "$archive_bundle/manifest.json" >/dev/null
cmp -s "$BUNDLE_ROOT/manifest.json" "$archive_bundle/manifest.json" || \
	fail "archive manifest differs from bundle directory"

manifest_files="$(python3 - "$BUNDLE_ROOT/manifest.json" <<'PY'
import json
import pathlib
import sys
print(len(json.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))["files"]))
PY
)"
module_files="$(find "$initramfs_modules" -type f -name '*.ko*' | wc -l)"
printf 'BUNDLE_GATE=PASS\n'
printf 'kernel_release=%s\n' "$EXPECTED_RELEASE"
printf 'manifest_files=%s\n' "$manifest_files"
printf 'initramfs_module_files=%s\n' "$module_files"
printf 'archive_sha256=%s\n' "$(sha256sum "$ARCHIVE" | cut -d' ' -f1)"
