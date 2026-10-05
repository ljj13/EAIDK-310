# P3.6 fail-safe U-Boot — future on-site flash plan (DO NOT EXECUTE REMOTELY)

This document is a **plan only**.  Every step requires the operator to be
physically present with: serial console (CH340, 1.5M 8N1), power control,
and ideally a prepared rescue SD.  No step may run over Tailscale/SSH.

## 0. Artifacts (built, verified, frozen)

From `~/eaidk310-uboot/p36-candidateA/` (deterministic rebuild verified):

| Artifact | Bytes | SHA-256 |
| --- | --- | --- |
| `failsafe-bootcount-fs/u-boot-dtb.bin` | 812,288 | `dee563aacfac048bd0f28f3f004168a11d99cb1a633f8e823f2162bb8ec7903f` |
| `failsafe-bootcount-fs/u-boot-initial-env` | — | `28e71ad7abe4e5d505fe3251597f2e3e48eb244a11ddf5aa87d13477baa2b24d` |
| `control/u-boot-dtb.bin` (reference) | 809,984 | `f7f04704497c154aedab61f92228248f75c3298384464dc3a94b114d394c1c4f` |
| `*/dts/dt.dtb` (byte-identical) | 38,520 | `20cfb9790cdadebad9c56aa31fbfcf9f10360654f98dd714ba342788863e96ee` |

**Critical artifact-type check (PRE-FLASH step 5):** the eMMC U-Boot-proper
region must receive the same artifact *type* that is currently there.
The board SPL is built with `SPL_ATF`/`SPL_LOAD_FIT`, which implies the
deployed image at 8 MiB is the **FIT blob (`u-boot.itb`)**, not the legacy
`u-boot-dtb.bin`.  The candidate A directory therefore must provide the FIT
artifact for flashing; `u-boot-dtb.bin` is the build-verification payload
(size gate, strings audit) — flash it **only** if the backup shows a legacy
(non-FIT) image.  Decide from evidence, never from habit.

## 1. eMMC regions involved

| Region | Offset | Size (window) | Content today | Action |
| --- | --- | --- | --- | --- |
| idbloader (TPL+SPL) | sector 64 (32 KiB) | 32 KiB … 8 MiB | stock SPL+TPL+ATF loader | **untouched** |
| U-Boot proper | sector 16384 (8 MiB) | 8 MiB … 12 MiB (4 MiB window) | stock control U-Boot (FIT) | backup → flash failsafe → verify readback |
| U-Boot env | 0x3F8000 (~4.1 MiB) | 8 KiB | all-zero, unused (default env every boot) | **untouched** — fail-safe design needs no env writes |
| /boot partition 1 | GPT/MBR part 1 | ≈230 MiB ext4 | extlinux, kernels | later OTA phase (not in this flash) |

Sizes: failsafe payload 812,208 B ≪ 4 MiB window.  SPL size gate
(`≤ 1,046,528`) enforced at build time.

## 2. PRE-FLASH CHECKLIST (all must be true on-site)

1. [ ] Serial console works at 1.5M 8N1 and U-Boot interrupt (key press at
       `bootdelay`) is reachable — proven live, not assumed.
2. [ ] `mmc list` output captured and **confirm eMMC dev number = 0**
       (DTS alias `mmc0=&emmc`).  If eMMC is NOT dev 0, STOP: rebuild with
       corrected `CONFIG_SYS_BOOTCOUNT_EXT_DEVPART` (one line) first.
3. [ ] `mmc dev <emmc>; mmc info` captured; board healthy, Tailscale up.
4. [ ] Rescue SD prepared and boot-tested separately (control U-Boot +
       rescue system), then REMOVED for the flash itself.
5. [ ] Identify deployed U-Boot-proper image type from the backup (step 3.1):
       FIT magic (`d00dfeed` at FIT header / `dumpimage -l` parses) → flash
       `u-boot.itb`; legacy raw → flash `u-boot-dtb.bin`.  Record decision.
6. [ ] Bundle hashes recomputed on-site against the table in §0; mismatch → STOP.
7. [ ] Power stable; no other SSH sessions; operator has physical reset/power.
8. [ ] **/boot ext4 write-compatibility check**: `dumpe2fs -h /dev/mmcblk2p1
       | grep features` — if `metadata_csum` is present (likely: default
       since e2fsprogs 1.43/2016), U-Boot v2024.07 cannot persist the
       bootcount there and an armed trial would repeat forever.  Remedy
       (online, safe, BEFORE flashing the new U-Boot):
       ```sh
       systemctl isolate rescue.target      # or: umount /boot after stopping boot-mounters
       umount /boot
       e2fsck -f /dev/mmcblk2p1
       tune2fs -O ^metadata_csum /dev/mmcblk2p1
       e2fsck -f /dev/mmcblk2p1             # mandatory after feature change
       mount /boot && dumpe2fs -h /dev/mmcblk2p1 | grep -c metadata_csum   # must be 0
       ```
       `eaidk-ota` will refuse to arm (`arm` gate) while metadata_csum is
       present, so this is a hard prerequisite for the OTA phase, not
       optional hygiene.

## 3. Backup (before any write)

```sh
# on the board, as root, eMMC unmounted-partitions read-only access is fine
mmc dev 0                     # confirm device
dd if=/dev/mmcblk2 of=/var/lib/eaidk-ota/backups/uboot-region-8M-12M.img \
   bs=1M skip=8 count=4
sha256sum /var/lib/eaidk-ota/backups/uboot-region-8M-12M.img
dd if=/dev/mmcblk2 of=/var/lib/eaidk-ota/backups/idbloader-32K-8M.img \
   bs=1M skip=0 count=8
sha256sum /var/lib/eaidk-ota/backups/idbloader-32K-8M.img
# copy both backups OFF the board (scp to PC) before flashing
```

Rollback artifact = `uboot-region-8M-12M.img` (byte-exact).  Its SHA-256 is
the reference for POST-FLASH verification of the restore path.

## 4. Flash (only after §2 complete)

```sh
# write ONLY the U-Boot proper window; conv=fsync; never the idbloader
dd if=u-boot.itb of=/dev/mmcblk2 bs=1M seek=8 conv=fsync notrunc
sync
# readback verification
dd if=/dev/mmcblk2 of=/tmp/readback.bin bs=1M skip=8 count=4
cmp /tmp/readback.bin u-boot.itb-padded || echo MISMATCH-ABORT
```

(`u-boot.itb-padded` = candidate artifact zero-padded to exactly 4 MiB so
`cmp` compares the whole window.  Prepare it on the PC before the session.)

## 5. POST-FLASH CHECKLIST

1. [ ] Power-cycle.  Serial shows the new U-Boot banner; version string
       checked (`version` command at U-Boot shell).
2. [ ] `printenv` captured: `bootlimit=1`, `bootcount_file=`,
       `altbootcmd=`, `bootcmd=` policy present; `boot_targets` unchanged
       (`mmc1 mmc0 …`).
3. [ ] Unarmed behaviour: normal boot reaches Debian 6.12.108 default
       exactly as before (bootflow scan → extlinux default).  No bootcount
       error spam on serial after `bootcount.bin` absent.
4. [ ] `ls mmc 0:1 /eaidk-ota` — directory present, no bootcount.bin yet;
       U-Boot must NOT create one (fail-closed proof on real hardware).
5. [ ] Deploy `/usr/local/sbin/eaidk-bootstate`; `eaidk-bootstate arm
       /boot/eaidk-ota/bootcount.bin` → serial retry shows one candidate
       attempt → fallback (TEST A, per `ota/docs/REHEARSAL-PLANS.md`).
6. [ ] Only after TEST A/B pass: `printf 'BootcountFsBackend 1\n' >
       /boot/eaidk-ota/BACKEND` (backend activation, P3.6 closure).

## 6. RECOVERY PROCEDURE (failure during/after flash)

| Symptom | Recovery |
| --- | --- |
| Board boots, U-Boot shell reachable | interrupt bootdelay → `mmc dev 0; load mmc 0:2 ${loadaddr} /var/lib/eaidk-ota/backups/uboot-region-8M-12M.img` is NOT possible into eMMC without write cmds → instead boot the **rescue SD** (insert, power-cycle; `boot_targets` puts SD first), then from rescue Linux: `dd if=uboot-region-8M-12M.img of=/dev/mmcblk2 bs=1M seek=8 conv=fsync` + readback `cmp`. |
| U-Boot banner on serial but no boot (policy script error) | interrupt at bootdelay → `setenv bootcmd bootflow scan; bootflow scan` boots stable → then reflash stock region from rescue SD/Linux as above. |
| No serial output at all (pre-console crash) | BootROM will NOT fall through to SD (eMMC loader still valid) → **maskrom USB recovery on-site**: short maskrom pads / button per EAIDK-310 manual, `rkdeveloptool rl` backup → `rkdeveloptool wl 0x2000 <backup>` write-back of the 8–12 MiB window (or full image), then power-cycle.  This is the only path for this scenario; it is why the flash is on-site-only. |
| extlinux/eMMC /boot damaged (unrelated to U-Boot) | rescue SD → rescue Linux → eaidk-ota backups + extlinux.conf.bak restore (P3 backups already on board). |

Rules that do not change: never write the idbloader region, never write
0x3F8000 env region, never `saveenv`/`fw_setenv`, never rewrite
/boot kernels outside the OTA tooling.

## 7. Explicitly NOT part of this plan (future, separate decision)

* Candidate B (env-backed bootcount): not needed — candidate A fully
  implements trial/fallback without persistent env.
* Rebuilding U-Boot with `BOOTCOUNT_LIMIT` for the **rescue SD**: rescue
  must stay stock-control; only the eMMC payload carries failsafe logic.
* P4 (6.18.54 on-device A/B): remains frozen until the fail-safe U-Boot is
  flashed and TEST A/B are green on-site.
