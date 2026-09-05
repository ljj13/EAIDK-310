#!/usr/bin/env bash
set -euo pipefail

readonly script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
readonly project_dir="$(dirname -- "$script_dir")"
readonly patch_path="$project_dir/patches/0001-arm-dts-add-eaidk310-variants.patch"

if [ "$#" -ne 2 ]; then
	echo "usage: $0 SOURCE_DIR OUTPUT_DIR" >&2
	exit 2
fi

source_dir="$(CDPATH= cd -- "$1" && pwd)"
mkdir -p "$2"
output_root="$(CDPATH= cd -- "$2" && pwd)"

if [ -e "$output_root/control" ] || [ -e "$output_root/sdio-handoff" ]; then
	echo "output variant directories already exist: $output_root" >&2
	exit 1
fi

bash "$script_dir/verify-source-and-patch.sh" "$source_dir"
for command in make aarch64-linux-gnu-gcc strings stat dtc fdtget; do
	command -v "$command" >/dev/null
done

work_root="$(mktemp -d "${TMPDIR:-/tmp}/eaidk310-uboot-cross-$(date -u +%Y%m%dT%H%M%SZ)-XXXXXX")"
trap 'test -n "${work_root:-}" && rm -rf -- "$work_root"' EXIT
work_source="$work_root/source"
mkdir -p "$work_source"
cp -a "$source_dir/." "$work_source/"
git -C "$work_source" apply "$patch_path"

make -C "$work_source" O="$output_root/control" CROSS_COMPILE=aarch64-linux-gnu- eaidk310-control-rk3328_defconfig
make -C "$work_source" O="$output_root/control" CROSS_COMPILE=aarch64-linux-gnu- -j2 u-boot-dtb.bin
make -C "$work_source" O="$output_root/sdio-handoff" CROSS_COMPILE=aarch64-linux-gnu- eaidk310-sdio-handoff-rk3328_defconfig
make -C "$work_source" O="$output_root/sdio-handoff" CROSS_COMPILE=aarch64-linux-gnu- -j2 u-boot-dtb.bin

verify_variant() {
	variant="$1"
	expected_status="$2"
	payload="$output_root/$variant/u-boot-dtb.bin"
	dtb="$output_root/$variant/dts/dt.dtb"
	live_dts="$output_root/$variant/live.dts"

	test -s "$payload"
	test -s "$dtb"
	size="$(stat -c %s "$payload")"
	test "$size" -le 1046528
	strings "$payload" | grep -F "Rockchip RK3328 EAIDK310" >/dev/null
	dtc -I dtb -O dts "$dtb" > "$live_dts"
	test "$(fdtget "$dtb" "/mmc@ff510000" status)" = "$expected_status"
}

verify_variant control disabled
if fdtget "$output_root/control/dts/dt.dtb" "/mmc@ff510000" mmc-pwrseq >/dev/null 2>&1; then
	echo "control DT unexpectedly contains mmc-pwrseq" >&2
	exit 1
fi

verify_variant sdio-handoff okay
test "$(fdtget -t u "$output_root/sdio-handoff/dts/dt.dtb" "/mmc@ff510000" max-frequency)" = "125000000"
fdtget -t x "$output_root/sdio-handoff/dts/dt.dtb" "/mmc@ff510000" mmc-pwrseq >/dev/null
read -r reset_phandle reset_pin reset_flags <<EOF
$(fdtget -t x "$output_root/sdio-handoff/dts/dt.dtb" "/sdio-pwrseq" reset-gpios)
EOF
test -n "$reset_phandle"
test "$reset_pin" = "12"
test "$reset_flags" = "1"

printf 'Cross-built EAIDK310 control and SDIO-handoff artifacts verified under %s.\n' "$output_root"
