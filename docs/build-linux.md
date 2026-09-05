# Build Linux 6.12.108 for EAIDK-310

The maintained project is `kernel/linux-6.12.108-zramfix1`. It pins Linux `6.12.108`, kernel.org archive SHA-256 `e1d1ea200d22d55c9f5d5fae59e69bb3b494515705fc3390cd54231ee4f4baaf`, signing fingerprint `647F28654894E3BD457199BE38DBBDC86092693E`, the reviewed EAIDK-310 configuration and the board DTS.

Build on Ubuntu 24.04, preferably inside a WSL2 ext4 filesystem. Do not compile directly under `/mnt/c`, `/mnt/d` or another DrvFS mount. The scripts deliberately use `/home/Fog/eaidk310-kernel` for source, build, stage and ARM64 initramfs work.

## Dependencies

```bash
sudo apt-get update
sudo apt-get install --no-install-recommends \
  build-essential bc bison flex libssl-dev libelf-dev libncurses-dev \
  dwarves device-tree-compiler u-boot-tools ccache rsync cpio xz-utils zstd \
  gnupg curl git kmod file python3 crossbuild-essential-arm64 \
  debootstrap qemu-user-static binfmt-support initramfs-tools-core busybox
```

## Fetch and authenticate Linux

```bash
mkdir -p /home/Fog/eaidk310-kernel/downloads /home/Fog/eaidk310-kernel/src
curl --fail --location --output /home/Fog/eaidk310-kernel/downloads/linux-6.12.108.tar.xz \
  https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.12.108.tar.xz
curl --fail --location --output /home/Fog/eaidk310-kernel/downloads/linux-6.12.108.tar.sign \
  https://cdn.kernel.org/pub/linux/kernel/v6.x/linux-6.12.108.tar.sign
gpg --keyserver hkps://keys.openpgp.org --recv-keys 647F28654894E3BD457199BE38DBBDC86092693E
tar -C /home/Fog/eaidk310-kernel/src -xf /home/Fog/eaidk310-kernel/downloads/linux-6.12.108.tar.xz
```

The verification script separately checks the compressed SHA-256 and the detached signature over the uncompressed tar stream. It requires the exact `VALIDSIG` fingerprint in `source-lock.json`.

## Build, initramfs and package

From the repository checkout:

```bash
bash kernel/linux-6.12.108-zramfix1/scripts/verify-inputs.sh
bash kernel/linux-6.12.108-zramfix1/scripts/install-board-inputs.sh
bash kernel/linux-6.12.108-zramfix1/scripts/build-kernel.sh --clean
sudo bash kernel/linux-6.12.108-zramfix1/scripts/build-initramfs-arm64.sh
bash kernel/linux-6.12.108-zramfix1/scripts/package-artifacts.sh
bash kernel/linux-6.12.108-zramfix1/scripts/verify-bundle.sh
```

The output bundle must be named `eaidk310-linux-6.12.108-eaidk310-zramfix1.tar.zst`. A reproducible build is accepted only when its manifest verifies, the exact kernel release is correct, the ARM64 initramfs contains the matching module tree and repeated packaging produces the recorded SHA-256.

The scripts never download source implicitly and never write a block device as part of the build. Rescue-TF/eMMC deployment is a separate, default-dry-run action.
