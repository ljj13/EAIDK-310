# P10 — System Optimization & Long-term Validation

Scope: turn the verified-stable board into a low-risk long-term
unattended server, without physical access and without touching the boot
chain (U-Boot / idbloader / BL31 / GPT / RAW ABI stay frozen; wireless
hardware investigation stays paused per P9 policy).

Board baseline at phase start (2026-10-07, STABLE =
`6.18.55-eaidk310-wifi3`, uptime-fresh reboot verified):

- boot 31.5 s total = 5.163 s kernel + 26.381 s userspace
- zram0 384 MiB lz4, idle; swappiness 60, page-cluster 3
- eMMC HBD08G 7.3 GiB: PRE_EOL 0x01, life est A/B 0x01 (healthy)
- journal volatile (lost every boot), 0 failed units
- `/var/lib/eaidk-ota/staging` 850 MB (5 candidate bundles)
- apt cache 91 MB, `/var/tmp` 35 MB stale artifacts
- Ethernet link 100 Mbit full duplex (switch-side negotiation), v6
  native + Mihomo v4 egress, Tailscale direct-path 2 ms to PC peer

## Instrumentation (Phase 1)

`tools/eaidk-health` (deployed to `/usr/local/sbin/eaidk-health`) gives
four read-only views — `status`, `boot`, `storage`, `network` — each in
human-readable form and `--json`.  Root-locked fields degrade to null
instead of failing.  Baseline evidence: `evidence/p10-baseline/`.

## Runtime observability (Phase 2)

Applied by `tools/apply-p10-runtime-policy.sh` (idempotent, userspace
only, reversible by deleting the marked drop-ins):

- `/var/log/journal` created + journald drop-in `99-eaidk-p10.conf`
  (`Storage=persistent`, `SystemMaxUse=64M`,
  `SystemKeepFree=128M`).  Boot history, reboot reason and previous-boot
  kernel evidence now survive reboots; before this the journal was
  volatile and every boot lost its tail.
- The board's pre-existing `10-eaidk-server.conf` rate/compression
  limits remain in force and merge with the drop-in.
- pstore/ramoops: kernel has `CONFIG_PSTORE_RAM=m`, but arming it needs
  a reserved-memory kernel cmdline change, i.e. an extlinux mutation.
  Per the fail-closed OTA design that requires the physical console
  (`eaidk-ota authorize-local` only runs on ttyS2/tty1), so it is
  deferred as PHYSICAL_VALIDATION_REQUIRED rather than bypassed.

## eMMC endurance (Phase 3)

Health registers (via mmc-utils + sysfs):
PRE_EOL_INFO 0x01 (normal), DEVICE_LIFE_TIME_EST_TYP_A/B 0x01
(<10 % used each), cache 8 MiB, SEC_COUNT 15 269 888.  EMMC_HEALTH=NORMAL.

Write-amplification audit before/after policy (KiB):

| source            | before | after |
|-------------------|-------:|------:|
| apt cache         | 92 796 |    52 |
| /var/tmp          | 35 120 |   104 |
| ota staging       |850 000 |510 000|
| journal           | volatile, uncapped | persistent, 64M cap |
| coredump          | defaults (ProcessSizeMax up to 1G) | 32M/dump, 64M total |

- `eaidk-ota prune-staging [--keep N] [--yes]`: refuses outside
  IDLE/COMMITTED, protects the state-file release and any bundle whose
  hash matches the state file, dry-run by default, records an
  append-only history entry.  First run removed 2 of 5 superseded
  bundles (341 MB freed).
- apt `Keep-Downloaded-Packages=false` + empty pkgcache paths; stale
  pkgcache.bin/srcpkgcache.bin reclaimed (91 MB).
- coredump capped (forensics kept, one dump can no longer fill the
  disk).
- `/var/tmp` stale updater/deploy artifacts removed (34 MB).
- TRIM: weekly `fstrim.timer` already enabled; manual `fstrim -av`
  after cleanup discarded 1.9 GiB.
- Conservative forensics: persistent journal and capped coredumps
  deliberately kept — crash evidence beats maximum write savings here.

## Not changed (deliberately)

- swappiness / page-cluster / zram size: Phase 4 A/B pending.
- boot unit graph (shellcrash.service 17.4 s is the userspace boot
  hog): Phase 5 pending.
- extlinux / kernel cmdline / DTS: frozen outside the P7→P8 candidate
  pipeline.
