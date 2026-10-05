# Build U-Boot failsafe-raw for EAIDK-310

Source: upstream `v2024.07-rc1` pinned at `38ea74d6d5c05224acdb03f799897c1bdd56f8cc` (`bootloader/u-boot-eaidk310/source-lock.json`).

## Patch stack (applied in order by the build scripts)

1. `0001-arm-dts-add-eaidk310-variants.patch` — board DTS (control + sdio-handoff)
2. `0002-failsafe-bootcount-fs.patch` — fail-closed `BOOTCOUNT_EXT` + compiled boot policy (candidate A backend, built, not installed)
3. `0003-failsafe-raw-bootstate.patch` — `bootcount_eaidk310_raw` dual-copy driver, raw sectors LBA `0x6400`/`0x7800`, identity gate
4. `0004-trial-watchdog.patch` — armed-only hardware watchdog start (DesignWare, 30 s TOP), clock fallback

## Build

```bash
git clone https://github.com/u-boot/u-boot.git ~/src/u-boot-v2024.07-rc1
git -C ~/src/u-boot-v2024.07-rc1 checkout --detach 38ea74d6d5c05224acdb03f799897c1bdd56f8cc
bash bootloader/u-boot-eaidk310/scripts/verify-source-and-patch.sh ~/src/u-boot-v2024.07-rc1
bash bootloader/u-boot-eaidk310/scripts/build-cross-failsafe-raw.sh \
  ~/src/u-boot-v2024.07-rc1 ~/build/eaidk310-uboot-failsafe
```

The script builds a control reference and the `failsafe-raw` variant, enforces the payload size gate, byte-compares DTBs, asserts the compiled default env policy and the watchdog/bootcount config symbols, and writes a full audit (`config-diff`, `env-diff`, `sha256sums`) into the output directory.  Two independent builds are byte-identical (`SOURCE_DATE_EPOCH` pinned).

## Deployment format

The board's U-Boot proper region (eMMC offset **8 MiB**, 4 MiB window) uses the Rockchip `LOADER` redundant format: four byte-identical 1 MiB slots.  Pack the payload with `tools/rockchip_loaderimage.py`
(`build_redundant_uboot_image`) and verify the composed 16 MiB prefix with `compose_prefix` before flashing; flash only the 8–12 MiB window and readback-`cmp` before any reboot.

The raw bootstate sectors this U-Boot reads/writes are documented in
[bootloader/u-boot-eaidk310/docs/EMMC-RAW-REGION.md](../bootloader/u-boot-eaidk310/docs/EMMC-RAW-REGION.md).
