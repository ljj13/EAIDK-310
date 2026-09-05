import importlib.util
from pathlib import Path
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPOSITORY_ROOT / "tools" / "sync_public_tree.py"
BOARD_TOOL_FIXTURES = (
    "build_eaidk310_st7789_test.py",
    "capture-eaidk-boot.ps1",
    "eaidk-uboot-write-lib.ps1",
    "eaidk310-emmc-excludes.txt",
    "eaidk310_emmc.py",
    "eaidk310_emmc_lib.py",
    "prepare_eaidk310_uboot.py",
    "rockchip_loaderimage.py",
    "serial-console.ps1",
    "serial-run.ps1",
    "st7789_framebuffer_test.py",
    "test-capture-eaidk-boot.ps1",
    "test-serial-console.ps1",
    "test-write-uboot-proper-to-tf.ps1",
    "test_build_eaidk310_st7789.py",
    "test_eaidk310_emmc.py",
    "test_eaidk310_emmc_lib.py",
    "test_prepare_eaidk310_uboot.py",
    "test_rockchip_loaderimage.py",
    "test_st7789_framebuffer_test.py",
    "write-uboot-proper-to-tf.ps1",
    "write-uboot-to-tf.ps1",
)


def load_sync():
    spec = importlib.util.spec_from_file_location("sync_public_tree", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SyncBootstrapTests(unittest.TestCase):
    def test_sync_module_exists(self):
        self.assertTrue(MODULE_PATH.is_file(), "tools/sync_public_tree.py is missing")


@unittest.skipUnless(MODULE_PATH.is_file(), "sync tool not implemented yet")
class SyncPublicTreeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temp.name) / "workspace"
        self.public = Path(self.temp.name) / "public"
        self.workspace.mkdir()
        self.public.mkdir()
        for name in BOARD_TOOL_FIXTURES:
            path = self.workspace / "tools" / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("fixture\n", encoding="utf-8")
        base_dts = self.workspace / "wifi-test1" / "current-rk3328-eaidk-310.dts"
        base_dts.parent.mkdir(parents=True, exist_ok=True)
        base_dts.write_text("stable board dts\n", encoding="utf-8")

    def tearDown(self):
        self.temp.cleanup()

    def write(self, relative: str, content: str = "fixture\n") -> None:
        path = self.workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_sync_includes_build_inputs_but_excludes_artifacts(self):
        self.write("kernel-6.12.108-zramfix1/source-lock.json", "{}\n")
        self.write("kernel-6.12.108-zramfix1/README.md")
        self.write("kernel-6.12.108-zramfix1/config/eaidk310.config")
        self.write("kernel-6.12.108-zramfix1/baseline/config")
        self.write("kernel-6.12.108-zramfix1/dts/board.dts")
        self.write("kernel-6.12.108-zramfix1/initramfs/initramfs.conf")
        self.write("kernel-6.12.108-zramfix1/patches/board.patch")
        self.write("kernel-6.12.108-zramfix1/scripts/build.sh")
        self.write("kernel-6.12.108-zramfix1/tests/test_build.py")
        self.write("kernel-6.12.108-zramfix1/tests/test_documentation_contract.py")
        self.write("kernel-6.12.108-zramfix1/tools/manifest.py")
        self.write("kernel-6.12.108-zramfix1/artifacts/kernel.tar.zst")
        self.write("kernel-6.12.108-zramfix1/analysis/board-port-matrix.json", "{}\n")
        self.write("kernel-6.12.108-zramfix1/analysis/build.log")
        self.write("u-boot-eaidk310/source-lock.json", "{}\n")
        self.write("u-boot-eaidk310/README.md")
        self.write("u-boot-eaidk310/patches/board.patch")
        self.write("u-boot-eaidk310/scripts/build.sh")
        self.write("u-boot-eaidk310/tests/test_build.py")

        copied = load_sync().sync(self.workspace, self.public)

        self.assertIn("kernel/linux-6.12.108-zramfix1/source-lock.json", copied)
        self.assertTrue((self.public / "kernel/linux-6.12.108-zramfix1/config/eaidk310.config").is_file())
        self.assertTrue((self.public / "kernel/linux-6.12.108-zramfix1/analysis/board-port-matrix.json").is_file())
        self.assertTrue((self.public / "bootloader/u-boot-eaidk310/patches/board.patch").is_file())
        self.assertFalse((self.public / "kernel/linux-6.12.108-zramfix1/artifacts").exists())
        self.assertFalse((self.public / "kernel/linux-6.12.108-zramfix1/analysis/build.log").exists())
        self.assertFalse((self.public / "kernel/linux-6.12.108-zramfix1/tests/test_documentation_contract.py").exists())

    def test_sync_refuses_unknown_kernel_top_level_entry(self):
        self.write("kernel-6.12.108-zramfix1/source-lock.json", "{}\n")
        self.write("kernel-6.12.108-zramfix1/private-dump/board-secrets.txt")
        self.write("u-boot-eaidk310/source-lock.json", "{}\n")

        with self.assertRaisesRegex(ValueError, "private-dump"):
            load_sync().sync(self.workspace, self.public)

    def test_sync_copies_only_allowlisted_board_tools(self):
        for relative in (
            "README.md",
            "baseline/config",
            "config/eaidk310.config",
            "dts/board.dts",
            "initramfs/initramfs.conf",
            "patches/board.patch",
            "scripts/build.sh",
            "source-lock.json",
            "tests/test_build.py",
            "tools/manifest.py",
            "analysis/board-port-matrix.json",
        ):
            self.write(f"kernel-6.12.108-zramfix1/{relative}", "{}\n" if relative.endswith(".json") else "fixture\n")
        for relative in (
            "README.md",
            "patches/board.patch",
            "scripts/build.sh",
            "source-lock.json",
            "tests/test_build.py",
        ):
            self.write(f"u-boot-eaidk310/{relative}", "{}\n" if relative.endswith(".json") else "fixture\n")
        self.write("tools/rufus-4.15p.exe", "binary\n")

        load_sync().sync(self.workspace, self.public)

        self.assertTrue((self.public / "tools/write-uboot-to-tf.ps1").is_file())
        self.assertTrue((self.public / "tools/eaidk310_emmc.py").is_file())
        self.assertEqual(
            (self.public / "hardware/st7789/current-rk3328-eaidk-310.dts").read_text(encoding="utf-8"),
            "stable board dts\n",
        )
        self.assertFalse((self.public / "tools/rufus-4.15p.exe").exists())
        self.assertFalse((self.public / "tools/test_rockchip_loaderimage.py").exists())


if __name__ == "__main__":
    unittest.main()
