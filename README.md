# EAIDK-310 mainline rescue toolkit

This repository contains the reproducible build inputs, recovery manifests and safety-checked tools used to run Debian on the OpenAILab EAIDK-310 (RK3228H/RK3328 family).

The current validated kernel is **Linux `6.12.108-eaidk310-zramfix1`**. Its release bundle SHA-256 is:

```text
e61ba8aa0f095658c6557be4ee108a7a31709b65dba81629c621571907a53cfa
```

The repository intentionally separates source from recovery binaries:

- Git history contains source locks, configuration, DTS, patches, build scripts, tests and documentation.
- GitHub Release `v2026.09.05` contains the large checked artifacts needed for immediate recovery.
- Linux `6.12.108-eaidk310-zramfix1` is source-rebuildable from the locked kernel.org release.
- Linux `6.8.4-rk3328` is retained as a binary recovery baseline. Its original publisher did not provide a complete source tree proven to reproduce that exact binary.

## Start here

- [Build Linux 6.12.108](docs/build-linux.md)
- [Build U-Boot](docs/build-uboot.md)
- [Recreate the rescue TF](docs/rescue-tf.md)
- [Recover or upgrade eMMC](docs/emmc-recovery.md)
- [Current hardware status](docs/hardware-status.md)

## Repository layout

```text
kernel/       Linux 6.12.108 configuration, DTS, patches and build pipeline
bootloader/   pinned U-Boot source identity, EAIDK-310 patch and build pipeline
rescue/       release-asset manifests and offline verification
tools/        checked serial, image, U-Boot, eMMC and ST7789 utilities
evidence/     sanitized machine-readable acceptance records
docs/         build, recovery and hardware runbooks
```

## Safety boundary

Disk and eMMC tools default to inspection or dry-run where supported. Raw-media writes are destructive. Always verify the exact disk number, hardware serial, capacity, current root device and expected SHA-256 before an apply operation. Never use an EAIDK-310 eMMC as a write target while it is the running root filesystem.

The board's Wi-Fi/Bluetooth module and the external ST7789 panel are still unresolved hardware paths; neither is claimed as working by this repository. Ethernet, SSH, eMMC/TF storage, systemd and 384 MiB LZ4 zram passed both the Linux 6.12.108 rescue-TF run and the final no-TF eMMC boot acceptance. The eMMC boot menu now automatically selects the validated kernel after three seconds.

## Verification

On Ubuntu 24.04 or WSL Ubuntu 24.04:

```bash
make test
make verify
```

On Windows PowerShell 7, also run the PowerShell contract scripts under `tools/` whose names begin with `test-`. U-Boot parser tests that require the 16 MiB release asset are run only after that asset passes its manifest hash gate.

## Provenance and license

The original 6.8.4 recovery image is from [`yjdwbj/rockchip-eaidk-310` release `v1.0`](https://github.com/yjdwbj/rockchip-eaidk-310/releases/tag/v1.0). Linux and U-Boot remain governed by their upstream licenses. Original code and documentation in this repository are provided under GPL-2.0-only; retained upstream files keep their existing notices.
