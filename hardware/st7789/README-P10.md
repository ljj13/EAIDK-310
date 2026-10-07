# ST7789 integration status (P10 Phase 9)

Status: **READY_FOR_DISPLAY_HARDWARE_VALIDATION** — every step that does
not require the physical panel is done and verified on-board.

## What exists

| piece | location | verified by |
|---|---|---|
| test DTS builder (reversible, hash-pinned base) | `tools/build_eaidk310_st7789_test.py` | `tools/test_build_eaidk310_st7789.py` |
| compiled test DTB, SPI mode 0, fbtft bindings | `hardware/st7789/st7789-test-spi0.dtb` | `dtc -I dtb -O dts` round-trip: `display@0` node with `sitronix,st7789v` present |
| status renderer (2 pages: CPU/RAM/TEMP/ZRAM/UPT, TS/ETH/DISK/KERN/OTA/FAILED) | `tools/st7789-statusd.py` | `tools/test_st7789_statusd.py` (12 offline tests) |
| on-board loopback render | board run against a file-backed framebuffer | 2688 lit pixels, `--once` exit 0 |
| systemd unit (condition-gated on /dev/fb1) | `hardware/st7789/st7789-statusd.service` | not installed yet (no panel) |
| data interface | `eaidk-health status/network --json` + `eaidk-ota status --json` | live on board |
| kernel driver | `fb_st7789v.ko` + `fbtft.ko` in the deployed 6.18.55 modules | `modinfo` on board |

The test DTB artifact is committed so the panel bring-up does not need a
build step; `render_test_dts()` refuses any base DTS whose SHA-256 drifts,
so regeneration is always deliberate.

## When the panel arrives (physical steps only)

1. Wire the panel per `docs/hardware-status.md` (SPI0, DC=GPIO20,
   RESET=GPIO15, backlight PWMIR).
2. Copy the test DTB over the boot DTB on a TEST boot first (P7/P8
   candidate flow if it should become permanent — kernel-identity rules
   apply to DTS changes).
3. `sudo cp tools/st7789-statusd.py /usr/local/sbin/st7789-statusd.py &&
   sudo cp hardware/st7789/st7789-statusd.service /etc/systemd/system/ &&
   sudo systemctl enable --now st7789-statusd`
4. Confirm the two pages alternate every 10 s and the colors match
   state (temp ≥70 °C yellow, ≥85 °C red; disk ≥80 % warn; OTA not
   COMMITTED/IDLE warn; failed units > 0 red).
5. Record results under `evidence/` and flip this file to
   DISPLAY_HARDWARE_VALIDATED.
