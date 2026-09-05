#!/usr/bin/env bash
set -euo pipefail

RELEASE="6.12.108-eaidk310-zramfix1"
BUNDLE_NAME="eaidk310-linux-$RELEASE"
EXPECTED_RUNNING_ROOT="/dev/mmcblk0p2"
EXPECTED_RUNNING_BOOT="/dev/mmcblk0p1"
EXPECTED_RUNNING_KERNEL="6.8.4-rk3328"
EXPECTED_EMMC_MODEL="HBD08G"
EXPECTED_EMMC_BOOT_UUID="cbdb447a-125d-4d30-bc3d-07807a1f4578"
EXPECTED_EMMC_ROOT_UUID="781e1dc3-166b-46e9-8578-4b9c003d7305"
EXPECTED_OLD_DEFAULT="rockchip-kernel-6.8.4"
NEW_DEFAULT="rockchip-kernel-$RELEASE"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
DEFAULT_BUNDLE_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
BUNDLE_ROOT="$DEFAULT_BUNDLE_ROOT"
BUNDLE_ARCHIVE="$DEFAULT_BUNDLE_ROOT.tar.zst"
BUNDLE_SHA256=""
ROOT_PREFIX="/mnt/eaidk310-emmc"
EXPECTED_EXTLINUX_SHA256=""
APPLY=0

fail() {
	printf 'EMMC_DEPLOY_GATE=FAIL: %s\n' "$*" >&2
	exit 1
}

usage() {
	cat <<'EOF'
usage: deploy-emmc.sh [--apply] --bundle-sha256 SHA256
                      [--expected-extlinux-sha256 SHA256]
                      [--bundle-root PATH] [--bundle-archive PATH]
                      [--root-prefix PATH]

The HBD08G root partition must be mounted at ROOT_PREFIX and its BOOT partition
at ROOT_PREFIX/boot. Without --apply the command is read-only. A real apply also
requires the SHA-256 of the extlinux.conf observed during dry-run.
EOF
}

while (($#)); do
	case "$1" in
		--apply) APPLY=1; shift ;;
		--bundle-sha256) BUNDLE_SHA256="${2:?missing value for --bundle-sha256}"; shift 2 ;;
		--expected-extlinux-sha256) EXPECTED_EXTLINUX_SHA256="${2:?missing value for --expected-extlinux-sha256}"; shift 2 ;;
		--bundle-root) BUNDLE_ROOT="${2:?missing value for --bundle-root}"; shift 2 ;;
		--bundle-archive) BUNDLE_ARCHIVE="${2:?missing value for --bundle-archive}"; shift 2 ;;
		--root-prefix) ROOT_PREFIX="${2:?missing value for --root-prefix}"; shift 2 ;;
		-h|--help) usage; exit 0 ;;
		*) fail "unknown argument: $1" ;;
	esac
done

if ((APPLY)); then printf 'write=true\n' >&2; else printf 'write=false\n' >&2; fi

for tool in awk blkid cmp cp df diff du find findmnt grep install lsblk mkdir mktemp mv \
	python3 realpath sha256sum stat sync tar uname zstd; do
	command -v "$tool" >/dev/null || fail "required tool is missing: $tool"
done

[[ "$BUNDLE_SHA256" =~ ^[0-9a-f]{64}$ ]] || fail "bundle SHA-256 must be 64 lowercase hex characters"
if ((APPLY)); then
	[[ "$EXPECTED_EXTLINUX_SHA256" =~ ^[0-9a-f]{64}$ ]] || \
		fail "--apply requires --expected-extlinux-sha256 from the accepted dry-run"
fi

if [[ "${EAIDK310_TEST_MODE:-0}" == 1 ]]; then
	[[ "$ROOT_PREFIX" != / ]] || fail "test mode requires an explicit non-root --root-prefix"
else
	[[ "$ROOT_PREFIX" == /mnt/eaidk310-emmc ]] || \
		fail "production root prefix must be /mnt/eaidk310-emmc"
fi
ROOT_PREFIX="$(realpath -e "$ROOT_PREFIX")" || fail "could not resolve eMMC root mount"
BOOT_ROOT="$ROOT_PREFIX/boot"
[[ -d "$BOOT_ROOT" ]] || fail "eMMC BOOT mountpoint is missing: $BOOT_ROOT"

BUNDLE_ROOT="$(realpath -e "$BUNDLE_ROOT")" || fail "could not resolve bundle root"
BUNDLE_ARCHIVE="$(realpath -e "$BUNDLE_ARCHIVE")" || fail "could not resolve bundle archive"
[[ "$(basename -- "$BUNDLE_ROOT")" == "$BUNDLE_NAME" ]] || fail "wrong bundle directory name"
MANIFEST="$BUNDLE_ROOT/manifest.json"
HELPER="$BUNDLE_ROOT/deploy/kernel_artifacts.py"
BASELINE_LOCK="$BUNDLE_ROOT/deploy/stable-baseline-sha256.txt"
for required in "$MANIFEST" "$HELPER" "$BASELINE_LOCK"; do
	[[ -f "$required" && ! -L "$required" ]] || fail "required bundle file is missing: $required"
done

actual_archive_sha="$(sha256sum "$BUNDLE_ARCHIVE" | awk '{print $1}')"
[[ "$actual_archive_sha" == "$BUNDLE_SHA256" ]] || fail "bundle SHA-256 mismatch"
SCRATCH="$(mktemp -d)"
trap 'rm -rf -- "$SCRATCH"' EXIT
zstd -dc -- "$BUNDLE_ARCHIVE" | tar -xOf - "$BUNDLE_NAME/manifest.json" > "$SCRATCH/archive-manifest.json" || \
	fail "could not read archived manifest"
cmp -s "$MANIFEST" "$SCRATCH/archive-manifest.json" || fail "bundle manifest differs from archive"
python3 "$HELPER" verify-bundle-layout --root "$BUNDLE_ROOT" --manifest "$MANIFEST" \
	> "$SCRATCH/bundle-gate.json" || fail "bundle manifest verification failed"

ROOT_SOURCE="$(findmnt -n -o SOURCE /)"
BOOT_SOURCE="$(findmnt -n -o SOURCE /boot)"
[[ "$ROOT_SOURCE" == "$EXPECTED_RUNNING_ROOT" ]] || fail "running root must be the rescue TF"
[[ "$BOOT_SOURCE" == "$EXPECTED_RUNNING_BOOT" ]] || fail "running boot must be the rescue TF"
[[ "$(uname -r)" == "$EXPECTED_RUNNING_KERNEL" ]] || fail "running kernel must be $EXPECTED_RUNNING_KERNEL"
[[ "$(findmnt -n -o SOURCE --target "$ROOT_PREFIX")" == /dev/mmcblk2p2 ]] || \
	fail "eMMC ROOTFS must be mounted at $ROOT_PREFIX"
[[ "$(findmnt -n -o SOURCE --target "$BOOT_ROOT")" == /dev/mmcblk2p1 ]] || \
	fail "eMMC BOOT must be mounted at $BOOT_ROOT"

EMMC_SYSFS_MODEL="/sys/block/mmcblk2/device/name"
if [[ -r "$EMMC_SYSFS_MODEL" ]]; then
	EMMC_MODEL="$(awk '{$1=$1; print}' "$EMMC_SYSFS_MODEL")"
else
	# Test fixtures and a few non-MMC block implementations expose only MODEL.
	EMMC_MODEL="$(lsblk -dn -o MODEL /dev/mmcblk2 | awk '{$1=$1; print}')"
fi
[[ "$EMMC_MODEL" == "$EXPECTED_EMMC_MODEL" ]] || fail "eMMC model mismatch: $EMMC_MODEL"
BOOT_UUID="$(blkid -s UUID -o value /dev/mmcblk2p1)"
ROOT_UUID="$(blkid -s UUID -o value /dev/mmcblk2p2)"
[[ "${BOOT_UUID,,}" == "$EXPECTED_EMMC_BOOT_UUID" ]] || fail "eMMC BOOT UUID mismatch"
[[ "${ROOT_UUID,,}" == "$EXPECTED_EMMC_ROOT_UUID" ]] || fail "eMMC ROOTFS UUID mismatch"

CURRENT_EXTLINUX="$BOOT_ROOT/extlinux/extlinux.conf"
[[ -f "$CURRENT_EXTLINUX" && ! -L "$CURRENT_EXTLINUX" ]] || fail "unsafe or missing extlinux.conf"
CURRENT_EXTLINUX_SHA256="$(sha256sum "$CURRENT_EXTLINUX" | awk '{print $1}')"
if ((APPLY)); then
	[[ "$CURRENT_EXTLINUX_SHA256" == "$EXPECTED_EXTLINUX_SHA256" ]] || fail "extlinux SHA-256 changed after dry-run"
fi
mapfile -t defaults < <(awk 'tolower($1)=="default" {print $2}' "$CURRENT_EXTLINUX")
[[ "${#defaults[@]}" == 1 && "${defaults[0]}" == "$EXPECTED_OLD_DEFAULT" ]] || fail "old extlinux default mismatch"
mapfile -t append_lines < <(awk 'tolower($1)=="append" {$1=""; sub(/^[[:space:]]+/, ""); print}' "$CURRENT_EXTLINUX")
[[ "${#append_lines[@]}" -ge 1 ]] || fail "extlinux contains no APPEND line"
for append_line in "${append_lines[@]}"; do
	[[ " $append_line " == *" root=UUID=$EXPECTED_EMMC_ROOT_UUID "* ]] || fail "extlinux APPEND uses the wrong root UUID"
done

declare -A STABLE_HASH=()
while read -r digest relative extra; do
	[[ -n "${digest:-}" ]] || continue
	[[ -z "${extra:-}" ]] || fail "invalid stable baseline line"
	case "$relative" in
		boot/Image|boot/uInitrd|boot/dtb/rockchip/rk3328-eaidk-310.dtb) STABLE_HASH["$relative"]="$digest" ;;
		boot/extlinux/extlinux.conf) ;;
		*) fail "unexpected stable baseline path: $relative" ;;
	esac
done < "$BASELINE_LOCK"
[[ "${#STABLE_HASH[@]}" == 3 ]] || fail "stable baseline lock is incomplete"
for relative in "${!STABLE_HASH[@]}"; do
	target="$ROOT_PREFIX/$relative"
	[[ -f "$target" && ! -L "$target" ]] || fail "stable boot file is missing or unsafe: $relative"
	[[ "$(sha256sum "$target" | awk '{print $1}')" == "${STABLE_HASH[$relative]}" ]] || fail "stable boot hash mismatch: $relative"
done

SOURCE_IMAGE="$BUNDLE_ROOT/boot/Image-$RELEASE"
SOURCE_UINITRD="$BUNDLE_ROOT/boot/uInitrd-$RELEASE"
SOURCE_DTB="$BUNDLE_ROOT/boot/dtb/rockchip/rk3328-eaidk-310-6.12.108.dtb"
SOURCE_MODULES="$BUNDLE_ROOT/root/lib/modules/$RELEASE"
for source in "$SOURCE_IMAGE" "$SOURCE_UINITRD" "$SOURCE_DTB" "$SOURCE_MODULES/modules.dep"; do
	[[ -f "$source" && ! -L "$source" ]] || fail "bundle source is missing or unsafe: $source"
done

MODULES_ROOT="$ROOT_PREFIX/lib/modules"
DEST_MODULES="$MODULES_ROOT/$RELEASE"
[[ -d "$MODULES_ROOT" ]] || fail "module root is missing"
if [[ -e "$DEST_MODULES" || -L "$DEST_MODULES" ]]; then
	[[ -d "$DEST_MODULES" && ! -L "$DEST_MODULES" ]] || fail "module destination is unsafe"
	diff -qr "$SOURCE_MODULES" "$DEST_MODULES" >/dev/null || fail "existing module tree conflicts"
fi
if find "$BOOT_ROOT" "$MODULES_ROOT" -name ".eaidk310-emmc-*.tmp" -print -quit | grep -q .; then
	fail "interrupted deployment temporary path is present"
fi

OLD_APPEND="${append_lines[0]}"
CANDIDATE_EXTLINUX="$SCRATCH/extlinux.conf"
cat > "$CANDIDATE_EXTLINUX" <<EOF
default $NEW_DEFAULT
timeout 30
menu title EAIDK-310 boot menu

label $NEW_DEFAULT
    LINUX /Image
    FDT /dtb/rockchip/rk3328-eaidk-310.dtb
    INITRD /uInitrd
    APPEND $OLD_APPEND
EOF

ROOT_AVAILABLE="$(df -PB1 "$ROOT_PREFIX" | awk 'NR==2 {print $4}')"
BOOT_AVAILABLE="$(df -PB1 "$BOOT_ROOT" | awk 'NR==2 {print $4}')"
MODULE_BYTES="$(du -sb "$SOURCE_MODULES" | awk '{print $1}')"
BOOT_BYTES=$(( $(stat -c %s "$SOURCE_IMAGE") + $(stat -c %s "$SOURCE_UINITRD") + $(stat -c %s "$SOURCE_DTB") + 33554432 ))
ROOT_BYTES=$(( MODULE_BYTES + 67108864 ))
[[ "$ROOT_AVAILABLE" =~ ^[0-9]+$ && "$BOOT_AVAILABLE" =~ ^[0-9]+$ ]] || fail "could not determine free space"
(( ROOT_AVAILABLE >= ROOT_BYTES )) || fail "insufficient eMMC root space"
(( BOOT_AVAILABLE >= BOOT_BYTES )) || fail "insufficient eMMC boot space"

export REPORT_MODE="$([[ "$APPLY" == 1 ]] && printf apply || printf dry-run)"
export REPORT_BUNDLE_SHA="$BUNDLE_SHA256" REPORT_MODEL="$EMMC_MODEL"
export REPORT_BOOT_UUID="$BOOT_UUID" REPORT_ROOT_UUID="$ROOT_UUID"
export REPORT_EXTLINUX_SHA="$CURRENT_EXTLINUX_SHA256"
export REPORT_SOURCE_IMAGE="$SOURCE_IMAGE" REPORT_SOURCE_UINITRD="$SOURCE_UINITRD"
export REPORT_SOURCE_DTB="$SOURCE_DTB" REPORT_CANDIDATE_EXTLINUX="$CANDIDATE_EXTLINUX"

write_report() {
	python3 - "$1" <<'PY'
import hashlib, json, os, pathlib, sys
def sha(path):
    digest = hashlib.sha256()
    with pathlib.Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
report = {
    "gate": "PASS",
    "mode": os.environ["REPORT_MODE"],
    "write": os.environ["REPORT_MODE"] == "apply",
    "bundle_sha256": os.environ["REPORT_BUNDLE_SHA"],
    "target_device": "/dev/mmcblk2",
    "target_model": os.environ["REPORT_MODEL"],
    "boot_uuid": os.environ["REPORT_BOOT_UUID"].lower(),
    "root_uuid": os.environ["REPORT_ROOT_UUID"].lower(),
    "extlinux_before_sha256": os.environ["REPORT_EXTLINUX_SHA"],
    "planned_sha256": {
        "Image": sha(os.environ["REPORT_SOURCE_IMAGE"]),
        "uInitrd": sha(os.environ["REPORT_SOURCE_UINITRD"]),
        "dtb/rockchip/rk3328-eaidk-310.dtb": sha(os.environ["REPORT_SOURCE_DTB"]),
        "extlinux/extlinux.conf": sha(os.environ["REPORT_CANDIDATE_EXTLINUX"]),
    },
    "rollback_directory": "/boot/rollback/6.8.4-pre-6.12.108",
}
path = pathlib.Path(sys.argv[1])
path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

if ((!APPLY)); then
	write_report "$SCRATCH/report.json"
	cat "$SCRATCH/report.json"
	printf 'EMMC_DEPLOY_GATE=PASS: dry-run only; no target files changed\n' >&2
	exit 0
fi

ROLLBACK="$BOOT_ROOT/rollback/6.8.4-pre-6.12.108"
[[ ! -e "$ROLLBACK" ]] || fail "rollback directory already exists: $ROLLBACK"
mkdir -p -- "$ROLLBACK"
cp --preserve=all "$BOOT_ROOT/Image" "$ROLLBACK/Image"
cp --preserve=all "$BOOT_ROOT/uInitrd" "$ROLLBACK/uInitrd"
mkdir -p -- "$ROLLBACK/dtb/rockchip"
cp --preserve=all "$BOOT_ROOT/dtb/rockchip/rk3328-eaidk-310.dtb" "$ROLLBACK/dtb/rockchip/rk3328-eaidk-310.dtb"
cp --preserve=all "$CURRENT_EXTLINUX" "$ROLLBACK/extlinux.conf"
cp --preserve=all "$BASELINE_LOCK" "$ROLLBACK/stable-baseline-sha256.txt"

if [[ ! -e "$DEST_MODULES" ]]; then
	MODULE_TMP="$MODULES_ROOT/.eaidk310-emmc-modules.tmp"
	mkdir "$MODULE_TMP"
	cp -a "$SOURCE_MODULES/." "$MODULE_TMP/"
	diff -qr "$SOURCE_MODULES" "$MODULE_TMP" >/dev/null || fail "temporary modules verification failed"
	mv -T "$MODULE_TMP" "$DEST_MODULES"
fi

install_generic() {
	local source="$1" destination="$2" temporary
	temporary="$(dirname -- "$destination")/.eaidk310-emmc-$(basename -- "$destination").tmp"
	install -m 0644 "$source" "$temporary"
	cmp -s "$source" "$temporary" || fail "temporary verification failed: $destination"
	mv -T "$temporary" "$destination"
}
install_generic "$SOURCE_IMAGE" "$BOOT_ROOT/Image"
install_generic "$SOURCE_UINITRD" "$BOOT_ROOT/uInitrd"
install_generic "$SOURCE_DTB" "$BOOT_ROOT/dtb/rockchip/rk3328-eaidk-310.dtb"
install_generic "$CANDIDATE_EXTLINUX" "$CURRENT_EXTLINUX"

cmp -s "$SOURCE_IMAGE" "$BOOT_ROOT/Image" || fail "Image read-back mismatch"
cmp -s "$SOURCE_UINITRD" "$BOOT_ROOT/uInitrd" || fail "uInitrd read-back mismatch"
cmp -s "$SOURCE_DTB" "$BOOT_ROOT/dtb/rockchip/rk3328-eaidk-310.dtb" || fail "DTB read-back mismatch"
diff -qr "$SOURCE_MODULES" "$DEST_MODULES" >/dev/null || fail "modules read-back mismatch"
grep -Fqx "default $NEW_DEFAULT" "$CURRENT_EXTLINUX" || fail "new extlinux default verification failed"
sync
write_report "$ROLLBACK/deployment-report.json"
sync
cat "$ROLLBACK/deployment-report.json"
printf 'EMMC_DEPLOY_GATE=PASS: eMMC kernel promotion applied and verified\n' >&2
