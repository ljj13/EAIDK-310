# EAIDK310 U-Boot Wi-Fi handoff experiment

本目录为 EAIDK310 生成两个 U-Boot v2024.07-rc1 变体：`control` 使用正确板级描述但关闭 Wi-Fi SDIO；`sdio-handoff` 仅额外启用 GPIO1_C2 pwrseq 与 125MHz SDIO。两者用于验证作者成功镜像是否依赖 U-Boot 对无线模组的上电交接。

P3.6 新增第三个变体 `failsafe-bootcount-fs`（candidate A）：在 control 之上加入 `BOOTCOUNT_EXT` 引导计数文件后端（4 字节状态文件，`/eaidk-ota/bootcount.bin`）、`CMD_SYSBOOT`、compiled default env 启动策略（`bootlimit=1` / candidate `bootcmd` / stable `altbootcmd`），并对 `bootcount_ext.c` 做 fail-closed 加固（缺失/损坏/超限状态一律回退 stable，绝不写回）。设计、审计与刷写方案见 `docs/FAILSAFE-BOOT.md` 与 `ota/docs/FLASH-PLAN-P36.md`；构建用 `scripts/build-cross-failsafe.sh`（同时产出 control 基准做二进制/DTB/默认 env 对照）。

P3.6-B 新增第四个变体 `failsafe-raw`（candidate B）：自定义 `bootcount_eaidk310_raw` 驱动（patch 0003），把双副本 CRC32 保护的 512 字节 bootstate 记录放在 eMMC 12–16 MiB 无分区尾带（LBA 0x6400/0x7800，经四镜像+板端只读复核审计，见 `docs/EMMC-RAW-REGION.md`）——完全脱离文件系统/journal/持久 env，掉电语义"至多丢最新一次更新"。设备身份门（manfid 0xd6 + `HBD08G` + 容量）不符即 fail-closed。两候选对比与最终推荐见 `docs/BACKEND-COMPARISON.md`（推荐 RAW_REDUNDANT）。构建用 `scripts/build-cross-failsafe-raw.sh`。两候选目前均**只构建未刷写**；control/sdio-handoff 产物与 0001 补丁保持字节不动。

## 安全边界

- 构建脚本只读取干净的上游源码，并把补丁应用到 `mktemp` 生成的临时副本。
- 脚本只生成普通文件，不包含任何块设备或物理磁盘写入功能。
- eMMC 必须保持 unmounted，且本实验永远不向 eMMC 写入。
- 后续打包只允许替换 TF 前缀中的 8–12MiB U-Boot proper 区；物理测试必须另行经过 dry-run、磁盘身份和读回校验。

## 锁定源码

```bash
git clone https://github.com/u-boot/u-boot.git ~/src/u-boot-v2024.07-rc1
git -C ~/src/u-boot-v2024.07-rc1 checkout --detach \
  38ea74d6d5c05224acdb03f799897c1bdd56f8cc
```

源码必须处于上述提交且无任何已跟踪或未跟踪改动；验证脚本还会检查补丁可应用性。

## Debian 原生构建依赖

```bash
sudo apt-get install --no-install-recommends \
  build-essential bc bison flex libssl-dev libgnutls28-dev \
  device-tree-compiler swig python3-dev python3-setuptools python3-pyelftools
```

板上已经配置 384 MiB zram。为避免 1GiB 内存系统在链接阶段发生内存压力，原生构建固定使用 `-j1`。

## 构建

在 EAIDK310 Debian/ARM64 上：

```bash
bash u-boot-eaidk310/scripts/build-native.sh \
  ~/src/u-boot-v2024.07-rc1 \
  ~/build/eaidk310-uboot-$(date -u +%Y%m%dT%H%M%SZ)
```

在安装了 GNU AArch64 交叉工具链的 Linux 主机上：

```bash
bash u-boot-eaidk310/scripts/build-cross.sh \
  ~/src/u-boot-v2024.07-rc1 \
  ~/build/eaidk310-uboot-cross-$(date -u +%Y%m%dT%H%M%SZ)
```

每个变体输出 `u-boot-dtb.bin`、选中的 `dts/dt.dtb` 和反编译的 `live.dts`。脚本会验证 payload 不超过 1,046,528 字节、板型字符串为 `Rockchip RK3328 EAIDK310`，并核对 control/handoff 的 SDIO 状态、频率和复位引脚。
