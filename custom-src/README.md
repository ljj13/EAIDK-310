# EAIDK-310 Custom Source

This directory exposes the project-owned source files that are otherwise
carried as patches against pinned upstream Linux/U-Boot trees.

Three facts govern this directory:

1. **The full upstream Linux and U-Boot source is NOT vendored in this
   repository.** Upstream trees are pinned by `source-lock.json`
   (U-Boot `v2024.07-rc1 @ 38ea74d6`, Linux 6.18.54 via kernel.org
   archive hash) and never committed here.
2. **Production builds still use the patch-stack model only:**
   `source-lock.json` + `patches/0001..000N` + build scripts
   (`bootloader/u-boot-eaidk310/`, `kernel/linux-6.18.54-zramfix1/`).
   Nothing in `custom-src/` is a build input.
3. **`custom-src/` is a human-readable, byte-exact mirror** of the files
   this project owns — not a second implementation. Every file is
   byte-for-byte identical to the content produced by applying the
   production patch stack to the pinned upstream commit, and
   `tools/verify_custom_source.py --check` enforces this.

## Project-owned sources

| Mirror | Source patch | Role |
|---|---|---|
| [`u-boot/drivers/bootcount/bootcount_eaidk310_raw.c`](u-boot/drivers/bootcount/bootcount_eaidk310_raw.c) | [`0003-failsafe-raw-bootstate.patch`](../bootloader/u-boot-eaidk310/patches/0003-failsafe-raw-bootstate.patch) (+ `0004-trial-watchdog.patch`) | production — RAW_REDUNDANT boot-state backend |
| [`u-boot/arch/arm/dts/rk3328-eaidk310-common.dtsi`](u-boot/arch/arm/dts/rk3328-eaidk310-common.dtsi) | [`0001-arm-dts-add-eaidk310-variants.patch`](../bootloader/u-boot-eaidk310/patches/0001-arm-dts-add-eaidk310-variants.patch) | production |
| [`u-boot/arch/arm/dts/rk3328-eaidk310-control.dts`](u-boot/arch/arm/dts/rk3328-eaidk310-control.dts) | `0001-arm-dts-add-eaidk310-variants.patch` | production (base variant, default FDT of the failsafe build) |
| [`u-boot/arch/arm/dts/rk3328-eaidk310-control-u-boot.dtsi`](u-boot/arch/arm/dts/rk3328-eaidk310-control-u-boot.dtsi) | `0001-arm-dts-add-eaidk310-variants.patch` | production |
| [`u-boot/arch/arm/dts/rk3328-eaidk310-sdio-handoff.dts`](u-boot/arch/arm/dts/rk3328-eaidk310-sdio-handoff.dts) | `0001-arm-dts-add-eaidk310-variants.patch` | experimental (Wi-Fi SDIO handoff variant) |
| [`u-boot/arch/arm/dts/rk3328-eaidk310-sdio-handoff-u-boot.dtsi`](u-boot/arch/arm/dts/rk3328-eaidk310-sdio-handoff-u-boot.dtsi) | `0001-arm-dts-add-eaidk310-variants.patch` | experimental |
| [`u-boot/configs/eaidk310-control-rk3328_defconfig`](u-boot/configs/eaidk310-control-rk3328_defconfig) | `0001-arm-dts-add-eaidk310-variants.patch` | production (base variant) |
| [`u-boot/configs/eaidk310-sdio-handoff-rk3328_defconfig`](u-boot/configs/eaidk310-sdio-handoff-rk3328_defconfig) | `0001-arm-dts-add-eaidk310-variants.patch` | experimental |
| [`u-boot/configs/eaidk310-failsafe-raw-rk3328_defconfig`](u-boot/configs/eaidk310-failsafe-raw-rk3328_defconfig) | `0003-failsafe-raw-bootstate.patch` (+ `0004-trial-watchdog.patch`) | production — failsafe boot entry point |
| [`u-boot/configs/eaidk310-failsafe-fs-rk3328_defconfig`](u-boot/configs/eaidk310-failsafe-fs-rk3328_defconfig) | [`0002-failsafe-bootcount-fs.patch`](../bootloader/u-boot-eaidk310/patches/0002-failsafe-bootcount-fs.patch) | **ALTERNATIVE / NOT PRODUCTION BACKEND** |
| [`linux/arch/arm64/boot/dts/rockchip/rk3328-eaidk-310.dts`](linux/arch/arm64/boot/dts/rockchip/rk3328-eaidk-310.dts) | maintained directly as the build input [`kernel/linux-6.18.54-zramfix1/dts/rk3328-eaidk-310.dts`](../kernel/linux-6.18.54-zramfix1/dts/rk3328-eaidk-310.dts) | production — Linux board device tree |

> **ALTERNATIVE / NOT PRODUCTION BACKEND**: `eaidk310-failsafe-fs-*` and the
> `0002` patch implement the BOOTCOUNT_EXT (ext4 file) boot-state backend
> (candidate A). It is kept for comparison only. The production backend is
> **RAW_REDUNDANT** (`bootcount_eaidk310_raw.c`, candidate B) — see
> `bootloader/u-boot-eaidk310/docs/BACKEND-COMPARISON.md`.

## Patch-only modifications

These changes exist only as patch hunks against upstream files that this
project does not own; the full upstream files are deliberately **not**
mirrored here. Read them in the patches:

| Patch | Upstream file | Purpose |
|---|---|---|
| `0001` | `arch/arm/dts/Makefile` | register the EAIDK-310 U-Boot DTB variants |
| `0002` | `drivers/bootcount/bootcount_ext.c` | fail-closed hardening of the ALTERNATIVE ext4 bootcount backend |
| `0002`+`0003`+`0004` | `include/configs/rk3328_common.h` | boot-state backend selection glue (BOOTCOUNT_EXT / EAIDK310_RAW) |
| `0003` | `drivers/bootcount/Kconfig` | `BOOTCOUNT_EAIDK310_RAW` driver option + raw boot-state config |
| `0003` | `drivers/bootcount/Makefile` | build `bootcount_eaidk310_raw.o` |
| `0004` | `drivers/bootcount/Kconfig` | `SYS_BOOTCOUNT_TRIAL_WDT_TIMEOUT_MS` trial-watchdog option |
| `0004` | `drivers/watchdog/designware_wdt.c` | prefer the DT clock, fall back to `CFG_DW_WDT_CLOCK_KHZ`, so the trial watchdog works when the SoC clock driver cannot enable it |
| Linux `0001-arm64-dts-rockchip-register-eaidk310.patch` | `Documentation/devicetree/bindings/arm/rockchip.yaml` | register the `openailab,eaidk-310` root compatible |
| Linux `0001-arm64-dts-rockchip-register-eaidk310.patch` | `arch/arm64/boot/dts/rockchip/Makefile` | register the `rk3328-eaidk-310.dtb` build target |

The Linux DTS is not carried inside a patch: it is maintained directly as
the build input `kernel/linux-6.18.54-zramfix1/dts/rk3328-eaidk-310.dts`
(identical across the 6.12.108 / 6.12.111 / 6.18.54 trees), copied into the
tree by `install-board-inputs.sh`, and registered by the patch above. This
directory mirrors it for review; tier-1 verification enforces byte equality
in both directions.

## License / provenance

All files in this directory are distributed under the repository's
GPL-2.0-only license policy. U-Boot DTS files carry their original
`(GPL-2.0+ OR MIT)` SPDX headers and copyright notices, which are preserved
byte-for-byte from the patches. The Linux board DTS is derived from the
vendor device tree reconstruction documented in
`kernel/linux-6.18.54-zramfix1/baseline/` and keeps its original copyright
and attribution headers. No copyright notices were rewritten.

## Verification

```bash
python tools/verify_custom_source.py --check   # tier 1 always; tier 2 when upstream is materialized
python tools/verify_custom_source.py --sync    # maintainer only: re-extract mirrors, refresh hashes
```

`--sync` may clone the pinned upstream U-Boot tag into an external cache
(`~/.cache/eaidk310/upstream/`, override with `EAIDK310_UBOOT_SRC`) and is
never invoked by `make`/CI. `manifest.json` records the upstream path,
source patch, role and sha256 of every mirror.
