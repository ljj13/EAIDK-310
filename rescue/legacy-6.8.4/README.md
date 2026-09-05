# Linux 6.8.4 binary recovery baseline

This directory describes the original EAIDK-310 Debian Bookworm rescue image and the checked 16 MiB boot prefix. The image was published by `yjdwbj/rockchip-eaidk-310` in release `v1.0`; its original URL, byte size and SHA-256 are locked in `manifest.json`.

The image contains Linux `6.8.4-rk3328` and can restore a bootable rescue TF. It is a binary recovery baseline, not a source-reproducible kernel: the original release includes image and binary kernel packages but no complete source tree proven to produce the exact kernel binary.

The 16 MiB `eaidk-310-uboot.img` is independently useful for restoring the checked GPT/loader/U-Boot prefix. Writing either asset is destructive to the selected card; follow `docs/rescue-tf.md` and verify the card identity before writing.
