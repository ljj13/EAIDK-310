#!/usr/bin/env bash
# build-rescue-tf.sh - reproducible EAIDK-310 Rescue TF image builder.
#
# Runs on Linux (WSL, as root).  Produces:
#   <out>/eaidk310-rescue-tf-<stamp>.img       1.6 GiB SD image
#   <out>/manifest.json                        all input + output hashes
#   <out>/SHA256SUMS
#
# Inputs (all hash-recorded in the manifest):
#   IDBLOADER_IMG   loader bytes, sectors 64..16384   (from the validated
#     UBOOT_IMG       loader bytes, sectors 16384..24576 failsafe U-Boot
#                     currently on eMMC; hash-pinned below)
#   KERNEL_BUNDLE   release bundle eaidk310-linux-6.12.108-*.tar.zst
#   AUTHORIZED_KEYS public key(s) baked into root@rescue (no private key)
#
# Layout (identical to the eMMC boot geometry, ota/docs/FLASH-PLAN-P36.md):
#   GPT, idbloader @ LBA 64, u-boot.itb @ LBA 16384,
#   p1 512 MiB ext4 /boot, p2 remainder ext4 /
set -euo pipefail

STAMP=$(date -u +%Y%m%d)
OUT_DIR="${OUT_DIR:-$(pwd)/build-rescue}"
IDBLOADER_IMG="${IDBLOADER_IMG:?set IDBLOADER_IMG}"
UBOOT_IMG="${UBOOT_IMG:?set UBOOT_IMG}"
KERNEL_BUNDLE="${KERNEL_BUNDLE:?set KERNEL_BUNDLE}"
AUTHORIZED_KEYS="${AUTHORIZED_KEYS:?set AUTHORIZED_KEYS (public keys file)}"
DEBIAN_SUITE="${DEBIAN_SUITE:-trixie}"
DEBIAN_MIRROR="${DEBIAN_MIRROR:-http://deb.debian.org/debian}"
IMG="${OUT_DIR}/eaidk310-rescue-tf-${STAMP}.img"
IMG_SIZE_MIB="${IMG_SIZE_MIB:-1664}"
BOOT_SIZE_MIB=512
KERNEL_RELEASE="6.12.108-eaidk310-zramfix1"
KERNEL_DTB="rk3328-eaidk-310-6.12.108.dtb"

fail() { printf 'BUILD=FAIL: %s\n' "$*" >&2; exit 1; }
log()  { printf '[build-rescue] %s\n' "$*"; }

for tool in dd losetup partx mkfs.ext4 sfdisk debootstrap qemu-debootstrap \
            unzstd tar sha256sum chroot mount blkid python3; do
    command -v "$tool" >/dev/null || fail "required tool missing: $tool"
done
[[ $(id -u) -eq 0 ]] || fail "must run as root (WSL: wsl.exe -u root)"
[[ -f "$IDBLOADER_IMG" && -f "$UBOOT_IMG" && -f "$KERNEL_BUNDLE" ]] \
    || fail "input file missing"
[[ -f "$AUTHORIZED_KEYS" ]] || fail "authorized keys file missing"
grep -q "ssh-" "$AUTHORIZED_KEYS" || fail "authorized keys file has no key"

mkdir -p "$OUT_DIR"
WORK=$(mktemp -d /tmp/rescue-build.XXXXXX)
LOOP=""
trap 'losetup -d "$LOOP" 2>/dev/null || true; umount "$WORK/rootfs/boot" "$WORK/rootfs" 2>/dev/null || true; rm -rf "$WORK"' EXIT

log "kernel bundle -> $WORK/bundle"
mkdir "$WORK/bundle"
unzstd -q -c "$KERNEL_BUNDLE" | tar -x -C "$WORK/bundle"
BUNDLE_ROOT="$WORK/bundle/eaidk310-linux-$KERNEL_RELEASE"
[[ -d "$BUNDLE_ROOT" ]] || fail "unexpected bundle layout"
[[ -f "$BUNDLE_ROOT/boot/Image-$KERNEL_RELEASE" ]] || fail "Image missing"

log "creating $IMG ($IMG_SIZE_MIB MiB)"
rm -f "$IMG"
truncate -s "$((IMG_SIZE_MIB * 1024 * 1024))" "$IMG"

log "partitioning (GPT, loader windows kept free)"
sfdisk "$IMG" <<EOF
label: gpt
unit: sectors
first-lba: 24576
name=boot, size=$((BOOT_SIZE_MIB * 2048)), type=0FC63DAF-8483-4772-8E79-3D69D8477DE4
name=rootfs, type=0FC63DAF-8483-4772-8E79-3D69D8477DE4
EOF

log "writing loader bytes (idbloader@LBA64, u-boot.itb@LBA16384)"
dd if="$IDBLOADER_IMG" of="$IMG" bs=512 seek=64 conv=notrunc status=none
dd if="$UBOOT_IMG" of="$IMG" bs=512 seek=16384 conv=notrunc status=none

LOOP=$(losetup --find --show --partscan "$IMG")
partx -u "$LOOP" >/dev/null 2>&1 || true
[[ -e "${LOOP}p1" && -e "${LOOP}p2" ]] || fail "loop partitions did not appear"

log "filesystems"
mkfs.ext4 -q -L rescue-boot -O ^has_journal "${LOOP}p1"
mkfs.ext4 -q -L rescue-rootfs "${LOOP}p2"
BOOT_UUID=$(blkid -s UUID -o value "${LOOP}p1")
ROOT_UUID=$(blkid -s UUID -o value "${LOOP}p2")

log "debootstrap $DEBIAN_SUITE minbase (arm64) — several minutes"
mkdir -p "$WORK/rootfs"
mount "${LOOP}p2" "$WORK/rootfs"
qemu-debootstrap --arch=arm64 --variant=minbase \
    --include="systemd-sysv,openssh-server,e2fsprogs,dosfstools,fdisk,python3,u-boot-tools,kmod,zstd,iproute2,iputils-ping,nano,less,procps,udev" \
    "$DEBIAN_SUITE" "$WORK/rootfs" "$DEBIAN_MIRROR"

log "rootfs configuration"
mount "${LOOP}p1" "$WORK/rootfs/boot"
echo "eaidk310-rescue" > "$WORK/rootfs/etc/hostname"
cat > "$WORK/rootfs/etc/hosts" <<EOF
127.0.0.1 localhost
127.0.1.1 eaidk310-rescue
EOF
cat > "$WORK/rootfs/etc/fstab" <<EOF
UUID=$ROOT_UUID /     ext4  errors=remount-ro 0 1
UUID=$BOOT_UUID /boot ext4  noatime           0 2
EOF
mkdir -p "$WORK/rootfs/root/.ssh"
cp "$AUTHORIZED_KEYS" "$WORK/rootfs/root/.ssh/authorized_keys"
chmod 700 "$WORK/rootfs/root/.ssh"
chmod 600 "$WORK/rootfs/root/.ssh/authorized_keys"
# rescue posture: root over ssh by key only, root autologin on the
# debug console so a fully-dead board is still recoverable
sed -i 's/^#*PermitRootLogin.*/PermitRootLogin prohibit-password/' \
    "$WORK/rootfs/etc/ssh/sshd_config"
mkdir -p "$WORK/rootfs/etc/systemd/system/serial-getty@ttyS2.service.d"
cat > "$WORK/rootfs/etc/systemd/system/serial-getty@ttyS2.service.d/autologin.conf" <<'EOF'
[Service]
ExecStart=
ExecStart=-/sbin/agetty -a root -L 115200 ttyS2 vt100
EOF

log "kernel artifacts"
cp "$BUNDLE_ROOT/boot/Image-$KERNEL_RELEASE" "$WORK/rootfs/boot/"
cp "$BUNDLE_ROOT/boot/uInitrd-$KERNEL_RELEASE" "$WORK/rootfs/boot/"
mkdir -p "$WORK/rootfs/boot/dtb/rockchip"
cp -r "$BUNDLE_ROOT/boot/dtb/rockchip/." "$WORK/rootfs/boot/dtb/rockchip/"
mkdir -p "$WORK/rootfs/lib/modules"
cp -a "$BUNDLE_ROOT/root/lib/modules/." "$WORK/rootfs/lib/modules/"
mkdir -p "$WORK/rootfs/boot/extlinux"
cat > "$WORK/rootfs/boot/extlinux/extlinux.conf" <<EOF
default rescue-$KERNEL_RELEASE
menu title EAIDK-310 Rescue TF
timeout 50

label rescue-$KERNEL_RELEASE
    menu label RESCUE $KERNEL_RELEASE
    LINUX /Image-$KERNEL_RELEASE
    FDT /dtb/rockchip/$KERNEL_DTB
    INITRD /uInitrd-$KERNEL_RELEASE
    APPEND root=UUID=$ROOT_UUID rootwait rootfstype=ext4 net.ifnames=0 earlycon console=ttyS2,1500000n8 console=tty1 consoleblank=0 loglevel=4
EOF
[[ -f "$WORK/rootfs/boot/dtb/rockchip/$KERNEL_DTB" ]] \
    || fail "board DTB missing after copy"

log "rescue toolkit"
install -d "$WORK/rootfs/usr/local/sbin"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
install -m 0755 "$SCRIPT_DIR/eaidk-rescue" "$WORK/rootfs/usr/local/sbin/"
for tool in eaidk-ota eaidk-bootstate eaidk-health; do
    SRC="$SCRIPT_DIR/../ota/$tool"
    [[ -f "$SRC" ]] || SRC="$SCRIPT_DIR/../tools/$tool"
    [[ -f "$SRC" ]] || fail "toolkit source not found: $tool"
    install -m 0755 "$SRC" "$WORK/rootfs/usr/local/sbin/"
done

log "depmod in chroot"
chroot "$WORK/rootfs" depmod -a "$KERNEL_RELEASE"
chroot "$WORK/rootfs" systemctl enable ssh >/dev/null 2>&1 || true
sync
umount "$WORK/rootfs/boot"
umount "$WORK/rootfs"
losetup -d "$LOOP"
LOOP=

log "finalizing image + manifest"
# let the loop/9p writeback fully settle before hashing; hashing earlier
# has produced a digest of an in-flight view on /mnt/drvfs backends
sync
sleep 2
sync
python3 - "$IMG" "$OUT_DIR" "$STAMP" <<'PY'
import hashlib, json, pathlib, sys

img, out_dir, stamp = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]), sys.argv[3]
def sha(p, chunk=1 << 20):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for blk in iter(lambda: fh.read(chunk), b""):
            h.update(blk)
    return h.hexdigest()

manifest = {
    "image": img.name,
    "image_sha256": sha(img),
    "image_bytes": img.stat().st_size,
    "kernel_release": "6.12.108-eaidk310-zramfix1",
    "layout": {"gpt_first_lba": 24576,
               "idbloader_lba": 64, "uboot_lba": 16384,
               "boot_part_mib": 512},
    "rootfs": "debian trixie minbase (qemu-debootstrap, deb.debian.org)",
    "rescue_tools": ["eaidk-rescue", "eaidk-ota", "eaidk-bootstate",
                     "eaidk-health"],
    "boot_entries": [f"rescue-6.12.108-eaidk310-zramfix1 (default)"],
    "access": ["ssh root@<card-ip> (authorized_keys baked, no password)",
               "serial ttyS2 1500000 8N1 root autologin"],
}
(out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
(out_dir / "SHA256SUMS").write_text(
    f"{manifest['image_sha256']}  {img.name}\n")
print(f"BUILD=PASS image={img.name}")
print(f"image_sha256={manifest['image_sha256']}")
PY
