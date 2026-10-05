# Repository status audit — 2026-10-06

Audit performed at P6 (repository consolidation) start, on commit
`7f9bc87` (tag `v2026.10.06`).  Historical Git tags, GitHub Releases and
Git history are preserved untouched; this audit only classifies *current
documentation* state.

## Kernel / version references

| Artifact | References found | Verdict |
| --- | --- | --- |
| `README.md` | 6.8.4 / 6.12.108 as "current", v2026.09.05 as latest | **STALE** — rewritten this round |
| `docs/build-linux.md` | 6.12.108 as primary build target (14 refs) | **STALE** — rewritten (6.18.54 primary, 108/111 as references) |
| `docs/rescue-tf.md` | 6.8.4 factory image write-card flow (5 refs) | **STALE** — rewritten to current rescue model |
| `docs/emmc-recovery.md` | 6.8.4 full image as recovery path (5 refs) | **STALE** — rewritten to OTA/rollback/rescue model |
| `docs/hardware-status.md` | validated against 6.12.108 (3 refs) | **STALE** — updated to 6.18.54 validation |
| `docs/build-uboot.md` | pre-P3.6 patch stack (no 0003/0004) | **STALE** — rewritten (0001-0004, failsafe-raw) |
| `ota/README.md` | BLOCKED-era sections mixed with production (3 refs) | **STALE** — rewritten (production state only, history moved) |
| `releases/v2026.09.05/` | tracked metadata duplicate of GitHub Release | **REMOVE_FROM_CURRENT_DOCS** — replaced by `docs/releases.md`; GitHub Releases remain the single source of truth (the GitHub Release itself is preserved untouched) |
| `evidence/*6.12.108*` | 6.12.108 acceptance JSONs | **HISTORICAL_BUT_VALID** — moved to `evidence/history/6.12.108/` |

## Status keywords

- `BLOCKED` / `not flashed` / `future P3.6` remnants: `ota/README.md`
  (superseded — backend is RAW_REDUNDANT, failsafe U-Boot is flashed and
  validated).
- `REMOTE_OTA_ELIGIBLE` now YES (kernel scope); `UNATTENDED_...=YES`.
- 6.8.4 references that remain anywhere are either Git history, the
  binary recovery baseline note, or historical incident evidence — none
  are presented as current.

## Decisions

1. Current docs describe **6.18.54 STABLE** with the three-role model
   (RESCUE 108 / PREVIOUS-KNOWN-GOOD 111 / STABLE 6.18.54).
2. Historical accuracy is preserved via Git history and the
   `v2026.09.05` GitHub Release; stale *current-claim* text is removed.
3. GitHub Releases (`v2026.09.05`, `v2026.10.06`) and all assets are
   preserved unmodified.  No tag retargeting, no history rewrite.
