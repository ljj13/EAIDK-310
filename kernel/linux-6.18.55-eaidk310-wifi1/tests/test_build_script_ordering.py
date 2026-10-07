import os
import pathlib
import subprocess
import unittest

PROJECT_DIR = pathlib.Path(__file__).resolve().parents[1]
BUILD_SCRIPT = PROJECT_DIR / "scripts" / "build-kernel.sh"
WSL_SOURCE = "/home/Fog/eaidk310-kernel/src/linux-6.18.55"


class BuildScriptContractTests(unittest.TestCase):
    """Static contract: the gate chain must be structurally immune to the
    P1 defect (conditional-wrapped run_build suppressing `set -e`, and
    olddefconfig consuming a missing/non-EAIDK config)."""

    def setUp(self):
        self.text = BUILD_SCRIPT.read_text(encoding="utf-8")

    def test_seed_config_is_installed_before_olddefconfig(self):
        seed = self.text.index("ensure_seed_config")
        olddef = self.text.index('make -C "$SOURCE_DIR" O="$BUILD_DIR" olddefconfig')
        self.assertLess(seed, olddef)

    def test_run_build_is_not_conditionally_wrapped(self):
        self.assertNotIn("if ! run_build", self.text)
        self.assertIn("PIPESTATUS", self.text)

    def test_critical_steps_fail_explicitly(self):
        for marker in (
            'fail "olddefconfig failed"',
            'fail "syncconfig failed"',
            'fail "final config gate failed"',
            'fail "seed config gate failed',
            'fail "kernel compile failed"',
            'fail "dtbs validation failed"',
            'fail "modules_install failed"',
        ):
            self.assertIn(marker, self.text)

    def test_config_only_mode_exists(self):
        self.assertIn("--config-only", self.text)
        self.assertIn("CONFIG_ONLY_GATE=PASS", self.text)

    def test_source_dts_gate_exists(self):
        self.assertIn("board DTS missing from source tree", self.text)


@unittest.skipUnless(os.name == "nt", "Windows WSL integration tests")
class BuildScriptOrderingTests(unittest.TestCase):
    """Functional: clean/config order can no longer produce a non-EAIDK
    config, and the seed gate rejects impostors at the earliest step."""

    @classmethod
    def setUpClass(cls):
        check = subprocess.run(
            [
                "wsl",
                "-d",
                "Ubuntu-24.04",
                "-u",
                "Fog",
                "--",
                "bash",
                "-c",
                f"test -f {WSL_SOURCE}/Makefile && echo PRESENT",
            ],
            text=True,
            capture_output=True,
        )
        if "PRESENT" not in check.stdout:
            raise unittest.SkipTest("WSL kernel source tree unavailable")

    @staticmethod
    def wsl_path(path: pathlib.Path) -> str:
        resolved = path.resolve()
        return f"/mnt/{resolved.drive[0].lower()}{resolved.as_posix()[2:]}"

    def run_config_only(self, build_wsl: str):
        script = self.wsl_path(BUILD_SCRIPT)
        log = build_wsl + "-run.log"
        meta = build_wsl + "-meta.json"
        command = (
            f"bash {script} --config-only"
            f" --source-dir {WSL_SOURCE}"
            f" --build-dir {build_wsl}"
            f" --log {log}"
            f" --metadata {meta}"
        )
        return subprocess.run(
            [
                "wsl",
                "-d",
                "Ubuntu-24.04",
                "-u",
                "Fog",
                "--",
                "bash",
                "-c",
                command,
            ],
            text=True,
            capture_output=True,
        )

    def test_config_only_restores_seed_and_passes_repeatably(self):
        build_wsl = "/home/Fog/eaidk310-kernel/build-zramfix1-6.18.55-selftest"

        def wsl_bash(command: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                ["wsl", "-d", "Ubuntu-24.04", "-u", "Fog", "--", "bash", "-c", command],
                text=True,
                capture_output=True,
            )

        first = self.run_config_only(build_wsl)
        self.assertEqual(first.returncode, 0, first.stderr + first.stdout)
        self.assertIn("CONFIG_ONLY_GATE=PASS", first.stdout)
        self.assertIn("kernel_release=6.18.55-eaidk310-wifi1", first.stdout)
        check = wsl_bash(f"grep -Fq 'CONFIG_LOCALVERSION=\"-eaidk310-wifi1\"' {build_wsl}/.config && echo OK")
        self.assertIn("OK", check.stdout)

        # Idempotent rerun (config present) must still pass.
        second = self.run_config_only(build_wsl)
        self.assertEqual(second.returncode, 0, second.stderr + second.stdout)

        # Corrupt the config: seed gate must reject the impostor at once.
        # The gate message is routed into the build log by `run_build | tee`.
        wsl_bash(f"printf 'CONFIG_LOCALVERSION=\"\"\\nCONFIG_LOCALVERSION_AUTO=y\\n' > {build_wsl}/.config")
        third = self.run_config_only(build_wsl)
        self.assertNotEqual(third.returncode, 0)
        log_wsl = build_wsl + "-run.log"
        gate_hit = wsl_bash(f"grep -F 'seed config gate failed' {log_wsl}")
        self.assertEqual(gate_hit.returncode, 0, gate_hit.stdout + gate_hit.stderr)

        # And the pipeline self-heals: remove config, rerun, seed restored.
        wsl_bash(f"rm -f {build_wsl}/.config")
        fourth = self.run_config_only(build_wsl)
        self.assertEqual(fourth.returncode, 0, fourth.stderr + fourth.stdout)

    @classmethod
    def tearDownClass(cls):
        subprocess.run(
            [
                "wsl",
                "-d",
                "Ubuntu-24.04",
                "-u",
                "Fog",
                "--",
                "bash",
                "-c",
                "rm -rf /home/Fog/eaidk310-kernel/build-zramfix1-6.18.55-selftest*",
            ],
            text=True,
            capture_output=True,
        )


if __name__ == "__main__":
    unittest.main()
