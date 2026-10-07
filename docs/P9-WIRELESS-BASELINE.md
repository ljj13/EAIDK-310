# P9 — CYW43455 Wi-Fi / Bluetooth baseline & diagnosis

Date: 2026-10-07.  Baseline collected on the production board running
`6.18.55-eaidk310-zramfix1` (v2026.10.07 stable) **before any change**.
Raw captures: local working-copy `logs/p9-baseline/` (not published).

## Runtime identity

| Item | Value |
| --- | --- |
| Kernel | `6.18.55-eaidk310-zramfix1 #1 SMP PREEMPT @1791023943` |
| cmdline | `root=UUID=781e1dc3-… rootwait rootfstype=ext4 net.ifnames=0 earlycon console=ttyS2,1500000n8 console=tty1 consoleblank=0 loglevel=4` |
| boot DTB | `/dtb/rockchip/rk3328-eaidk-310-6.18.55.dtb` (extlinux default) |
| LIVE_DTS_MATCH | **YES** — board DTB sha256 `e7844891…` equals `RELEASE-MANIFEST.json` `dtb_sha256` of v2026.10.07 |

Note: `kernel/linux-6.18.55-zramfix1/baseline/` is the **legacy pre-P7
stable-baseline lock** (factory-era `config-6.8.4`, extlinux, DTB); it is
not the shipped 6.18.55 DTB and is not expected to match it.

## L0 — module identity

- Factory (Fedora 4.4) platform data: `wifi_chip_type = "ap6354"` (vendor
  string; the enumerated silicon disagrees, see below).
- Archived Fedora journal (recovered from the factory eMMC backup on
  2026-09-01, recorded in working findings): `mmc1` enumerated chip
  **`0x4345` revision `0x6`** and loaded
  `/system/etc/firmware/fw_bcm43455c0_ag.bin` — **the module has worked
  on this board**.
- The factory firmware set shipped on this board contains
  `fw_bcm43455c0_ag.bin`, `fw_bcm43455c0_ag_apsta.bin` and
  `nvram_ap6255.txt` — the AP6255-class NVRAM pair for BCM43455.
- BT side is described as `brcm,bcm4345c5` in the vendor DTS lineage.

```
MODULE_VENDOR=    Ampak-class combo (per factory firmware set)
MODULE_MARKING=   UNKNOWN (no silkscreen evidence in repo)
WIFI_SILICON=     BCM43455 (chip 0x4345 rev 0x6, factory journal)
BT_SILICON=       BCM4345C5-class (DTS + factory BCM4345C5.hcd)
BOARD_VARIANT=    EAIDK-310 build by lcy v2
```

## Live kernel / driver state (6.18.55)

- `WIFI_SDIO_HOST=mmc1` → `ff510000.mmc` (`rockchip,rk3328-dw-mshc`),
  `allocated mmc-pwrseq`, `non-removable`.
- `/sys/bus/mmc/devices` contains only `mmc2:0001` (eMMC). No SDIO
  function: **SDIO_ENUMERATION=FAIL (≤ L3)**.
- `lsmod`: `hci_uart` + `btbcm/btqca/btrtl/btintel` loaded at 12.37 s;
  `brcmfmac`/`brcmutil` present in `modules.dep` but never loaded (no
  card → no modalias). `cfg80211` builtin.
- No `wlan*`, no `rfkill` entries, `btmgmt info` → 0 controllers.
- `/sys/bus/serial` (the 6.18 serdev bus, renamed from `serdev`) lists
  device `serial0-0` (OF node `/serial@ff110000/bluetooth`,
  compatible `brcm,bcm4345c5`) with **no driver bound** and
  `waiting_for_supplier=0`; the device sits in the deferred-probe list.

## Live DT wireless nodes (verified against repo DTS)

- `sdio-pwrseq`: `reset-gpios = <&gpio1 RK_PC2 GPIO_ACTIVE_LOW>`,
  pinctrl group `wifi_enable_h` = GPIO1_C2 only (the historical two-pin
  group [C2+C3] divergence was already fixed before this phase).
- `mmc@ff510000`: bus-width 4, `cap-sdio-irq`, `keep-power-in-suspend`,
  `max-frequency = <125000000>`, `sd-uhs-sdr104`, `no-mmc`, `no-sd`,
  `non-removable`, child `wifi@1` compatible
  `brcm,bcm43455-fmac`/`brcm,bcm4329-fmac`, host-wake IRQ GPIO1_C3.
- `serial@ff110000`: `uart-has-rtscts`, pinctrl uart0 xfer/cts/rts
  (GPIO1_B0/B1/B2/B3), child `bluetooth` compatible `brcm,bcm4345c5`,
  `clocks = <&rk805 1>` ("lpo"), shutdown GPIO1_C5 active-high,
  host-wakeup GPIO1_D2, `max-speed = <4000000>`,
  `vbat-supply = vcc_io (3.3 V)`, `vddio-supply = vcc_18 (1.8 V)`.
- Pinmux (debugfs `pinmux-pins`): SDIO bus pins gpio1-12..17 (B4..C1)
  muxed to `sdmmc1` and claimed by `ff510000.mmc`; gpio1-18 (C2) owned
  by `sdio-pwrseq`. **No pin conflicts** (SDIO bus and UART0 pins do not
  overlap the control pins).
- IO domains: `vccio1/5/6 = vcc_io (3.3 V)`, `vccio2 = vcc18_emmc`,
  `vccio3 = vcc_18 (1.8 V)`, `vccio4 = vcc_18`, `pmuio = vcc_io`.
  Upstream cross-check (ROC-RK3328-CC, working SDIO Wi-Fi) maps
  **vccio3 to the SDIO pin bank** → EAIDK-310 runs the SDIO bank at
  1.8 V, matching the module VDDIO. `rockchip-iodomain` driver is bound.

## Root-level evidence (sudo)

- `/sys/kernel/debug/devices_deferred`:
  ```
  analog-sound   asoc-simple-card: parse error
  serial0-0
  ```
  → the Bluetooth serdev client is stuck in a **silent permanent probe
  deferral**.
- `/sys/kernel/debug/gpio`:
  ```
  gpio-18 (reset) out lo ACTIVE LOW     ← logical lo = physical HIGH = WL_REG_ON released
  gpio-28 (vccio_sd) out lo             ← TF IO selector at 3.3 V state
  ```
  → the pwrseq owns GPIO1_C2 and **executes its sequence**; WL_REG_ON is
  left released after the last power-up.
- `/sys/kernel/debug/clk/clk_summary`: **no `xin32k`, no `rk805-clkout2`,
  no rk805 clock provider at all**; `/proc/kallsyms` has no
  `rk805_clk`/`xin32k` symbols.
  → `CONFIG_COMMON_CLK_RK808` is `not set` in the shipped kernel; the
  rk805 DT node declares `#clock-cells = <1>` +
  `clock-output-names = "xin32k","rk805-clkout2"`, so
  `hci_bcm`'s `devm_clk_get_optional(dev, "lpo")` returns
  `-EPROBE_DEFER` **forever** (the provider never registers) and the
  probe never logs anything.

### Bluetooth root cause (proven)

```
HISTORICAL_BT_ROOT_CAUSE = KERNEL_CONFIG_MISSING (CONFIG_COMMON_CLK_RK808)
FIX = CONFIG_COMMON_CLK_RK808=y (candidate 6.18.55-eaidk310-wifi1)
```

### Wi-Fi enumeration behaviour (dynamic-debug rebind experiment)

`unbind`/`rebind` of `dwmmc_rockchip` + `ff510000.mmc` with
`dw_mmc.c`, `pwrseq_simple.c`, `sdio_ops.c`, `mmc_ops.c` dynamic debug
enabled reproduces the failure and exposes more detail than the boot
log:

```
mmc_host mmc1: Bus speed 400000Hz
mmc1: Signal voltage switch failed, power cycling card    ← ×4
mmc1: error -110 whilst initialising SDIO card            ← ×4 (400k→300k→200k→100k)
mmc1: Failed to initialize a non-removable card
dwmmc_rockchip ff510000.mmc: Unexpected CMD11 timeout      ← late cmd11_timer, spurious
```

Interpretation (mmc core semantics):

- `Signal voltage switch failed` is printed by `mmc_sdio_init_card`
  **only after CMD5 was answered with the S18A bit set**. The card is
  alive and talking on the bus during the warm rebind; the 1.8 V switch
  handshake (CMD11 + DAT0-low acknowledgement via `card_busy`) never
  completes.
- At **cold boot** the same four attempts show plain `-110` **without**
  the voltage-switch warning → CMD5 never got a response: every attempt
  re-toggles WL_REG_ON (power cycle per retry) and issues CMD5 only
  ~13 ms after the rising edge.
- The pwrseq has **no `post-power-on-delay-ms`** → default 0 ms.
  BCM43455/AP6255-class modules need ≈50 ms after WL_REG_ON before the
  SDIO host is ready; the retry loop restarts the settle window at every
  attempt, so all four attempts lose the race.
- The warm-rebind CMD11 failure is consistent with the same
  under-settled state: the module answered CMD5 while still completing
  its internal boot and cannot perform the voltage switch.

```
HISTORICAL_WIFI_FAILURE_LAYER = L3 (SDIO card enumeration)
HISTORICAL_WIFI_ROOT_CAUSE    = POWER_SEQUENCE (missing post-power-on-delay-ms)
FIX = sdio-pwrseq post-power-on-delay-ms = <100> (candidate 6.18.55-eaidk310-wifi1)
```

### Supporting negative evidence

- U-Boot (production `control` variant) does not register
  `ff510000` at all (`mmc list`: ff500000/ff520000/ff5f0000); its
  `mmc dev 1 -110` message is the **empty TF slot**, not Wi-Fi.
- GRF_SOC_CON4 (`0xff100410`) reset value read from U-Boot: `0x0`
  (SDIO domain defaults to 3.3 V select); Linux `rockchip-iodomain`
  rewrites it per the io-domains supplies at boot.
- Historical U-Boot `sdio-handoff` experiment: same `-110` → not
  re-attempted (§16).
- Firmware inventory: `/lib/firmware/brcm/` already carries the Debian
  `firmware-brcm80211` set (`brcmfmac43455-sdio.bin/.txt/.clm_blob`,
  `BCM4345C5.hcd`) — the L5 layer is provisioned but unreachable until
  L3 passes. See `vendor/provenance/cyw43455.md`.

## Failure-layer map at baseline

| Layer | Status |
| --- | --- |
| L0 module identity | RESOLVED (BCM43455 + BCM4345C5-class) |
| L1 power/reset/LPO | WL_REG_ON sequence executed; LPO clock provider missing (BT) |
| L2 RK3328 SDIO host | PASS (pinmux/clock/pinctrl verified) |
| L3 SDIO enumeration | **FAIL** — root cause identified (power sequence) |
| L4 brcmfmac probe | NOT_REACHED |
| L5 firmware/NVRAM/CLM | NOT_REACHED (files present) |
| L6–L7 wlan0/association | NOT_REACHED |
| L8 UART0/serdev | PASS to the bus layer; probe deferred (clock) |
| L9–L10 BT firmware/hci0 | NOT_REACHED |
| L11 coexistence | NOT_REACHED |

## P9 candidate trials (2026-10-07, same day)

Three candidates went through the full P7 → OTA trial chain; each trial
booted once under the watchdog and was committed after
CRITICAL+REMOTE health PASS.

| Candidate | Delta | Outcome |
| --- | --- | --- |
| `6.18.55-eaidk310-wifi1` | pwrseq `post-power-on-delay-ms=<100>`; `CONFIG_COMMON_CLK_RK808=y` | BT software chain fixed: serial0-0 binds `hci_uart_bcm`, rk805-clkout2 lpo registered (32 768 Hz, consumer serial0-0), `hci0` created; SDIO still dead (CMD5 silent cold) |
| `6.18.55-eaidk310-wifi2` | − `sd-uhs-sdr104` (TEST B) | **`mmc1: new high speed SDIO card`** — the S18R request was suppressing the CMD5 response; brcmfmac probe −52 with `F1 signature read = 0xffffffff` |
| `6.18.55-eaidk310-wifi3` | `bus-width=<1>` (TEST C) | **`F1 signature = 0x15264345` → chip BCM4345/6 alive** (identity matches the factory journal); firmware request chain starts; download stalls at `brcmf_sdio_htclk: HT Avail timeout (clkctl 0x50)` |

Isolated hardware faults, in order of discovery:

1. D1–D3 SDIO data lines: 4-bit backplane reads return `0xffffffff`
   while 1-bit (D0-only) reads return correct chip data.
2. Chip PMU never reports ALP available → firmware download cannot
   start (HT/ALP timeout with clkctl `0x50`) despite the LPO clock being
   registered and register-enabled at the RK805.
3. Bluetooth UART: `hci0` runs the full probe but every vendor command
   (`0xfc45`) tx-times out — the module never drives UART RX.

Reliability on the final stable (`6.18.55-eaidk310-wifi3`): 5/5 warm
reboots with kernel + Tailscale + 0 failed units + SDIO card + hci0
present every time.  Cold boots: NOT_RUN_WITH_REASON (needs a physical
power cycle).  Remaining verification (module VBAT/VDDIO rails, LPO at
the module pin, D1–D3 continuity, UART RX) requires instruments: see
`evidence/current/p9-wireless.json` → `next_measurement`.
