import pathlib
import re
import unittest


PROJECT_DIR = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PROJECT_DIR / "scripts"


class BuildScriptContractTests(unittest.TestCase):
    def read_script(self, name: str) -> str:
        return (SCRIPTS_DIR / name).read_text(encoding="utf-8")

    def test_every_script_is_strict_and_has_no_disk_write_surface(self):
        for name in (
            "verify-source-and-patch.sh",
            "build-native.sh",
            "build-cross.sh",
        ):
            with self.subTest(name=name):
                script = self.read_script(name)
                self.assertIn("set -euo pipefail", script)
                self.assertNotRegex(script, r"\bdd\b")
                self.assertNotIn("/dev/mmcblk2", script)
                self.assertNotIn("PhysicalDrive", script)

    def test_source_gate_checks_exact_commit_cleanliness_and_patch(self):
        script = self.read_script("verify-source-and-patch.sh")

        self.assertIn("38ea74d6d5c05224acdb03f799897c1bdd56f8cc", script)
        self.assertIn("rev-parse HEAD", script)
        self.assertIn("status --porcelain", script)
        self.assertIn("apply --check", script)

    def test_native_build_uses_separate_outputs_and_one_job(self):
        script = self.read_script("build-native.sh")

        self.assertIn("eaidk310-control-rk3328_defconfig", script)
        self.assertIn("eaidk310-sdio-handoff-rk3328_defconfig", script)
        self.assertRegex(script, r'O="\$output_root/control"')
        self.assertRegex(script, r'O="\$output_root/sdio-handoff"')
        self.assertGreaterEqual(script.count("-j1 u-boot-dtb.bin"), 2)
        self.assertIn("mktemp -d", script)

    def test_cross_build_uses_aarch64_toolchain_and_two_jobs(self):
        script = self.read_script("build-cross.sh")

        self.assertIn("CROSS_COMPILE=aarch64-linux-gnu-", script)
        self.assertGreaterEqual(script.count("-j2 u-boot-dtb.bin"), 2)
        self.assertIn("mktemp -d", script)

    def test_builds_gate_payload_size_model_and_live_dtb(self):
        for name in ("build-native.sh", "build-cross.sh"):
            with self.subTest(name=name):
                script = self.read_script(name)
                self.assertIn("1046528", script)
                self.assertIn("Rockchip RK3328 EAIDK310", script)
                self.assertIn("dtc -I dtb -O dts", script)
                self.assertIn("fdtget", script)
                self.assertIn('"/mmc@ff510000"', script)
                self.assertIn("125000000", script)

    def test_builds_validate_the_selected_dtb_with_supported_fdtget_type(self):
        for name in ("build-native.sh", "build-cross.sh"):
            with self.subTest(name=name):
                script = self.read_script(name)
                self.assertIn('dtb="$output_root/$variant/dts/dt.dtb"', script)
                self.assertIn('fdtget -t u', script)
                self.assertNotIn('fdtget -t d', script)
                self.assertNotIn('$variant/u-boot.dtb', script)

    def test_readme_documents_native_dependencies_and_safety_boundary(self):
        readme = (PROJECT_DIR / "README.md").read_text(encoding="utf-8")

        for dependency in (
            "build-essential",
            "device-tree-compiler",
            "python3-pyelftools",
        ):
            self.assertIn(dependency, readme)
        self.assertIn("384 MiB zram", readme)
        self.assertIn("-j1", readme)
        self.assertIn("eMMC", readme)
        self.assertIn("unmounted", readme)


if __name__ == "__main__":
    unittest.main()
