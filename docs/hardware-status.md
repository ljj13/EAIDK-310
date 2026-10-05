# Hardware status

## Validated with Linux 6.18.54-eaidk310-zramfix1

- Four Cortex-A53 CPUs, ~1 GiB RAM
- HBD08G eMMC (user area + boot partitions) and TF enumeration
- eMMC-only operation with no TF inserted
- Ethernet (rk_gmac-dwmac, 100M/1G, zero-error links)
- USB host enumeration (xHCI/EHCI)
- zram LZ4 swap (384 MiB)
- nftables (ruleset loads; iptables-nft compatible)
- DesignWare hardware watchdog (dw_wdt, 28.6 s max TOP, handoff proven)
- thermal zones and cpufreq (schedutil) under OTA load and idle
- RTC-backed time via network sync
- SSH and Tailscale (auto recovery after every OTA reboot cycle)
- automatic extlinux default boot of 6.18.54

## Known unresolved

- Onboard Wi-Fi: the SDIO power-sequencing experiment (failsafe
  `sdio-handoff` variant) did not restore the CYW43455; no `wlan`
  interface.  Retained as diagnostic provenance.
- Bluetooth: depends on the same unresolved Wi-Fi module bring-up.
- ST7789 display panel: experimental driver test only
  (see hardware/st7789); not integrated into the boot path.
