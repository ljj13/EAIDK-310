# EAIDK-310 Rescue TF — P10 build and validation record

Status: **READY_FOR_PHYSICAL_VALIDATION** (the card must be physically
written and boot-tested; everything before that is done and verified).

## Provenance

| component | source | pinned by |
|---|---|---|
| idbloader (32 KiB…8 MiB) | extracted from the validated failsafe U-Boot on the board's eMMC (`/dev/mmcblk2` LBA 64…16384) | `b0986fbe7129739c08eee9e51fd1ce49b5a241924349e2e49b2304454c4a6a6a` |
| U-Boot FIT (8…12 MiB) | same eMMC, LBA 16384…24576 | `ffeedcb6607c4cda2e9a87754fec875fd24fc8499af573ad4b865587033b4904` |
| kernel 6.12.108-eaidk310-zramfix1 (Image/uInitrd/DTB/modules) | GitHub release v2026.09.05 bundle | byte-compared during verify |
| rootfs | Debian trixie minbase, qemu-debootstrap from deb.debian.org | manifest records suite + mirror |
| access | `root` over ssh via baked `authorized_keys` (public key only) + root autologin on ttyS2 | no secrets in the repo |

## Layout

GPT, first partition at LBA 24576; loader windows identical to the eMMC
boot geometry (`ota/docs/FLASH-PLAN-P36.md`): idbloader @ LBA 64,
u-boot.itb @ LBA 16384 — so `boot_targets=mmc1…` boots the card without
touching eMMC.  p1 = 512 MiB ext4 `/boot`, p2 = ext4 `/`.

## Build + verify (WSL, root)

```bash
wsl.exe -u root -e bash -c 'cd <repo> && \
  OUT_DIR=<out> IDBLOADER_IMG=<idb> UBOOT_IMG=<ubt> \
  KERNEL_BUNDLE=<bundle.tar.zst> AUTHORIZED_KEYS=<id_ed25519.pub> \
  bash rescue/build-rescue-tf.sh'
wsl.exe -u root -e bash -c 'bash rescue/verify-rescue-tf.sh \
  <out>/<img> <bundle.tar.zst> <idb> <ubt>'
```

## Loopback verification (done, twice)

- loader bytes byte-identical to source (both windows)
- geometry: p1 starts at LBA 24576
- `e2fsck -fn` clean on both filesystems
- Image/uInitrd/DTB byte-identical to the release bundle
- extlinux default `rescue-6.12.108-eaidk310-zramfix1` + serial console
- python3 3.13.5 and the toolkit run under qemu-user inside the image
- ssh `PermitRootLogin prohibit-password`, authorized_keys non-empty,
  serial-getty@ttyS2 root autologin override present, UUID fstab
- `manifest.json` hash/size match the image (double-run stable)

Image built 2026-10-07:
`eaidk310-rescue-tf-20261007.img`, 1.6 GiB,
SHA-256 `859da2c33fa9b020056be5a8de83589586fce2dd84d594f571c06deece3130a7`
(`rescue-build/manifest.json` + `SHA256SUMS` alongside).

## On-card toolkit: `eaidk-rescue`

`status`, `gpt`, `fsck` (eMMC both partitions, refuses while mounted),
`mount-root`/`umount-root`, `extlinux-check`, `bootstate` (RAW bootstate
inspect via the installed `eaidk-bootstate`), `kernel-install
BUNDLE.tar.zst [--yes]` (dry-run default), and `uboot-restore FILE
--window {idbloader|uboot} --yes` — the only raw writer; requires all
three arguments plus typing `RESTORE`, then verifies the readback.

## Remaining physical steps

1. Write the image to a TF card (dd/Etcher; the card becomes the boot
   device because U-Boot scans mmc1 before eMMC).
2. Boot the board from the card (power cycle, no eMMC change), confirm
   serial console autologin and ssh with the baked key.
3. Exercise `eaidk-rescue status/gpt/bootstate` against the real eMMC.
4. Record evidence and flip this file to PHYSICALLY VALIDATED.
