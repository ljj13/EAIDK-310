# Releases

The single source of truth for releases is
[GitHub Releases](https://github.com/ljj13/EAIDK-310/releases).  This
repository no longer tracks per-release metadata directories.

## Current

**v2026.10.07** — Linux 6.18.55-eaidk310-zramfix1 stable
(promoted after true-hang watchdog rollback validation; candidate-1's
trial failure was trial-infrastructure, not a kernel regression — see
evidence/current/linux-6.18.55-promotion.json).  Assets: kernel OTA
bundle, manifests, sanitized evidence.

**v2026.10.06** — Linux 6.18.54-eaidk310-zramfix1 stable + fail-safe
kernel OTA infrastructure (failsafe-raw U-Boot, raw dual-copy bootstate,
trial watchdog, verified OTA tooling).  Assets: kernel OTA bundle,
flash-ready U-Boot region image, bare payload, build metadata, sanitized
release evidence.

## Historical

**v2026.09.05** — Linux 6.12.108-eaidk310-zramfix1 rescue toolkit
(includes the 6.8.4 factory image as a binary recovery baseline).

All binaries and evidence for both releases remain on their respective
GitHub Release pages; they are preserved unmodified.
