"""P8.2 watchdog userspace-handoff contract + feeder regression tests.

Ten cases from the P8.2 spec: normal stable boots must never start the
feeder, the feeder refuses unmarked/foreign trials, the pre-arm contract
refuses arms without feeder binary/unit/watchdog device, a valid armed
trial is allowed, health success finalizes cleanly (magic 'V'), and a
stopped feeder stops the keepalive.

All WSL commands are passed through a base64 pipe: wsl.exe re-runs its
argument list through the user's default shell, which would otherwise eat
$-expansions and quoting.
"""
import base64
import json
import os
import pathlib
import subprocess
import unittest


HERE = pathlib.Path(__file__).resolve().parent
TOOL = HERE.parent / "eaidk-ota"
FEEDER = HERE.parent / "templates" / "eaidk-trial-feed"
RELEASE = "6.18.55-eaidk310-zramfix1"
BUNDLE = "869c410a08484fd8337ad7a5fa214d54ef807f4fd270a9f18c592397d0c43eea"
MARKER = f"eaidk_ota_trial={RELEASE}@{BUNDLE[:12]}"
STABLE_APPEND = "root=UUID=781e1dc3-166b-46e9-8578-4b9c003d7305 rootwait console=ttyS2,1500000n8"


@unittest.skipUnless(os.name == "nt", "Windows WSL integration tests")
class WatchdogHandoffTests(unittest.TestCase):
    wsl_tool = TOOL.as_posix().replace("D:/", "/mnt/d/")
    wsl_feeder = FEEDER.as_posix().replace("D:/", "/mnt/d/")

    @classmethod
    def setUpClass(cls):
        ok = subprocess.run(
            ["wsl", "-d", "Ubuntu-24.04", "--", "bash", "-c", "command -v python3"],
            capture_output=True, text=True)
        if ok.returncode != 0:
            raise unittest.SkipTest("WSL python3 unavailable")

    def wsl(self, command: str, timeout: int = 240) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["wsl", "-d", "Ubuntu-24.04", "--", "bash", "-c", command],
            text=True, capture_output=True, timeout=timeout)

    def wsl_bash(self, inner: str) -> subprocess.CompletedProcess:
        b64 = base64.b64encode(inner.encode()).decode()
        return self.wsl(f"echo {b64} | base64 -d | bash")

    def wsl_python(self, script: str) -> str:
        proc = self.wsl_bash("python3 - <<'PYEOF'\n" + script + "\nPYEOF")
        if proc.returncode != 0 and "RESULTS=" not in proc.stdout \
                and "FEEDER=" not in proc.stdout:
            return "SCRIPT_ERROR: " + proc.stderr[-800:]
        return proc.stdout

    # ------------------------------------------------------------------ --
    # fixture
    # ------------------------------------------------------------------ --

    def build_fixture(self, work: str) -> str:
        script = (
            "import hashlib, json, pathlib, shutil\n"
            f"work = pathlib.Path({work!r})\n"
            "if work.exists():\n"
            "    shutil.rmtree(work)\n"
            f"release = {RELEASE!r}\n"
            f"bundle = {BUNDLE!r}\n"
            "marker = 'eaidk_ota_trial=' + release + '@' + bundle[:12]\n"
            "boot = work / 'boot'\n"
            "staging = work / 'state' / 'staging' / release\n"
            "mods = work / 'modules' / release\n"
            "for d in (boot / 'extlinux', boot / 'dtb/rockchip',\n"
            "          boot / 'eaidk-ota', staging / 'boot' / 'dtb/rockchip',\n"
            "          staging / 'root/lib/modules' / release,\n"
            "          mods / 'kernel', work / 'wd', work / 'run',\n"
            "          work / 'config', work / 'feeder-bin', work / 'stub'):\n"
            "    d.mkdir(parents=True, exist_ok=True)\n"
            "(boot / 'eaidk-ota' / 'BACKEND').write_text('RawBootstateBackend 1\\n')\n"
            "(work / 'wd' / 'watchdog0').write_bytes(b'')\n"
            "(work / 'config' / 'allow-boot-mutation').write_text('1\\n')\n"
            "(work / 'run' / 'local-authorization').write_text(json.dumps(\n"
            "    {'issued': 'now', 'expires': '2999-01-01T00:00:00+00:00',\n"
            "     'tty': '/dev/ttyS2', 'ttl_seconds': 900}))\n"
            "def blob(p):\n"
            "    p.write_bytes(release.encode() + bytes(512))\n"
            "img = boot / ('Image-' + release)\n"
            "uin = boot / ('uInitrd-' + release)\n"
            "dtb = boot / 'dtb/rockchip' / ('rk3328-eaidk-310-' + release + '.dtb')\n"
            "for f in (img, uin, dtb):\n"
            "    blob(f)\n"
            "shutil.copy(img, staging / 'boot' / img.name)\n"
            "shutil.copy(uin, staging / 'boot' / uin.name)\n"
            "shutil.copy(dtb, staging / 'boot/dtb/rockchip' / dtb.name)\n"
            "(mods / 'modules.dep').write_text('kernel/x.ko: x\\n')\n"
            "(mods / '.eaidk-ota-installed').write_text('release=' + release\n"
            "    + '\\nbundle=' + bundle + '\\n')\n"
            "(mods / 'kernel' / 'x.ko').write_bytes(b'fake')\n"
            "shutil.copy(mods / 'modules.dep',\n"
            "    staging / 'root/lib/modules' / release / 'modules.dep')\n"
            "append = 'root=UUID=781e1dc3-166b-46e9-8578-4b9c003d7305 ' + marker\n"
            "label = 'rockchip-kernel-' + release + '-test'\n"
            "(boot / 'extlinux' / 'extlinux.conf').write_text(\n"
            "    'default rockchip-kernel-' + release + '\\n'\n"
            "    + 'label rockchip-kernel-' + release + '\\n'\n"
            "    + '    APPEND root=UUID=781e1dc3-166b-46e9-8578-4b9c003d7305\\n')\n"
            "conf_text = ('default ' + label + '\\nlabel ' + label + '\\n'\n"
            "    + '    LINUX  /Image-' + release + '\\n'\n"
            "    + '    FDT    /dtb/rockchip/rk3328-eaidk-310-' + release"
            " + '.dtb\\n'\n"
            "    + '    INITRD /uInitrd-' + release + '\\n'\n"
            "    + '    APPEND ' + append + '\\n')\n"
            "(boot / 'extlinux' / 'extlinux-candidate.conf').write_text(conf_text)\n"
            "identity = {'release': release, 'bundle_sha256': bundle,\n"
            "    'backend': 'RAW_REDUNDANT', 'marker': marker,\n"
            "    'conf': str(boot / 'extlinux/extlinux-candidate.conf'),\n"
            "    'conf_sha256': hashlib.sha256(conf_text.encode()).hexdigest(),\n"
            "    'stable_default': 'rockchip-kernel-' + release,\n"
            "    'parsed': {'label': label, 'linux': '/Image-' + release,\n"
            "    'fdt': '/dtb/rockchip/rk3328-eaidk-310-' + release + '.dtb',\n"
            "    'initrd': '/uInitrd-' + release, 'append': append,\n"
            "    'marker': marker}}\n"
            "(work / 'state' / 'state.json').write_text(json.dumps({\n"
            "    'state': 'STAGED', 'release': release,\n"
            "    'bundle_sha256': bundle, 'candidate_identity': identity}))\n"
            "feeder = work / 'feeder-bin' / 'eaidk-trial-feed'\n"
            "feeder.write_text('#!/bin/sh\\necho feeder-stub\\n')\n"
            "feeder.chmod(0o755)\n"
            "unit = work / 'feeder-bin' / 'eaidk-trial-feed.service'\n"
            "unit.write_text('[Unit]\\nConditionKernelCommandLine="
            "eaidk_ota_trial\\n\\n[Service]\\n"
            "ExecStart=/usr/local/sbin/eaidk-trial-feed\\n')\n"
            "stub = work / 'stub' / 'eaidk-bootstate'\n"
            "stub.parent.mkdir(parents=True, exist_ok=True)\n"
            "stub.write_text('#!/bin/sh\\necho \\'{\"upgrade_available\": 0}\\'\\n')\n"
            "stub.chmod(0o755)\n"
            "ota_stub = work / 'stub' / 'eaidk-ota'\n"
            "ota_stub.write_text('#!/bin/sh\\necho \\'{\"active_backend\": "
            "\"RawBootstateBackend\", \"bootstate\": "
            "{\"upgrade_available\": 0}}\\'\\n')\n"
            "ota_stub.chmod(0o755)\n"
            "print('FIXTURE_OK')\n")
        return self.wsl_python(script)

    def run_contract(self, work: str, mutate: str = "none") -> dict:
        self.assertIn("FIXTURE_OK", self.build_fixture(work))
        pre = {
            "missing-watchdog": "shutil.rmtree(work / 'wd')",
            "missing-unit": "(work / 'feeder-bin' / "
                            "'eaidk-trial-feed.service').unlink()",
            "missing-binary": "(work / 'feeder-bin' / "
                              "'eaidk-trial-feed').unlink()",
        }.get(mutate, "")
        script = (
            "import json, pathlib, shutil\n"
            "from importlib.machinery import SourceFileLoader\n"
            "from importlib.util import module_from_spec, spec_from_loader\n"
            f"loader = SourceFileLoader('ota', {self.wsl_tool!r})\n"
            "m = module_from_spec(spec_from_loader('ota', loader))\n"
            "loader.exec_module(m)\n"
            f"work = pathlib.Path({work!r})\n"
            + (pre + "\n" if pre else "")
            + "raw = {'bootcount': 0, 'upgrade_available': 0, 'fail_closed': False}\n"
            "checks = m.prearm_contract(work / 'boot', work / 'state',\n"
            f"    {RELEASE!r}, {BUNDLE!r}, 'RawBootstateBackend', raw,\n"
            "    modules_root=str(work / 'modules'),\n"
            "    watchdog_gates=True,\n"
            "    feeder_bin=str(work / 'feeder-bin' / 'eaidk-trial-feed'),\n"
            "    feeder_unit=str(work / 'feeder-bin' / "
            "'eaidk-trial-feed.service'),\n"
            "    watchdog_dev=str(work / 'wd' / 'watchdog0'),\n"
            "    sysfs_watchdog=str(work / 'wd' / 'watchdog0'))\n"
            "print('RESULTS=' + json.dumps([{'name': n, 'ok': o}\n"
            "    for n, o, _ in checks]))\n")
        out = self.wsl_python(script)
        for line in out.splitlines():
            if line.startswith("RESULTS="):
                return {c["name"]: c["ok"] for c in json.loads(line[8:])}
        self.fail("no RESULTS in output: " + out[-600:])

    # ------------------------------------------------------------------ --
    # CASE 1-3, 8, 9: feeder script behaviour
    # ------------------------------------------------------------------ --

    def feeder_run(self, work: str, cmdline: str) -> subprocess.CompletedProcess:
        inner = (f"rm -f '{work}/watchdog0'; "
                 f"env EAIDK_TEST_CMDLINE='{cmdline}' "
                 f"EAIDK_CANDIDATE_CONF='{work}/boot/extlinux/"
                 f"extlinux-candidate.conf' "
                 f"EAIDK_TEST_WATCHDOG='{work}/wd/watchdog0' "
                 f"sh {self.wsl_feeder}; echo RC=$?")
        return self.wsl_bash(inner)

    def test_case1_normal_stable_boot_feeder_not_started(self):
        r = self.feeder_run("/tmp/eaidk-wd-c1", "root=UUID=781e1dc3 ro quiet")
        self.assertIn("not a candidate trial boot", r.stdout + r.stderr)
        self.assertIn("RC=0", r.stdout)

    def test_case2_trial_marker_absent_in_conf_refuses(self):
        work = "/tmp/eaidk-wd-c2"
        self.assertIn("FIXTURE_OK", self.build_fixture(work))
        stale = self.wsl_bash(
            f"printf 'default stale\\n' > {work}/boot/extlinux/"
            f"extlinux-candidate.conf")
        self.assertEqual(stale.returncode, 0, stale.stderr)
        r = self.feeder_run(work, "root=UUID=x "
                            "eaidk_ota_trial="
                            "6.18.55-eaidk310-zramfix1@869c410a0848")
        self.assertIn("identity mismatch", r.stdout + r.stderr)
        self.assertIn("RC=3", r.stdout)

    def test_case3_candidate_identity_mismatch_refuses(self):
        work = "/tmp/eaidk-wd-c3"
        self.assertIn("FIXTURE_OK", self.build_fixture(work))
        r = self.feeder_run(work,
                            "root=UUID=x eaidk_ota_trial="
                            f"{RELEASE}@deadbeefcafe")
        self.assertIn("identity mismatch", r.stdout + r.stderr)
        self.assertIn("RC=3", r.stdout)

    def test_case8_health_success_clean_finalization_magic_v(self):
        work = "/tmp/eaidk-wd-c8"
        self.assertIn("FIXTURE_OK", self.build_fixture(work))
        inner = (f"env EAIDK_TEST_CMDLINE='root=UUID=x {MARKER}' "
                 f"EAIDK_CANDIDATE_CONF='{work}/boot/extlinux/"
                 f"extlinux-candidate.conf' "
                 f"EAIDK_TEST_WATCHDOG='{work}/wd/watchdog0' "
                 f"EAIDK_TEST_BOOTSTATE='{work}/stub/eaidk-bootstate' "
                 f"EAIDK_TEST_OTA_STATUS='{work}/stub/eaidk-ota' "
                 f"sh {self.wsl_feeder}; echo RC=$?; "
                 f"grep -c V '{work}/wd/watchdog0'")
        r = self.wsl_bash(inner)
        self.assertIn("RC=0", r.stdout)
        self.assertIn("magic V", r.stdout + r.stderr)
        self.assertTrue(r.stdout.strip().splitlines()[-1] == "1", r.stdout)

    def test_case9_feeder_stop_stops_keepalive(self):
        work = "/tmp/eaidk-wd-c9"
        self.assertIn("FIXTURE_OK", self.build_fixture(work))
        inner = (f"env EAIDK_TEST_CMDLINE='root=UUID=x {MARKER} "
                 f"eaidk_ota_hang_test=1' "
                 f"EAIDK_CANDIDATE_CONF='{work}/boot/extlinux/"
                 f"extlinux-candidate.conf' "
                 f"EAIDK_TEST_WATCHDOG='{work}/wd/watchdog0' "
                 f"timeout 3 sh {self.wsl_feeder}; echo RC=$?; "
                 f"od -c '{work}/wd/watchdog0' | head -3")
        r = self.wsl_bash(inner)
        self.assertIn("HANG TEST engaged", r.stdout + r.stderr,
                      "feeder must engage the hang path")
        self.assertIn("x", r.stdout, "keepalive byte must be present")
        self.assertNotIn("V", r.stdout, "stopped feeder must not emit magic V")

    # ------------------------------------------------------------------ --
    # CASE 4/5/6/7: pre-arm watchdog gates
    # ------------------------------------------------------------------ --

    def expect_refusal(self, results: dict, failed_check: str):
        self.assertFalse(all(results.values()),
                         f"expected refusal, all passed: {results}")
        self.assertFalse(results.get(failed_check, True),
                         f"expected {failed_check} to be the failing check")

    def test_case4_missing_watchdog_device_prearm_refuses(self):
        r = self.run_contract("/tmp/eaidk-wd-p4", "missing-watchdog")
        self.expect_refusal(r, "watchdog_device_present")

    def test_case5_missing_unit_prearm_refuses(self):
        r = self.run_contract("/tmp/eaidk-wd-p5", "missing-unit")
        self.expect_refusal(r, "trial_feed_unit_present")

    def test_case6_missing_binary_prearm_refuses(self):
        r = self.run_contract("/tmp/eaidk-wd-p6", "missing-binary")
        self.expect_refusal(r, "trial_feed_binary_present")

    def test_case7_valid_armed_trial_allowed(self):
        r = self.run_contract("/tmp/eaidk-wd-p7", "none")
        self.assertTrue(all(r.values()),
                        {k: v for k, v in r.items() if not v})

    def test_embedded_feeder_matches_template(self):
        script = (
            "import pathlib\n"
            f"tool = pathlib.Path('{self.wsl_tool}')\n"
            f"tmpl = pathlib.Path('{self.wsl_feeder}')\n"
            "src = tool.read_text()\n"
            "key = \"TRIAL_FEED_SCRIPT = '''\"\n"
            "s = src.index(key)\n"
            "e = src.index(\"'''\", s + len(key))\n"
            "print('EMBED_MATCH=' + str(src[s + len(key):e] == tmpl.read_text()))\n")
        out = self.wsl_python(script)
        self.assertIn("EMBED_MATCH=True", out)


if __name__ == "__main__":
    unittest.main()
