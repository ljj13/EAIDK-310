# Custom Source Inventory

P6.1 audit of every file touched by the production patch stacks, classified
by ownership. This table is the review-facing counterpart of
`custom-src/manifest.json` (machine-checked by `tools/verify_custom_source.py`).

Kinds:

- **A** — FULL PROJECT-ADDED FILE: upstream does not contain it; the patch
  adds it (`new file mode`). Mirrored byte-exactly in `custom-src/`.
- **B** — PROJECT-OWNED DTS: board device tree maintained directly by this
  project (DTB-reconstruction provenance), mirrored in `custom-src/`.
- **C** — MODIFICATION TO AN EXISTING UPSTREAM FILE: stays patch-only; the
  upstream file is never mirrored.
- **D** — GENERATED / CONFIG / METADATA: stays in `config/`, `source-lock/`,
  `patches/`, `docs/`; never mirrored in `custom-src/`.

Canonical presentation: **Linux 6.18.54 (STABLE)**; the 6.12.108 /
6.12.111 trees carry byte-identical patches and board DTS (verified by
`tools/verify_custom_source.py`).

## U-Boot v2024.07-rc1 (`bootloader/u-boot-eaidk310/`)

| Patch | Upstream path | Kind | Project-owned full file? | Mirror in custom-src? | Canonical source of truth | Notes |
|---|---|---|---|---|---|---|
| 0001 | `arch/arm/dts/rk3328-eaidk310-common.dtsi` | A | yes | yes | patch body (tier-1 + tier-2 verified) | shared board include |
| 0001 | `arch/arm/dts/rk3328-eaidk310-control.dts` | A | yes | yes | patch body | control variant root |
| 0001 | `arch/arm/dts/rk3328-eaidk310-control-u-boot.dtsi` | A | yes | yes | patch body | U-Boot pre-reloc nodes |
| 0001 | `arch/arm/dts/rk3328-eaidk310-sdio-handoff.dts` | A | yes | yes | patch body | experimental Wi-Fi handoff variant |
| 0001 | `arch/arm/dts/rk3328-eaidk310-sdio-handoff-u-boot.dtsi` | A | yes | yes | patch body | experimental |
| 0001 | `configs/eaidk310-control-rk3328_defconfig` | A | yes | yes | patch body | base variant |
| 0001 | `configs/eaidk310-sdio-handoff-rk3328_defconfig` | A | yes | yes | patch body | experimental |
| 0001 | `arch/arm/dts/Makefile` | C | no | no | patch hunk | registers the two DTB variants |
| 0002 | `configs/eaidk310-failsafe-fs-rk3328_defconfig` | A | yes | yes | patch body | ALTERNATIVE / NOT PRODUCTION BACKEND |
| 0002 | `drivers/bootcount/bootcount_ext.c` | C | no | no | patch hunk | fail-closed hardening of the alternative ext4 backend |
| 0002 | `include/configs/rk3328_common.h` | C | no | no | patch hunk | BOOTCOUNT_EXT env glue |
| 0003 | `configs/eaidk310-failsafe-raw-rk3328_defconfig` | A | yes | yes | patched tree (0003 + 0004) | production failsafe entry point |
| 0003 | `drivers/bootcount/bootcount_eaidk310_raw.c` | A | yes | yes | patched tree (0003 + 0004) | RAW_REDUNDANT boot-state driver (production backend) |
| 0003 | `drivers/bootcount/Kconfig` | C | no | no | patch hunk | `BOOTCOUNT_EAIDK310_RAW` + raw config options |
| 0003 | `drivers/bootcount/Makefile` | C | no | no | patch hunk | builds the raw driver |
| 0003 | `include/configs/rk3328_common.h` | C | no | no | patch hunk | EAIDK310_RAW env glue |
| 0004 | `configs/eaidk310-failsafe-raw-rk3328_defconfig` | C | yes (own file) | yes (same mirror as 0003) | patched tree | enables DesignWare watchdog for armed trials |
| 0004 | `drivers/bootcount/Kconfig` | C | no | no | patch hunk | `SYS_BOOTCOUNT_TRIAL_WDT_TIMEOUT_MS` |
| 0004 | `drivers/bootcount/bootcount_eaidk310_raw.c` | C | yes (own file) | yes (same mirror as 0003) | patched tree | starts the trial watchdog for armed candidate boots only |
| 0004 | `drivers/watchdog/designware_wdt.c` | C | no | no | patch hunk | prefer DT clock with `CFG_DW_WDT_CLOCK_KHZ` fallback |
| 0004 | `include/configs/rk3328_common.h` | C | no | no | patch hunk | trial-watchdog env glue |

Mirrored U-Boot files (byte-exact, enforced):

- custom-src/u-boot/arch/arm/dts/rk3328-eaidk310-common.dtsi
- custom-src/u-boot/arch/arm/dts/rk3328-eaidk310-control.dts
- custom-src/u-boot/arch/arm/dts/rk3328-eaidk310-control-u-boot.dtsi
- custom-src/u-boot/arch/arm/dts/rk3328-eaidk310-sdio-handoff.dts
- custom-src/u-boot/arch/arm/dts/rk3328-eaidk310-sdio-handoff-u-boot.dtsi
- custom-src/u-boot/configs/eaidk310-control-rk3328_defconfig
- custom-src/u-boot/configs/eaidk310-sdio-handoff-rk3328_defconfig
- custom-src/u-boot/configs/eaidk310-failsafe-fs-rk3328_defconfig
- custom-src/u-boot/configs/eaidk310-failsafe-raw-rk3328_defconfig
- custom-src/u-boot/drivers/bootcount/bootcount_eaidk310_raw.c

## Linux 6.18.54 (`kernel/linux-6.18.54-zramfix1/`, canonical)

| Patch | Upstream path | Kind | Project-owned full file? | Mirror in custom-src? | Canonical source of truth | Notes |
|---|---|---|---|---|---|---|
| — (build input) | `arch/arm64/boot/dts/rockchip/rk3328-eaidk-310.dts` | B | yes | yes | `kernel/linux-6.18.54-zramfix1/dts/rk3328-eaidk-310.dts` (identical in all three trees) | copied into the tree by `scripts/install-board-inputs.sh`; DTB-reconstruction provenance in `baseline/` |
| 0001 | `Documentation/devicetree/bindings/arm/rockchip.yaml` | C | no | no | patch hunk | registers `openailab,eaidk-310` compatible |
| 0001 | `arch/arm64/boot/dts/rockchip/Makefile` | C | no | no | patch hunk | registers `rk3328-eaidk-310.dtb` |
| — | `config/`, `source-lock.json`, `initramfs/`, `analysis/`, `artifacts/` | D | n/a | no | repo files | build config, evidence, metadata — not source mirrors |

Mirrored Linux file (byte-exact, enforced against every kernel tree):

- custom-src/linux/arch/arm64/boot/dts/rockchip/rk3328-eaidk-310.dts

The 6.12.108 and 6.12.111 trees carry a byte-identical `0001` patch and the
same board DTS; they are previous-known-good / rescue references, so they are
not separately inventoried. Full upstream Linux/U-Boot sources are pinned by
`source-lock.json` and are intentionally never vendored.
