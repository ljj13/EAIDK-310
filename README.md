# EAIDK-310 — Mainline Linux & Fail-safe Kernel OTA

| | |
| --- | --- |
| Board | OpenAILab EAIDK-310 · Rockchip RK3328 · 1 GiB · HBD08G 8 GiB eMMC |
| OS | Debian 13 |
| **Stable kernel** | **6.18.54-eaidk310-zramfix1** (extlinux default) |
| Previous known-good | 6.12.111-eaidk310-zramfix1 |
| Rescue | 6.12.108-eaidk310-zramfix1 |
| Bootloader | U-Boot 2024.07-rc1 **failsafe-raw** (raw dual-copy bootstate + trial watchdog) |
| OTA backend | **RAW_REDUNDANT** |
| Hang recovery | DesignWare hardware watchdog (28.6 s) armed on candidate trials only |
| Latest release | **v2026.10.06** |
| Kernel OTA | **PRODUCTION_READY** — remote, unattended |

Latest binaries: [GitHub Release v2026.10.06](https://github.com/ljj13/EAIDK-310/releases/tag/v2026.10.06)

## OTA flow

```text
verify bundle (22 checks, manifest-exhaustive)
  -> stage (atomic, SHA-pinned)
  -> install Image / uInitrd / DTB        (versioned files in /boot)
  -> install /lib/modules/<release>       (atomic tree + modules.dep check)
  -> arm RAW_REDUNDANT                    (bootstate A/B, CRC32, sequence)
  -> reboot
  -> trial boot: hardware watchdog armed, bootcount incremented
  -> health (critical boot + remote/network layers)
  -> commit  (clear trial, promote extlinux default)
```

Failure handling:

```text
candidate panic / hang / userspace failure
  -> hardware watchdog reset (or panic reset)
  -> bootcount exceeds bootlimit
  -> altbootcmd -> previous known-good via its extlinux entry
  -> network + Tailscale come back automatically
```

Rollback state lives in two CRC32-protected 512-byte records on audited
raw eMMC sectors (LBA 0x6400/0x7800) plus a diagnostic breadcrumb sector
(LBA 0x6C00) — no dependency on any filesystem or U-Boot environment.
Every abnormal state fails closed to the stable entry.

## Real-board validation

* normal 6.18 stable boot (unarmed, serial-verified, no trial marker)
* candidate boot path (cmdline trial marker proves sysboot of
  `extlinux-candidate.conf`)
* pre-handoff failure fallback (missing kernel image -> same-boot stable)
* kernel panic rollback (panic + reset -> bootcount exceeds limit ->
  previous known-good)
* true no-feed hang -> DesignWare watchdog reset -> raw rollback
* normal trial NOT killed by the watchdog
* RAW dual-copy rollback across a 4-day power-off window
* Tailscale auto recovery after every reboot cycle
* remote 6.18.54 OTA performed end to end
* /lib/modules persistent install (the first-attempt incident and fix)

**279 automated tests** (OTA framework, bootstate operators, backend
routing, U-Boot patch invariants, sandbox state matrices, kernel
pipeline suites) plus **4 real-board validation classes**.

## Repository layout

```text
kernel/       per-version pipelines: config, DTS, patches, build/verify
              scripts, source locks (6.18.54 = current, 6.12.111 =
              previous known-good, 6.12.108 = rescue reference)
bootloader/   U-Boot v2024.07-rc1 pipeline: source lock, patches
              0001-0004 (board DTS, fail-closed bootcount, raw dual-copy
              bootstate driver, armed-only trial watchdog), audits
custom-src/   human-readable mirror of the project-owned C/DTS sources
              (byte-exact vs the patch stack; see below)
ota/          eaidk-ota (verify/stage/install/arm/health/commit) and
              eaidk-bootstate (raw bootstate operator)
hardware/     board hardware notes (ST7789 display experiment)
tools/        eMMC backup/flash libs, serial capture, U-Boot packaging
tests/        release/publication gates
docs/         build, recovery and status documentation
evidence/     machine-readable acceptance records
rescue/       rescue asset verification (legacy 6.8.4 baseline is
              historical, not a current rescue path)
```

Historical releases and per-release metadata live on
[GitHub Releases](https://github.com/ljj13/EAIDK-310/releases)
(see [docs/releases.md](docs/releases.md)).

## Custom board source

This repository deliberately does **not** vendor the full upstream
Linux/U-Boot source.  Upstream trees are pinned by `source-lock.json`
and applied as a patch stack; this keeps upstream code separate from
EAIDK-310-specific work while making the board-specific implementation
directly reviewable in [`custom-src/`](custom-src/README.md):

* U-Boot raw dual-copy bootstate driver:
  [`bootcount_eaidk310_raw.c`](custom-src/u-boot/drivers/bootcount/bootcount_eaidk310_raw.c)
* U-Boot EAIDK-310 board DTS/defconfigs: [`custom-src/u-boot/`](custom-src/u-boot/)
* Linux board device tree:
  [`rk3328-eaidk-310.dts`](custom-src/linux/arch/arm64/boot/dts/rockchip/rk3328-eaidk-310.dts)

Every mirrored file is byte-identical to the content produced by applying
the production patch stack to the pinned upstream commit;
`tools/verify_custom_source.py --check` (part of `make verify`) enforces
this, and [docs/CUSTOM-SOURCE-INVENTORY.md](docs/CUSTOM-SOURCE-INVENTORY.md)
classifies every file the patches touch.  Files this project only modifies
upstream (Kconfig, Makefiles, watchdog glue) stay patch-only and are listed
in the inventory.

## Quick start

* Build the current kernel: [kernel/linux-6.18.54-zramfix1/](kernel/linux-6.18.54-zramfix1/)
* OTA framework and runbooks: [ota/README.md](ota/README.md)
* U-Boot failsafe variants and patch stack: [bootloader/u-boot-eaidk310/](bootloader/u-boot-eaidk310/)
* Recovery models: [docs/emmc-recovery.md](docs/emmc-recovery.md),
  [docs/rescue-tf.md](docs/rescue-tf.md)

## Safety boundary

Unattended **kernel** OTA is enabled.  It never touches the bootloader
chain.  U-Boot, idbloader, BL31/ATF, the GPT and the raw bootstate
layout ABI are separate high-risk infrastructure: changing them requires
an independent review, on-site serial access and a written
backup/recovery procedure (see ota/docs/FLASH-PLAN-P36.md).

## Hardware status (validated on 6.18.54)

Working: eMMC, Ethernet, USB, RTC (via network time), thermal, cpufreq,
zram (LZ4), nftables, hardware watchdog, SSH/Tailscale.
Unresolved: onboard Wi-Fi (SDIO power-sequencing experiment did not
restore it), Bluetooth, ST7789 display panel.

## Incident note

The first remote 6.18 OTA attempt went offline because the OTA installer
omitted `/lib/modules/<release>`.  The Linux 6.18 kernel, DTB and
initramfs were **not** the cause; `install-candidate` now installs the
module tree atomically as part of every OTA.  Full evidence: the
`release-evidence` asset of release v2026.10.06.
