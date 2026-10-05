# eMMC recovery and rollback models (current)

## Layer 1 — OTA failure (automatic)

A failed candidate kernel rolls back automatically:

```text
candidate failure (panic / hang / userspace failure)
  → hardware watchdog reset (or panic reset)
  → raw bootcount exceeds bootlimit=1
  → altbootcmd → previous known-good (6.12.111) extlinux entry
  → network + Tailscale recover
```

No operator action is required.  After recovery: `eaidk-ota rollback`
(records the rollback) and `eaidk-bootstate inspect/clear` to return the
bootstate to committed/safe.

## Layer 2 — stable entry broken (manual)

If the 6.18.54 stable extlinux entry itself cannot boot (e.g. damaged
/boot), interrupt U-Boot at the `Hit any key` prompt over serial and
select the previous known-good entry (6.12.111), then repair /boot or
re-run the OTA install.  The rescue entry (6.12.108) stays available as
the last boot-menu choice.

## Layer 3 — bootloader failure (on-site)

U-Boot/idbloader damage is NOT recoverable remotely:

1. rescue SD: `boot_targets=mmc1 mmc0 …` scans the SD first; a prepared
   rescue card boots without touching eMMC
2. serial console (ttyS2, 1500000 8N1) for U-Boot shell recovery
3. Maskrom USB recovery (rkdeveloptool) as the last resort

Backup before touching the bootloader chain, readback-verify after, and
never flash without on-site access — see
[ota/docs/FLASH-PLAN-P36.md](../ota/docs/FLASH-PLAN-P36.md) for the
region map, checklists and recovery matrix.

## Historical baseline

The 6.8.4 factory image (Git LFS-free copy retained on the v2026.09.05
GitHub Release) is a **historical recovery baseline**, not a current
recovery path.  The vendor layout facts extracted from it are documented
in [vendor/README.md](../vendor/README.md).
