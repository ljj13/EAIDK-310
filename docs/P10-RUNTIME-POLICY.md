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

## Memory / zram (Phase 4)

Six-configuration pressure matrix (700 MB anonymous load with a 120 MB
`MemAvailable` guard, evidence `evidence/p10-zram/`): all candidate
policies — 384 M vs 512 M, lz4 vs lzo-rle, swappiness 60 vs 100 — swap
under 4 MiB, stay within CPU noise, keep a 112–139 MB available floor
and never approach OOM.  zstd is not registered by this kernel's zram
(runtime listing: lzo, lzo-rle, lz4; writing `zstd` fails EINVAL).

ZRAM: NO_CHANGE_NEEDED — 384 MiB lz4, swappiness 60 stays (also the
configuration the OTA health gate asserts).

Kernel quirk worth remembering: on 6.18.55 the per-device zram
accounting (`/sys/block/zram0/mm_stat`, `zramctl`) under-reports while
`/proc/meminfo` SwapFree and `/proc/swaps` are authoritative.

## Boot performance (Phase 5)

Measured with `systemd-analyze` across three real reboots per
configuration (boot-to-boot variance ≈ 0.5 s):

| metric                    | before | after |
|---------------------------|-------:|------:|
| kernel                    |  5.18s | 5.16s |
| userspace → multi-user    | 26.61s | 9.62s |
| total to multi-user       | 31.80s | 14.78s |
| sshd listening (journal Δ)|   ~12s |  ~12s |

Changes (both via drop-ins, ships in `tools/apply-p10-runtime-policy.sh`):

- `shellcrash.service.d/99-p10-async.conf`: `After=multi-user.target`.
  bfstart.sh (~17 s: /tmp rebuild, provider re-download, node URL
  tests) left the boot-critical path; the local proxy is a
  late-starting service, its consumers tolerate that.  Verified
  listening on 7890 after every reboot.
- `exim4.service.d/99-p10-async.conf`: same ordering.  The MTA pulled
  `network-online.target` (DHCP wait) onto the critical chain.

Tailscaled deliberately stays on the critical path (remote-access
lifeline).  `systemd-random-seed` at 4.5 s is early-boot eMMC
contention; not worth touching for reliability reasons.

Journal persistence verified across three real reboots: `--list-boots`
keeps previous boots and the shutdown sequence ("Finished
systemd-reboot.service") is captured as reboot evidence.  Note: the
Storage= switch only takes effect on a full reboot, not on
`systemctl restart systemd-journald`.
