# Raw evidence inventory — 2026-10-06 (P6 PHASE 14/15)

Inventory of un-sanitized raw evidence in the local workspace
`D:/Project/EAIDK310/logs/` at P6 start: 174 files, ~50 MiB.  Sanitized
copies of the load-bearing records are published in the
[v2026.10.06 release-evidence asset](https://github.com/ljj13/EAIDK-310/releases/tag/v2026.10.06).

Legend: ARCHIVED = sanitized copy on GitHub; KEEP = local original
retained (rollback artifact / unique context); DEL = local original
deleted after this audit.

## 1–4. Serial baselines and OTA tests

| local file | size | event | archived | disposition |
| --- | --- | --- | --- | --- |
| `p4-final-closeout/stable-618-normal-boot.log` | 8.4 K | final 6.18 normal stable boot (unarmed) | YES | DEL |
| `p4-618-repro/serial-S1-111-baseline-ch340-*.log` | 13 K | 111 serial baseline on failsafe-raw v2 | YES | DEL |
| `p4-618-repro/serial-TESTC-v3-watchdog-*.log` | 10.8 K | P3.7 TEST C true-hang watchdog cycle | YES | DEL |
| `p4-618-repro/serial-618-repro-*.log` | 46.5 K | S2 6.18.54 controlled repro (healthy boot) | YES | DEL |
| `p4-618-repro/serial-TESTC-hang-cycle-rerun-*.log` | 10.7 K | TEST C round 2 (pre-fix hook, silent) | no | KEEP (unique: documents the silent-hook symptom) |
| `p4-618-repro/serial-S1-111-baseline-20261005-192705.log` | 16.7 K | first S1 attempt — captured the WRONG board (WCH-Link on another device) | no | DEL (no EAIDK content) |
| `p4-618-repro/serial-TESTC-hang-cycle-*.log` (first) | — | capture failed (port busy) | — | DEL (empty/failed) |

## 5. Raw backend evidence

| local file | size | event | archived (verbatim binary) | disposition |
| --- | --- | --- | --- | --- |
| `p4-618-forensics/lba-0x6400-A.bin`, `lba-0x7800-B.bin`, `lba-0x6c00-diag.bin` | 512 B ea. | P4 incident post-rollback state (bc=2 armed) | YES | KEEP (512 B each; the literal sector state behind ROLLBACK_ON_RESET) |

## 6. Bootloader flash / readback

| local file | size | event | disposition |
| --- | --- | --- | --- |
| `p36-bootstrap-20261001/uboot-region-new.img` | 4 MiB | flash-ready region image (regenerated per candidate) | DEL (regenerable; final published in release) |
| `p36-bootstrap-20261001/board-backups/{idbloader-0-8M, uboot-region-8M-12M, tail-12M-16M}.img` | 8+4+4 MiB | live eMMC region backups before U-Boot flashes (rollback artifacts) | KEEP |
| `p36-bootstrap-20261001/prefix-new.img` | 16 MiB | composed full prefix (baseline + new region) | DEL (deterministic composition of published artifacts) |
| `p36-bootstrap-20261001/flash-artifact-report.json` | 10.6 M | per-candidate build/pack report (contains full hex changed-ranges dump) | KEEP (trim candidate: hash summary already in audit docs) |

## 7. extlinux snapshots

`board-backups/extlinux.conf.{pre,post}-promotion-*.conf` — archived
YES; KEEP locally (tiny, rollback context).

## 8. Linux journal/dmesg/pstore

`kernel-6.12.111/dmesg-*.log`, `board-runtime-config.config`,
`kernel-6.12.108/*-first-boot.log` — historical baselines, not archived.
KEEP (small, unique pre-OTA baselines).  pstore: empty on the board at
every check; nothing to archive.

## 9. Network

`network-20260929/`, `kernel-incident-20260930-remote/`,
`wireless/` (Tailscale upgrade watch, recovery probes, LAN diagnostics,
factory coldboot log) — contains real campus IPs and Tailscale IPs.
KEEP locally (unique incident context; contains live identifiers, not
published).  The Tailscale 1.102.4 upgrade conclusion is recorded in
progress.md.

## 10. Development

`p36-uboot-failsafe/` (patch copies, patched driver sources, candidate
payloads v1-v3 + env files, audits) — payload/env duplicated in
release assets and audits; KEEP v3 payload + audits, DEL superseded v1/v2
payload copies.  Build audit JSON/patch files are small provenance.

## Disposition summary

- archived → local DEL: 4 serial logs, 2 region/prefix images
- KEEP: live region backups, P4 sector dumps, TEST C round-2 log,
  kernel baselines, network incident logs, dev provenance
- net effect: logs/ shrinks by ~30 MiB (the two 16/4 MiB composed
  images dominate); the remainder is small, unique or load-bearing
