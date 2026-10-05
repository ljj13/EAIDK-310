# eaidk-ota — fail-safe kernel OTA for EAIDK-310

Production status:

```
FAILSAFE_BOOT_BACKEND=RAW_REDUNDANT
KERNEL_OTA_FRAMEWORK=PRODUCTION_READY
REMOTE_OTA_ELIGIBLE=YES
UNATTENDED_REMOTE_OTA_ELIGIBLE=YES     (kernel OTA only)
```

Rollback backend is the `failsafe-raw` U-Boot variant: a raw dual-copy
bootstate record pair inside the audited eMMC 12–16 MiB tail — no
filesystem, journal or U-Boot environment dependency.

## Bootstate layout

| Sector | Content |
| --- | --- |
| LBA `0x6400` (25600, 12 MiB + 512 KiB) | record A |
| LBA `0x7800` (30720, 15 MiB) | record B |
| LBA `0x6C00` (27648, 13.5 MiB) | diagnostic breadcrumb (observability only) |

Record (512 bytes): magic `EA310BS1`, format version, bootcount,
`upgrade_available`, candidate slot/label, monotonic sequence (u32 LE),
CRC32 over bytes 0–507.  Selection: usable = magic ∧ version ∧ CRC;
highest sequence wins; equal-sequence conflicts reject both copies.
Store always writes the copy that is **not** selected, so a torn write
costs at most the newest increment.  Any unreadable, corrupt, conflicting
or identity-mismatched state fails closed to the stable boot and is never
rewritten.  The device identity gate (eMMC manfid `0xd6`, product
`HBD08G`, minimum capacity) refuses all writes on unknown hardware.

## Watchdog trial chain

```text
U-Boot armed trial
  → bootcount driver starts dw_wdt (30 s TOP)
  → kernel probe marks WDOG_HW_RUNNING and auto-pings
    (CONFIG_WATCHDOG_HANDLE_BOOT_ENABLED=y)
  → eaidk-trial-feed service opens /dev/watchdog0 and feeds every 10 s
    while upgrade_available=1
  → health PASS → commit → feeder stops the watchdog (magic 'V') and exits
```

A candidate that panics, hangs or never reaches the feeder stops the
feed; the watchdog resets the SoC; the next boot exceeds `bootlimit=1`
and `altbootcmd` boots the previous known-good.  A plain `close()` does
NOT stop a U-Boot-started watchdog (it fires ~28 s later) — magic `'V'`
is the only clean stop; the feeder implements this.

## Commands

`eaidk-ota` (run as root):

| command | purpose |
| --- | --- |
| `inspect <bundle>` | summarize a bundle |
| `verify <bundle> --release <rel>` | 22-check manifest-exhaustive verification |
| `stage <bundle>` | atomic staging, SHA-pinned marker |
| `install-candidate` | install Image/uInitrd/DTB **and** /lib/modules/<release> (atomic, hash-verified) |
| `status` | state machine + backend + bootstate |
| `health` | critical boot layer + remote/network layer |
| `arm` | arm the raw backend for one candidate trial |
| `commit` | clear trial + record promotion |
| `rollback` | clear trial + record rollback |

`eaidk-bootstate` (raw/ext4 bootstate operator):

| command | purpose |
| --- | --- |
| `inspect` | decode records A/B (+ breadcrumb), report selection and fail-closed state |
| `arm` / `clear` | write armed / committed records (atomic, flock, block devices read-only unless `--allow-block-write`) |
| `simulate` | model consecutive U-Boot boots against the current state |

Backends: `NoBackend` (default; every boot mutation refused),
`BootcountFsBackend` (BOOTCOUNT_EXT file on /boot; alternative, built,
not installed) and `RawBootstateBackend` (active).  Activation requires
`/boot/eaidk-ota/BACKEND` to name the backend; anything unexpected
degrades to NoBackend.

## Safety

Every boot mutation requires a locally-issued authorization token
optionally bound to stable/candidate/bundle/backend; remote sessions can
never mint one.  Install refuses to overwrite existing boot files or
module trees with different content.  Kernel OTA never touches U-Boot,
idbloader, BL31/ATF, the GPT or the raw bootstate layout ABI.

History of the P3.5/P3.6 investigation phases:
[docs/history/README-p35-p36-era.md](docs/history/README-p35-p36-era.md).
Current flash/recovery plans:
[docs/FLASH-PLAN-P36.md](docs/FLASH-PLAN-P36.md),
[docs/P3.6-ONSITE-RUNBOOK.md](docs/P3.6-ONSITE-RUNBOOK.md),
[docs/PHASE9-WATCHDOG.md](docs/PHASE9-WATCHDOG.md),
[docs/REHEARSAL-PLANS.md](docs/REHEARSAL-PLANS.md).
