# u-boot-v2024.07-rc1 — provenance

* filename (local, removed 2026-10-06): `vendor/u-boot-v2024.07-rc1/`
  (shallow git clone, 287 MiB) and `vendor/u-boot-v2024.07-rc1.bundle`
  (40 MiB)
* upstream: https://github.com/u-boot/u-boot
* pin: tag `v2024.07-rc1`, commit `38ea74d6d5c05224acdb03f799897c1bdd56f8cc`
* also recorded in: `bootloader/u-boot-eaidk310/source-lock.json`
* purpose: offline source for the source-locked failsafe U-Boot builds
* license: upstream U-Boot licenses (GPL-2.0+ / BSD mixture per file)
* recoverable: `git clone` upstream + `git checkout` the pin; or restore
  the bundle from any peer holding it
* dependents: `bootloader/u-boot-eaidk310/scripts/*` (verify + build),
  patches 0001-0004
* extraction: none needed — upstream open source; project-specific
  changes are the committed patch stack, not local modifications
