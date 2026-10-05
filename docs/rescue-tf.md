# Rescue strategy

## Logical rescue kernel (current)

6.12.108-eaidk310-zramfix1 is the designated **rescue** kernel: it stays
installed as the last extlinux entry on eMMC and is never removed.  It
is reached manually (serial boot-menu selection, or after the stable and
previous-known-good entries both fail) — not by normal OTA rollbacks,
which target the previous known-good (6.12.111).

A prepared rescue TF card is an independent boot path: U-Boot's
`boot_targets=mmc1 mmc0 …` scans the SD before eMMC, so a card with a
bootable prefix boots without touching eMMC.  The card image is not yet
produced by a fully automated flow — build one by writing the failsafe
U-Boot prefix (tools in `tools/`) plus a rootfs of your choice, and
record its SHA-256.  Do not assume an unverified "one-click rescue
image".

## Bootloader-level failure (on-site only)

If eMMC cannot boot at all: rescue SD (above), serial console
(ttyS2 1500000 8N1) or Maskrom USB recovery.  Region map, checklists and
the recovery matrix: [ota/docs/FLASH-PLAN-P36.md](../ota/docs/FLASH-PLAN-P36.md).

## Historical baseline

The 6.8.4 factory image write-card flow is retired from the current
rescue model.  The binary remains archived on the
[v2026.09.05 GitHub Release](https://github.com/ljj13/EAIDK-310/releases/tag/v2026.09.05)
for provenance; extracted vendor layout facts live in
[vendor/README.md](../vendor/README.md).
