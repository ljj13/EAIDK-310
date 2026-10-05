# Fail-safe boot design (P3.6 candidate A) — EAIDK-310

Status: **DESIGN BUILD READY** — built, sandbox-proven, NOT flashed.
Active board backend remains `NoBackend`; `REMOTE_OTA_ELIGIBLE=NO` until the
candidate is flashed on-site and validated by fault injection (TEST A/B).

Locked source: `v2024.07-rc1@38ea74d6d5c05224acdb03f799897c1bdd56f8cc`
(`source-lock.json`).  Control and sdio-handoff variants and patch 0001 are
untouched; this design adds patch
`patches/0002-failsafe-bootcount-fs.patch` and the
`failsafe-bootcount-fs` variant built by `scripts/build-cross-failsafe.sh`.

## 1. Source forensics (pinned v2024.07-rc1, all verified by reading source)

| Question | Answer | Evidence |
| --- | --- | --- |
| FS-backed bootcount available? | YES — named **`BOOTCOUNT_EXT`** ("Boot counter on EXT filesystem"), driver `drivers/bootcount/bootcount_ext.c`. There is no `BOOTCOUNT_FS` symbol in this tree. | `drivers/bootcount/Kconfig` |
| Exact Kconfig symbols | `BOOTCOUNT_LIMIT` (menu), `BOOTCOUNT_EXT` (choice), `SYS_BOOTCOUNT_EXT_INTERFACE` (default "mmc"), `SYS_BOOTCOUNT_EXT_DEVPART` (default "0:1"), `SYS_BOOTCOUNT_EXT_NAME` (default "/boot/failures"), `SYS_BOOTCOUNT_ADDR` (RAM buffer, no usable default for EXT — must be set), `SYS_BOOTCOUNT_MAGIC` (default 0xB001C041), `SYS_BOOTCOUNT_LE`/`_BE` (LE default), `BOOTCOUNT_BOOTLIMIT` (int → compiled `bootlimit=` default env entry via `include/env_default.h`). | `drivers/bootcount/Kconfig`, `include/env_default.h:117` |
| EXT4 write required? | YES — `BOOTCOUNT_EXT depends on FS_EXT4` and `select EXT4_WRITE` (pulls `fs/ext4/ext4_write.o` + `ext4_journal.o`). We enable `FS_EXT4`/`EXT4_WRITE` but NOT `CMD_EXT4_WRITE` (no interactive command). FS-library write support ≠ ext4write command. | Kconfig + build log |
| Bootstate file format | **4 bytes**: `u8 magic=0xBD, u8 version=1, u8 bootcount, u8 upgrade_available`. U-Boot requires EXACTLY 4 bytes with valid magic+version. | `bootcount_ext.c` `bootcount_ext_t` |
| upgrade_available semantics | `bootcount_load()` returns the stored bootcount only when magic+version valid AND `upgrade_available=1`; otherwise 0. `bootcount_store()` writes only while the driver's `upgrade_available` is 1 ("Only update bootcount during upgrade process"). | `bootcount_ext.c` |
| Where bootcount runs | `bootdelay_process()` (common/autoboot.c) calls `bootcount_inc()` first; then `bootcount_error()` re-loads and, when `bootcount > bootlimit` (env `bootlimit`, from compiled default env), swaps `bootcmd` → `altbootcmd` for that boot. | `common/autoboot.c:454-479`, `include/bootcount.h` |
| bootlimit from compiled default env? | YES — `CONFIG_BOOTCOUNT_BOOTLIMIT=1` emits `bootlimit=1` into the default environment; altbootcmd/bootcmd policy is compiled in the same way. No `saveenv` anywhere. | `env_default.h`, verified in built `u-boot-initial-env` |

### Upstream fail-open defect found and patched (0002)

Stock `bootcount_load()` leaves the driver-static `upgrade_available` at its
initial value **1** whenever the file is unreadable or malformed. Combined
with `bootcount_store()`, a corrupt file is silently **re-armed** on the next
boot (written back as `bootcount=1, upgrade_available=1`) and the system
would run a candidate trial that nobody armed.  Patch 0002 makes every
abnormal load path **fail closed**:

* device-select error → `upgrade_available = 0` (no write-back);
* read error / short read → `upgrade_available = 0` (no write-back);
* wrong magic/version → `upgrade_available = 0` (no write-back);
* `bootcount_load()` exports the resulting state as env
  `upgrade_available` so the `bootcmd` script branches on it deterministically.

`bootcount_store()` keeps its upstream guard: nothing is written unless the
*same boot's* load saw a valid, armed record.  Missing/corrupt/committed
state can never boot a candidate and never mutates the file.

### Second hardening: `bootcount_stored` — the infinite-loop guard

The sandbox harness surfaced a **real-board risk**: U-Boot v2024.07's
`ext4fs_write()` refuses ext4 filesystems carrying `metadata_csum`
(fs/ext4/ext4_write.c: "Unsupported feature metadata_csum found, not
writing."), and modern e2fsprogs (≥1.43, 2016) enables it by default — so
the board's /boot very likely carries it.  If the bootcount increment
cannot persist, an armed trial would boot the candidate, fail, reboot,
re-read the *unchanged* armed file, and trial again — **forever, with the
bootlimit never tripping**.

Patch 0002 therefore tracks the store outcome: `bootcount_store()` exports
env `bootcount_stored` (1 only when `fs_write` succeeded this boot), and
the compiled `bootcmd` policy requires **both** `upgrade_available=1` AND
`bootcount_stored=1` before invoking the candidate `sysboot`.  Write
failure → stable boot, always; an unprotected trial is impossible even on
an incompatible filesystem.  Proven by sandbox scenario
`test_unwritable_fs_refuses_candidate_infinite_loop_guard`.

### Third hardening: exact-length records

Upstream `bootcount_load()` reads exactly 4 bytes; a longer (corrupt) file
would be trusted by its valid-looking prefix while eaidk-bootstate
classifies the same file invalid — a driver/tool semantic split.  Patch
0002 reads `sizeof(bootcount_ext_t) + 1` and requires `len_read ==
sizeof(bootcount_ext_t)`: anything that is not EXACTLY 4 bytes is corrupt
→ fail closed to stable, matching `parse_record()` byte for byte.
Covered by sandbox scenarios `test_short_file…` / `test_oversized_file…`.

Two complementary userspace gates in eaidk-ota:
* `BootcountFsBackend.arm()` refuses to arm when `dumpe2fs` proves the boot
  filesystem carries `metadata_csum` (remedy documented in
  `ota/docs/FLASH-PLAN-P36.md`: on-site `tune2fs -O ^metadata_csum`).
* eaidk-bootstate's file semantics stay byte-exact and fs-agnostic.

## 2. Candidate A — what changes (and what does not)

Payload: `failsafe-bootcount-fs/u-boot-dtb.bin`
SHA-256 `dee563aacfac048bd0f28f3f004168a11d99cb1a633f8e823f2162bb8ec7903f`,
812,288 bytes (control reference: 809,984; **+2,304 bytes, +0.28%**; limit
1,046,528).  Deterministic rebuild proven byte-identical (fixed
`SOURCE_DATE_EPOCH`/`KBUILD_BUILD_USER`/`KBUILD_BUILD_HOST`); default env
record `28e71ad7abe4e5d505fe3251597f2e3e48eb244a11ddf5aa87d13477baa2b24d`.

Config delta vs control (see `audit/config-diff.txt`): `BOOTCOUNT_LIMIT`,
`BOOTCOUNT_EXT` (+`FS_EXT4`/`EXT4_WRITE`), bootcount location/address/magic,
`BOOTCOUNT_BOOTLIMIT=1`, `CMD_SYSBOOT`, `USE_BOOTCOMMAND` + policy
`BOOTCOMMAND`.  No other subsystem touched — no DDR/BL31/DT/pinmux/network/
SDIO changes.

**Device tree byte-identical** to control (both embed
`20cfb9790cdadebad9c56aa31fbfcf9f10360654f98dd714ba342788863e96ee`);
failsafe reuses the control DTS (`rk3328-eaidk310-control`, SDIO disabled).

Compiled default env delta (verified from built `u-boot-initial-env`,
`audit/env-diff.txt` — exactly four entries):

```
bootlimit=1
bootcount_file=/eaidk-ota/bootcount.bin
bootcmd=if test ${upgrade_available} -eq 1 -a ${bootcount_stored} -eq 1; then sysboot mmc 0:1 ext2 ${scriptaddr} /extlinux/extlinux-candidate.conf; fi; bootflow scan
altbootcmd=sysboot mmc 0:1 ext2 ${scriptaddr} /extlinux/extlinux.conf; bootflow scan
```

Everything else (including `boot_targets=mmc1 mmc0 …`, memory layout,
`bootdelay=2`) is untouched.  The policy lives in
`include/configs/rk3328_common.h` guarded by `#ifdef CONFIG_BOOTCOUNT_EXT`,
so control/sdio-handoff builds are unaffected byte for byte.

### State selection (deterministic, two files)

* `/extlinux/extlinux.conf` — **stable** default entry, never touched while
  arming a trial.
* `/extlinux/extlinux-candidate.conf` — the trial entry, created atomically
  by eaidk-ota at arm time.
* `/eaidk-ota/bootcount.bin` — 4-byte trial state (the only mutable state).

| Boot-time state | U-Boot route |
| --- | --- |
| bootstate missing / corrupt / committed | `bootflow scan` → stable (unchanged current behaviour) |
| armed (`bd 01 00 01`) | `sysboot …-candidate.conf` (one trial; bootcount 0→1) |
| trial failed → reboot | bootcount exceeds `bootlimit=1` → `altbootcmd` → stable |
| candidate healthy (Linux) | eaidk-ota commit: install files + flip extlinux default + `clear` bootstate |
| candidate failed (Linux up) | eaidk-ota rollback: restore extlinux default + `clear` bootstate |

sysboot (cmd/sysboot.c, `CONFIG_CMD_SYSBOOT`, `select PXE_UTILS`) reads a
specific extlinux-format file from `mmc 0:1` — deterministic label selection
that bootstd cannot express in v2024.07-rc1 (the extlinux bootmeth hardcodes
`EXTLINUX_FNAME` / fixed prefixes).  If the candidate conf is missing or
unbootable, `sysboot` fails and execution falls through to
`bootflow scan` → stable (belt and suspenders).

### Trial semantics

* `bootlimit=1` = **exactly one candidate attempt**.  First armed boot:
  file 0→1, `1 > 1` false → candidate.  Any subsequent boot: 1→2,
  `2 > 1` → altbootcmd (stable).  (Verified in sandbox.)
* `bootlimit`/`altbootcmd`/`bootcmd` are compiled default-env values; the
  environment is never persisted (the 0x3F8000 region stays untouched and
  unused, as today).
* While a trial is armed, every boot rewrites 4 bytes to the ext4 file
  (journal wear negligible; /boot ≈ 230 MiB).
* Known upstream quirk (documented, not relied upon): u8 bootcount wraps at
  255 → counter resets to 0 while still armed.  eaidk-ota disarms within a
  few boots in all realistic flows, so this is unreachable in practice.
* Wrong `SYS_BOOTCOUNT_EXT_DEVPART` mapping degrades safely: the device
  select fails → fail-closed → always stable, trials simply never start
  (an operator-visible "Error selecting device" line on serial).

### Devpart mapping assumption (to be confirmed on-site)

DTS aliases in 0001: `mmc0 = &emmc`, `mmc1 = &sdmmc`; rockchip default
`boot_targets=mmc1 mmc0 …` (SD first — rescue priority preserved).  Best
evidence says eMMC = `dev 0`, hence `SYS_BOOTCOUNT_EXT_DEVPART="0:1"`.
**On-site `mmc list` must confirm this before flashing**; if reversed, the
value is a one-line defconfig change + rebuild (deterministic pipeline).

## 3. SD rescue semantics — preserved by design

* The rescue SD runs the **stock control U-Boot** (no bootcount logic);
  the failsafe build is written only to the eMMC U-Boot region.
* `boot_targets=mmc1 mmc0 …` is untouched: an inserted rescue SD is scanned
  before eMMC; rescue boot never reads eMMC boot state.
* BootROM order on rk3328 is SPI-NOR → eMMC → SD → USB maskrom: an SD
  rescue works when the eMMC loader is invalid/unreadable, but **not** when
  a valid-looking eMMC SPL hands off to a U-Boot proper that then crashes
  before serial.  Honest limit: that scenario needs on-site maskrom
  (rkdeveloptool/upgrade_tool) recovery.  Candidate A's blast radius is
  deliberately tiny (driver + 4 env entries + sysboot) and its behaviour is
  sandbox-proven; a pre-console crash is not a credible failure mode of
  this delta, but the recovery procedure is documented in
  `ota/docs/FLASH-PLAN-P36.md` anyway.

## 4. Sandbox proof (tests/test_failsafe_sandbox.py)

The real pinned U-Boot + patch 0002 runs as a sandbox binary against a real
MBR/ext4 image (`host bind`, `debugfs`-verified file bytes).  Proven matrix:

| Initial file | Route taken | File after |
| --- | --- | --- |
| missing | stable, `upgrade_available=0` | still missing (no creation) |
| `bd010001` armed (fs writable) | candidate, `bootcount=1` | `bd010101` |
| armed on metadata_csum fs | **stable — candidate refused** (`bootcount_stored=0`) | unchanged |
| armed → reboot | `altbootcmd` (stable) | `bd010201` |
| `bd010000` committed | stable | unchanged |
| `deadbeef` corrupt | stable, no rewrite | unchanged |
| `bd02…` wrong version | stable, no rewrite | unchanged |
| 2-byte truncated | stable, no rewrite | unchanged |
| 5-byte oversized | stable, no rewrite | unchanged |
| armed + candidate.conf missing | sysboot fails → falls to stable | unchanged |
| armed + candidate.conf unbootable | sysboot attempt → falls to stable | unchanged |

Unit tests for the Linux-side operator: `ota/tests/test_eaidk_bootstate.py`
(20 cases: golden bytes, parse fail-closed matrix, atomic-write, lock,
simulate chains).  Backend integration: `ota/tests/test_eaidk_backend.py`
(10 cases: marker gating, arm/commit/rollback flows, refusal ordering).

## 5. Future activation (out of scope here)

After the on-site flash + TEST A/B pass: create
`/boot/eaidk-ota/BACKEND` containing exactly `BootcountFsBackend 1`, deploy
`eaidk-bootstate` to the board, then `eaidk-ota status` reports
`active: BootcountFsBackend, available: True` and try/arm/commit/rollback
become functional.  Until then the marker does not exist and every boot
mutation refuses (fail closed).
