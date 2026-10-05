# EAIDK-310 eMMC prefix audit & raw boot-state region (P3.6-B)

Evidence-backed map of eMMC user-area LBA 0 … 0x8000 (0 … 16 MiB), the
candidate raw boot-state sectors, and the write-safety argument.  All
claims below were verified against four independent artifacts plus a live
read-only board check on 2026-10-01; nothing was written to the board.

## Evidence sources

| ID | Artifact | Hash anchor |
| --- | --- | --- |
| E1 | Factory full backup `backups/emmc-factory-20260901/` (sfdisk + 7.3 GB gz + boot areas) | user area `0BEA2312…BA68`, boot0/1 `BB9F8DF6…` |
| E2 | Current-board prefix backup `artifacts/eaidk310-emmc-20260901/control-prefix.img` (16 MiB) | `C466D977…92B1` |
| E3 | Rescue-TF image `eaidk-310-uboot.img` (16 MiB) | `6254986C…3EB` |
| E4 | Vendor flashable image `debian-bookworm-kernel-6.8.4-….img` (2.5 GB) | author image |
| E5 | Live board, read-only SSH check 2026-10-01 (`sfdisk -d`, `dd`-range hashes, CID) | — |
| E6 | Locked migration runbook `docs/runbooks/eaidk310-emmc-migration.md` + `tools/eaidk310_emmc_lib.py` | tail baseline `870A6910…3902` |
| E7 | Our U-Boot write tooling `tools/eaidk-uboot-write-lib.ps1` | writes 8–12 MiB only |
| E8 | Pinned U-Boot `PARTS_DEFAULT` (only used by `gpt write`, unused flow) | — |

## Layout map (current board, post-migration, 512-byte sectors)

| Start LBA | End LBA | Byte offset | Size | Owner | Evidence | Write-safe for bootstate |
| --- | --- | --- | --- | --- | --- | --- |
| 0 | 0 | 0x0 | 512 B | protective MBR | E2/E5 sfdisk | NO (GPT flows) |
| 1 | 1 | 0x200 | 512 B | primary GPT header (`EFI PART`) | E2 scan, E5 | NO |
| 2 | 2 | 0x400 | 512 B | GPT entries (2 partitions fit) | E2 scan (only LBA2 nonzero) | NO |
| 3 | 63 | 0x600 | 30 KiB | GPT entry-area padding | E2 (zero) | UNKNOWN-adjacent (GPT rewrites only touch header+entries; still NO) |
| 64 (0x40) | ≈250 | 0x8000 | ≈95 KiB | **idbloader** (TPL `…82000014…5253414b` + SPL headers visible) | E2 nonzero runs 0x40–0xfa; E5 | NO — BootROM/loader domain |
| ≈251 | 16383 | 0x1F400 | ≈7.9 MiB | **unused gap** (includes legacy env `0x3F8000` = 4,186,112 B, all-zero, P3.5-proven) | E2 scan, E5 | UNKNOWN — inside `loader1`-style window of `PARTS_DEFAULT`; excluded by conservative rule |
| 16384 (0x4000) | 24575 | 0x800000 | 4 MiB | **U-Boot slots ×4** (byte-identical 0.8 MiB `LOADER`-header payloads at 1 MiB stride, hash `19934F55…`) | E2 scan + slot compare | NO — this IS the U-Boot update window (E7, FLASH-PLAN) |
| 24576 (0x6000) | ≈24948 | 0xC00000 | ≈90 KiB | **ATF/BL31 backup copy #1** (`BL35X` header) | E2 scan, E4 (same convention) | NO — loader-repair domain |
| ≈24949 | 28671 | — | ≈1.83 MiB | **zero gap (zone A)** | E2 (all-zero), E5 (tail hash = baseline) | **YES — record A = LBA 0x6400 (25600)** |
| 28672 (0x7000) | ≈29049 | 0xE00000 | ≈90 KiB | **ATF/BL31 backup copy #2** | E2 scan, E4 | NO |
| ≈29050 | 32767 | — | ≈1.82 MiB | **zero gap (zone B)** | E2 (all-zero), E5 | **YES — record B = LBA 0x7800 (30720)** |
| 32768 (0x8000) | 557055 | 0x1000000 | 256 MiB | p1 `XBOOTLDR` (/boot, ext4) | E5 sfdisk live | NO |

Zone separation: record A sits ≥ 650 sectors (325 KiB) past ATF copy #1 and
record B sits ≥ 655 sectors past ATF copy #2, with 5,120 sectors (2.5 MiB)
between A and B — different eMMC erase groups by a wide margin.

## Why 12–16 MiB is provably out of every known write path

1. **Migration tooling (E6)**: the runbook's success criteria REQUIRE
   `12–16 MiB == 870A6910…3902` (locked "baseline tail") — the tool treats
   the tail as read-only by construction.  Live board (E5) still matches
   the baseline bit-for-bit after weeks of operation.
2. **U-Boot updates (E7 / FLASH-PLAN-P36)**: write window is 8–12 MiB only.
3. **Rescue-TF boots (E3)**: TF image never writes the eMMC.
4. **`gpt write` (E8)**: not used by any runbook, and writes only GPT
   structures (LBA 1/2 + backup header at disk end), never data LBAs.
5. **Full-firmware reflash (E1/E4)** — the honest exception: rkdeveloptool/
   SDDiskTool/vendor-image restores rewrite the whole prefix.  Proven
   harmless for safety: at LBAs 25600 and 30720 the **factory image,
   the TF image and the current-board baseline are ALL ZERO** (E2, E3, E4,
   E5), so any full reflash deterministically writes zeros = both records
   invalid = fail-closed stable boot.  A CRC collision needed to fake a
   valid armed record is ~2⁻³² and the refashed system is the vendor one
   anyway (our U-Boot and OTA no longer present).
6. Any *partial* overwrite (loader-repair tools touching ATF copies):
   records sit hundreds of KiB away from the ATF extents; and any corruption
   of our two sectors is caught by CRC32 → fail-closed stable.

## Bootstate record format (v1, 512 B, little-endian)

```
off  size  field
0    8     magic "EA310BS1"
8    1     format_version = 1
9    1     bootcount
10   1     upgrade_available
11   1     candidate_slot
12   4     sequence (monotonic; 0xffffffff = refuse to write, fail closed)
16   16    candidate_label
32   476   reserved (zeros)
508  4     crc32 over bytes 0..507 (zlib poly = U-Boot crc32())
```

Selection: usable = magic ∧ version ∧ crc.  Highest sequence wins; equal
sequences with differing payload reject BOTH copies (fail closed).  Store
never touches the selected copy — the write targets the other sector with
sequence+1, so a torn write costs at most the newest increment and the
surviving copy keeps counting (no uncounted candidate trials).  If the
store cannot persist, `bootcount_stored` stays 0 and the compiled policy
stays on stable.

Device identity gate (hardware only): eMMC must report
manfid `0xd6`, product name `HBD08G`, capacity ≥ `0x1d1f000000` —
verified against the live CID `d60103484244303847390e14d12f5655` (E5) and
factory `metadata.txt` (E1).  Any mismatch → no reads count, no writes,
stable boot.

## Update obligations for future tooling (flash-flow §9)

* Any new full-prefix/full-image flash path MUST, after writing, either
  preserve LBAs 0x6400/0x7800 or explicitly reinitialize them to zeros
  (SAFE = stable, upgrade_available=0).
* `eaidk310_emmc_lib.py` migration runs after a bootstate exists must
  accept `baseline-tail == zeros OR valid SAFE records` in addition to the
  locked `870A6910…` baseline, or snapshot/restore the two sectors around
  the migration.
* Ordinary U-Boot updates (8–12 MiB window) need no change.
