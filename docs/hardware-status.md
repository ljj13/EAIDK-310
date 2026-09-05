# Hardware status

## Validated with Linux 6.12.108 zramfix1

- Four Cortex-A53 CPUs and approximately 1 GiB RAM
- TF and HBD08G eMMC enumeration
- Independent HBD08G eMMC root/boot operation with no TF inserted
- Ethernet and SSH
- systemd server boot
- 384 MiB LZ4 zram, priority 100
- USB host enumeration
- HDMI ALSA nodes
- GPIO and I2C controllers
- RK805 RTC access improved compared with the 6.8.4 baseline
- nftables kernel support
- Automatic extlinux selection of the Linux 6.12.108 eMMC default

## Known unresolved paths

- Onboard CYW43455 Wi-Fi does not enumerate as an SDIO function in the current mainline board path; no `wlan` interface is claimed.
- Bluetooth has no working HCI device. Firmware presence alone does not resolve the missing platform initialization.
- The external 240×320 ST7789 SPI panel binds and receives SPI traffic in the recorded tests but remains white. Physical wiring, signal integrity and panel-specific initialization remain pending.

These unresolved devices are deliberately non-gating for the kernel/eMMC promotion because they were already unresolved on the accepted baseline. Do not interpret a driver module or device-tree node as proof that the physical device works.
