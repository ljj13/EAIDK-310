# eMMC kernel upgrade and recovery

The stable target is Linux `6.12.108-eaidk310-zramfix1`. It was first validated as a versioned, non-default rescue-TF entry. eMMC promotion uses the same Image, uInitrd, DTB and module tree as one coherent unit.

## Promotion safety gates

Run the board from rescue TF and keep the HBD08G eMMC unmounted. The deployer must verify:

- the running root and `/boot` are on the TF, not eMMC;
- the target device reports HBD08G and matches the recorded capacity;
- BOOT UUID is `cbdb447a-125d-4d30-bc3d-07807a1f4578`;
- ROOTFS UUID is `781e1dc3-166b-46e9-8578-4b9c003d7305`;
- stable 6.8.4 Image, uInitrd, DTB and extlinux hashes still match the preflight baseline;
- bundle SHA-256 is `e61ba8aa0f095658c6557be4ee108a7a31709b65dba81629c621571907a53cfa`;
- space is sufficient and no stale temporary or symlink destination exists.

Run `kernel/linux-6.12.108-zramfix1/scripts/deploy-emmc.sh` without `--apply` first. A real write requires `--apply`, the exact bundle hash and the extlinux hash accepted from that dry-run. The script creates `/boot/rollback/6.8.4-pre-6.12.108/`, uses temporary paths and read-back hashing, writes a coherent extlinux entry with a three-second automatic-selection timeout, and syncs. The operator must then unmount both eMMC filesystems before shutdown.

## Runtime acceptance

Remove the TF before powering on. Confirm the exact kernel release, eMMC root/boot mount sources, systemd running state, zero blocking failed units, active LZ4 zram at 384 MiB and priority 100, Ethernet/default route and an SSH reconnect using a previously verified host identity.

The recorded no-TF acceptance passed all of those checks. It also captured a complete serial reboot showing U-Boot automatically selecting `rockchip-kernel-6.12.108-eaidk310-zramfix1` after `timeout 30`, without operator input. See `evidence/emmc-6.12.108-zramfix1-acceptance.json`.

## Recovery

If eMMC does not boot, recreate the 6.8.4 rescue TF using `docs/rescue-tf.md`, start the board from TF, keep eMMC unmounted until identity checks pass, and restore the four boot files/extlinux plus old module information from `/boot/rollback/6.8.4-pre-6.12.108/`. Do not copy only an Image: kernel, initramfs, DTB and modules must remain a matched set.
