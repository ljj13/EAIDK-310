"""P3.6-B raw dual-copy bootstate: U-Boot sandbox integration tests.

Runs the REAL pinned U-Boot (v2024.07-rc1@38ea74d6 + patches 0001+0002+0003)
as a sandbox binary against a plain 16 MiB image through the `host` block
device (the raw driver speaks the generic blk API, so the same code path is
exercised that talks to eMMC on hardware).  Record sectors are the audited
board LBAs (0x6400 / 0x7800).

Fail-closed matrix proven here (driver level, no filesystem involved):
  empty image / both records random / equal-seq conflict / seq exhausted
      -> bootcmd(stable), sectors NEVER written
  armed A            -> candidate, increment stored into B (seq+1)
  exceeded           -> altbootcmd(stable), counter keeps rising in A
  committed          -> stable, nothing written
  corrupt A + valid B armed -> candidate via B
"""
import base64
import os
import pathlib
import subprocess
import unittest

HERE = pathlib.Path(__file__).resolve().parent
PROJECT_DIR = HERE.parent
P = (PROJECT_DIR / "patches").as_posix().replace("D:/", "/mnt/d/")
BOOTSTATE_TOOL = (HERE.parents[2] / "ota" / "eaidk-bootstate").as_posix().replace("D:/", "/mnt/d/")
WSL_SRC = "~/src/u-boot-v2024.07-rc1"
WSL_SANDBOX = "~/eaidk310-uboot/p36-sandbox-raw"
WSL_WORK = "/tmp/p36-raw-sandbox-tests"
IMAGE_BYTES = 16 * 1024 * 1024
LBA_A, LBA_B = 0x6400, 0x7800

FRAGMENT = """\
CONFIG_BOOTCOUNT_LIMIT=y
CONFIG_BOOTCOUNT_EAIDK310_RAW=y
CONFIG_SYS_BOOTCOUNT_RAW_INTERFACE="host"
CONFIG_SYS_BOOTCOUNT_RAW_DEVID="0"
CONFIG_SYS_BOOTCOUNT_RAW_LBA_A=0x6400
CONFIG_SYS_BOOTCOUNT_RAW_LBA_B=0x7800
CONFIG_BOOTCOUNT_BOOTLIMIT=1
CONFIG_CMD_SYSBOOT=y
CONFIG_CMD_HOST=y
CONFIG_SYS_BOOTCOUNT_TRIAL_WDT_TIMEOUT_MS=30000
CONFIG_WATCHDOG=y
CONFIG_WATCHDOG_AUTOSTART=n
CONFIG_WDT=y
CONFIG_WDT_SANDBOX=y
CONFIG_CMD_WDT=y
CONFIG_USE_BOOTCOMMAND=y
CONFIG_BOOTCOMMAND="if test ${upgrade_available} -eq 1 -a ${bootcount_stored} -eq 1; then echo BF_CANDIDATE; else echo BF_STABLE; fi"
CONFIG_USE_PREBOOT=y
CONFIG_PREBOOT="host bind 0 /tmp/p36-raw-sandbox-tests/boot.img; setenv altbootcmd echo BF_ALTBOOTCMD"
# -2: unconditional autoboot (no stdin keypress consumption, no abort),
# bootcount_inc still runs inside bootdelay_process
CONFIG_BOOTDELAY=-2
# CONFIG_SANDBOX_SDL is not set
# CONFIG_TOOLS_MKEFICAPSULE is not set
"""


@unittest.skipUnless(os.name == "nt", "Windows WSL integration tests")
class RawSandboxTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        probe = cls.wsl(f"test -x {WSL_SANDBOX}/u-boot && echo READY || echo BUILD")
        if "BUILD" in probe.stdout:
            print("building U-Boot raw sandbox (one-time, a few minutes)...")
            build = cls.wsl_script(cls.build_script(), timeout=1800)
            if build.returncode != 0 or "SANDBOX-READY" not in build.stdout:
                raise unittest.SkipTest(
                    f"raw sandbox build failed: {build.stdout[-500:]} {build.stderr[-500:]}")

    def setUp(self):
        r = self.wsl(f"rm -rf {WSL_WORK} && mkdir -p {WSL_WORK}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.image = f"{WSL_WORK}/boot.img"

    @staticmethod
    def wsl(command: str, timeout: int = 900) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["wsl", "-d", "Ubuntu-24.04", "-u", "Fog", "--", "bash", "-c", command],
            text=True, capture_output=True, timeout=timeout)

    @classmethod
    def wsl_script(cls, script: str, timeout: int = 900) -> subprocess.CompletedProcess:
        encoded = base64.b64encode(script.encode()).decode()
        return cls.wsl(f"echo {encoded} | base64 -d | bash", timeout)

    @staticmethod
    def build_script() -> str:
        return f"""
set -euo pipefail
git -C {WSL_SRC} worktree add --detach ~/src/uboot-wt-sandbox-raw 38ea74d6d5c05224acdb03f799897c1bdd56f8cc >/dev/null 2>&1 || true
git -C ~/src/uboot-wt-sandbox-raw reset -q --hard 38ea74d6d5c05224acdb03f799897c1bdd56f8cc
git -C ~/src/uboot-wt-sandbox-raw clean -qfd
git -C ~/src/uboot-wt-sandbox-raw apply {P}/0001-arm-dts-add-eaidk310-variants.patch
git -C ~/src/uboot-wt-sandbox-raw apply {P}/0002-failsafe-bootcount-fs.patch
git -C ~/src/uboot-wt-sandbox-raw apply {P}/0003-failsafe-raw-bootstate.patch
rm -rf {WSL_SANDBOX}
mkdir -p {WSL_SANDBOX}
make -C ~/src/uboot-wt-sandbox-raw O={WSL_SANDBOX} sandbox_defconfig
cat >> {WSL_SANDBOX}/.config <<'FRAG'
{FRAGMENT}FRAG
make -C ~/src/uboot-wt-sandbox-raw O={WSL_SANDBOX} olddefconfig
make -C ~/src/uboot-wt-sandbox-raw O={WSL_SANDBOX} -j2 KCFLAGS=-fno-stack-protector u-boot >/dev/null
grep -q 'CONFIG_BOOTCOUNT_EAIDK310_RAW=y' {WSL_SANDBOX}/.config
grep -q 'CONFIG_CMD_SYSBOOT=y' {WSL_SANDBOX}/.config
echo SANDBOX-READY
"""

    def make_image(self, a: bytes = None, b: bytes = None):
        script = [f"rm -f {self.image}", f"truncate -s {IMAGE_BYTES} {self.image}"]
        for lba, data in ((LBA_A, a), (LBA_B, b)):
            if data is None:
                continue
            script.append(dd_write(self.image, lba, data))
        r = self.wsl_script("set -e\n" + "\n".join(script) + "\n")
        self.assertEqual(r.returncode, 0, r.stderr)

    def run_uboot(self, probes: str = "", timeout: int = 120) -> str:
        full = probes + "\necho ENV_UPG=${upgrade_available}\n" \
                        "echo ENV_BC=${bootcount}\n" \
                        "echo ENV_STORED=${bootcount_stored}\npoweroff\n"
        encoded = base64.b64encode(full.encode()).decode()
        r = self.wsl(
            f"echo {encoded} | base64 -d > {WSL_WORK}/cmds.txt && "
            f"timeout {timeout} {WSL_SANDBOX}/u-boot < {WSL_WORK}/cmds.txt",
            timeout=timeout + 60)
        self.assertEqual(r.returncode, 0,
                         f"rc={r.returncode}\n{r.stdout[-1500:]}\n{r.stderr[-300:]}")
        return r.stdout

    def read_sector_hex(self, lba: int) -> str:
        r = self.wsl(
            f"dd if={self.image} bs=512 skip={lba} count=1 status=none | "
            f"od -An -tx1 -v | tr -d ' \\n'")
        return r.stdout.strip()

    def put_bootstate(self, lba: int, seq: int, bootcount: int, upgrade: int):
        # ensure a full-size zero image exists BEFORE seeking into it:
        # dd would otherwise silently create a sparse file that ends right
        # after the record, and reads near LBA 0x7800 would fail
        r = self.wsl(f"truncate -s {IMAGE_BYTES} {self.image}")
        self.assertEqual(r.returncode, 0, r.stderr)
        import zlib
        buf = bytearray(512)
        buf[0:8] = b"EA310BS1"
        buf[8] = 1
        buf[9] = bootcount
        buf[10] = upgrade
        buf[11] = 0
        import struct
        struct.pack_into("<I", buf, 12, seq)
        struct.pack_into("<I", buf, 508,
                         zlib.crc32(bytes(buf[:508])) & 0xFFFFFFFF)
        r = self.wsl(dd_write(self.image, lba, bytes(buf)))
        self.assertEqual(r.returncode, 0, r.stderr)


def dd_write(image: str, lba: int, record: bytes) -> str:
    return (f"printf '{record.hex()}' | xxd -r -p | dd of={image} "
            f"bs=512 seek={lba} conv=notrunc status=none")


class RawSandboxStateMatrixTests(RawSandboxTestBase):
    def test_empty_image_boots_stable_and_writes_nothing(self):
        self.make_image()
        out = self.run_uboot()
        self.assertIn("BF_STABLE", out)
        self.assertNotIn("BF_CANDIDATE", out)
        self.assertIn("ENV_UPG=0", out)
        self.assertIn("ENV_STORED=0", out)
        self.assertEqual(self.read_sector_hex(LBA_A), "00" * 512)
        self.assertEqual(self.read_sector_hex(LBA_B), "00" * 512)

    def test_armed_boots_candidate_and_stores_into_other_copy(self):
        self.put_bootstate(LBA_A, seq=1, bootcount=0, upgrade=1)
        out = self.run_uboot()
        self.assertIn("BF_CANDIDATE", out)
        self.assertIn("ENV_UPG=1", out)
        self.assertIn("ENV_BC=1", out)
        self.assertIn("ENV_STORED=1", out)
        # A untouched (never overwrite the selected copy), B carries seq 2
        self.assertEqual(self.read_sector_hex(LBA_A)[24:32], "01000000")
        b = self.read_sector_hex(LBA_B)
        self.assertEqual(b[0:16], "4541333130425331")     # "EA310BS1"
        self.assertEqual(b[18:20], "01")                  # bootcount 1
        self.assertEqual(b[24:32], "02000000")            # seq 2 (LE)

    def test_exceeded_trial_falls_back_via_altbootcmd(self):
        self.put_bootstate(LBA_A, seq=1, bootcount=0, upgrade=1)
        first = self.run_uboot()
        self.assertIn("BF_CANDIDATE", first)
        second = self.run_uboot()
        self.assertIn("BF_ALTBOOTCMD", second)
        self.assertNotIn("BF_CANDIDATE", second)
        # second boot stored seq 3 / bootcount 2 into A
        self.assertEqual(self.read_sector_hex(LBA_A)[24:32], "03000000")

    def test_committed_boots_stable_without_writes(self):
        self.put_bootstate(LBA_A, seq=4, bootcount=0, upgrade=0)
        out = self.run_uboot()
        self.assertIn("BF_STABLE", out)
        self.assertIn("ENV_UPG=0", out)
        self.assertEqual(self.read_sector_hex(LBA_B), "00" * 512)

    def test_both_records_random_fail_closed_no_writes(self):
        self.make_image(a=bytes(range(256)) * 2,
                        b=bytes((i * 7 + 3) % 256 for i in range(512)))
        out = self.run_uboot()
        self.assertIn("BF_STABLE", out)
        self.assertIn("ENV_UPG=0", out)
        self.assertIn("ENV_STORED=0", out)
        a = self.read_sector_hex(LBA_A)
        self.assertEqual(a[0:32], "000102030405060708090a0b0c0d0e0f")

    def test_equal_sequence_conflict_fails_closed(self):
        import struct, zlib
        def rec(seq, bootcount, upgrade):
            buf = bytearray(512)
            buf[0:8] = b"EA310BS1"
            buf[8] = 1
            buf[9] = bootcount
            buf[10] = upgrade
            struct.pack_into("<I", buf, 12, seq)
            struct.pack_into("<I", buf, 508,
                             zlib.crc32(bytes(buf[:508])) & 0xFFFFFFFF)
            return bytes(buf)
        self.make_image(a=rec(6, 1, 1), b=rec(6, 0, 0))
        out = self.run_uboot()
        self.assertIn("BF_STABLE", out)
        self.assertIn("ENV_UPG=0", out)

    def test_sequence_exhausted_stays_stable(self):
        self.put_bootstate(LBA_A, seq=0xFFFFFFFF, bootcount=0, upgrade=1)
        out = self.run_uboot()
        # armed is readable, but the store must refuse -> policy stays stable
        self.assertIn("BF_STABLE", out)
        self.assertNotIn("BF_CANDIDATE", out)
        self.assertIn("ENV_STORED=0", out)

    def test_corrupt_a_valid_armed_b_boots_candidate(self):
        garbage = bytes((i * 13 + 5) % 256 for i in range(512))
        self.make_image(a=garbage)
        self.put_bootstate(LBA_B, seq=9, bootcount=0, upgrade=1)
        out = self.run_uboot()
        self.assertIn("BF_CANDIDATE", out)
        # store targeted the unusable copy A (repair semantics)
        self.assertEqual(self.read_sector_hex(LBA_A)[24:32], "0a000000")


if __name__ == "__main__":
    unittest.main()
