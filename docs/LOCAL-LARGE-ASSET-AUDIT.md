# Local large-asset audit — 2026-10-06 (P6 PHASE 8)

Point-in-time inventory of large binary assets in the local workspace
`D:/Project/EAIDK310` (not part of the Git repository).  SHA-256 values
are truncated to 16 hex chars here for readability; full values were
verified during P6.

## Factory images / full dumps

| path | size | SHA256 | type / purpose | GitHub archived | safe_to_delete_local |
| --- | --- | --- | --- | --- | --- |
| `debian-bookworm-kernel-6.8.4-eaidk-310-rk3328.img` | 2.5 GiB | `c42f7a77fcae9e9c` | decompressed 6.8.4 vendor flash image (P3.6-B layout audit source) | NO (only `.img.xz` is) | **YES** (recompressible from the `.xz`) |
| `debian-bookworm-kernel-6.8.4-eaidk-310-rk3328.img.xz` | 505 MiB | `23edecf91e089593` | vendor 6.8.4 flash image | **YES** — v2026.09.05 asset, digest matches | **YES** |
| `backups/emmc-factory-20260901/mmcblk2-user-area.img.gz` | 1.7 GiB | `e0c92b0c8e2c8afd` | original full eMMC user-area dump (factory state) | NO | **YES** (user-authorized; layout facts extracted to docs, sector hashes recorded in the audit trail) |
| `backups/emmc-factory-20260901/mmcblk2boot0.img` / `boot1.img` | 4 MiB ea. | `bb9f8df61474d25e` (both identical, all-zero) | eMMC hardware boot partitions | NO | **YES** (verified all-zero; content is trivial) |
| `eaidk-310-uboot.img` | 16 MiB | `6254986c3e1e12d9` | TF rescue prefix baseline (failsafe-era predecessor) | **YES** — v2026.09.05 asset, digest matches | **YES** (superseded by v2026.10.06 failsafe-raw region image) |
| `backups/eaidk310-prefix-backups/*.img` (2×) | 16 MiB ea. | `6254986c3e1e12d9` | duplicate copies of the same TF prefix baseline | YES (same digest as above) | **YES** (duplicates) |
| `artifacts/eaidk310-emmc-20260901/control-prefix.img` | 16 MiB | `c466d977606598a7` | current-board 16 MiB prefix baseline (P3.6-B raw-region audit source) | NO | NO — small and load-bearing: P3.6-B `EMMC-RAW-REGION.md` evidence chain references its exact hash |

## Kernel bundles (regenerable from source-locked pipelines)

| path | size | SHA256 | GitHub archived | safe_to_delete_local |
| --- | --- | --- | --- | --- |
| `github/.../kernel/linux-6.18.54-zramfix1/artifacts/*.tar.zst` | 50 MiB | `989093725bdec974` | **YES** — v2026.10.06 asset | YES (also kept in `release/v2026.10.06/`) |
| `github/.../kernel/linux-6.12.111-zramfix1/artifacts/*.tar.zst` | 48 MiB | deleted in P5 | NO | YES (pipeline rebuilds it) |

## Kernel 108/111 local copies

Pipeline sources (config/DTS/patches/scripts/tests/source-lock) are Git
tracked and preserved.  Generated artifacts (build trees, Image/uInitrd/
DTB copies, modules staging) were already removed in P5; nothing large
remains.

## Kept intentionally (with reason)

| path | size | reason |
| --- | --- | --- |
| `release/v2026.10.06/` | ~59 MiB | current official release assets (local mirror of the GitHub Release) |
| `logs/p4-final-closeout/`, `logs/p4-618-repro/` (serial logs) | <1 MiB | original un-sanitized evidence for the published sanitized copies |
| `logs/p36-bootstrap-20261001/board-backups/` | 33 MiB | live eMMC region backups taken immediately before the two U-Boot flashes (rollback artifacts) |
| `backups/emmc-factory-20260901/` small files (sfdisk, manifest, reports) | <1 MiB | provenance metadata for the deleted dump |
| `vendor/` | see VENDOR-ASSET-INVENTORY | audited separately |
