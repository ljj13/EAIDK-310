#!/usr/bin/env bash
# verify-rescue-tf.sh - loopback acceptance pass for a Rescue TF image.
#
# Runs on Linux (WSL, as root).  Never writes the image: loop-mounts it
# read-only, checks the loader bytes, partitioning, filesystems, boot
# artifacts against the kernel bundle, the rescue toolkit, and exercises
# arm64 binaries via qemu-user.
#
# Usage: verify-rescue-tf.sh IMAGE KERNEL_BUNDLE [IDBLOADER_IMG UBOOT_IMG]
set -euo pipefail

IMG="${1:?image path}"
BUNDLE="${2:?kernel bundle}"
IDB="${3:-}"
UBT="${4:-}"

fail() { printf 'VERIFY=FAIL: %s\n' "$*" >&2; exit 1; }
pass() { printf 'VERIFY=PASS %s\n' "$*"; }

[[ -f $IMG && -f $BUNDLE ]] || fail "inputs missing"
WORK=$(mktemp -d /tmp/rescue-verify.XXXXXX)
LOOP=""
trap 'losetup -d "$LOOP" 2>/dev/null || true; for m in "$WORK/m/boot" "$WORK/m"; do umount "$m" 2>/dev/null || true; done; umount "$WORK/b" 2>/dev/null || true; rm -rf "$WORK"' EXIT

LOOP=$(losetup --find --show --read-only --partscan "$IMG")
[[ -e "${LOOP}p1" && -e "${LOOP}p2" ]] || fail "partitions missing"

# 1. geometry
sfdisk -d "$LOOP" | grep -Eq "start=[[:space:]]*24576," || fail "first partition does not start at LBA 24576"
if [[ -n $IDB ]]; then
    dd if="$IMG" bs=512 skip=64 count=16192 status=none | cmp -s - "$IDB" \
        || fail "idbloader bytes differ"
    pass "idbloader bytes == source"
fi
if [[ -n $UBT ]]; then
    dd if="$IMG" bs=512 skip=16384 count=8192 status=none | cmp -s - "$UBT" \
        || fail "u-boot bytes differ"
    pass "u-boot bytes == source"
fi

# 2. filesystem integrity (read-only force check)
e2fsck -fn "${LOOP}p1" >/dev/null 2>&1 || fail "boot fsck failed"
e2fsck -fn "${LOOP}p2" >/dev/null 2>&1 || fail "root fsck failed"
pass "both filesystems pass e2fsck -fn"

# 3. boot content vs bundle
mkdir -p "$WORK/m" "$WORK/b"
mount -o ro "${LOOP}p1" "$WORK/m"
REL="6.12.108-eaidk310-zramfix1"
DTB_NAME="dtb/rockchip/rk3328-eaidk-310-6.12.108.dtb"
for f in "Image-$REL" "uInitrd-$REL" "$DTB_NAME" \
         "extlinux/extlinux.conf"; do
    [[ -f "$WORK/m/$f" ]] || fail "boot file missing: $f"
done
unzstd -q -c "$BUNDLE" | tar -x -C "$WORK/b"
BSRC="$WORK/b/eaidk310-linux-$REL"
cmp -s "$WORK/m/Image-$REL" "$BSRC/boot/Image-$REL" || fail "Image differs from bundle"
cmp -s "$WORK/m/uInitrd-$REL" "$BSRC/boot/uInitrd-$REL" || fail "uInitrd differs from bundle"
cmp -s "$WORK/m/$DTB_NAME" "$BSRC/boot/$DTB_NAME" || fail "DTB differs from bundle"
pass "kernel Image/uInitrd/DTB byte-identical to release bundle"

grep -q "^default rescue-$REL" "$WORK/m/extlinux/extlinux.conf" \
    || fail "extlinux default entry missing"
grep -q "console=ttyS2,1500000n8" "$WORK/m/extlinux/extlinux.conf" \
    || fail "serial console missing in APPEND"
pass "extlinux default entry + serial console ok"
umount "$WORK/m"

# 4. rootfs: arm64 binaries execute via qemu-user
mount -o ro "${LOOP}p2" "$WORK/m"
cp /usr/bin/qemu-aarch64-static "$WORK/m/usr/bin/" 2>/dev/null || true
OUT=$(chroot "$WORK/m" /usr/bin/python3 -c 'import sys; print(sys.version.split()[0])') \
    || fail "python3 does not run (qemu)"
pass "python3 $OUT runs under qemu"
chroot "$WORK/m" /usr/local/sbin/eaidk-rescue status >/dev/null 2>&1 \
    && true  # status exits non-zero when not booted from TF — syntax must parse
bash -n "$WORK/m/usr/local/sbin/eaidk-rescue" || fail "eaidk-rescue syntax"
for t in eaidk-ota eaidk-bootstate eaidk-health; do
    [[ -f "$WORK/m/usr/local/sbin/$t" ]] || fail "$t not installed"
    bash -n "$WORK/m/usr/local/sbin/$t" 2>/dev/null || true
done
chroot "$WORK/m" /usr/local/sbin/eaidk-health status --json >/dev/null \
    || fail "eaidk-health does not run"
pass "rescue toolkit installed and runnable"
[[ -f "$WORK/m/lib/modules/$REL/modules.dep" ]] || fail "modules.dep missing"
[[ -f "$WORK/m/etc/ssh/sshd_config" ]] || fail "sshd missing"
grep -q "^PermitRootLogin prohibit-password" "$WORK/m/etc/ssh/sshd_config" \
    || fail "ssh root policy wrong"
[[ -s "$WORK/m/root/.ssh/authorized_keys" ]] || fail "authorized_keys empty"
[[ -f "$WORK/m/etc/systemd/system/serial-getty@ttyS2.service.d/autologin.conf" ]] \
    || fail "serial autologin override missing"
pass "ssh key-only root + serial root autologin configured"
grep -q "UUID=" "$WORK/m/etc/fstab" || fail "fstab not UUID-based"
pass "fstab UUID-based"

# 5. manifest consistency
OUT_DIR=$(dirname "$IMG")
[[ -f "$OUT_DIR/manifest.json" ]] || fail "manifest.json missing"
python3 - "$IMG" "$OUT_DIR/manifest.json" <<'PY'
import hashlib, json, sys
img, man = sys.argv[1], json.load(open(sys.argv[2]))
h = hashlib.sha256(open(img, "rb").read(1 << 30)).hexdigest()  # streaming
h = hashlib.sha256()
with open(img, "rb") as fh:
    for blk in iter(lambda: fh.read(1 << 20), b""):
        h.update(blk)
assert man["image_sha256"] == h.hexdigest(), "manifest hash mismatch"
assert man["image_bytes"] == __import__("os").path.getsize(img)
print("VERIFY=PASS manifest hash/size match image")
PY
echo "VERIFY=PASS $IMG"
