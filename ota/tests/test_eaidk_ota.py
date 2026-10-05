"""eaidk-ota test suite. WSL-hosted (needs python3 + tar + zstd inside WSL).

Runs on Windows and drives the tool inside the Ubuntu-24.04 WSL distro,
matching the board's Linux execution environment.
"""
import os
import pathlib
import subprocess
import unittest

HERE = pathlib.Path(__file__).resolve().parent
TOOL = HERE.parent / "eaidk-ota"
FIXTURE_MAKER = HERE / "make_fixture.py"
WSL_BASE = "/tmp/eaidk-ota-tests"
DEFAULT_RELEASE = "6.18.54-eaidk310-zramfix1"


@unittest.skipUnless(os.name == "nt", "Windows WSL integration tests")
class OtaTestBase(unittest.TestCase):
    wsl_tool = TOOL.as_posix().replace("D:/", "/mnt/d/")
    wsl_maker = FIXTURE_MAKER.as_posix().replace("D:/", "/mnt/d/")

    @classmethod
    def setUpClass(cls):
        ok = subprocess.run(
            ["wsl", "-d", "Ubuntu-24.04", "-u", "Fog", "--", "bash", "-c",
             "command -v zstd >/dev/null && command -v python3 >/dev/null"],
            capture_output=True, text=True)
        if ok.returncode != 0:
            raise unittest.SkipTest("WSL zstd/python3 unavailable")

    def wsl(self, command: str, timeout: int = 300) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["wsl", "-d", "Ubuntu-24.04", "-u", "Fog", "--", "bash", "-c", command],
            text=True, capture_output=True, timeout=timeout)

    def make_fixture(self, work: str, release: str = DEFAULT_RELEASE,
                     variant: str = "valid") -> str:
        r = self.wsl(f"mkdir -p {work} && python3 {self.wsl_maker} "
                     f"--out {work} --release {release} --variant {variant}")
        self.assertEqual(r.returncode, 0, r.stderr)
        return f"{work}/eaidk310-linux-{release}.tar.zst"

    def run_tool(self, args: str, json_mode: bool = False):
        flag = "--json " if json_mode else ""
        return self.wsl(f"python3 {self.wsl_tool} {flag}"
                        f"--state-dir {WSL_BASE}/state "
                        f"--boot-dir {WSL_BASE}/boot {args}")


class VerifyTests(OtaTestBase):
    def test_valid_bundle_passes(self):
        self.wsl(f"rm -rf {WSL_BASE}")
        archive = self.make_fixture(f"{WSL_BASE}/fx-valid")
        r = self.run_tool(f"verify {archive}")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("VERIFY=PASS", r.stdout)
        self.assertIn("manifest_exhaustive", r.stdout)

    def test_wrong_sha_fails(self):
        archive = self.make_fixture(f"{WSL_BASE}/fx-wrongsha", variant="wrong-sha")
        r = self.run_tool(f"verify {archive}")
        self.assertEqual(r.returncode, 2)
        self.assertIn("payload_sha256", r.stdout)

    def test_corrupt_size_fails(self):
        archive = self.make_fixture(f"{WSL_BASE}/fx-size", variant="corrupt-size")
        r = self.run_tool(f"verify {archive}")
        self.assertEqual(r.returncode, 2)
        self.assertIn("payload_sizes", r.stdout)

    def test_missing_dtb_fails(self):
        archive = self.make_fixture(f"{WSL_BASE}/fx-nodtb", variant="missing-dtb")
        r = self.run_tool(f"verify {archive}")
        self.assertEqual(r.returncode, 2)
        self.assertTrue(("dtb_present" in r.stdout) or ("manifest_exhaustive" in r.stdout))

    def test_wrong_board_dtb_fails(self):
        archive = self.make_fixture(f"{WSL_BASE}/fx-wrongdtb", variant="wrong-dtb")
        r = self.run_tool(f"verify {archive}")
        self.assertEqual(r.returncode, 2)
        self.assertIn("dtb_board_identity", r.stdout)

    def test_wrong_kernel_release_fails(self):
        archive = self.make_fixture(f"{WSL_BASE}/fx-rel", variant="wrong-release")
        r = self.run_tool(f"verify {archive}")
        self.assertEqual(r.returncode, 2)
        self.assertIn("build_release_matches", r.stdout)

    def test_old_modules_pollution_fails(self):
        archive = self.make_fixture(f"{WSL_BASE}/fx-pollute", variant="polluted")
        r = self.run_tool(f"verify {archive}")
        self.assertEqual(r.returncode, 2)
        self.assertIn("modules_single_release", r.stdout)

    def test_path_traversal_fails(self):
        archive = self.make_fixture(f"{WSL_BASE}/fx-traverse", variant="traversal")
        r = self.run_tool(f"verify {archive}")
        self.assertEqual(r.returncode, 2)
        self.assertIn("no_path_traversal", r.stdout)

    def test_release_mismatch_against_request_fails(self):
        archive = self.make_fixture(f"{WSL_BASE}/fx-req")
        r = self.run_tool(f"verify {archive} --release 9.9.9-eaidk310-zramfix1")
        self.assertEqual(r.returncode, 2)
        self.assertIn("release_matches_request", r.stdout)


class StageStateTests(OtaTestBase):
    def test_stage_idempotent_and_conflict(self):
        self.wsl(f"rm -rf {WSL_BASE}")
        archive = self.make_fixture(f"{WSL_BASE}/fx-stage")
        first = self.run_tool(f"stage {archive}")
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertIn("STAGE=PASS", first.stdout)
        second = self.run_tool(f"stage {archive}")
        self.assertEqual(second.returncode, 0)
        self.assertIn("idempotent", second.stdout)
        status = self.run_tool("status")
        self.assertEqual(status.returncode, 0)
        self.assertIn("STAGED", status.stdout)

        # same release, different valid bytes -> refuse overwrite
        archive2 = self.make_fixture(f"{WSL_BASE}/fx-stage2", variant="valid-modified")
        conflict = self.run_tool(f"stage {archive2}")
        self.assertEqual(conflict.returncode, 5)
        self.assertIn("different", conflict.stdout + conflict.stderr)

    def test_corrupted_state_resets_to_idle(self):
        self.wsl(f"rm -rf {WSL_BASE}")
        self.wsl(f"mkdir -p {WSL_BASE}/state")
        self.wsl(f"printf 'not-json{{' > {WSL_BASE}/state/state.json")
        status = self.run_tool("status")
        self.assertEqual(status.returncode, 0)
        self.assertIn("IDLE", status.stdout)


class SafetyGateTests(OtaTestBase):
    def test_try_and_arm_refuse_blocked_backend(self):
        self.wsl(f"rm -rf {WSL_BASE}")
        r = self.run_tool("try")
        self.assertEqual(r.returncode, 4)
        self.assertIn("RawBootstateBackend", r.stdout + r.stderr)
        r = self.run_tool("arm")
        self.assertEqual(r.returncode, 4)

    def test_commit_without_healthy_conflicts(self):
        r = self.run_tool("commit")
        self.assertEqual(r.returncode, 5)
        self.assertIn("HEALTHY", r.stdout + r.stderr)

    def test_install_candidate_refuses_without_local_authorization(self):
        self.wsl(f"rm -rf {WSL_BASE}/run /tmp/eaidk-auth-*")
        r = self.wsl(f"mkdir -p {WSL_BASE}/config && "
                     f"python3 {self.wsl_tool} --state-dir {WSL_BASE}/state "
                     f"--run-dir /tmp/eaidk-auth-ssh --config-dir {WSL_BASE}/config "
                     f"install-candidate")
        self.assertEqual(r.returncode, 3)
        self.assertIn("Boot mutation disabled", r.stdout + r.stderr)

    def test_authorize_local_denied_for_remote_session(self):
        # This test IS a remote-ish session: stdin is not /dev/ttyS2 or /dev/tty1.
        run_dir = "/tmp/eaidk-auth-remote-test"
        self.wsl(f"rm -rf {run_dir}")
        r = self.wsl(f"python3 {self.wsl_tool} --run-dir {run_dir} authorize-local")
        self.assertEqual(r.returncode, 3)
        combined = r.stdout + r.stderr
        self.assertIn("authorization refused for remote session", combined)
        # and no token was created
        check = self.wsl(f"test -e {run_dir}/local-authorization && echo EXISTS || echo ABSENT")
        self.assertIn("ABSENT", check.stdout)

    def test_local_authorization_token_flow_with_forced_tty(self):
        # Simulate a physical console session: run authorize-local with stdin
        # bound to a fake ttyS2 via a symlink, using the real /proc fd check.
        setup = self.wsl(
            "rm -rf /tmp/eaidk-auth-local-test /tmp/faketty && "
            "mkdir -p /tmp/eaidk-auth-local-test /tmp/faketty && "
            "ln -s /dev/ttyS2 /tmp/faketty/tty 2>/dev/null; echo done")
        self.assertIn("done", setup.stdout)
        # python snippet impersonating a console session by patching fd 0's
        # readlink target is not possible; instead call the internal check
        # through a wrapper that stubs /proc self fd0 -> accept the design
        # limitation and verify the gate rejects pts and accepts tty strings
        # at the unit level.
        unit = self.wsl(
            "python3 - <<'PY'\n"
            "import sys, pathlib\n"
            "from importlib.machinery import SourceFileLoader\n"
            "from importlib.util import module_from_spec, spec_from_loader\n"
            "path = '/mnt/d/Project/EAIDK310/github/EAIDK-310/ota/eaidk-ota'\n"
            "loader = SourceFileLoader('eaidk_ota_mod', path)\n"
            "spec = spec_from_loader('eaidk_ota_mod', loader)\n"
            "mod = module_from_spec(spec)\n"
            "loader.exec_module(mod)\n"
            "auth = mod.LocalAuthorization(\n"
            "    pathlib.Path('/tmp/eaidk-auth-unit'),\n"
            "    pathlib.Path('/tmp/eaidk-auth-unit-cfg'))\n"
            "orig = mod.LocalAuthorization.session_tty\n"
            "mod.LocalAuthorization.session_tty = staticmethod(lambda: '/dev/ttyS2')\n"
            "rc = auth.create()\n"
            "print('TTY2_CREATE_RC', rc)\n"
            "mod.LocalAuthorization.session_tty = staticmethod(lambda: '/dev/pts/3')\n"
            "rc2 = auth.create()\n"
            "print('PTS_CREATE_RC', rc2)\n"
            "PY")
        self.assertIn("TTY2_CREATE_RC 0", unit.stdout)
        self.assertIn("PTS_CREATE_RC 3", unit.stdout)
        self.assertIn("authorization refused for remote session", unit.stdout)
        self.wsl("rm -rf /tmp/eaidk-auth-unit /tmp/eaidk-auth-unit-cfg")


class HealthTests(OtaTestBase):
    def test_health_rejects_wrong_expected_release(self):
        r = self.run_tool("health --expected-release 0.0.1-not-running")
        self.assertEqual(r.returncode, 6)
        self.assertIn("kernel_is_candidate", r.stdout)


class PlanInstallTests(OtaTestBase):
    def test_plan_requires_staged_release(self):
        self.wsl(f"rm -rf {WSL_BASE}")
        r = self.run_tool("plan-install 9.9.9-eaidk310-zramfix1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("not staged", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
