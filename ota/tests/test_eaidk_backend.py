"""eaidk-ota BootcountFsBackend fixture tests (PC/WSL only).

Exercises the P3.6 backend abstraction end to end against a fixture boot
directory: marker detection, arm/clear via eaidk-bootstate, state machine
transitions and refusal ordering.  The board still runs NoBackend; these
tests prove the code path that will activate AFTER the fail-safe U-Boot is
flashed and on-site validated.
"""
import json
import os
import pathlib
import subprocess
import unittest
from datetime import datetime, timedelta, timezone

HERE = pathlib.Path(__file__).resolve().parent
TOOL = HERE.parent / "eaidk-ota"
BOOTSTATE_SRC = HERE.parent / "eaidk-bootstate"
WSL_BASE = "/tmp/eaidk-backend-tests"

MARKER = "BootcountFsBackend 1"


@unittest.skipUnless(os.name == "nt", "Windows WSL integration tests")
class BackendFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ok = subprocess.run(
            ["wsl", "-d", "Ubuntu-24.04", "-u", "Fog", "--", "bash", "-c",
             "command -v python3 >/dev/null"],
            capture_output=True, text=True)
        if ok.returncode != 0:
            raise unittest.SkipTest("WSL python3 unavailable")
        cls.wsl_tool = f"{WSL_BASE}/bin/eaidk-bootstate"
        prep = subprocess.run(
            ["wsl", "-d", "Ubuntu-24.04", "-u", "Fog", "--", "bash", "-c",
             f"mkdir -p {WSL_BASE}/bin && "
             f"cp {BOOTSTATE_SRC.as_posix().replace('D:/', '/mnt/d/')} {cls.wsl_tool} && "
             f"chmod +x {cls.wsl_tool} && {cls.wsl_tool} --help >/dev/null && echo TOOL-OK"],
            capture_output=True, text=True)
        if "TOOL-OK" not in prep.stdout:
            raise unittest.SkipTest(f"bootstate tool unusable: {prep.stderr}")

    def wsl(self, command: str, timeout: int = 120) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["wsl", "-d", "Ubuntu-24.04", "-u", "Fog", "--", "bash", "-c", command],
            text=True, capture_output=True, timeout=timeout)

    def setUp(self):
        self.work = f"{WSL_BASE}/{self.id().split('.')[-1]}"
        r = self.wsl(
            f"rm -rf {self.work} && mkdir -p {self.work}/boot/eaidk-ota "
            f"{self.work}/state {self.work}/config {self.work}/run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.boot = f"{self.work}/boot"
        self.bootstate = f"{self.boot}/eaidk-ota/bootcount.bin"
        self.tool_args = (f"--state-dir {self.work}/state --boot-dir {self.boot} "
                          f"--config-dir {self.work}/config --run-dir {self.work}/run "
                          f"--modules-dir {self.work}/modules "
                          f"--bootstate-tool {self.wsl_tool}")

    def run_tool(self, args: str, backend: str = "bootcount-fs", json_mode: bool = False):
        flag = "--json " if json_mode else ""
        return self.wsl(
            f"python3 {TOOL.as_posix().replace('D:/', '/mnt/d/')} {flag}"
            f"--backend {backend} {self.tool_args} {args}")
    def add_marker(self):
        r = self.wsl(f"printf '%s\\n' '{MARKER}' > {self.boot}/eaidk-ota/BACKEND")
        self.assertEqual(r.returncode, 0, r.stderr)

    def craft_state(self, state: str):
        # P8.1: the pre-arm contract requires a complete, consistent candidate
        # deployment (conf + versioned assets + modules + identity), so the
        # fixture builds that world for every crafted state.
        script = (
            "import hashlib, json, pathlib, shutil\n"
            f"work = pathlib.Path({self.work!r})\n"
            "release = '6.18.54-eaidk310-zramfix1'\n"
            "bundle = '989093725bdec974ebda8a031dcb083bcc7802ba"
            "51ae7c24a143557834505f06'\n"
            "boot = work / 'boot'\n"
            "staging = work / 'state' / 'staging' / release\n"
            "mods = work / 'modules' / release\n"
            "for d in (boot / 'extlinux', boot / 'dtb/rockchip',\n"
            "          staging / 'boot' / 'dtb/rockchip',\n"
            "          staging / 'root/lib/modules' / release,\n"
            "          mods / 'kernel', boot / 'eaidk-ota'):\n"
            "    d.mkdir(parents=True, exist_ok=True)\n"
            "def blob(p):\n"
            "    p.write_bytes(release.encode() + bytes(4096))\n"
            "img = boot / ('Image-' + release)\n"
            "uin = boot / ('uInitrd-' + release)\n"
            "dtb = boot / 'dtb/rockchip' / ('rk3328-eaidk-310-' + release + '.dtb')\n"
            "for f in (img, uin, dtb):\n"
            "    blob(f)\n"
            "shutil.copy(img, staging / 'boot' / img.name)\n"
            "shutil.copy(uin, staging / 'boot' / uin.name)\n"
            "shutil.copy(dtb, staging / 'boot/dtb/rockchip' / dtb.name)\n"
            "(mods / 'modules.dep').write_text('kernel/board_test.ko: \\n')\n"
            "(mods / '.eaidk-ota-installed').write_text("
            "'release=' + release + '\\nbundle=' + bundle + '\\n')\n"
            "(mods / 'kernel' / 'board_test.ko').write_bytes(b'fake')\n"
            "shutil.copy(mods / 'modules.dep', "
            "staging / 'root/lib/modules' / release / 'modules.dep')\n"
            "marker = 'eaidk_ota_trial=' + release + '@' + bundle[:12]\n"
            "append = 'root=UUID=781e1dc3-166b-46e9-8578-4b9c003d7305 ' + marker\n"
            "label = 'rockchip-kernel-' + release + '-test'\n"
            "(boot / 'extlinux' / 'extlinux.conf').write_text("
            "'default rockchip-kernel-' + release + '\\n' + "
            "'label rockchip-kernel-' + release + '\\n' + "
            "'    APPEND root=UUID=781e1dc3-166b-46e9-8578-4b9c003d7305\\n')\n"
            "(boot / 'extlinux' / 'extlinux-candidate.conf').write_text("
            "'default ' + label + '\\n' + 'label ' + label + '\\n' + "
            "'    LINUX  /Image-' + release + '\\n' + "
            "'    FDT    /dtb/rockchip/rk3328-eaidk-310-' + release + '.dtb\\n' + "
            "'    INITRD /uInitrd-' + release + '\\n' + "
            "'    APPEND ' + append + '\\n')\n"
            "identity = {'release': release, 'bundle_sha256': bundle,\n"
            "            'backend': 'RAW_REDUNDANT', 'marker': marker,\n"
            "            'conf': str(boot / 'extlinux/extlinux-candidate.conf'),\n"
            "            'conf_sha256': hashlib.sha256((boot / "
            "'extlinux/extlinux-candidate.conf').read_bytes()).hexdigest(),\n"
            "            'stable_default': 'rockchip-kernel-' + release,\n"
            "            'parsed': {'label': label, 'linux': '/Image-' + release,\n"
            "                       'fdt': '/dtb/rockchip/rk3328-eaidk-310-' + release"
            " + '.dtb',\n"
            "                       'initrd': '/uInitrd-' + release,\n"
            "                       'append': append, 'marker': marker}}\n"
            "payload = {'state': " + json.dumps(state) + ",\n"
            "           'updated': '2026-10-01T00:00:00+00:00',\n"
            "           'release': release, 'bundle_sha256': bundle,\n"
            "           'candidate_identity': identity}\n"
            "(work / 'state' / 'state.json').write_text("
            "json.dumps(payload, indent=2) + '\\n')\n"
            "print('CRAFT_OK')\n")
        r = self.wsl(f"python3 - <<'PYEOF'\n{script}\nPYEOF")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("CRAFT_OK", r.stdout, r.stdout[-400:])

    def grant_authorization(self):
        expiry = (datetime.now(timezone.utc) + timedelta(minutes=15)).isoformat()
        token = json.dumps({"issued": "2026-10-01T00:00:00+00:00",
                            "expires": expiry, "tty": "fixture", "ttl_seconds": 900})
        r = self.wsl(
            f"printf '%s\\n' '{token}' > {self.work}/run/local-authorization && "
            f"touch {self.work}/config/allow-boot-mutation")
        self.assertEqual(r.returncode, 0, r.stderr)

    def bootstate_hex(self) -> str:
        r = self.wsl(f"cat {self.bootstate} | od -An -tx1 | tr -d ' \\n'")
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def state_of(self) -> str:
        r = self.wsl(f"cat {self.work}/state/state.json")
        return json.loads(r.stdout)["state"]

    # ---------------------------------------------------------------- tests --

    def test_forced_backend_without_marker_refuses_with_marker_reason(self):
        r = self.run_tool("try")
        self.assertEqual(r.returncode, 4, r.stdout)
        self.assertIn("not installed", r.stdout + r.stderr)
        self.assertIn("BACKEND", r.stdout + r.stderr)

    def test_status_reports_backend_and_bootstate(self):
        self.add_marker()
        r = self.run_tool("status", json_mode=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        data = json.loads(r.stdout)
        self.assertEqual(data["active_backend"], "BootcountFsBackend")
        self.assertTrue(data["backend_available"])
        self.assertEqual(data["bootstate"]["state"], "missing")
        self.assertTrue(data["bootstate"]["fail_closed"])

    def test_status_without_marker_degrades_to_nobackend(self):
        r = self.run_tool("status", backend="auto", json_mode=True)
        data = json.loads(r.stdout)
        self.assertEqual(data["active_backend"], "NoBackend")
        self.assertFalse(data["backend_available"])

    def test_arm_refuses_without_authorization_even_with_backend(self):
        self.add_marker()
        self.craft_state("STAGED")
        r = self.run_tool("try")
        self.assertEqual(r.returncode, 3, r.stdout)

    def test_arm_full_flow_writes_armed_bootstate(self):
        self.add_marker()
        self.craft_state("STAGED")
        self.grant_authorization()
        r = self.run_tool("try")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.state_of(), "ARMED")
        self.assertEqual(self.bootstate_hex(), "bd010001")

    def test_arm_is_idempotent_when_already_armed(self):
        self.add_marker()
        self.craft_state("ARMED")
        self.grant_authorization()
        r = self.run_tool("try")
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("already armed", r.stdout)
        self.assertEqual(self.state_of(), "ARMED")

    def test_arm_requires_staged_candidate(self):
        self.add_marker()
        self.craft_state("IDLE")
        self.grant_authorization()
        r = self.run_tool("try")
        self.assertEqual(r.returncode, 5, r.stdout)
        self.assertIn("staged candidate", r.stdout + r.stderr)

    def test_commit_clears_bootstate_and_commits(self):
        self.add_marker()
        self.craft_state("HEALTHY")
        self.grant_authorization()
        r = self.run_tool("arm")  # create an armed record first (needs STAGED?)
        self.assertEqual(r.returncode, 5, r.stdout)  # not staged -> refused
        r = self.wsl(f"printf 'bd010001' | xxd -r -p > {self.bootstate}")
        self.assertEqual(r.returncode, 0)
        r = self.run_tool("commit")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.state_of(), "COMMITTED")
        self.assertEqual(self.bootstate_hex(), "bd010000")

    def test_rollback_clears_bootstate(self):
        self.add_marker()
        self.craft_state("ROLLBACK_PENDING")
        self.grant_authorization()
        r = self.wsl(f"printf 'bd010201' | xxd -r -p > {self.bootstate}")
        self.assertEqual(r.returncode, 0)
        r = self.run_tool("rollback")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.state_of(), "ROLLED_BACK")
        self.assertEqual(self.bootstate_hex(), "bd010000")

    def test_commit_still_refused_without_backend(self):
        self.craft_state("HEALTHY")
        self.grant_authorization()
        r = self.run_tool("commit", backend="none")
        self.assertEqual(r.returncode, 4, r.stdout)
        self.assertIn("RawBootstateBackend", r.stdout + r.stderr)
        self.assertIn("flashed and validated", r.stdout + r.stderr)


class MetadataCsumGateTests(unittest.TestCase):
    """Pure-function tests for the arm-time ext4 write-compatibility gate."""

    @classmethod
    def setUpClass(cls):
        ok = subprocess.run(
            ["wsl", "-d", "Ubuntu-24.04", "-u", "Fog", "--", "bash", "-c",
             "command -v python3 >/dev/null"],
            capture_output=True, text=True)
        if ok.returncode != 0:
            raise unittest.SkipTest("WSL python3 unavailable")

    def load_module(self):
        script = (
            "python3 - <<'PY'\n"
            "from importlib.machinery import SourceFileLoader\n"
            "from importlib.util import module_from_spec, spec_from_loader\n"
            f"path = '{TOOL.as_posix().replace('D:/', '/mnt/d/')}'\n"
            "loader = SourceFileLoader('eaidk_ota_gate', path)\n"
            "spec = spec_from_loader('eaidk_ota_gate', loader)\n"
            "mod = module_from_spec(spec)\n"
            "loader.exec_module(mod)\n"
            "b = mod.BootcountFsBackend(__import__('pathlib').Path('/tmp'))\n"
            "print('GATE', b.fs_has_metadata_csum(open('/tmp/gate-dumpe2fs.txt').read()))\n"
            "PY\n")
        return script

    def gate_result(self, features_line: str) -> str:
        setup = (f"printf '%s\\n' 'Filesystem features: {features_line}' "
                 f"> /tmp/gate-dumpe2fs.txt")
        r = subprocess.run(
            ["wsl", "-d", "Ubuntu-24.04", "-u", "Fog", "--", "bash", "-c",
             setup + " && " + self.load_module()],
            text=True, capture_output=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        for line in r.stdout.splitlines():
            if line.startswith("GATE "):
                return line.split()[1]
        self.fail(f"no GATE output: {r.stdout} {r.stderr}")

    def test_metadata_csum_detected(self):
        self.assertEqual(
            self.gate_result(
                "has_journal ext_attr resize_inode dir_index filetype "
                "extent 64bit flex_bg sparse_super large_file huge_file "
                "dir_nlink extra_isize metadata_csum"),
            "True")

    def test_without_metadata_csum_passes(self):
        self.assertEqual(
            self.gate_result(
                "has_journal ext_attr resize_inode dir_index filetype "
                "extent flex_bg sparse_super large_file huge_file "
                "dir_nlink extra_isize"),
            "False")

    def test_unparseable_output_fails_open_false(self):
        # no features line -> no proof -> gate does not fire (fixture mode)
        self.assertEqual(self.gate_result("some unrelated output"), "False")


if __name__ == "__main__":
    unittest.main()


class RawBackendFixtureTests(BackendFixtureTests):
    """RawBootstateBackend routing through the shared marker; full arm and
    commit flows run against a 16 MiB image file used as --raw-device."""
    MARKER = "RawBootstateBackend 1"

    def setUp(self):
        super().setUp()
        self.raw_dev = f"{self.work}/mmcblk2.fixture"
        r = self.wsl(f"truncate -s 16777216 {self.raw_dev}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.tool_args += f" --raw-device {self.raw_dev}"

    def run_tool(self, args: str, backend: str = "raw", json_mode: bool = True):
        flag = "--json " if json_mode else ""
        return self.wsl(
            f"python3 {TOOL.as_posix().replace('D:/', '/mnt/d/')} {flag}"
            f"{args} --backend {backend} {self.tool_args}")

    def add_marker(self):
        marker_line = "printf '%%s\\n' '%s' > %s" % (self.MARKER, self.boot + "/eaidk-ota/BACKEND")
        r = self.wsl(marker_line)
        self.assertEqual(r.returncode, 0, r.stderr)

    def record_hex(self, lba: int) -> str:
        cmd = ("dd if=" + self.raw_dev + " bs=512 skip=" + str(lba) +
               " count=1 status=none | od -An -tx1 -v | tr -d ' \\n'")
        r = self.wsl(cmd)
        return r.stdout.strip()
        return r.stdout.strip()

    def test_auto_routes_raw_marker(self):
        self.add_marker()
        r = self.run_tool("status", backend="auto")
        data = json.loads(r.stdout)
        self.assertEqual(data["active_backend"], "RawBootstateBackend")
        self.assertTrue(data["backend_available"])

    def test_raw_arm_refused_without_marker(self):
        r = self.run_tool("try")
        self.assertEqual(r.returncode, 4, r.stdout)
        self.assertIn("RawBootstateBackend is not installed", r.stdout + r.stderr)

    def test_raw_try_full_flow_arms_record_a(self):
        self.add_marker()
        self.craft_state("STAGED")
        self.grant_authorization()
        r = self.run_tool("try")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.state_of(), "ARMED")
        a = self.record_hex(0x6400)
        self.assertEqual(a[0:16], "4541333130425331")   # "EA310BS1"
        self.assertEqual(a[18:20], "01")                # bootcount 0
        self.assertEqual(a[20:22], "01")                # armed
        self.assertEqual(a[24:32], "00000001")          # seq 1
        self.assertEqual(self.record_hex(0x7800), "00" * 512)

    def test_raw_commit_clears_state(self):
        self.add_marker()
        self.craft_state("HEALTHY")
        self.grant_authorization()
        self.run_tool("try")   # needs STAGED -> expect conflict exit 5
        self.craft_state("STAGED")
        self.run_tool("try")   # now arms
        self.craft_state("HEALTHY")
        r = self.run_tool("commit")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.state_of(), "COMMITTED")
        # clear wrote the committed record into the OTHER copy (B)
        b = self.record_hex(0x7800)
        self.assertEqual(b[20:22], "00")                # committed
        self.assertEqual(b[24:32], "00000002")          # seq 2


if __name__ == "__main__":
    unittest.main()
