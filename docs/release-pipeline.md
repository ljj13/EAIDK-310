# Release pipeline

P7 turns "one clean clone + one version number" into a verifiable release
candidate.  The engine is [`tools/release-kernel.py`](../tools/release-kernel.py)
(`tools/release-kernel.sh` is a thin wrapper).

## Architecture

```
./tools/release-kernel.sh 6.18.54          # full pipeline
  INIT -> SOURCE_VERIFIED -> MATERIALIZED -> PATCHED -> CONFIG_VERIFIED
       -> BUILT -> INITRAMFS_READY -> PACKAGED -> VERIFIED -> TESTED
       -> READY_FOR_BOARD_TRIAL
```

* **source authentication** — `source-lock.json` (SHA-256 + kernel.org GPG
  signature over the uncompressed tar, streamed through `xz -dc | gpg`).
* **isolated materialization** — the tarball is extracted fresh into the run
  workspace; the shared mutable `$WSL_ROOT/src` tree of the legacy scripts is
  never touched (see [P7-BUILD-PIPELINE-AUDIT.md](P7-BUILD-PIPELINE-AUDIT.md)).
* **patch** — board DTS copy + registration patch applied to the run-private
  tree; patched-tree identity recorded (4-file digest).
* **config pipeline** — seed config -> olddefconfig -> final config gates
  (`kernel_artifacts.py check-config` seed+final modes, config/DTS invariant
  tests) producing `config-diff.txt` + `config-contract.json`.
* **build** — `make Image modules dtb` with the exact determinism environment
  of the reference build (`SOURCE_DATE_EPOCH` from the tarball Makefile mtime,
  `KBUILD_BUILD_VERSION=1`, `KBUILD_BUILD_USER=Fog`, `KBUILD_BUILD_HOST=eaidk310-wsl`),
  `CHECK_DTBS=y`, `modules_install`, `depmod`, per-module ARM64 checks.
* **initramfs** — the debootstrap base is an immutable, policy-hashed cache;
  every run snapshots it (`cp -a`) and modifies only the snapshot.  No shared
  chroot exists any more.
* **package** — bundle directory (Image / uInitrd / DTB / full
  `/lib/modules/<release>` / deploy helpers / manifest) compressed with
  `tar --sort=name --mtime=@1788352262 --owner=0 --group=0 | zstd -19 -T0`.
* **verify/test (Tier 2)** — layout, DTB model/compatible, uInitrd header epoch,
  initramfs contents (ARM64 busybox/kmod, explicit module policy, no device
  nodes), extlinux entry, archive round-trip, modules contract
  (modules.dep/depmod -e/count), custom-source gate, manifest byte match.

## Workspace model

```
${EAIDK_WORK_ROOT:-~/.cache/eaidk310/release}
├── cache/{downloads,gnupg,rootfs,upstream,tools}   # shared, read-only in runs
├── work/<release>-<run-id>/{source,build,modules,rootfs,initramfs,bundle,logs}
└── output/<version>/                                # deliverables
```

Overrides: `--work-root`, `--cache-root`, `--output-dir` (env
`EAIDK_WORK_ROOT` / `EAIDK_CACHE_ROOT`).  DrvFS/9p mounts (`/mnt/*`,
`/media/*`) are rejected as work roots — kernel compiles must happen on a
Linux filesystem.  The state machine lives in
`work/<release>-<run-id>/state.json` (not in the repo); `--resume` re-verifies
every recorded artifact hash before continuing; `--clean` deletes a run
workspace (cache and output are kept); `--purge-cache` is explicit-only.

The git worktree is never written: logs, config diffs and state stay in the
run workspace, deliverables land in `output/`.

## Running it

Linux (or WSL from Windows; the initramfs stage requires root):

```bash
wsl.exe -u root -e bash -c "cd <repo> && ./tools/release-kernel.sh 6.18.54"
# or, inside WSL/Linux:
make release VERSION=6.18.54
make release-verify VERSION=6.18.54   # verify/test/finalize an existing run
make reproduce VERSION=6.18.54        # clean rebuild + REPRO_COMPARE
./tools/release-kernel.py 6.18.54 --status
```

Host prerequisites: `gcc-aarch64-linux-gnu`, `make`, `rsync`, `patch`,
`zstd`, `device-tree-compiler`, `u-boot-tools` (mkimage/dumpimage/fdtget),
`kmod`, `initramfs-tools-core`, `debootstrap`, `qemu-user-static` (+ binfmt),
`python3`.  `dtschema` is installed automatically when `dt-validate` is
missing (pip).  Toolchain drift against the committed reference build record
(`analysis/verify-inputs.json`) is a hard gate, not a warning.

## Cold vs warm cache

The first run downloads the upstream tarball and builds the debootstrap base
(cold).  Subsequent runs reuse `cache/` (warm).  Both must produce identical
bundle hashes — the graduation record lives in the release evidence;
`REPRO_COMPARE … MATCH=YES` is printed by the engine whenever a previous
candidate exists in `output/`.

## Version bumps

`python3 tools/update-kernel-lock.py 6.18.60` fetches and authenticates a new
upstream tarball and writes `source-lock.candidate.json` (never touching the
production lock).  After review: create `kernel/linux-<v>-zramfix1/` with the
candidate lock, seed config, board DTS, patches and `release-metadata.json`.
If the patch stack does not apply cleanly the pipeline stops with
`PATCHED` failure — no auto-rebasing.

## Board trial boundary

`READY_FOR_BOARD_TRIAL` means: reproducible build, all Tier-2 acceptance
checks passed, artifacts hashed and manifested.  It does NOT mean stable:
board installation runs through `eaidk-ota verify/stage/install/arm` with the
watchdog trial, and promotion to stable plus any GitHub Release publishing
remain explicit human steps.
