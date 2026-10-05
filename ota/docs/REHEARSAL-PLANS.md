# OTA rehearsal plans (generated 2026-09-30 — DO NOT execute remotely)

## TEST A — known-good one-shot (candidate = 6.12.111, stable = 6.12.108)

Precondition: operator physically present, serial console on ttyS2, power
control available.  All commands run on the board's local console.

```
1. eaidk-ota authorize-local                 # expects ttyS2 → token in /run
2. eaidk-ota stage <6.12.111 bundle>         # already known-good; skip if staged
3. eaidk-ota plan-install 6.12.111-eaidk310-zramfix1   # review plan
4. eaidk-ota install-candidate               # copies files + appends extlinux entry
5. eaidk-ota arm 6.12.111-eaidk310-zramfix1  # one-shot try (requires backend PASS)
6. reboot
7. board boots candidate once (watchdog/altbootcmd path)
8. eaidk-ota health --expected-release 6.12.111-eaidk310-zramfix1
   → CRITICAL_BOOT_HEALTH=PASS required; REMOTE_HEALTH=PASS required for
     unattended commit
9. eaidk-ota commit                          # clears try-once, resets bootcount,
                                             # promotes candidate to stable
10. second reboot → candidate boots automatically (committed)
```

Pass criteria: one-shot semantics observed (a failed commit path falls back
to 108), health gates behave, Tailscale returns automatically after every
boot, 108 rescue entry untouched.

## TEST B — fault injection (candidate that cannot boot)

Construct a dedicated INVALID extlinux candidate entry pointing at a
non-existent test Image (`/boot/Image-0.0.0-test-does-not-exist`) — no
existing file is damaged.

```
1. authorize-local (console)
2. add test entry (label rockchip-kernel-0.0.0-test) + arm try-once for it
3. reboot → extlinux picks try-once → boot fails (file missing)
4. EXPECT: watchdog/altbootcmd/bootlimit path reboots and next boot
   selects stable 6.12.108
5. EXPECT: Tailscale re-establishes automatically (DERP hkg)
6. clean up test entry; record serial transcript of the whole cycle
```

MANDATORY on-site (serial + power button in reach).  Absolutely forbidden
remotely.  Until TEST B passes on real hardware:
`REMOTE_OTA_ELIGIBLE=NO`.

## 6.18.54 OTA plan (after TEST A/B pass)

```
VERIFY   eaidk-ota verify <6.18 bundle>          (already PASS as of 2026-09-30)
STAGE    eaidk-ota stage <6.18 bundle>
INSTALL  authorize-local + install-candidate
ARM      eaidk-ota arm 6.18.54-eaidk310-zramfix1 (+ watchdog trial config)
REBOOT   local or `systemctl reboot` over TWO independent channels
HEALTH   eaidk-ota health --expected-release 6.18.54-eaidk310-zramfix1
         11-item network baseline + zram lz4 runtime + dmesg A/B vs 111
COMMIT   only if CRITICAL_BOOT_HEALTH=PASS and REMOTE_HEALTH=PASS
         and the 11-item baseline matches 111's result
```

Rollback assets that must exist before ARM: 108 rescue entry + 111
known-good entry + state backups.  Bundle SHA-256 reference:
989093725bdec974ebda8a031dcb083bcc7802ba51ae7c24a143557834505f06
