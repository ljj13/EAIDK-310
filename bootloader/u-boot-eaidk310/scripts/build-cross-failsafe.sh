#!/usr/bin/env bash
set -euo pipefail

# P3.6 candidate A builder: control reference + failsafe-bootcount-fs variant.
#
# Safety model: builds only read the pinned source and write ordinary files
# under OUTPUT_DIR.  No block device access, no dd, no eMMC writes.
# The existing control/sdio-handoff artifacts and patch 0001 stay untouched;
# this script applies 0001 + 0002 to a private mktemp copy of the source.

readonly script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
readonly project_dir="$(dirname -- "$script_dir")"
readonly patch_0001="$project_dir/patches/0001-arm-dts-add-eaidk310-variants.patch"
readonly patch_0002="$project_dir/patches/0002-failsafe-bootcount-fs.patch"

if [ "$#" -ne 2 ]; then
	echo "usage: $0 SOURCE_DIR OUTPUT_DIR" >&2
	exit 2
fi

source_dir="$(CDPATH= cd -- "$1" && pwd)"
mkdir -p "$2"
output_root="$(CDPATH= cd -- "$2" && pwd)"

if [ -e "$output_root/control" ] || [ -e "$output_root/failsafe-bootcount-fs" ]; then
	echo "output variant directories already exist: $output_root" >&2
	exit 1
fi

bash "$script_dir/verify-source-and-patch.sh" "$source_dir"
git -C "$source_dir" apply --check "$patch_0002"
for command in make aarch64-linux-gnu-gcc strings stat dtc fdtget sha256sum; do
	command -v "$command" >/dev/null
done

# Deterministic build inputs (same policy as the kernel pipeline).
export SOURCE_DATE_EPOCH="${SOURCE_DATE_EPOCH:-1711843200}"
export KBUILD_BUILD_USER=builder
export KBUILD_BUILD_HOST=build

work_root="$(mktemp -d "${TMPDIR:-/tmp}/eaidk310-uboot-failsafe-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
trap 'test -n "${work_root:-}" && rm -rf -- "$work_root"' EXIT
work_source="$work_root/source"
mkdir -p "$work_source"
cp -a "$source_dir/." "$work_source/"
git -C "$work_source" apply "$patch_0001"
git -C "$work_source" apply "$patch_0002"

make -C "$work_source" O="$output_root/control" CROSS_COMPILE=aarch64-linux-gnu- eaidk310-control-rk3328_defconfig
make -C "$work_source" O="$output_root/control" CROSS_COMPILE=aarch64-linux-gnu- -j2 u-boot-dtb.bin u-boot-initial-env
make -C "$work_source" O="$output_root/failsafe-bootcount-fs" CROSS_COMPILE=aarch64-linux-gnu- eaidk310-failsafe-fs-rk3328_defconfig
make -C "$work_source" O="$output_root/failsafe-bootcount-fs" CROSS_COMPILE=aarch64-linux-gnu- -j2 u-boot-dtb.bin u-boot-initial-env

verify_variant() {
	variant="$1"
	payload="$output_root/$variant/u-boot-dtb.bin"
	dtb="$output_root/$variant/dts/dt.dtb"
	live_dts="$output_root/$variant/live.dts"

	test -s "$payload"
	test -s "$dtb"
	size="$(stat -c %s "$payload")"
	test "$size" -le 1046528
	strings "$payload" | grep -F "Rockchip RK3328 EAIDK310" >/dev/null
	dtc -I dtb -O dts "$dtb" > "$live_dts"
	test "$(fdtget "$dtb" "/mmc@ff510000" status)" = "disabled"
	if fdtget "$dtb" "/mmc@ff510000" mmc-pwrseq >/dev/null 2>&1; then
		echo "DT unexpectedly contains mmc-pwrseq" >&2
		exit 1
	fi
}

verify_variant control
verify_variant failsafe-bootcount-fs

# The failsafe variant must not change the board description: the control
# and failsafe device trees have to be byte-identical.
cmp "$output_root/control/dts/dt.dtb" "$output_root/failsafe-bootcount-fs/dts/dt.dtb"

failsafe_config="$output_root/failsafe-bootcount-fs/.config"
for symbol in \
	"CONFIG_BOOTCOUNT_LIMIT=y" \
	"CONFIG_BOOTCOUNT_EXT=y" \
	'CONFIG_SYS_BOOTCOUNT_EXT_INTERFACE="mmc"' \
	'CONFIG_SYS_BOOTCOUNT_EXT_DEVPART="0:1"' \
	'CONFIG_SYS_BOOTCOUNT_EXT_NAME="/eaidk-ota/bootcount.bin"' \
	"CONFIG_SYS_BOOTCOUNT_ADDR=0x00300000" \
	"CONFIG_BOOTCOUNT_BOOTLIMIT=1" \
	"CONFIG_CMD_SYSBOOT=y" \
	"CONFIG_FS_EXT4=y" \
	"CONFIG_EXT4_WRITE=y" \
	"CONFIG_HUSH_PARSER=y"; do
	grep -qx "$symbol" "$failsafe_config" || {
		echo "failsafe config missing: $symbol" >&2
		exit 1
	}
done

# Compiled default env policy: present in failsafe, absent from control.
failsafe_env="$output_root/failsafe-bootcount-fs/u-boot-initial-env"
control_env="$output_root/control/u-boot-initial-env"
grep -q '^bootlimit=1$' "$failsafe_env"
grep -q '^bootcount_file=/eaidk-ota/bootcount.bin$' "$failsafe_env"
grep -q '^altbootcmd=sysboot mmc 0:1 ext2 ${scriptaddr} /extlinux/extlinux.conf; bootflow scan$' "$failsafe_env"
grep -q '^bootcmd=if test ${upgrade_available} -eq 1 -a ${bootcount_stored} -eq 1; then sysboot mmc 0:1 ext2 ${scriptaddr} /extlinux/extlinux-candidate.conf; fi; bootflow scan$' "$failsafe_env"
if grep -q '^altbootcmd=' "$control_env"; then
	echo "control env unexpectedly defines altbootcmd" >&2
	exit 1
fi
if grep -q '^bootcount_file=' "$control_env"; then
	echo "control env unexpectedly defines bootcount_file" >&2
	exit 1
fi

# The payload itself must carry the policy strings.
payload="$output_root/failsafe-bootcount-fs/u-boot-dtb.bin"
strings "$payload" | grep -F "/extlinux/extlinux-candidate.conf" >/dev/null
strings "$payload" | grep -F "/eaidk-ota/bootcount.bin" >/dev/null
strings "$payload" | grep -F "Incorrect bootcount file" >/dev/null

audit="$output_root/audit"
mkdir -p "$audit"
diff -u "$control_env" "$failsafe_env" > "$audit/env-diff.txt" || true
diff -u "$output_root/control/.config" "$failsafe_config" > "$audit/config-diff.txt" || true
stat -c "%n %s" "$output_root/control/u-boot-dtb.bin" \
	"$output_root/failsafe-bootcount-fs/u-boot-dtb.bin" \
	"$output_root/control/dts/dt.dtb" \
	"$output_root/failsafe-bootcount-fs/dts/dt.dtb" > "$audit/sizes.txt"
( cd "$output_root" && find control failsafe-bootcount-fs -type f \
	\( -name "u-boot-dtb.bin" -o -name "u-boot-initial-env" -o -name "dt.dtb" \) -print0 \
	| sort -z | xargs -0 sha256sum ) > "$audit/sha256sums.txt"

printf 'Cross-built EAIDK310 control and failsafe-bootcount-fs artifacts verified under %s.\n' "$output_root"
cat "$audit/sizes.txt"
