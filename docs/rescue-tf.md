# Recreate an EAIDK-310 rescue TF

## Fast recovery from the archived 6.8.4 image

1. Download `debian-bookworm-kernel-6.8.4-eaidk-310-rk3328.img.xz` and `SHA256SUMS` from release `v2026.09.05`.
2. Verify SHA-256 before opening any disk-writing tool. The expected image digest is `23edecf91e089593e0ad5539163e42be3e354c61ddfe11fec8083167900b3a62`.
3. Decompress the image or let a trusted imaging tool read the XZ file. The uncompressed image is approximately 2.5 GiB.
4. Identify the TF by its physical capacity and hardware serial. Disconnect unrelated removable media where practical.
5. Write the image with Rufus, balenaEtcher or an equivalent raw-image writer. This overwrites the chosen card.
6. Re-read the written bytes or use the imaging tool's verify option. Do not insert the TF into the board until verification succeeds.

The archived image is the original public Debian Bookworm/Linux 6.8.4 baseline. Treat its default credentials as installation defaults: change them before exposing a restored system to an untrusted network, regenerate SSH host keys when appropriate, and install only your own authorized keys.

## Restore only the checked boot prefix

`eaidk-310-uboot.img` is exactly 16 MiB and has SHA-256 `6254986c3e1e12d942d35769a8d8182422a017b6ca392237d0f284b31490a3eb`. The repository includes `tools/write-uboot-to-tf.ps1`, which checks disk number, capacity and expected serial and then verifies the bytes it wrote. Use it only when the partition filesystems are already correct and only the loader/U-Boot prefix needs restoration.

## Add the rebuilt 6.12.108 kernel

After the 6.8.4 image boots, build or download `eaidk310-linux-6.12.108-eaidk310-zramfix1.tar.zst`, verify SHA-256 `e61ba8aa0f095658c6557be4ee108a7a31709b65dba81629c621571907a53cfa`, and follow `kernel/linux-6.12.108-zramfix1/README.md`. Its deployer defaults to dry-run and keeps the 6.8.4 entry available unless an eMMC promotion is explicitly selected.
