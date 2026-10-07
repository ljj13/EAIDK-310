# Hardware status

## Validated with Linux 6.18.55-eaidk310-wifi3

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
- extlinux default boot of the committed candidate (5/5 warm reboots)
- CYW43455 SDIO enumeration at 1-bit high-speed and chip identification
  (BCM4345/6) — see the wireless section below

## Wireless — P9 outcome (2026-10-07)

Three software root causes were fixed and proven through the OTA trial
chain (details in `docs/P9-WIRELESS-BASELINE.md`):

- `sdio-pwrseq` had no post-power-on delay (CMD5 raced WL_REG_ON),
- requesting UHS/S18R suppressed the CMD5 response entirely,
- `CONFIG_COMMON_CLK_RK808` was unset, leaving the Bluetooth probe in a
  silent permanent deferral (no `lpo` clock provider → hci_uart_bcm
  never probed).

After the fixes the SDIO card enumerates every boot and identifies as
BCM4345/6 in 1-bit mode, and `hci0` is created with the full probe
sequence.  What remains is **below the software layers** and matches a
degraded module: 4-bit SDIO data lines (D1–D3) return `0xffffffff`, the
chip PMU never reports ALP available (firmware download stalls), and
the module never drives UART RX for Bluetooth.  The factory-era journal
shows this module once worked (chip 0x4345 rev 6, firmware loaded).
Physical measurement (module rails, LPO pin, D1–D3 continuity, UART RX)
is required before any further software action: see
`evidence/current/p9-wireless.json` → `next_measurement`.

## Known unresolved

- Onboard Wi-Fi data path and firmware bring-up: blocked by the module
  hardware condition above (enumeration itself is fixed and stable).
- Onboard Bluetooth data path: `hci0` exists; chip unresponsive (same
  module condition above).
- ST7789 display panel: experimental driver test only
  (see hardware/st7789); not integrated into the boot path.
