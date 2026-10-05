#!/usr/bin/env bash
set -euo pipefail

# P3.6-B candidate B builder: control reference + failsafe-raw variant
# (raw dual-copy bootstate on fixed eMMC sectors, patch 0003 on top of
# 0001+0002).  Builds only read the pinned source and write ordinary files
# under OUTPUT_DIR; no block device access, no dd, no eMMC writes.

readonly script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
readonly project_dir="$(dirname -- "$script_dir")"
readonly patch_0001="$project_dir/patches/0001-arm-dts-add-eaidk310-variants.patch"
readonly patch_0002="$project_dir/patches/0002-failsafe-bootcount-fs.patch"
readonly patch_0003="$project_dir/patches/0003-failsafe-raw-bootstate.patch"
readonly patch_0004="$project_dir/patches/0004-trial-watchdog.patch"

if [ "$#" -ne 2 ]; then
	echo "usage: $0 SOURCE_DIR OUTPUT_DIR" >&2
	exit 2
fi

source_dir="$(CDPATH= cd -- "$1" && pwd)"
mkdir -p "$2"
output_root="$(CDPATH= cd -- "$2" && pwd)"

if [ -e "$output_root/control" ] || [ -e "$output_root/failsafe-raw" ]; then
	echo "output variant directories already exist: $output_root" >&2
	exit 1
fi

bash "$script_dir/verify-source-and-patch.sh" "$source_dir"
git -C "$source_dir" apply --check "$patch_0002"
wt_check="$(mktemp -d)"
git -C "$source_dir" worktree add --detach "$wt_check" \
	38ea74d6d5c05224acdb03f799897c1bdd56f8cc >/dev/null 2>&1
trap 'git -C "$source_dir" worktree remove --force "$wt_check" >/dev/null 2>&1 || true; rm -rf "$wt_check"' EXIT
git -C "$wt_check" apply "$patch_0001"
git -C "$wt_check" apply "$patch_0002"
git -C "$wt_check" apply "$patch_0003"
git -C "$wt_check" apply --check "$patch_0004"
git -C "$source_dir" worktree remove --force "$wt_check" >/dev/null 2>&1 || true
for command in make aarch64-linux-gnu-gcc strings stat dtc fdtget sha256sum; do
	command -v "$command" >/dev/null
done

export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1711843200}"
export KBUILD_BUILD_USER=builder
export KBUILD_BUILD_HOST=build

work_root="$(mktemp -d "${TMPDIR:-/tmp}/eaidk310-uboot-raw-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
trap 'test -n "${work_root:-}" && rm -rf -- "$work_root"' EXIT
work_source="$work_root/source"
mkdir -p "$work_source"
cp -a "$source_dir/." "$work_source/"
git -C "$work_source" apply "$patch_0001"
git -C "$work_source" apply "$patch_0002"
git -C "$work_source" apply "$patch_0003"
git -C "$work_source" apply "$patch_0004"

make -C "$work_source" O="$output_root/control" CROSS_COMPILE=aarch64-linux-gnu- eaidk310-control-rk3328_defconfig
make -C "$work_source" O="$output_root/control" CROSS_COMPILE=aarch64-linux-gnu- -j2 u-boot-dtb.bin u-boot-initial-env
make -C "$work_source" O="$output_root/failsafe-raw" CROSS_COMPILE=aarch64-linux-gnu- eaidk310-failsafe-raw-rk3328_defconfig
make -C "$work_source" O="$output_root/failsafe-raw" CROSS_COMPILE=aarch64-linux-gnu- -j2 u-boot-dtb.bin u-boot-initial-env

verify_variant() {
	variant="$1"
	payload="$output_root/$variant/u-boot-dtb.bin"
	dtb="$output_root/$variant/dts/dt.dtb"

	test -s "$payload"
	test -s "$dtb"
	size="$(stat -c %s "$payload")"
	test "$size" -le 1046528
	strings "$payload" | grep -F "Rockchip RK3328 EAIDK310" >/dev/null
	dtc -I dtb -O dts "$dtb" > "$output_root/$variant/live.dts"
	test "$(fdtget "$dtb" "/mmc@ff510000" status)" = "disabled"
	if fdtget "$dtb" "/mmc@ff510000" mmc-pwrseq >/dev/null 2>&1; then
		echo "DT unexpectedly contains mmc-pwrseq" >&2
		exit 1
	fi
}

verify_variant control
verify_variant failsafe-raw

# DTB must stay byte-identical to control
cmp "$output_root/control/dts/dt.dtb" "$output_root/failsafe-raw/dts/dt.dtb"

raw_config="$output_root/failsafe-raw/.config"
for symbol in \
	"CONFIG_BOOTCOUNT_LIMIT=y" \
	"CONFIG_BOOTCOUNT_EAIDK310_RAW=y" \
	'CONFIG_SYS_BOOTCOUNT_RAW_INTERFACE="mmc"' \
	'CONFIG_SYS_BOOTCOUNT_RAW_DEVID="0"' \
	"CONFIG_SYS_BOOTCOUNT_RAW_LBA_A=0x6400" \
	"CONFIG_SYS_BOOTCOUNT_RAW_LBA_B=0x7800" \
	"CONFIG_SYS_BOOTCOUNT_RAW_LBA_DIAG=0x6C00" \
	"CONFIG_SYS_BOOTCOUNT_RAW_DIAG=y" \
	"CONFIG_SYS_BOOTCOUNT_TRIAL_WDT_TIMEOUT_MS=30000" \
	"CONFIG_WATCHDOG=y" \
	"# CONFIG_WATCHDOG_AUTOSTART is not set" \
	"CONFIG_WDT=y" \
	"CONFIG_DESIGNWARE_WATCHDOG=y" \
	"CONFIG_CMD_WDT=y" \
	"CONFIG_SYS_BOOTCOUNT_RAW_MMC_MANFID=0xd6" \
	'CONFIG_SYS_BOOTCOUNT_RAW_MMC_NAME="HBD08G"' \
	"CONFIG_SYS_BOOTCOUNT_RAW_MMC_MINSIZE=0x1d2000000" \
	"CONFIG_BOOTCOUNT_BOOTLIMIT=1" \
	"CONFIG_CMD_SYSBOOT=y" \
	"CONFIG_HUSH_PARSER=y"; do
	grep -qx "$symbol" "$raw_config" || {
		echo "failsafe-raw config missing: $symbol" >&2
		exit 1
	}
done
# candidate B must NOT pull the ext4 bootstate writer
if grep -qx 'CONFIG_BOOTCOUNT_EXT=y' "$raw_config"; then
	echo "failsafe-raw must not enable BOOTCOUNT_EXT" >&2
	exit 1
fi

failsafe_env="$output_root/failsafe-raw/u-boot-initial-env"
control_env="$output_root/control/u-boot-initial-env"
grep -q '^bootlimit=1$' "$failsafe_env"
grep -q '^altbootcmd=sysboot mmc 0:1 ext2 ${scriptaddr} /extlinux/extlinux.conf; bootflow scan$' "$failsafe_env"
grep -q '^bootcmd=if test ${upgrade_available} -eq 1 -a ${bootcount_stored} -eq 1; then sysboot mmc 0:1 ext2 ${scriptaddr} /extlinux/extlinux-candidate.conf; fi; bootflow scan$' "$failsafe_env"
if grep -q '^bootcount_file=' "$failsafe_env"; then
	echo "failsafe-raw env must not reference the ext4 bootstate file" >&2
	exit 1
fi
if grep -q '^altbootcmd=' "$control_env"; then
	echo "control env unexpectedly defines altbootcmd" >&2
	exit 1
fi

payload="$output_root/failsafe-raw/u-boot-dtb.bin"
strings "$payload" | grep -F "EA310BS1" >/dev/null
strings "$payload" | grep -F "bootcount-raw: identity mismatch" >/dev/null
strings "$payload" | grep -F "/extlinux/extlinux-candidate.conf" >/dev/null
strings "$payload" | grep -F "trial watchdog started" >/dev/null

audit="$output_root/audit"
mkdir -p "$audit"
diff -u "$control_env" "$failsafe_env" > "$audit/env-diff.txt" || true
diff -u "$output_root/control/.config" "$raw_config" > "$audit/config-diff.txt" || true
stat -c "%n %s" "$output_root/control/u-boot-dtb.bin" \
	"$output_root/failsafe-raw/u-boot-dtb.bin" \
	"$output_root/control/dts/dt.dtb" \
	"$output_root/failsafe-raw/dts/dt.dtb" > "$audit/sizes.txt"
( cd "$output_root" && find control failsafe-raw -type f \
	\( -name "u-boot-dtb.bin" -o -name "u-boot-initial-env" -o -name "dt.dtb" \) -print0 \
	| sort -z | xargs -0 sha256sum ) > "$audit/sha256sums.txt"

printf 'Cross-built EAIDK310 control and failsafe-raw artifacts verified under %s.\n' "$output_root"
cat "$audit/sizes.txt"
