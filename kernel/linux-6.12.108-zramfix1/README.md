# EAIDK-310 Linux 6.12.108 zramfix1 candidate

This directory is an isolated follow-up candidate. Its only functional kernel
change from the rejected `6.12.108-eaidk310` candidate is
`CONFIG_ZRAM_BACKEND_LZ4=y`; the `-zramfix1` localversion and separate build,
stage, initramfs, bundle, and extlinux names prevent overwriting the previous
candidate or its evidence.

This directory contains the maintained inputs and reproducible tooling for the
`6.12.108-eaidk310-zramfix1` rescue-TF test kernel. The approved design is
`../docs/superpowers/specs/2026-09-03-eaidk310-linux-6.12-migration-design.md`,
and the executable plan is
`../docs/superpowers/plans/2026-09-03-eaidk310-linux-6.12-migration.md`.

## Safety boundary

- Build only below `/home/Fog/eaidk310-kernel` in the Ubuntu 24.04 WSL ext4 filesystem.
- Do not build under `/mnt/c`, `/mnt/d`, or `/mnt/g`.
- Do not write `/dev/mmcblk2` or any eMMC boot partition, filesystem, or GPT.
- Do not overwrite rescue-TF `/Image`, `/uInitrd`, or
  `/dtb/rockchip/rk3328-eaidk-310.dtb`.
- Keep `rockchip-kernel-6.8.4` as the extlinux default.
- Rescue-TF deployment is a separate default-dry-run operation and requires
  explicit user authorization bound to the final bundle SHA-256.

## Locked release

- Linux: `6.12.108`
- Local version: `-eaidk310`
- Expected kernel release: `6.12.108-eaidk310-zramfix1`
- Cross compiler prefix: `aarch64-linux-gnu-`
- Maintained board source: `dts/rk3328-eaidk-310.dts`

Exact upstream URLs, archive digest, signer fingerprint, and baseline-file
digests are stored in `source-lock.json`.

## Test commands

Run the kernel-migration tests from the Windows workspace:

```powershell
python -m unittest discover -s kernel-6.12.108/tests -p 'test_*.py' -v
```

Run the pre-existing project regression suite:

```powershell
python -m unittest discover -s tools -p 'test_*.py' -v
```

Long Linux operations will be added as versioned scripts under `scripts/` and
invoked inside WSL. Do not translate those scripts into inline PowerShell/Bash
commands because doing so changes quoting and variable-expansion semantics.

## Reproducible build pipeline

Run the input gate and idempotent board-input installer before compiling:

```powershell
wsl.exe -d Ubuntu-24.04 -- bash /mnt/d/Project/EAIDK310/kernel-6.12.108-zramfix1/scripts/verify-inputs.sh
wsl.exe -d Ubuntu-24.04 -- bash /mnt/d/Project/EAIDK310/kernel-6.12.108-zramfix1/scripts/install-board-inputs.sh
wsl.exe -d Ubuntu-24.04 -- bash /mnt/d/Project/EAIDK310/kernel-6.12.108-zramfix1/scripts/build-kernel.sh
```

The defaults keep all compiler writes in WSL ext4:

```text
source: /home/Fog/eaidk310-kernel/src/linux-6.12.108
build:  /home/Fog/eaidk310-kernel/build-zramfix1
stage:  /home/Fog/eaidk310-kernel/stage-zramfix1
```

`verify-inputs.sh` verifies the compressed archive SHA-256 separately, streams
the uncompressed tar data into GPG, requires the locked `VALIDSIG` fingerprint,
checks all four captured baseline hashes, and writes
`analysis/verify-inputs.json`. It never downloads or replaces an input.

`build-kernel.sh` defaults to an incremental out-of-tree build. It locks the
release to `6.12.108-eaidk310-zramfix1`, reruns the config/DTS gates, builds `Image`,
modules and the EAIDK-310 DTB, runs the targeted DT schema check, stages modules,
runs `depmod`, and verifies the target release with `modinfo -k`. Its output is
captured in `analysis/build-kernel.log`; machine-readable timing and job-count
data are written to `analysis/build-metadata.json`.

The build identity is deterministic: `KBUILD_BUILD_VERSION=1`, user `Fog`, host
`eaidk310-wsl`, and the timestamp derived from the locked source tree's
`SOURCE_DATE_EPOCH`. Task 5 verified eight core output hashes across two
consecutive builds; the evidence is recorded in `snapshots/task-5.sha256.txt`.

Pass `--clean` only when a deliberate clean rebuild is required. The script
resolves and validates the exact build and stage paths below
`/home/Fog/eaidk310-kernel` before removing them. It has no block-device or TF/
eMMC write path.

The build outputs consumed by Task 6 are:

```text
/home/Fog/eaidk310-kernel/build-zramfix1/arch/arm64/boot/Image
/home/Fog/eaidk310-kernel/build-zramfix1/arch/arm64/boot/dts/rockchip/rk3328-eaidk-310.dtb
/home/Fog/eaidk310-kernel/build-zramfix1/System.map
/home/Fog/eaidk310-kernel/build-zramfix1/Module.symvers
/home/Fog/eaidk310-kernel/stage-zramfix1/lib/modules/6.12.108-eaidk310-zramfix1/
```

The three remaining RK3328 HDMI PHY/CRU schema notices are the inherited
upstream diagnostics already reproduced with the Rock64 control DTB; they are
documented in `analysis/dt-schema-report.md`.

## ARM64 initramfs and versioned test bundle

Task 6 builds the initramfs inside a minimal Debian 13 arm64 chroot under WSL.
It never runs the host-amd64 `mkinitramfs` against the target modules. The
builder checks the qemu-aarch64 binfmt registration, validates that the early
MMC/ext4/regulator/pinctrl dependencies are either built in or available as
modules, and reuses the chroot only when its release, architecture and policy
hash still match:

```powershell
wsl.exe -d Ubuntu-24.04 -u root -- bash /mnt/d/Project/EAIDK310/kernel-6.12.108-zramfix1/scripts/build-initramfs-arm64.sh
wsl.exe -d Ubuntu-24.04 -- bash /mnt/d/Project/EAIDK310/kernel-6.12.108-zramfix1/scripts/package-artifacts.sh
wsl.exe -d Ubuntu-24.04 -- bash /mnt/d/Project/EAIDK310/kernel-6.12.108-zramfix1/scripts/verify-bundle.sh
```

The fixed initramfs policy is in `initramfs/`. Early storage and root-filesystem
dependencies are built into this kernel, so `initramfs/modules` intentionally
contains no explicit module request. The generated `/init`, BusyBox, kmod and
modprobe are arm64; the initramfs contains the exact
`/usr/lib/modules/6.12.108-eaidk310-zramfix1` tree required for early userspace.

The final offline archive is:

```text
artifacts/eaidk310-linux-6.12.108-eaidk310-zramfix1.tar.zst
SHA-256: e61ba8aa0f095658c6557be4ee108a7a31709b65dba81629c621571907a53cfa
```

Its extlinux fragment uses only versioned paths and does not replace the
existing 6.8.4 default. Two reproducible package runs produced the same hash;
directory/archive verification reported `BUNDLE_GATE=PASS`, 2809 manifest files and 453 initramfs module
files. Repeating the package operation produced the same archive SHA-256.

`mkinitramfs` reports that `rockchip/dptx.bin` and `regulatory.db` are not in
this minimal early-userspace image. Neither is an early MMC/ext4 dependency:
serial remains the first-boot observation path, and the existing rescue root
filesystem supplies normal runtime firmware. These warnings are recorded and
are not treated as evidence that HDMI or wireless has passed hardware testing.

The bundle now contains the reviewed Task 7 default-dry-run deployer, its
manifest verifier, and the four-file stable baseline lock. A real apply requires
both `--apply` and an exact `--bundle-sha256`; it additionally locks the rescue
TF mount identity, the running 6.8.4 kernel, the stable default label, unchanged
stable hashes, available space, and an unmounted eMMC. Follow
`../docs/runbooks/eaidk310-linux-6.12-test.md`. Task 7 performs no real apply;
the exact authorization in that runbook is a separate Task 8 boundary.

## Final configuration

The reviewed configuration is `config/eaidk310-6.12.108-zramfix1.config`. It was
generated from the live 6.8.4 config with Linux 6.12.108 `olddefconfig`, then
fixed to:

```text
CONFIG_LOCALVERSION="-eaidk310-zramfix1"
# CONFIG_LOCALVERSION_AUTO is not set
```

After changing `.config` with `scripts/config`, run `make ... syncconfig`
before querying `make -s ... kernelrelease`. The `kernelrelease` target is a
no-sync target and otherwise may read a stale `include/config/auto.conf`.

The final config is idempotent under `olddefconfig + syncconfig`; its expected
kernel release is `6.12.108-eaidk310-zramfix1`. The complete 6.8.4-to-6.12.108
classification is in `analysis/config-migration-report.md`; the original
322-line `scripts/diffconfig` output remains unchanged beside it.
