# Build Linux 6.18.54 for EAIDK-310

The maintained project is `kernel/linux-6.18.54-zramfix1`. It pins Linux `6.18.54` (kernel.org archive SHA-256 and GPG signing fingerprint recorded in `source-lock.json`), the migrated EAIDK-310 configuration (semantic diff audited against 6.12.111) and the board DTS.

Reference pipelines retained:

- `kernel/linux-6.12.111-zramfix1` — previous known-good
- `kernel/linux-6.12.108-zramfix1` — rescue reference

Build on Ubuntu 24.04, preferably inside a WSL2 ext4 filesystem. Do not compile directly under `/mnt/c`, `/mnt/d` or another DrvFS mount. The scripts use `/home/<user>/eaidk310-kernel` for source, build, stage and ARM64 initramfs work.

## Dependencies

```bash
sudo apt-get update
sudo apt-get install --no-install-recommends \
  build-essential bc bison flex libssl-dev libelf-dev libncurses-dev \
  zstd debhelper-compat
```

(aarch64 cross builds use `gcc-aarch64-linux-gnu`; native board builds are not recommended for 6.18.)

## Pipeline

```bash
cd kernel/linux-6.18.54-zramfix1
# 1. fetch + verify pinned source (SHA-256 + GPG)
# 2. install board inputs (reviewed config + DTS registration patch)
# 3. build kernel + modules (config gate enforced)
# 4. build ARM64 initramfs (debootstrap trixie)
# 5. package the OTA bundle (manifest-exhaustive)
# 6. verify-bundle (22 checks) + tests
```

Each step has a dedicated script under `scripts/` with a hard gate; the
order is enforced by `tests/test_build_script_ordering.py` (the `--clean`
step must never run after board inputs are installed).  Deterministic
outputs are pinned via `SOURCE_DATE_EPOCH` and `KBUILD_BUILD_USER/HOST`.

## Tests

`tests/` covers build script ordering, config invariants (board-critical
symbols: ARM64/Rockchip/MMC/DWMAC/zram LZ4/nftables/TUN/IPv6/watchdog),
DTS invariants, bundle artifact checks and script contracts.  Run them
from the pipeline directory with `python3 -m unittest discover tests`.
