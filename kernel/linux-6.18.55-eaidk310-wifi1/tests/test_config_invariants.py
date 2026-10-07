import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


PROJECT_DIR = pathlib.Path(__file__).resolve().parents[1]
CLI = PROJECT_DIR / "tools" / "kernel_artifacts.py"
CONFIG = PROJECT_DIR / "config" / "eaidk310-6.18.55-wifi1.config"


class KernelConfigInvariantTests(unittest.TestCase):
    def run_check(self, config: pathlib.Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CLI), "check-config", "--config", str(config)],
            cwd=PROJECT_DIR,
            text=True,
            capture_output=True,
            check=False,
        )

    def test_final_config_passes_the_boot_and_hardware_gate(self):
        result = self.run_check(CONFIG)

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["kernel_release"], "6.18.55-eaidk310-wifi1")
        self.assertEqual(report["config"], str(CONFIG))
        self.assertEqual(report["required_builtin_symbols"], 22)
        self.assertEqual(report["required_driver_symbols"], 12)

    def test_seed_gate_accepts_the_reviewed_config(self):
        result = self.run_check_mode(CONFIG, "seed")

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["mode"], "seed")

    def test_seed_gate_rejects_an_empty_default_config(self):
        # Reproduces the P1 defect: olddefconfig on an empty build directory
        # produced an all-default config that must be caught in seconds.
        impostor = "\n".join(
            (
                "#",
                "# Automatically generated file; DO NOT EDIT.",
                "#",
                'CONFIG_LOCALVERSION=""',
                "CONFIG_LOCALVERSION_AUTO=y",
                "CONFIG_DRM_ROCKCHIP=m",
                "CONFIG_RTC_DRV_RK808=m",
            )
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = pathlib.Path(temporary_directory) / ".config"
            path.write_text(impostor, encoding="utf-8")
            result = self.run_check_mode(path, "seed")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("seed config is not the reviewed EAIDK310 config", result.stderr)

    def test_seed_gate_rejects_wrong_localversion(self):
        broken = CONFIG.read_text(encoding="utf-8").replace(
            'CONFIG_LOCALVERSION="-eaidk310-wifi1"',
            'CONFIG_LOCALVERSION="-impostor"',
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = pathlib.Path(temporary_directory) / ".config"
            path.write_text(broken, encoding="utf-8")
            result = self.run_check_mode(path, "seed")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CONFIG_LOCALVERSION", result.stderr)

    def test_seed_gate_rejects_missing_zram_lz4_backend(self):
        broken = CONFIG.read_text(encoding="utf-8").replace(
            "CONFIG_ZRAM_BACKEND_LZ4=y",
            "# CONFIG_ZRAM_BACKEND_LZ4 is not set",
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = pathlib.Path(temporary_directory) / ".config"
            path.write_text(broken, encoding="utf-8")
            result = self.run_check_mode(path, "seed")

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CONFIG_ZRAM_BACKEND_LZ4", result.stderr)

    def test_final_gate_rejects_a_defconfig_impostor_profile(self):
        # An arm64-defconfig-shaped profile: rockchip built-ins default y but
        # DRM_ROCKCHIP/RTC_DRV_RK808 end up modular and LOCALVERSION is empty.
        impostor = "\n".join(
            (
                "CONFIG_ARM64=y",
                "CONFIG_ARCH_ROCKCHIP=y",
                "CONFIG_MMC=y",
                "CONFIG_MMC_DW=y",
                "CONFIG_MMC_DW_ROCKCHIP=y",
                "CONFIG_DRM_ROCKCHIP=m",
                "CONFIG_RTC_DRV_RK808=m",
                'CONFIG_LOCALVERSION=""',
                "CONFIG_LOCALVERSION_AUTO=y",
            )
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = pathlib.Path(temporary_directory) / ".config"
            path.write_text(impostor, encoding="utf-8")
            result = self.run_check(path)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CONFIG_DRM_ROCKCHIP", result.stderr)

    def test_final_gate_accepts_audited_kconfig_renames(self):
        sys.path.insert(0, str(PROJECT_DIR / "tools"))
        try:
            import kernel_artifacts
        finally:
            sys.path.pop(0)
        reduced = "\n".join(
            line
            for line in CONFIG.read_text(encoding="utf-8").splitlines()
            if not line.startswith("CONFIG_INET=")
        ) + "\n"
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = pathlib.Path(temporary_directory) / ".config"
            path.write_text(reduced, encoding="utf-8")

            original = kernel_artifacts.CONFIG_ALIASES
            kernel_artifacts.CONFIG_ALIASES = {}
            try:
                # Without an audited alias the missing symbol must fail.
                try:
                    kernel_artifacts.check_config(path)
                except ValueError as exc:
                    self.assertIn("CONFIG_INET", str(exc))
                else:
                    raise AssertionError("expected gate failure without alias")
                # With an audited rename the successor is accepted and reported.
                kernel_artifacts.CONFIG_ALIASES = {"CONFIG_INET": ("CONFIG_IPV6",)}
                report = kernel_artifacts.check_config(path)
                self.assertEqual(
                    report["accepted_renames"], {"CONFIG_INET": "CONFIG_IPV6"}
                )
            finally:
                kernel_artifacts.CONFIG_ALIASES = original

    def run_check_mode(self, config: pathlib.Path, mode: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(CLI),
                "check-config",
                "--config",
                str(config),
                "--mode",
                mode,
            ],
            cwd=PROJECT_DIR,
            text=True,
            capture_output=True,
            check=False,
        )


    def test_gate_rejects_missing_zram_lz4_backend_required_by_userspace(self):
        broken = CONFIG.read_text(encoding="utf-8").replace(
            "CONFIG_ZRAM_BACKEND_LZ4=y",
            "# CONFIG_ZRAM_BACKEND_LZ4 is not set",
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = pathlib.Path(temporary_directory) / ".config"
            path.write_text(broken, encoding="utf-8")
            result = self.run_check(path)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CONFIG_ZRAM_BACKEND_LZ4", result.stderr)

    def test_gate_rejects_a_missing_builtin_mmc_driver(self):
        broken = CONFIG.read_text(encoding="utf-8").replace(
            "CONFIG_MMC_DW_ROCKCHIP=y",
            "# CONFIG_MMC_DW_ROCKCHIP is not set",
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = pathlib.Path(temporary_directory) / ".config"
            path.write_text(broken, encoding="utf-8")
            result = self.run_check(path)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CONFIG_MMC_DW_ROCKCHIP", result.stderr)

    def test_gate_rejects_a_disabled_required_module(self):
        broken = CONFIG.read_text(encoding="utf-8").replace(
            "CONFIG_DRM_LIMA=m",
            "# CONFIG_DRM_LIMA is not set",
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = pathlib.Path(temporary_directory) / ".config"
            path.write_text(broken, encoding="utf-8")
            result = self.run_check(path)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CONFIG_DRM_LIMA", result.stderr)

    def test_gate_rejects_the_wrong_release_suffix(self):
        broken = CONFIG.read_text(encoding="utf-8").replace(
            'CONFIG_LOCALVERSION="-eaidk310-wifi1"',
            'CONFIG_LOCALVERSION="-wrong"',
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = pathlib.Path(temporary_directory) / ".config"
            path.write_text(broken, encoding="utf-8")
            result = self.run_check(path)

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CONFIG_LOCALVERSION", result.stderr)

    def test_config_diff_report_classifies_every_input_line_once(self):
        fixture = "\n".join(
            (
                "-OLD_DRIVER y",
                " BASE_SMALL 0 -> n",
                ' LOCALVERSION \"\" -> \"-eaidk310\"',
                "+NEW_DEFAULT y",
            )
        ) + "\n"
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            input_path = root / "diff.txt"
            output_path = root / "report.md"
            input_path.write_text(fixture, encoding="utf-8")
            result = subprocess.run(
                [
                    sys.executable,
                    str(CLI),
                    "render-config-diff",
                    "--input",
                    str(input_path),
                    "--output",
                    str(output_path),
                ],
                cwd=PROJECT_DIR,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            counts = json.loads(result.stdout)["counts"]
            self.assertEqual(
                counts,
                {
                    "default_changed": 1,
                    "dependency_changed": 1,
                    "intentional_eaidk_setting": 1,
                    "removed_upstream": 1,
                },
            )
            report = output_path.read_text(encoding="utf-8")
            for line in fixture.splitlines():
                self.assertEqual(report.count(line), 1)
            self.assertIn('CONFIG_LOCALVERSION="-eaidk310-wifi1"', report)
            self.assertIn("# CONFIG_LOCALVERSION_AUTO is not set", report)


if __name__ == "__main__":
    unittest.main()
