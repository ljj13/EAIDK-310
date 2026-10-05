# Vendor asset inventory — 2026-10-06 (P6 PHASE 12)

Full scan of the local `vendor/` tree and other vendor-origin locations.

## Findings

`vendor/` contained **no proprietary vendor blobs**.  Its entire content
was one shallow U-Boot v2024.07-rc1 clone (287 MiB) plus a `git bundle`
(40 MiB) — pinned upstream open-source code, recorded in
[vendor/README.md](../vendor/README.md) and
[vendor/provenance/u-boot-v2024.07-rc1.md](../vendor/provenance/u-boot-v2024.07-rc1.md).

| local file | size | category | disposition |
| --- | --- | --- | --- |
| `vendor/u-boot-v2024.07-rc1/` | 287 MiB | B (open source, upstream-recoverable) | deleted locally (2026-10-06); recover from upstream at the pin |
| `vendor/u-boot-v2024.07-rc1.bundle` | 40 MiB | D (duplicate of upstream pack) | deleted locally |

Vendor-origin binaries elsewhere (factory 6.8.4 image, full eMMC dump)
are inventoried in
[LOCAL-LARGE-ASSET-AUDIT.md](LOCAL-LARGE-ASSET-AUDIT.md) with their
GitHub-archive status; user authorized local deletion after the audit.

## Knowledge extraction

All vendor-derived knowledge the project depends on is already committed
to main:

* eMMC / Rockchip loader layout: `bootloader/u-boot-eaidk310/docs/EMMC-RAW-REGION.md`
* board DTS + pin map: `kernel/*/dts/`, patch 0001
* TF prefix packaging format: `tools/rockchip_loaderimage.py` +
  `tools/prepare_eaidk310_uboot.py` (format reverse-engineered from the
  vendor image, verified against the live board)
* 6.8.4 binary provenance: v2026.09.05 GitHub Release (hashes recorded)
