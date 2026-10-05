# vendor/ — upstream source provenance

This directory documents pinned **upstream** sources used by
source-locked builds.  It deliberately contains no proprietary blobs:
every binary the project needs from the board vendor is either already
published on this repository's GitHub Releases or is inventoried with
full provenance in
[docs/LOCAL-LARGE-ASSET-AUDIT.md](../docs/LOCAL-LARGE-ASSET-AUDIT.md)
(local workspace record).

## Pinned upstream sources

| component | upstream | pin | provenance |
| --- | --- | --- | --- |
| U-Boot | https://github.com/u-boot/u-boot | `v2024.07-rc1` @ `38ea74d6d5c05224acdb03f799897c1bdd56f8cc` | [u-boot-v2024.07-rc1.md](provenance/u-boot-v2024.07-rc1.md) |
| Linux kernels | https://cdn.kernel.org | 6.12.108 / 6.12.111 / 6.18.54 (SHA-256 + GPG fingerprint per `kernel/*/source-lock.json`) | kernel pipelines |

The historical local `vendor/u-boot-v2024.07-rc1` shallow clone and
`.bundle` were working copies of the pinned upstream tree.  They are
fully recoverable from the upstream repository at the pinned commit and
are no longer kept locally.

## Extracted vendor facts

Facts reverse-engineered from vendor-provided images (6.8.4 factory
image, board layout) live in the project proper, not here:

* eMMC partition/layout map and Rockchip loader region layout:
  [bootloader/u-boot-eaidk310/docs/EMMC-RAW-REGION.md](../bootloader/u-boot-eaidk310/docs/EMMC-RAW-REGION.md)
* board DTS and pin descriptions: `kernel/*/dts/` and the patch stack
* 6.8.4 binary baseline provenance:
  [v2026.09.05 GitHub Release](https://github.com/ljj13/EAIDK-310/releases/tag/v2026.09.05)
  (the image is upstream-author-provided; kept there, not redistributed
  in the Git tree)
