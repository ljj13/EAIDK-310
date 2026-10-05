# Fail-safe boot backend comparison & decision (P3.6-B §8/§12)

Candidates (both built from pinned `v2024.07-rc1@38ea74d6`, both keep
`CONFIG_BOOTCOUNT_BOOTLIMIT=1`, compiled `bootlimit`/`altbootcmd`/dual-condition
`bootcmd` policy, zero persistent-environment dependency):

* **Candidate A — `BOOTCOUNT_EXT`** (`failsafe-bootcount-fs`): upstream
  driver, 4-byte record on the /boot ext4 filesystem
  (`/eaidk-ota/bootcount.bin`), hardened by patch 0002.  Payload
  `dee563aa…903f` (812,288 B).
* **Candidate B — `RAW_REDUNDANT`** (`failsafe-raw`): custom
  `bootcount_eaidk310_raw` driver (patch 0003), dual CRC32-protected
  512-byte records at eMMC LBAs 0x6400/0x7800 inside the audited
  12–16 MiB tail (`EMMC-RAW-REGION.md`).  Payload
  `427f24028ec49242433d405e4109a537e35da1c59d1be7f96e237d80ef35743c`
  (813,584 B; double-build byte-identical; default env
  `b6f46846…4d589`); device lookup via the DM-native
  `blk_get_devnum_by_uclass_idname` (the legacy registry is empty in
  DM-only builds and would fail every lookup closed).

## Decision table

| Dimension | BOOTCOUNT_EXT (A) | RAW_REDUNDANT (B) | Edge |
| --- | --- | --- | --- |
| Source delta vs control | ~30 lines (3 fail-closed patches + env export) | ~300 lines new driver + Kconfig + identity gate | **A** |
| Upstream code reuse | full upstream driver | blk API only; selection/store logic custom | **A** |
| Power-loss safety | single record, rewritten in place; ext4 block write not atomic-guaranteed; torn write → magic/version usually survive but data bytes may tear; `bootcount_stored` guard prevents uncounted trials | dual-copy + sequence + CRC32; any torn write leaves the other copy authoritative; provable "at most newest update lost" | **B** |
| metadata_csum dependency | **yes — hard**: U-Boot refuses to write; needs one-time on-site `tune2fs -O ^metadata_csum` on live /boot (e2fsck -f ×2) or the backend silently degrades to always-stable | none | **B** |
| Filesystem dependency | ext4 driver correctness (upstream, widely tested) | none | **B** |
| Environment dependency | none | none | tie |
| Corruption detection | magic+version+exact-length only — **no checksum**: a random byte flip inside the 4 data bytes with intact magic is accepted (could spuriously arm) | CRC32 over the whole payload; any corruption rejected | **B** |
| Write amplification / wear | one ext4 write (journal commit, ~KBs) per trial boot | one 512 B sector write per trial boot | **B** (both negligible) |
| Reflash-overwrite risk | state dies only with /boot destruction (full reflash) | state dies on any prefix rewrite; **proven**: factory image, TF image and live baseline are all ZERO at the two LBAs → any full reflash = deterministic fail-closed stable | **A** (slightly; B's exposure is bounded and fail-closed) |
| Recovery from bad state | `rm` the file | `dd zeros` onto two LBAs | tie |
| Debuggability | `cat`/`od` a file under /boot | `dd` sector reads | **A** |
| Future maintenance | upstream driver evolves with the tree; our delta stays ~30 lines | driver is EAIDK-310-specific forever | **A** |
| Fail-closed strength | strong (3 patched paths + length + stored-guard) | strongest (checksum + dual copy + sequence + identity gate + stored-guard) | **B** |
| On-site ops required before first arm | `/boot` metadata_csum check + tune2fs surgery on the live boot fs | `mmc list` + CID/capacity identity confirm (read-only) | **B** |

## Decision

```
FAILSAFE_BACKEND_RECOMMENDED=RAW_REDUNDANT
```

Rationale, strictly from evidence:

1. **The decisive operational difference is the metadata_csum blocker.**
   Candidate A cannot arm on the board's /boot until an on-site feature
   surgery (`e2fsck -f` ×2 + `tune2fs -O ^metadata_csum`) is performed on
   the *live boot filesystem* — the single most invasive step in the whole
   rollout, on the only boot fs, with a bricked-boot risk if interrupted.
   Candidate B removes that step entirely; its on-site prerequisites are
   read-only (`mmc list`, CID check — already verified remotely).
2. **B's fail-closed surface is strictly larger.** A accepts any
   magic/version-consistent 4 bytes (no checksum → a corrupt record can
   spuriously arm); B rejects anything that fails CRC32, sequence
   monotonicity or dual-copy agreement, and its dual-copy store makes
   power-loss outcomes provably bounded ("at most the newest update
   lost"), verified by the torn-write matrix.
3. **B's one structural risk — region overwrite — is measured, not
   assumed**: both candidate sectors are zero in the factory image, the
   rescue TF image and the live board baseline; every routine write path
   (U-Boot window 8–12 MiB, migration tool, TF path, `gpt write`) provably
   does not touch them; the only flows that do (full-firmware restores)
   write deterministic zeros = fail-closed stable, and §9 tooling updates
   make that reinitialization explicit.
4. A is **kept, not discarded**: it remains built, tested and flashable.
   If the on-site STEP 1 (storage-mapping verification) contradicts the
   raw-region audit, or the identity gate rejects the eMMC, the runbook
   falls back to candidate A + the tune2fs remedy.  Both candidates ship
   with identical compiled policy, so the userspace (eaidk-ota) treats
   them symmetrically via the BACKEND marker.

`RAW_REGION_AVAILABLE=YES`, `RAW_BACKEND_BUILD_READY=YES` — per §12 these
would have been `NO` only if no 100%-evidenced region existed; the audit
in `EMMC-RAW-REGION.md` meets the bar (four independent artifacts + live
read-only verification + explicit coverage of the one overwrite path).

Board activation stays `NoBackend` until the on-site runbook completes;
`REMOTE_OTA_ELIGIBLE` remains **NO** regardless of this recommendation.
