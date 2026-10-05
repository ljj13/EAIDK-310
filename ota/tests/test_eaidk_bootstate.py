"""eaidk-bootstate test suite. WSL-hosted (fcntl + Linux semantics).

Run driver: Windows python3 executes each test through WSL, matching the
board's Linux execution environment (same pattern as test_eaidk_ota.py).

Golden bytes are pinned to drivers/bootcount/bootcount_ext.c of pinned
U-Boot v2024.07-rc1@38ea74d6 (P3.6 source forensics):
    bootcount_ext_t { u8 magic=0xBD; u8 version=1; u8 bootcount; u8 upgrade_available; }
"""
import os
import pathlib
import subprocess
import unittest

HERE = pathlib.Path(__file__).resolve().parent
TOOL = HERE.parent / "eaidk-bootstate"
WSL_BASE = "/tmp/eaidk-bootstate-tests"

GOLDEN_ARM = b"\xbd\x01\x00\x01"
GOLDEN_CLEAR = b"\xbd\x01\x00\x00"


@unittest.skipUnless(os.name == "nt", "Windows WSL integration tests")
class BootstateTestBase(unittest.TestCase):
    wsl_tool = TOOL.as_posix().replace("D:/", "/mnt/d/")

    @classmethod
    def setUpClass(cls):
        ok = subprocess.run(
            ["wsl", "-d", "Ubuntu-24.04", "-u", "Fog", "--", "bash", "-c",
             "command -v python3 >/dev/null"],
            capture_output=True, text=True)
        if ok.returncode != 0:
            raise unittest.SkipTest("WSL python3 unavailable")

    def wsl(self, command: str, timeout: int = 60) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["wsl", "-d", "Ubuntu-24.04", "-u", "Fog", "--", "bash", "-c", command],
            text=True, capture_output=True, timeout=timeout)

    def setUp(self):
        self.work = f"{WSL_BASE}/{self.id().split('.')[-1]}"
        r = self.wsl(f"rm -rf {self.work} && mkdir -p {self.work}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.statefile = f"{self.work}/bootcount.bin"

    def run_tool(self, args: str, json_mode: bool = False):
        flag = "--json " if json_mode else ""
        return self.wsl(f"python3 {self.wsl_tool} {flag}{args}")

    def read_file(self, path: str) -> bytes:
        r = self.wsl(f"cat {path} | od -An -tx1 | tr -d ' \\n'")
        self.assertEqual(r.returncode, 0, r.stderr)
        return bytes.fromhex(r.stdout.strip())

    def write_file(self, path: str, data: bytes):
        hexs = data.hex()
        r = self.wsl(f"printf '{hexs}' | xxd -r -p > {path}")
        self.assertEqual(r.returncode, 0, r.stderr)

    def listdir(self) -> list:
        r = self.wsl(f"ls -A {self.work} | sort | tr '\\n' ' '")
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.split()


class InspectTests(BootstateTestBase):
    def test_inspect_missing_file_fail_closed(self):
        r = self.run_tool(f"inspect {self.statefile}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("state:            missing", r.stdout)
        self.assertIn("fail closed -> stable boot", r.stdout)

    def test_inspect_missing_strict_exits_2(self):
        r = self.run_tool(f"inspect {self.statefile} --strict")
        self.assertEqual(r.returncode, 2)

    def test_inspect_corrupt_magic_fails_closed(self):
        self.write_file(self.statefile, b"\xaa\x01\x05\x01")
        r = self.run_tool(f"inspect {self.statefile}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("state:            invalid", r.stdout)
        self.assertIn("magic 0xaa", r.stdout)

    def test_inspect_wrong_version_fails_closed(self):
        self.write_file(self.statefile, b"\xbd\x02\x05\x01")
        r = self.run_tool(f"inspect {self.statefile}")
        self.assertIn("state:            invalid", r.stdout)
        self.assertIn("version 2", r.stdout)

    def test_inspect_short_file_fails_closed(self):
        self.write_file(self.statefile, b"\xbd\x01")
        r = self.run_tool(f"inspect {self.statefile}")
        self.assertIn("state:            invalid", r.stdout)
        self.assertIn("length 2 != 4", r.stdout)

    def test_inspect_oversized_file_fails_closed(self):
        self.write_file(self.statefile, b"\xbd\x01\x00\x01\xff")
        r = self.run_tool(f"inspect {self.statefile}")
        self.assertIn("state:            invalid", r.stdout)
        self.assertIn("length 5 != 4", r.stdout)

    def test_inspect_valid_armed(self):
        self.write_file(self.statefile, GOLDEN_ARM)
        r = self.run_tool(f"inspect {self.statefile} --json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn('"state": "valid"', r.stdout)
        self.assertIn('"armed": true', r.stdout)

    def test_inspect_zeroed_file_fails_closed(self):
        self.write_file(self.statefile, b"\x00\x00\x00\x00")
        r = self.run_tool(f"inspect {self.statefile}")
        self.assertIn("state:            invalid", r.stdout)


class WriteTests(BootstateTestBase):
    def test_arm_writes_exact_golden_bytes(self):
        r = self.run_tool(f"arm {self.statefile}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.read_file(self.statefile), GOLDEN_ARM)
        self.assertIn("upgrade_available:1", r.stdout)

    def test_clear_writes_exact_golden_bytes(self):
        r = self.run_tool(f"clear {self.statefile}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.read_file(self.statefile), GOLDEN_CLEAR)
        self.assertIn("upgrade_available:0", r.stdout)

    def test_arm_creates_parent_directory(self):
        nested = f"{self.work}/eaidk-ota/bootcount.bin"
        r = self.run_tool(f"arm {nested}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.read_file(nested), GOLDEN_ARM)

    def test_atomic_write_leaves_no_tmp_files(self):
        self.run_tool(f"arm {self.statefile}")
        self.run_tool(f"clear {self.statefile}")
        leftovers = [f for f in self.listdir() if "tmp" in f]
        self.assertEqual(leftovers, [])
        self.assertEqual(self.read_file(self.statefile), GOLDEN_CLEAR)

    def test_lockfile_created_alongside_state(self):
        self.run_tool(f"arm {self.statefile}")
        self.assertIn("bootcount.bin.lock", self.listdir())


class SimulateTests(BootstateTestBase):
    def run_sim(self, filedata, boots, bootlimit=1):
        if filedata is not None:
            self.write_file(self.statefile, filedata)
        r = self.run_tool(
            f"simulate {self.statefile} --boots {boots} --bootlimit {bootlimit} --json")
        self.assertEqual(r.returncode, 0, r.stderr)
        import json
        return json.loads(r.stdout)

    def test_committed_boots_stable_without_writing(self):
        out = self.run_sim(GOLDEN_CLEAR, boots=2)
        for step in out["boots"]:
            self.assertEqual(step["route"], "bootcmd(stable)")
            self.assertEqual(step["file_after"],
                             {"bootcount": 0, "upgrade_available": 0})

    def test_armed_first_boot_is_candidate_with_increment(self):
        out = self.run_sim(GOLDEN_ARM, boots=1)
        step = out["boots"][0]
        self.assertEqual(step["route"], "bootcmd(candidate)")
        self.assertEqual(step["env"], {"bootcount": 1, "upgrade_available": 1})
        self.assertEqual(step["file_after"],
                         {"bootcount": 1, "upgrade_available": 1})

    def test_armed_trial_chain_falls_back_after_one_attempt(self):
        # (0,1) -> candidate attempt, file becomes (1,1); next boot exceeds
        # bootlimit=1 and U-Boot routes to altbootcmd (stable).
        out = self.run_sim(GOLDEN_ARM, boots=2, bootlimit=1)
        self.assertEqual(out["boots"][0]["route"], "bootcmd(candidate)")
        self.assertEqual(out["boots"][1]["route"], "altbootcmd(stable)")
        self.assertEqual(out["boots"][1]["file_after"],
                         {"bootcount": 2, "upgrade_available": 1})

    def test_armed_with_bootlimit_2_gets_two_attempts(self):
        out = self.run_sim(GOLDEN_ARM, boots=3, bootlimit=2)
        self.assertEqual(out["boots"][0]["route"], "bootcmd(candidate)")
        self.assertEqual(out["boots"][1]["route"], "bootcmd(candidate)")
        self.assertEqual(out["boots"][2]["route"], "altbootcmd(stable)")

    def test_missing_file_boots_stable_fail_closed(self):
        out = self.run_sim(None, boots=1)
        self.assertEqual(out["initial"]["state"], "missing")
        self.assertEqual(out["boots"][0]["route"], "bootcmd(stable)")

    def test_corrupt_file_boots_stable_fail_closed(self):
        out = self.run_sim(b"\xde\xad\xbe\xef", boots=1)
        self.assertEqual(out["initial"]["state"], "invalid")
        self.assertEqual(out["boots"][0]["route"], "bootcmd(stable)")

    def test_u8_wrap_resets_to_stable(self):
        # upstream quirk: (255 + 1) & 0xFF == 0 -- documented, not relied on
        out = self.run_sim(b"\xbd\x01\xff\x01", boots=1)
        step = out["boots"][0]
        self.assertEqual(step["file_after"],
                         {"bootcount": 0, "upgrade_available": 1})
        self.assertEqual(step["route"], "bootcmd(stable)")


if __name__ == "__main__":
    unittest.main()
