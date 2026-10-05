"""P3.6-B patch 0003 invariants: raw dual-copy bootstate variant.

Static invariants of patches/0003-failsafe-raw-bootstate.patch plus the
critical cross-artifact consistency: the LBA constants in the U-Boot
defconfig, in the driver, and in the Linux eaidk-bootstate tool MUST be
identical (a silent drift would point the bootloader and the operator at
different sectors).
"""
import json
import pathlib
import re
import unittest

PROJECT_DIR = pathlib.Path(__file__).resolve().parents[1]
PATCH = PROJECT_DIR / "patches" / "0003-failsafe-raw-bootstate.patch"
LOCK = PROJECT_DIR / "source-lock.json"
OTA_TOOL = PROJECT_DIR.parents[1] / "ota" / "eaidk-bootstate"
COMMIT = "38ea74d6d5c05224acdb03f799897c1bdd56f8cc"


class Patch0003StaticInvariants(unittest.TestCase):
    def setUp(self):
        self.patch = PATCH.read_text(encoding="utf-8")

    def test_source_lock_unchanged(self):
        self.assertEqual(
            json.loads(LOCK.read_text(encoding="utf-8"))["commit"], COMMIT)

    def test_touches_exactly_five_files(self):
        files = {a for a, b in re.findall(
            r"^diff --git a/(\S+) b/(\S+)$", self.patch, re.MULTILINE)}
        self.assertEqual(files, {
            "configs/eaidk310-failsafe-raw-rk3328_defconfig",
            "drivers/bootcount/Kconfig",
            "drivers/bootcount/Makefile",
            "drivers/bootcount/bootcount_eaidk310_raw.c",
            "include/configs/rk3328_common.h",
        })

    def test_does_not_touch_control_handoff_or_candidate_a(self):
        for _, b in re.findall(r"^diff --git a/(\S+) b/(\S+)$",
                               self.patch, re.MULTILINE):
            for banned in ("eaidk310-control-rk3328_defconfig",
                           "eaidk310-sdio-handoff-rk3328_defconfig",
                           "eaidk310-failsafe-fs-rk3328_defconfig",
                           "bootcount_ext.c", "bootcount_ext.o"):
                self.assertNotIn(banned, b)

    def test_driver_fail_closed_surface(self):
        drv = self._added("drivers/bootcount/bootcount_eaidk310_raw.c")
        # fail-closed: identity mismatch, read error, conflict, exhaustion
        for needle in ("identity mismatch", "read error",
                       "sequence conflict", "sequence exhausted",
                       "device not found", "refusing store"):
            self.assertIn(needle, drv)
        # store never touches the selected copy: write happens after
        # explicit target selection below the selection call
        self.assertIn("bootcount_store_ok = 0;", drv)
        self.assertIn('env_set_ulong("bootcount_stored", bootcount_store_ok);',
                      drv)
        # no environment persistence anywhere
        self.assertNotIn("saveenv", drv)
        self.assertNotIn("env_save", drv)

    def test_driver_uses_dm_native_lookup(self):
        drv = self._added("drivers/bootcount/bootcount_eaidk310_raw.c")
        self.assertIn("blk_get_devnum_by_uclass_idname(", drv)
        # the legacy registry lookup must not be used (empty in DM builds)
        self.assertNotIn("blk_get_device_by_str(", drv)

    def test_record_format_constants(self):
        drv = self._added("drivers/bootcount/bootcount_eaidk310_raw.c")
        self.assertIn('#define EA310_BS_MAGIC\t\t"EA310BS1"', drv)
        self.assertIn("#define EA310_BS_VERSION\t1", drv)
        self.assertIn("#define EA310_BS_SIZE\t\t512", drv)
        self.assertIn("#define EA310_BS_CRC_OFF\t508", drv)
        self.assertIn("#define EA310_BS_SEQ_MAX\t0xffffffffu", drv)

    def test_defconfig_targets_and_policy(self):
        cfg = self._added("configs/eaidk310-failsafe-raw-rk3328_defconfig")
        self.assertIn('CONFIG_DEFAULT_DEVICE_TREE="rk3328-eaidk310-control"', cfg)
        self.assertIn("CONFIG_BOOTCOUNT_EAIDK310_RAW=y", cfg)
        self.assertIn('CONFIG_SYS_BOOTCOUNT_RAW_INTERFACE="mmc"', cfg)
        self.assertIn('CONFIG_SYS_BOOTCOUNT_RAW_DEVID="0"', cfg)
        self.assertIn("CONFIG_SYS_BOOTCOUNT_RAW_LBA_A=0x6400", cfg)
        self.assertIn("CONFIG_SYS_BOOTCOUNT_RAW_LBA_B=0x7800", cfg)
        self.assertIn("CONFIG_SYS_BOOTCOUNT_RAW_LBA_DIAG=0x6C00", cfg)
        self.assertIn("CONFIG_SYS_BOOTCOUNT_RAW_DIAG=y", cfg)
        self.assertIn('CONFIG_SYS_BOOTCOUNT_RAW_MMC_NAME="HBD08G"', cfg)
        self.assertIn("CONFIG_SYS_BOOTCOUNT_RAW_MMC_MINSIZE=0x1d2000000", cfg)
        self.assertIn("CONFIG_BOOTCOUNT_BOOTLIMIT=1", cfg)
        self.assertIn(
            'CONFIG_BOOTCOMMAND="if test ${upgrade_available} -eq 1 -a '
            '${bootcount_stored} -eq 1; then sysboot mmc 0:1 ext2 ${scriptaddr} '
            '/extlinux/extlinux-candidate.conf; fi; bootflow scan"', cfg)
        self.assertNotIn("CONFIG_BOOTCOUNT_EXT=y", cfg)

    def test_header_guard_covers_both_backends(self):
        diff = self._diff("include/configs/rk3328_common.h")
        self.assertIn(
            "+#if defined(CONFIG_BOOTCOUNT_EXT) || "
            "defined(CONFIG_BOOTCOUNT_EAIDK310_RAW)", diff)
        # bootcount_file must stay ext4-only
        self.assertIn("+#ifdef CONFIG_BOOTCOUNT_EXT", diff)

    def test_lba_constants_match_linux_tool(self):
        """Cross-artifact invariant: bootloader and operator tool must agree
        on the record sectors."""
        tool = OTA_TOOL.read_text(encoding="utf-8")
        self.assertIn("RAW_DEFAULT_LBA_A = 0x6400", tool)
        self.assertIn("RAW_DEFAULT_LBA_B = 0x7800", tool)
        self.assertIn('RAW_MAGIC = b"EA310BS1"', tool)
        self.assertIn("RAW_CRC_OFFSET = 508", tool)
        cfg = self._added("configs/eaidk310-failsafe-raw-rk3328_defconfig")
        self.assertIn("CONFIG_SYS_BOOTCOUNT_RAW_LBA_A=0x6400", cfg)
        self.assertIn("CONFIG_SYS_BOOTCOUNT_RAW_LBA_B=0x7800", cfg)
        self.assertIn("CONFIG_SYS_BOOTCOUNT_RAW_LBA_DIAG=0x6C00", cfg)
        self.assertIn("CONFIG_SYS_BOOTCOUNT_RAW_DIAG=y", cfg)

    def _added(self, path: str) -> str:
        pattern = re.compile(
            rf"diff --git a/{re.escape(path)} b/{re.escape(path)}\n"
            rf"(?P<body>.*?)(?=\ndiff --git |\Z)", re.DOTALL)
        body = pattern.search(self.patch).group("body")
        return "\n".join(line[1:] for line in body.splitlines()
                         if line.startswith("+") and not line.startswith("+++"))

    def _diff(self, path: str) -> str:
        pattern = re.compile(
            rf"diff --git a/{re.escape(path)} b/{re.escape(path)}\n"
            rf"(?P<body>.*?)(?=\ndiff --git |\Z)", re.DOTALL)
        return pattern.search(self.patch).group("body")


if __name__ == "__main__":
    unittest.main()
