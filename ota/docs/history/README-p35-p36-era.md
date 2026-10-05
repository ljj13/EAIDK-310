# P3.5 — eaidk-ota: fail-safe kernel OTA framework (design)

## Scope of this round

`FAILSAFE_BOOT_BACKEND=RAW_REDUNDANT` (production).  This round shipped the userspace framework
only: verify / stage / plan / health / state machine / authorization gates.
`try`, `arm`, `commit`, `rollback` and `install-candidate` are implemented
but refuse to touch the boot chain (see `eaidk-ota try`).

## State machine

```
IDLE → VERIFIED → STAGED → ARMED → BOOTING → HEALTH_PENDING → HEALTHY
     → COMMITTED
ARMED → (boot failure) → ROLLBACK_PENDING → ROLLED_BACK → IDLE
any   → FAILED
```

State lives in `/var/lib/eaidk-ota/state.json` (atomic tmp+fsync+rename).
Every transition appends an immutable record to
`/var/lib/eaidk-ota/history/`.  A corrupted `state.json` is never trusted:
the tool resets to IDLE and says so.

## Safety model

- `/etc/eaidk-ota/allow-boot-mutation` must exist for ANY /boot mutation.
- `/run/eaidk-ota/local-authorization` (TTL 15 min) must exist and be valid.
- The token can only be created by `eaidk-ota authorize-local` running with
  stdin on a physical console (`/dev/ttyS2`, `/dev/tty1`).  Anything else —
  SSH pts, pipes, redirected fds — is refused (fail closed).
- Both gates are checked by `install-candidate`; `try`/`arm` additionally
  require a verified fail-safe boot backend, which does not exist yet.

## Rollback backend status (PHASE 1/2 investigation result)

`FAILSAFE_BOOT_BACKEND=BLOCKED`.

PROVEN facts (deployed U-Boot 2024.07-rc1-g38ea74d6-dirty, control variant,
pinned defconfig `eaidk310-control-rk3328_defconfig`):

- `CONFIG_ENV_IS_IN_MMC=y`, `CONFIG_SYS_MMC_ENV_DEV=1` (eMMC),
  `CONFIG_ENV_OFFSET=0x3F8000`.
- The eMMC env region at 0x3F8000 is **all-zero** (od dump) — never
  initialized; `fw_printenv` against it: "Cannot read environment"; U-Boot
  itself prints "using default environment" every boot.
- `CONFIG_BOOTCOUNT_LIMIT` and every `CONFIG_BOOTCOUNT_*` symbol are absent
  from the pinned defconfig → bootcount framework not built (LIKELY; a
  runtime `bootcount` command probe needs serial).
- `CONFIG_CMD_EXT4_WRITE`, `CONFIG_CMD_FAT_WRITE`, `CONFIG_CMD_SETEXPR`
  absent (`# CONFIG_CMD_SETEXPR is not set` explicitly) → no filesystem
  one-shot marker can be consumed by the bootloader.
- `CONFIG_WATCHDOG` / `CONFIG_WDT` absent → no bootloader-phase watchdog.

## Future P3.6 (unblocks Plan A)

Rebuild U-Boot from the same pinned commit with, minimally:
`CONFIG_BOOTCOUNT_LIMIT=y CONFIG_BOOTCOUNT_ENV=y CONFIG_BOOTLIMIT=N
CONFIG_ALTBOOTCMD` handling, and an env-region initializer step in the
deployment (write a valid env once), or move env to a small dedicated ext4
file consumed by a custom bootcmd.  Requires on-site flashing of the 16 MiB
prefix + serial supervision.  Nothing in P3.5 prevents it.

## P3.6 status (supersedes the outlook above)

P4 CLOSED (2026-10-06): 6.18.54-eaidk310-zramfix1 is the verified STABLE default (first remote kernel OTA PASS with true-hang watchdog rollback coverage).  Roles: RESCUE=6.12.108, PREVIOUS_KNOWN_GOOD=6.12.111, STABLE=6.18.54.  P3.6 candidate A (BOOTCOUNT_EXT) remains built as an alternative backend, not installed.  Historical P3.6 text below.

P3.6 candidate A is designed, built and tested; **not flashed**.  The
backend of choice is U-Boot's `BOOTCOUNT_EXT` driver (NOT `BOOTCOUNT_ENV`
and no persistent environment at all): a 4-byte record
`[magic=0xBD][version=1][bootcount][upgrade_available]` at
`/boot/eaidk-ota/bootcount.bin`, shared between the failsafe U-Boot and the
Linux-side operator tool `ota/eaidk-bootstate` (inspect/arm/clear/simulate,
atomic writes, flock).  Design + forensics:
`../bootloader/u-boot-eaidk310/docs/FAILSAFE-BOOT.md`; flash plan:
`docs/FLASH-PLAN-P36.md`.  Backend activation in eaidk-ota:
`--backend {auto,none,bootcount-fs}`; without the `/boot/eaidk-ota/BACKEND`
marker (written only after on-site flash + TEST A/B) selection degrades to
`NoBackend` and every boot mutation keeps refusing.

Tests: `tests/test_eaidk_bootstate.py` (20), `tests/test_eaidk_backend.py`
(10), sandbox state machine `../bootloader/u-boot-eaidk310/tests/
test_failsafe_sandbox.py`, patch invariants
`../bootloader/u-boot-eaidk310/tests/test_failsafe_invariants.py`.

## Exit codes

0 ok · 1 error · 2 verification failed · 3 authorization required/denied ·
4 fail-safe backend unavailable · 5 state conflict · 6 critical health fail ·
7 remote health fail (degraded only)
