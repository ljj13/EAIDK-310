# P10 Final Report — System Optimization & Long-term Validation

Phase window: 2026-10-07 → 2026-10-08, board `eaidk-310`
(STABLE `6.18.55-eaidk310-wifi3`).  Unattended throughout; every change
is userspace-only, boot chain untouched (no extlinux/kernel/DTS edits
outside the P7/P8 candidate pipeline, which P10 did not need).

PRE_HEAD=`8e370f0` → POST_HEAD=see git log (main, CI green).

## Results by phase

| phase | result |
|---|---|
| 1 baseline | `tools/eaidk-health` shipped (status/boot/storage/network, --json); snapshot in `evidence/p10-baseline/` |
| 2 observability | persistent capped journal (Storage=persistent, 64M), reboot-reason evidence chain verified across 3 real reboots; ramoops needs cmdline → PHYSICAL_VALIDATION_REQUIRED |
| 3 eMMC endurance | PRE_EOL 0x01, life A/B 0x01 (NORMAL); `eaidk-ota prune-staging` freed 341M; apt/-var/tmp cleanup freed 125M; TRIM discarded 1.9G; coredumps capped |
| 4 zram | 6-config matrix → NO_CHANGE_NEEDED (384M lz4, swappiness 60); 6.18.55 zram accounting quirk documented |
| 5 boot | multi-user 26.6s → 9.6s (31.8s → 14.8s total) via shellcrash+exim4 `After=multi-user.target` drop-ins; tailscaled kept on critical path |
| 6 cpu/thermal | stock OPP/schedui policies retained; 4-core stress peak 85.8°C with automatic freq capping (1296→1200/1008 MHz), zero instability |
| 7 network | v6 probe fixed (CERNET-reachable target); LAN scp 64 Mbit/s over the 100M link, Tailscale direct 34 Mbit/s; no persistent tuning justified |
| 8 hardware | USB2 hub/enumeration PASS; USB3 SuperSpeed + HDMI output need PHYSICAL_VALIDATION_REQUIRED; GPIO/I2C/UART/RTC/watchdog/eMMC/TF-controller PASS; SPI/PWM/ADC off by DT policy |
| 9 ST7789 | READY_FOR_DISPLAY_HARDWARE_VALIDATION — test DTB committed (dtc round-trip), statusd loopback-rendered on board, unit gated on /dev/fb1 |
| 10 rescue TF | READY_FOR_PHYSICAL_VALIDATION — reproducible builder + double-passed loopback verifier + `eaidk-rescue` toolkit; image SHA-256 `859da2c3…` |
| 11 soak | 24h sampler + hourly pulses (`eaidk-soak-probe`, systemd timer); live results in `/var/log/eaidk-soak/` on board |

## Regression gate (post-change, all green)

RAW bootcount=0, upgrade_available=0; no trial marker; watchdog inactive;
trial feeder inactive; Ethernet UP + default route; Tailscale Running
with 2 ms direct path; zram 384M lz4; nftables 8 tables; modules-load
active; failed units 0; rescue entry + artifacts present (`/Image`
6.12.108 under the P3-era generic names); PREVIOUS 6.18.54 entry +
modules present; default = `rockchip-kernel-6.18.55-eaidk310-wifi3`.

## Physical validation pending

- ramoops/pstore arming (needs extlinux cmdline → local authorization)
- USB3 SuperSpeed/UASP with a real device
- HDMI display + HDMI audio + analog audio (if wired)
- ST7789 panel bring-up (hardware + DTB swap test)
- Rescue TF card write + boot test
- CYW43455 module hardware repair (P9, frozen)

## Where to look

- `docs/P10-RUNTIME-POLICY.md` — policy changes + measurements
- `evidence/p10-{baseline,zram,boot,hardware}/` — raw data
- `rescue/README-P10.md` — rescue TF provenance + verification record
- `hardware/st7789/README-P10.md` — display integration status
