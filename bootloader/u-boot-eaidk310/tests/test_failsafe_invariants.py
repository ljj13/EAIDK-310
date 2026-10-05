"""P3.6 patch 0002 invariants: fail-safe bootcount-fs variant.

Static invariants of patches/0002-failsafe-bootcount-fs.patch plus a WSL
`git apply --check` proof that it stacks cleanly on patch 0001 (and on the
pristine tree), mirroring what build-cross-failsafe.sh enforces.
"""
import json
import pathlib
import re
import subprocess
import unittest

PROJECT_DIR = pathlib.Path(__file__).resolve().parents[1]
PATCHES = PROJECT_DIR / "patches"
P0001 = PATCHES / "0001-arm-dts-add-eaidk310-variants.patch"
P0002 = PATCHES / "0002-failsafe-bootcount-fs.patch"
LOCK = PROJECT_DIR / "source-lock.json"

COMMIT = "38ea74d6d5c05224acdb03f799897c1bdd56f8cc"
WSL_SRC = "~/src/u-boot-v2024.07-rc1"


def added_file(patch: str, path: str) -> str:
    pattern = re.compile(
        rf"diff --git a/{re.escape(path)} b/{re.escape(path)}\n"
        rf"(?P<body>.*?)(?=\ndiff --git |\Z)",
        re.DOTALL,
    )
    match = pattern.search(patch)
    if match is None:
        raise AssertionError(f"missing file in patch: {path}")
    return "\n".join(
        line[1:] for line in match.group("body").splitlines()
        if line.startswith("+") and not line.startswith("+++"))


class Patch0002StaticInvariants(unittest.TestCase):
    def setUp(self):
        self.patch = P0002.read_text(encoding="utf-8")

    def test_source_lock_still_pins_exact_commit(self):
        self.assertEqual(
            json.loads(LOCK.read_text(encoding="utf-8")),
            {"repository": "https://github.com/u-boot/u-boot.git",
             "tag": "v2024.07-rc1", "commit": COMMIT})

    def test_touches_exactly_three_files(self):
        files = re.findall(r"^diff --git a/(\S+) b/(\S+)$", self.patch,
                           re.MULTILINE)
        paths = {a for a, b in files}
        self.assertEqual(paths, {
            "configs/eaidk310-failsafe-fs-rk3328_defconfig",
            "drivers/bootcount/bootcount_ext.c",
            "include/configs/rk3328_common.h",
        })

    def test_does_not_touch_control_or_handoff_files(self):
        files = re.findall(r"^diff --git a/(\S+) b/(\S+)$", self.patch,
                           re.MULTILINE)
        for _, b in files:
            for banned in ("eaidk310-control-rk3328_defconfig",
                           "eaidk310-sdio-handoff-rk3328_defconfig",
                           "rk3328-eaidk310-control.dts",
                           "rk3328-eaidk310-sdio-handoff.dts",
                           "rk3328-eaidk310-common.dtsi"):
                self.assertNotIn(banned, b)

    def test_defconfig_reuses_control_device_tree(self):
        cfg = added_file(self.patch, "configs/eaidk310-failsafe-fs-rk3328_defconfig")
        self.assertIn('CONFIG_DEFAULT_DEVICE_TREE="rk3328-eaidk310-control"', cfg)
        self.assertIn('CONFIG_DEFAULT_FDT_FILE="rockchip/rk3328-eaidk310-control.dtb"', cfg)

    def test_defconfig_backend_symbols(self):
        cfg = added_file(self.patch, "configs/eaidk310-failsafe-fs-rk3328_defconfig")
        for symbol in (
            "CONFIG_BOOTCOUNT_LIMIT=y",
            "CONFIG_BOOTCOUNT_EXT=y",
            'CONFIG_SYS_BOOTCOUNT_EXT_INTERFACE="mmc"',
            'CONFIG_SYS_BOOTCOUNT_EXT_DEVPART="0:1"',
            'CONFIG_SYS_BOOTCOUNT_EXT_NAME="/eaidk-ota/bootcount.bin"',
            "CONFIG_SYS_BOOTCOUNT_ADDR=0x00300000",
            "CONFIG_SYS_BOOTCOUNT_MAGIC=0xB001C041",
            "CONFIG_SYS_BOOTCOUNT_LE=y",
            "CONFIG_BOOTCOUNT_BOOTLIMIT=1",
            "CONFIG_CMD_SYSBOOT=y",
            "CONFIG_USE_BOOTCOMMAND=y",
        ):
            self.assertIn(symbol, cfg)

    def test_defconfig_boot_policy(self):
        cfg = added_file(self.patch, "configs/eaidk310-failsafe-fs-rk3328_defconfig")
        self.assertIn(
            'CONFIG_BOOTCOMMAND="if test ${upgrade_available} -eq 1 -a '
            '${bootcount_stored} -eq 1; then sysboot mmc 0:1 ext2 ${scriptaddr} '
            '/extlinux/extlinux-candidate.conf; fi; bootflow scan"', cfg)
        self.assertIn("# CONFIG_CMD_SETEXPR is not set", cfg)
        self.assertNotIn("saveenv", cfg)
        self.assertNotIn("fw_setenv", cfg)

    def test_driver_fail_closed_paths(self):
        diff = self._file_diff(self.patch, "drivers/bootcount/bootcount_ext.c")
        self.assertEqual(diff.count("+\t\tupgrade_available = 0;"), 3)
        self.assertEqual(diff.count('+env_set_ulong("upgrade_available"'), 0)
        # two in-line exports inside error paths + one tail export
        self.assertGreaterEqual(diff.count("+\t\tenv_set_ulong("), 2)
        self.assertIn("+\tenv_set_ulong(", diff)
        # the store-side guard must remain untouched
        self.assertNotIn("-\tif (!upgrade_available)", diff)

    def test_driver_store_side_untouched(self):
        diff = self._file_diff(self.patch, "drivers/bootcount/bootcount_ext.c")
        # the write guard and store() keep their upstream shape
        self.assertNotIn("-\tif (!upgrade_available)", diff)
        self.assertNotIn("-\t\treturn;", diff)
        self.assertNotIn("-\tret = fs_write(", diff)

    def test_header_policy_guarded_by_bootcount_ext(self):
        diff = self._file_diff(self.patch, "include/configs/rk3328_common.h")
        self.assertIn("+#ifdef CONFIG_BOOTCOUNT_EXT", diff)
        added = diff.replace("+\t", "\t")
        self.assertIn('"altbootcmd=sysboot mmc 0:1 ext2 ${scriptaddr} '
                      '/extlinux/extlinux.conf; " \\\n'
                      '\t"bootflow scan\\0" \\\n'
                      '\t"bootcount_file=/eaidk-ota/bootcount.bin\\0"', added)
        self.assertIn("+#else", diff)
        self.assertIn("#define CFG_EAIDK310_FAILSAFE_ENV_SETTINGS\n+#endif", added)
        self.assertIn("+\tCFG_EAIDK310_FAILSAFE_ENV_SETTINGS", diff)

    @staticmethod
    def _file_diff(patch: str, path: str) -> str:
        pattern = re.compile(
            rf"diff --git a/{re.escape(path)} b/{re.escape(path)}\n"
            rf"(?P<body>.*?)(?=\ndiff --git |\Z)", re.DOTALL)
        return pattern.search(patch).group("body")


@unittest.skipUnless(pathlib.Path("C:/Program Files").exists(),
                     "runs only where WSL is available")
class Patch0002ApplyTests(unittest.TestCase):
    def wsl(self, command: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["wsl", "-d", "Ubuntu-24.04", "-u", "Fog", "--", "bash", "-c", command],
            text=True, capture_output=True, timeout=300)

    def wsl_script(self, script: str) -> subprocess.CompletedProcess:
        """Run a multi-line script through WSL via base64 packing.
        wsl.exe re-parses its command line, which mangles $() and quotes in
        multi-line scripts; base64 payloads are immune."""
        import base64
        encoded = base64.b64encode(script.encode()).decode()
        return self.wsl(f"echo {encoded} | base64 -d | bash")

    def test_patch_applies_pristine_and_stacked(self):
        p0002 = P0002.as_posix().replace("D:/", "/mnt/d/")
        p0001 = P0001.as_posix().replace("D:/", "/mnt/d/")
        script = f"""
set -euo pipefail
test -z "$(git -C {WSL_SRC} status --porcelain)"
actual=$(git -C {WSL_SRC} rev-parse HEAD)
test "$actual" = "{COMMIT}"
git -C {WSL_SRC} apply --check {p0002}
git -C {WSL_SRC} apply --check {p0001}
wt=$(mktemp -d)
git -C {WSL_SRC} worktree add --detach "$wt" {COMMIT} >/dev/null 2>&1
trap 'git -C {WSL_SRC} worktree remove --force "$wt" >/dev/null 2>&1' EXIT
git -C "$wt" apply {p0001}
git -C "$wt" apply --check {p0002}
echo APPLY-OK
"""
        r = self.wsl_script(script)
        self.assertEqual(r.returncode, 0, f"{r.stdout}\n{r.stderr}")
        self.assertIn("APPLY-OK", r.stdout)


if __name__ == "__main__":
    unittest.main()
