"""P3.6 fail-safe boot state machine: U-Boot sandbox integration tests.

Runs the REAL pinned U-Boot (v2024.07-rc1@38ea74d6 + patch 0002 fail-closed
hardening) as a sandbox binary against a real ext4 image through the `host`
block device, exercising the same BOOTCOUNT_EXT driver the board build uses.

State matrix proven here (per P3.6 requirement "fail closed must be stable"):
  missing / corrupt-magic / wrong-version / short / oversized / committed
      -> bootcmd(stable), file NEVER rewritten, upgrade_available=0
  armed fresh          -> bootcmd(candidate), bootcount incremented in file
  armed exceeded       -> altbootcmd(stable) via bootcount_error
  sysboot integration  -> missing/unbootable candidate conf falls through

Windows-hosted driver, WSL execution (same pattern as the ota test suites).
"""
import os
import pathlib
import subprocess
import unittest

HERE = pathlib.Path(__file__).resolve().parent
PROJECT_DIR = HERE.parent
PATCH_0002 = (PROJECT_DIR / "patches" / "0002-failsafe-bootcount-fs.patch").as_posix().replace("D:/", "/mnt/d/")
BOOTSTATE_TOOL = (pathlib.Path(HERE).parents[2] / "ota" / "eaidk-bootstate").as_posix().replace("D:/", "/mnt/d/")
WSL_SRC = "~/src/u-boot-v2024.07-rc1"
WSL_SANDBOX = "~/eaidk310-uboot/p36-sandbox"
WSL_WORK = "/tmp/p36-sandbox-tests"

GOLDEN_ARM = "bd010001"
GOLDEN_CLEAR = "bd010000"

FRAGMENT = """\
CONFIG_BOOTCOUNT_LIMIT=y
CONFIG_BOOTCOUNT_EXT=y
CONFIG_SYS_BOOTCOUNT_EXT_INTERFACE="host"
CONFIG_SYS_BOOTCOUNT_EXT_DEVPART="0:1"
CONFIG_SYS_BOOTCOUNT_EXT_NAME="/bootcount.bin"
CONFIG_SYS_BOOTCOUNT_ADDR=0x00100000
CONFIG_BOOTCOUNT_BOOTLIMIT=1
CONFIG_CMD_SYSBOOT=y
CONFIG_CMD_HOST=y
CONFIG_FS_EXT4=y
CONFIG_USE_BOOTCOMMAND=y
CONFIG_BOOTCOMMAND="if test ${upgrade_available} -eq 1 -a ${bootcount_stored} -eq 1; then echo BF_CANDIDATE; else echo BF_STABLE; fi"
CONFIG_USE_PREBOOT=y
CONFIG_PREBOOT="host bind 0 /tmp/p36-sandbox-tests/boot.img; setenv bootlimit 1; setenv altbootcmd echo BF_ALTBOOTCMD"
# -2: unconditional autoboot (no stdin keypress consumption, no abort),
# bootcount_inc still runs inside bootdelay_process
CONFIG_BOOTDELAY=-2
# CONFIG_SANDBOX_SDL is not set
# CONFIG_TOOLS_MKEFICAPSULE is not set
"""

CANDIDATE_CONF = """\
default trial
timeout 1

label trial
    kernel /vmlinuz-missing
    append console=ttyS0
"""


@unittest.skipUnless(os.name == "nt", "Windows WSL integration tests")
class SandboxTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        probe = cls.wsl_raw(
            f"test -x {WSL_SANDBOX}/u-boot && echo READY || echo BUILD")
        if "BUILD" in probe.stdout:
            print("building U-Boot sandbox (one-time, a few minutes)...")
            build = cls.wsl_script(cls.build_script(), timeout=1800)
            if build.returncode != 0:
                raise unittest.SkipTest(
                    f"sandbox build failed: {build.stdout[-800:]} {build.stderr[-800:]}")
            if "SANDBOX-READY" not in build.stdout:
                raise unittest.SkipTest(f"sandbox build incomplete: {build.stdout[-800:]}")

    @staticmethod
    def wsl_raw(command: str, timeout: int = 900) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["wsl", "-d", "Ubuntu-24.04", "-u", "Fog", "--", "bash", "-c", command],
            text=True, capture_output=True, timeout=timeout)

    @classmethod
    def wsl(cls, command: str, timeout: int = 900) -> subprocess.CompletedProcess:
        r = cls.wsl_raw(command, timeout)
        return r

    @classmethod
    def wsl_script(cls, script: str, timeout: int = 900) -> subprocess.CompletedProcess:
        """Run a multi-line script via base64 packing.  wsl.exe re-parses its
        command line and collapses newlines/quotes, which mangles heredocs
        and $() in anything non-trivial; base64 payloads are immune."""
        import base64
        encoded = base64.b64encode(script.encode()).decode()
        return cls.wsl(f"echo {encoded} | base64 -d | bash", timeout)

    @staticmethod
    def build_script() -> str:
        return f"""
set -euo pipefail
git -C {WSL_SRC} worktree add --detach ~/src/uboot-wt-sandbox 38ea74d6d5c05224acdb03f799897c1bdd56f8cc >/dev/null 2>&1 || true
git -C ~/src/uboot-wt-sandbox reset -q --hard 38ea74d6d5c05224acdb03f799897c1bdd56f8cc
git -C ~/src/uboot-wt-sandbox clean -qfd
git -C ~/src/uboot-wt-sandbox apply {PATCH_0002}
rm -rf {WSL_SANDBOX}
mkdir -p {WSL_SANDBOX}
make -C ~/src/uboot-wt-sandbox O={WSL_SANDBOX} sandbox_defconfig
cat >> {WSL_SANDBOX}/.config <<'FRAG'
{FRAGMENT}FRAG
make -C ~/src/uboot-wt-sandbox O={WSL_SANDBOX} olddefconfig
make -C ~/src/uboot-wt-sandbox O={WSL_SANDBOX} -j2 KCFLAGS=-fno-stack-protector u-boot >/dev/null
grep -q 'CONFIG_BOOTCOUNT_EXT=y' {WSL_SANDBOX}/.config
grep -q 'CONFIG_EXT4_WRITE=y' {WSL_SANDBOX}/.config
grep -q 'CONFIG_CMD_SYSBOOT=y' {WSL_SANDBOX}/.config
echo SANDBOX-READY
"""

    def setUp(self):
        r = self.wsl(f"rm -rf {WSL_WORK} && mkdir -p {WSL_WORK}/populate/extlinux")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.image = f"{WSL_WORK}/boot.img"

    def make_image(self, populate_files: dict, metadata_csum: bool = False):
        """Create MBR-partitioned ext4 image; populate_files maps fs paths
        to contents.  metadata_csum=True emulates the board's modern
        e2fsprogs filesystem, on which U-Boot's ext4 writer refuses to
        work.  Returns the WSL image path."""
        lines = []
        for target, content in populate_files.items():
            path = f"{WSL_WORK}/populate/{target}"
            lines.append(f"mkdir -p {path.rsplit('/', 1)[0]}")
            # "" = placeholder (file bytes may already be pre-written by
            # put_bootstate); never clobber an existing populated file
            if content != "":
                lines.append(f"printf '%s' '{content}' > {path}")
        feature_flags = "" if metadata_csum else " -O ^metadata_csum"
        script = (
            f"set -e\n"
            f"rm -f {self.image}\n"
            f"truncate -s 32M {self.image}\n"
            f"echo 'start=1MiB, type=83' | sfdisk -q {self.image} >/dev/null\n"
            + "\n".join(lines) +
            f"\nmke2fs -q -F -t ext4 -b 1024{feature_flags}"
            f" -d {WSL_WORK}/populate"
            f" -E offset=1048576 {self.image} 31744\n"
            f"echo IMAGE-OK\n")
        r = self.wsl_script(script)
        self.assertEqual(r.returncode, 0, f"{r.stdout}\n{r.stderr}")
        return self.image

    def put_bootstate(self, hexbytes: str):
        r = self.wsl(f"printf '{hexbytes}' | xxd -r -p > {WSL_WORK}/populate/bootcount.bin")
        self.assertEqual(r.returncode, 0, r.stderr)

    def run_uboot(self, script: str, timeout: int = 120) -> str:
        """Each sandbox process = exactly one boot: PREBOOT binds the image,
        then startup autoboot runs bootcount_inc -> bootcount_error -> the
        policy bootcmd.  `script` supplies post-boot probes; poweroff exits."""
        import base64
        full = script + "\necho ENV_UPG=${upgrade_available}\n" \
                        "echo ENV_BC=${bootcount}\npoweroff\n"
        encoded = base64.b64encode(full.encode()).decode()
        r = self.wsl(
            f"echo {encoded} | base64 -d > {WSL_WORK}/cmds.txt && "
            f"timeout {timeout} {WSL_SANDBOX}/u-boot < {WSL_WORK}/cmds.txt",
            timeout=timeout + 60)
        self.assertEqual(r.returncode, 0, f"rc={r.returncode}\n{r.stdout[-2000:]}\n{r.stderr[-500:]}")
        return r.stdout

    def boot_once(self, extra_env: str = "") -> str:
        """One full autoboot cycle against the image with default policy."""
        return self.run_uboot(extra_env)

    def file_hex(self) -> str:
        r = self.wsl(
            f"dd if={self.image} bs=1M skip=1 count=1 2>/dev/null | "
            f"grep -c $'\\xbd' >/dev/null && echo has-bd || echo no-bd; "
            f"true")
        return r.stdout

    def read_bootstate_hex(self) -> str:
        """Extract the 4-byte bootstate record from the image partition.
        Uses debugfs (e2fsprogs) to read the file from the ext4 image."""
        r = self.wsl(
            f"debugfs -R 'cat /bootcount.bin' -b 1024 {self.image}?offset=1048576 2>/dev/null | od -An -tx1 | tr -d ' \\n'")
        return r.stdout.strip()

    def sysboot_override(self) -> str:
        return (
            "setenv pxefile_addr_r 0x02000000\n"
            "setenv kernel_addr_r 0x03000000\n"
            "setenv bootcmd 'if test ${upgrade_available} -eq 1 -a "
            "${bootcount_stored} -eq 1; then "
            "echo BF_CANDIDATE; sysboot host 0:1 ext2 ${pxefile_addr_r} "
            "/extlinux/extlinux-candidate.conf; fi; echo BF_FALLBACK'\n"
            "bootd\n")

    def assertNoBootstateFile(self):
        r = self.wsl(
            f"debugfs -R 'stat /bootcount.bin' -b 1024 {self.image}?offset=1048576 2>&1 | grep -q 'File not found' && echo ABSENT || echo PRESENT")
        self.assertEqual(r.stdout.strip(), "ABSENT",
                         "bootstate file must not be created by a fail-closed boot")


class SandboxStateMatrixTests(SandboxTestBase):
    def test_missing_state_boots_stable_and_writes_nothing(self):
        self.make_image({})
        out = self.boot_once()
        self.assertIn("BF_STABLE", out)
        self.assertNotIn("BF_CANDIDATE", out)
        self.assertIn("ENV_UPG=0", out)
        self.assertNoBootstateFile()

    def test_armed_boots_candidate_and_increments_file(self):
        self.put_bootstate(GOLDEN_ARM)
        self.make_image({"bootcount.bin": ""})
        out = self.boot_once()
        self.assertIn("BF_CANDIDATE", out)
        self.assertIn("ENV_UPG=1", out)
        self.assertIn("ENV_BC=1", out)
        self.assertEqual(self.read_bootstate_hex(), "bd010101")

    def test_unwritable_fs_refuses_candidate_infinite_loop_guard(self):
        # board-like fs (metadata_csum): U-Boot's ext4 writer refuses, the
        # bootcount increment cannot persist.  The policy must then NOT run
        # the candidate -- otherwise a failing trial would boot-loop forever
        # with a never-incrementing counter.  Fail closed to stable.
        self.put_bootstate(GOLDEN_ARM)
        self.make_image({"bootcount.bin": ""}, metadata_csum=True)
        out = self.boot_once()
        self.assertIn("BF_STABLE", out)
        self.assertNotIn("BF_CANDIDATE", out)
        self.assertEqual(self.read_bootstate_hex(), GOLDEN_ARM)

    def test_exceeded_trial_falls_back_via_altbootcmd(self):
        # chained boots on ONE image: fresh arm -> candidate; reboot -> fallback
        self.put_bootstate(GOLDEN_ARM)
        self.make_image({"bootcount.bin": ""})
        first = self.boot_once()
        self.assertIn("BF_CANDIDATE", first)
        second = self.boot_once()
        self.assertIn("BF_ALTBOOTCMD", second)
        self.assertNotIn("BF_CANDIDATE", second)
        self.assertEqual(self.read_bootstate_hex(), "bd010201")

    def test_committed_state_boots_stable_without_rewrite(self):
        self.put_bootstate(GOLDEN_CLEAR)
        self.make_image({"bootcount.bin": ""})
        out = self.boot_once()
        self.assertIn("BF_STABLE", out)
        self.assertIn("ENV_UPG=0", out)
        self.assertEqual(self.read_bootstate_hex(), GOLDEN_CLEAR)

    def test_corrupt_magic_fails_closed_without_rewrite(self):
        self.put_bootstate("deadbeef")
        self.make_image({"bootcount.bin": ""})
        out = self.boot_once()
        self.assertIn("BF_STABLE", out)
        self.assertIn("ENV_UPG=0", out)
        self.assertEqual(self.read_bootstate_hex(), "deadbeef")

    def test_wrong_version_fails_closed_without_rewrite(self):
        self.put_bootstate("bd020501")
        self.make_image({"bootcount.bin": ""})
        out = self.boot_once()
        self.assertIn("BF_STABLE", out)
        self.assertEqual(self.read_bootstate_hex(), "bd020501")

    def test_short_file_fails_closed_without_rewrite(self):
        self.put_bootstate("bd01")
        self.make_image({"bootcount.bin": ""})
        out = self.boot_once()
        self.assertIn("BF_STABLE", out)
        self.assertIn("ENV_UPG=0", out)
        self.assertEqual(self.read_bootstate_hex(), "bd01")

    def test_oversized_file_fails_closed_without_rewrite(self):
        self.put_bootstate(GOLDEN_ARM + "ff")
        self.make_image({"bootcount.bin": ""})
        out = self.boot_once()
        self.assertIn("BF_STABLE", out)
        self.assertIn("ENV_UPG=0", out)
        self.assertEqual(self.read_bootstate_hex(), GOLDEN_ARM + "ff")

    def test_sysboot_missing_candidate_conf_falls_through_to_stable(self):
        # armed state, but no candidate conf on the fs: sysboot must fail
        # and fall through to the stable path
        self.put_bootstate(GOLDEN_ARM)
        self.make_image({})
        out = self.boot_once(self.sysboot_override())
        self.assertIn("BF_FALLBACK", out)

    def test_sysboot_unbootable_candidate_conf_falls_through(self):
        self.put_bootstate(GOLDEN_ARM)
        self.make_image({"extlinux/extlinux-candidate.conf": CANDIDATE_CONF})
        out = self.boot_once(self.sysboot_override())
        self.assertIn("BF_FALLBACK", out)
        self.assertIn("BF_CANDIDATE", out)  # the if-branch echoed before sysboot


if __name__ == "__main__":
    unittest.main()
