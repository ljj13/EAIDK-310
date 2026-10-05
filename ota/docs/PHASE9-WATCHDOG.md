# PHASE 9 — watchdog investigation & design (P3.5, investigation only)

## Current facts (proven, 2026-09-30)

- `/dev/watchdog` and `/dev/watchdog0` exist (dw_wdt, Synopsys DesignWare).
- Boot message: `dw_wdt ff1a0000.watchdog: No valid TOPs array specified`
  (timeout topology not configured by the driver/DT — timeout defaults apply).
- No userspace watchdog daemon observed (`systemd watchdog` property and
  /etc/systemd/system.conf have no Watchdog settings on this board —
  `RuntimeWatchdogUSec` is 0/disabled by default).
- U-Boot has no watchdog compiled (`CONFIG_WATCHDOG`/`CONFIG_WDT` absent
  from the pinned defconfig) → bootloader phase is unwatched.

## Answers to the six design questions

1. **Earliest watchdog start**: dw_wdt probes early (kernel ~1.1s) but the
   device is left idle unless opened.  Effective earliest enforcement today
   = whenever `RuntimeWatchdogUSec` is enabled (pid1 takes the device) or a
   user daemon opens /dev/watchdog.  Kernel-early-panic coverage therefore
   does NOT exist until pid1 arms it.
2. **Kernel early panic → reset?**  Only if the watchdog was already armed
   by someone before the panic AND the panic stops the petting loop.
   Bare-arm not configured today → early panics hang forever (matches the
   requirement for bootloader-level rollback on multi-boot failure).
3. **initramfs feeding**: with `RuntimeWatchdogUSec` the watchdog is armed
   by pid1 AFTER root pivot; initramfs does not feed it.  A candidate that
   hangs inside initramfs is only caught if the PREVIOUS system left the
   device armed (it does not today) — accept as a documented gap; rely on
   bootloader rollback for initramfs-stage failures.
4. **Is systemd RuntimeWatchdog early enough?**  It is early enough for
   userspace-stage hangs (the common OTA failure mode) but not for
   initramfs/early-kernel hangs.
5. **Health checker takeover**: the candidate's boot unit (or
   `eaidk-ota health` service) opens /dev/watchdog during
   HEALTH_PENDING and keeps petting while checks run; on COMMIT it hands
   petting back to systemd's normal watchdog (or closes it cleanly with
   `V` magic close), on failure it stops petting → reboot → bootloader
   rollback boots stable.
6. **After commit**: watchdog policy returns to steady-state
   (RuntimeWatchdogUSec as configured, or closed).

## Template (NOT applied this round)

`/etc/systemd/system.conf.d/10-eaidk-ota-watchdog.conf`:
```
[Manager]
RuntimeWatchdogUSec=15s
RuntimeWatchdogPreUSec=0
RebootWatchdogUSec=2min
```
Candidate trial unit sketch:
```
[Service]
ExecStartPost=/usr/local/sbin/eaidk-ota watchdog-arm --timeout 30
WatchdogSec=20s
```
Enabling these is deferred until TEST A/B are executed on-site.
