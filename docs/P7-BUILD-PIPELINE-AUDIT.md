# P7 Build Pipeline Audit

Audit of the three per-release kernel pipelines (`kernel/linux-{6.12.108,6.12.111,6.18.54}-zramfix1/`)
before the P7 reproducible release pipeline. Canonical reference: 6.18.54 (STABLE).
The 6.12.111 and 6.18.54 script sets are byte-identical modulo version strings;
6.12.108 (rescue reference) predates the current gate layout and is out of scope
for the unified engine.

## Pipeline dataflow (as of PRE_HEAD 5980888)

```
verify-inputs.sh      archive+sig download -> SHA256+GPG -> toolchain probe
                      writes analysis/verify-inputs.json  (into the REPO worktree)
install-board-inputs.sh  copy DTS+config into SHARED source tree, apply patch 0001
build-kernel.sh       seed config -> olddefconfig -> gates -> make Image/modules/DTB
                      -> modules_install -> depmod; writes analysis/*.json (repo)
build-initramfs-arm64.sh  SHARED debootstrap chroot -> /lib/modules swap ->
                      mkinitramfs -> mkimage uInitrd
package-artifacts.sh  assemble bundle dir -> manifest -> tar|zstd -> repo artifacts/
verify-bundle.sh      unpack, layout/DTB/uInitrd/initramfs/extlinux checks
```

## Findings

### F1 — hard-coded workspace root

Every script defaults `DEFAULT_WSL_ROOT="/home/Fog/eaidk310-kernel"` and several
guard against paths *outside* that root (`install-board-inputs.sh` literally
rejects source trees not below `/home/Fog/eaidk310-kernel/src`). Overrides exist
as flags but the guard constants remain absolute. P5 deleted the workspace, so
nothing reproduces from a clean machine without hand-building the tree.

### F2 — shared mutable source tree

`$WSL_ROOT/src/linux-$VERSION` is materialized once and patched **in place**
(`install-board-inputs.sh` copies DTS and applies the registration patch with
`patch --forward`). A second build silently continues from whatever state the
first left; nothing re-verifies the patched-tree identity. Any hand edit to
that tree silently enters the next release.

### F3 — shared mutable chroot (the known incident)

`build-initramfs-arm64.sh` reuses `$WSL_ROOT/chroot-trixie-arm64-zramfix1`
across ALL releases. Per build it does `rm -rf $CHROOT/lib/modules/<release>` +
`rsync --delete` the new modules in, and installs
`$CHROOT/boot/config-<release>`. This is exactly how "building 6.18.54 changed
the 6.12.111 baseline" happened: the 6.12.111 `script_contracts` tests assert
state under the shared chroot and the 6.18 build erased/overwrote parts of it.
The policy hash (`etc/eaidk310-chroot.sha256`) only protects the package
policy, not the per-release writes.

### F4 — repo worktree as build output area

`build-kernel.sh` writes `analysis/build-kernel.log`, `analysis/build-metadata.json`;
`package-artifacts.sh` writes `artifacts/*.tar.zst` — all **inside the git
worktree**. Build outputs and release evidence are mixed with source; a build
dirties the tree and `git status` stops being meaningful.

### F5 — cross-run state coupling (config)

Seed-config self-healing in `build-kernel.sh` (reinstall seed if
`$BUILD_DIR/.config` empty, seed check before olddefconfig) already neutralizes
the worst cross-run config pollution between builds of the SAME version, but
build dirs are per-version (`build-zramfix1-<v>`) while the seed lives in the
repo — re-runs reuse a previous `.config` only after the seed gate revalidates
it. Acceptable per-build; the new engine keeps per-run build dirs and installs
the seed fresh every run regardless.

### F6 — external implicit dependencies

* `dtschema` venv at `$WSL_ROOT/tools/dtschema-venv` (survived P5, but is an
  untracked machine artifact).
* gpg keyring with the kernel.org release key (647F2865…93E) — machine state.
* debootstrap/qemu-user-static/binfmt — host packages (documented, checked).
* `analysis/verify-inputs.json` of a PREVIOUS build is consumed by
  `package-artifacts.sh` for tool versions (gcc/binutils) — committed, so this
  one is fine as an immutable input.

### F7 — classification (source cache / build output / stage / immutable)

| Class | Current location | Belongs to |
|---|---|---|
| source cache | `$WSL_ROOT/src/*`, tarball+sig downloads | shared, read-only, content-addressed |
| build output | `$WSL_ROOT/build-zramfix1-<v>` | per-run private |
| modules stage | `$WSL_ROOT/stage-zramfix1-<v>` | per-run private |
| initramfs output | `$WSL_ROOT/initramfs-zramfix1-<v>` | per-run private |
| rootfs/chroot | `$WSL_ROOT/chroot-trixie-arm64-zramfix1` | **must become immutable cache + per-run snapshot** |
| bundle stage | `$WSL_ROOT/artifacts/<bundle>` | per-run private |
| release archive | `<repo>/artifacts/*.tar.zst`, `<repo>/analysis/*` | output dir outside repo |
| immutable inputs | repo: config/, dts/, patches/, initramfs/, source-lock.json, tools/, per-release analysis records | read-only |

## Workspace contract (P7)

```
EAIDK_WORK_ROOT (default ${XDG_CACHE_HOME:-$HOME/.cache}/eaidk310/release)
├── cache/                      # shared, never written during a run phase
│   ├── downloads/              # upstream tarballs + signatures (content-addressed by SHA256)
│   ├── gnupg/                  # kernel.org release keyring
│   ├── upstream/               # optional pinned-source checkouts (custom-src verify)
│   └── rootfs/trixie-arm64/    # prepared debootstrap base (immutable, policy-hashed)
├── work/
│   └── <release>-<run-id>/     # ALL mutable state of exactly one build
│       ├── source/  build/  modules/  rootfs/  initramfs/  bundle/  logs/  state.json
└── output/
    └── <release>/              # READY_FOR_BOARD_TRIAL deliverables
```

Rules:

1. A run writes only inside its `work/<release>-<run-id>/` and, at the final
   stage, `output/<release>/`. Never the repo worktree, never another run.
2. `cache/` is consumed read-only; building it is an explicit phase.
3. The rootfs cache is snapshotted (`cp -a`) into the run before any
   modification; the cached base is only replaced by an explicit rebuild.
4. Different kernel versions can never share `build/`, `modules/`, `rootfs/`,
   initramfs root or `.config` because all of those live under the per-run
   directory, which is named after the release AND a fresh run id.
5. Kernel compilation happens on a Linux ext4 filesystem (WSL ext4, native
   Linux, or a GH runner) — never on a DrvFS/9p mount; the engine refuses
   `work-root` paths under `/mnt/*` and `/media/*`.

## Reproducibility ledger (6.18.54 reference build)

Already deterministic in the current scripts (P7 engine must preserve exactly):

* `SOURCE_DATE_EPOCH` = mtime of the tarball's `Makefile` (1790346954 for
  6.18.54) exported to the kernel build with `KBUILD_BUILD_VERSION=1`,
  `KBUILD_BUILD_USER=Fog`, `KBUILD_BUILD_HOST=eaidk310-wsl`.
* initramfs + mkimage + bundle tar use a separate fixed epoch 1788352262.
* bundle tar: `--sort=name --mtime=@1788352262 --owner=0 --group=0 --numeric-owner | zstd -19 -T0`.
* manifest metadata comes from committed `analysis/verify-inputs.json` + lock
  fields, not from ambient machine state.

Residual nondeterminism surface (analysed in the P7 graduation report):

* cross-compiler version (Image bytes) — reference: gcc 13.3.0 / binutils 2.42
  (Ubuntu 24.04), still current on the build host;
* trixie package versions inside the initramfs (busybox/kmod/initramfs-tools/
  zstd from apt at chroot-build time);
* host tool versions for depmod/zstd/tar (reference: kmod 31, zstd 1.5.5, tar 1.35).

## Disposition of the legacy scripts

Left byte-identical on purpose: they are pinned by the per-release
`test_script_contracts.py` suites and remain the manual fallback. The P7
engine (`tools/release-kernel.py`) re-implements orchestration around the
shared verifier `tools/kernel_artifacts.py` and does not modify them. Future
versions add `source-lock` + `release-metadata.json` + config delta — not a
copy of the pipeline code.
