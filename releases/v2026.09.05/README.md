# v2026.09.05 release assets

The large files named in `manifest.json` are attached to the matching GitHub Release and intentionally excluded from Git history. `SHA256SUMS` is the human-readable checksum list for the same five files.

The Linux 6.12.108 bundle and U-Boot prefix are backed by the locked build inputs in this repository. The Linux 6.8.4 image and Debian packages are retained as an exact binary recovery baseline from the upstream `yjdwbj/rockchip-eaidk-310` v1.0 release; they are not claimed to be source-reproducible here.

Verify a downloaded directory with:

```bash
python3 rescue/verify_release_assets.py \
  --manifest releases/v2026.09.05/manifest.json \
  --directory /path/to/downloads
```
