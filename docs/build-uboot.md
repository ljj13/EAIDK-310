# Build U-Boot for EAIDK-310

The U-Boot project pins upstream tag `v2024.07-rc1` at commit `38ea74d6d5c05224acdb03f799897c1bdd56f8cc`. The maintained patch adds the EAIDK-310 control and SDIO-handoff variants.

```bash
git clone https://github.com/u-boot/u-boot.git /home/Fog/src/u-boot-v2024.07-rc1
git -C /home/Fog/src/u-boot-v2024.07-rc1 checkout --detach 38ea74d6d5c05224acdb03f799897c1bdd56f8cc
bash bootloader/u-boot-eaidk310/scripts/verify-source-and-patch.sh /home/Fog/src/u-boot-v2024.07-rc1
bash bootloader/u-boot-eaidk310/scripts/build-cross.sh \
  /home/Fog/src/u-boot-v2024.07-rc1 /home/Fog/build/eaidk310-uboot
```

For a native ARM64 build on the board, use `build-native.sh` and keep its fixed `-j1` memory-pressure protection. Packaging and physical prefix writing are separate steps. `tools/write-uboot-proper-to-tf.ps1` changes only the validated 8–12 MiB U-Boot proper region; `tools/write-uboot-to-tf.ps1` restores the checked complete 16 MiB prefix. Both require explicit disk identity parameters and read-back verification.

The historical SDIO-handoff experiment did not restore the onboard Wi-Fi module. It is retained as diagnostic provenance, not as a recommended wireless fix.
