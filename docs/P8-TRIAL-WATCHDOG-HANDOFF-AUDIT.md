# P8.2 Trial Watchdog Handoff Audit

## Where everything lives

| Piece | Location | Deployed to board by |
|---|---|---|
| feeder script (canonical) | `ota/templates/eaidk-trial-feed` | `eaidk-ota install-trial-feed` (embedded copy, Tier-1-equal to template) |
| systemd unit (canonical) | `ota/templates/eaidk-trial-feed.service` | same, to `/etc/systemd/system/eaidk-trial-feed.service` + `daemon-reload` + `enable` |
| install entry point | `eaidk-ota install-trial-feed` (root; persistent infra — no bootstate token) | — |
| pre-arm handoff gates | `prearm_contract(..., watchdog_gates=True)` — 8 checks: trial_feed_binary_present, trial_feed_unit_present, trial_feed_unit_valid, watchdog_device_present, kernel_watchdog_handoff_supported, candidate_trial_marker_valid, feeder_can_identify_current_trial, feeder_not_active_on_normal_stable_boot | evaluated on every `arm` |

## Semantics (empirically established)

- U-Boot starts the DesignWare WDT (30 s requested → TOP15 ≈ 28.6 s) ONLY on
  a true candidate trial (armed, increment within bootlimit).
- The kernel auto-pings (`CONFIG_WATCHDOG_HANDLE_BOOT_ENABLED=y`) until the
  feeder opens `/dev/watchdog0`; from that point **userspace owns feeding**.
  Kernel auto-ping alone is NOT the fail-safe: it keeps a hung userspace
  alive forever.  The real safety property is the EMPIRICAL one recorded in
  the feeder: **after the first userspace close, the kernel auto-ping does
  not resume** — so a crashed/stopped feeder converges to a hardware reset.
- Feeder state machine (runtime only, no persistence): INACTIVE →
  TRIAL_DETECTED (cmdline marker) → identity check vs candidate conf →
  WATCHDOG_OWNED (fd open) → FEEDING (10 s) → upgrade_available=0 →
  magic 'V' + RELEASED.  Any hang/crash/intentional stop ⇒ NO_KEEPALIVE ⇒
  hardware reset ⇒ U-Boot rollback.
- Normal stable boots: the unit carries
  `ConditionKernelCommandLine=eaidk_ota_trial`, so systemd skips it and the
  U-Boot driver prints `WDT: Not starting watchdog@ff1a0000`.

## Gaps found by this audit and their disposition

1. feeder binary/unit existed only as templates ("board may or may not have
   them") → **closed**: `install-trial-feed` + pre-arm gates.
2. feeder could not prove it was feeding the RIGHT trial → **closed**:
   identity check (cmdline marker vs candidate conf marker) before opening
   the device; test hooks (`EAIDK_TEST_CMDLINE/BOOTSTATE/WATCHDOG`) are
   environment overrides for fixtures only.
3. "watchdog still running" was previously accepted as handoff evidence →
   **closed**: CASE 8/9 tests assert userspace ownership (keepalive bytes)
   and that a stopped feeder stops the keepalive (no magic 'V' after hang
   injection); the board-level proof is the P8.2 true-hang test.

## Regression coverage

`ota/tests/test_watchdog_contract.py` — CASE 1 (normal boot: feeder not
started), 2 (unmarked conf refused), 3 (identity mismatch refused),
4/5/6 (missing watchdog device / unit / binary refused by pre-arm),
7 (valid armed trial allowed), 8 (health success → magic 'V'
finalization), 9 (hang injection → keepalive stops, no 'V'), 10 (rollback
normal boot → unit skipped via condition) plus an embedded-copy ==
template byte-equality test.
