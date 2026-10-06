"""P8.1 pre-arm candidate contract regression tests (the 6.18.55 trial
incident, fixed): arming must refuse unless the candidate deployment is
provably complete, and a refusal must never touch the raw bootstate.

WSL-hosted like the rest of the ota suite (needs python3 inside WSL).
"""
import json
import os
import pathlib
import subprocess
import unittest


HERE = pathlib.Path(__file__).resolve().parent
TOOL = HERE.parent / "eaidk-ota"
RELEASE = "6.18.55-eaidk310-zramfix1"
BUNDLE = "869c410a08484fd8337ad7a5fa214d54ef807f4fd270a9f18c592397d0c43eea"
DTB = f"rk3328-eaidk-310-{RELEASE}.dtb"
MARKER = f"eaidk_ota_trial={RELEASE}@{BUNDLE[:12]}"
STABLE_APPEND = ("root=UUID=781e1dc3-166b-46e9-8578-4b9c003d7305 rootwait "
                 "rootfstype=ext4 console=ttyS2,1500000n8")


@unittest.skipUnless(os.name == "nt", "Windows WSL integration tests")
class PrearmContractTests(unittest.TestCase):
    wsl_tool = TOOL.as_posix().replace("D:/", "/mnt/d/")

    @classmethod
    def setUpClass(cls):
        ok = subprocess.run(
            ["wsl", "-d", "Ubuntu-24.04", "--", "bash", "-c", "command -v python3"],
            capture_output=True, text=True)
        if ok.returncode != 0:
            raise unittest.SkipTest("WSL python3 unavailable")

    def wsl_python(self, script: str) -> str:
        proc = subprocess.run(
            ["wsl", "-d", "Ubuntu-24.04", "--", "bash", "-c",
             f"python3 - <<'PYEOF'\n{script}\nPYEOF"],
            text=True, capture_output=True, timeout=120)
        if proc.returncode != 0 and "ASSERT" not in proc.stdout:
            return f"SCRIPT_ERROR: {proc.stderr[-800:]}"
        return proc.stdout

    def build_fixture_script(self, work: str, mutate: str = "none") -> str:
        """Emit python that builds a full candidate fixture under `work` and
        applies `mutate` (one of none/missing-conf/wrong-image/wrong-uinitrd/
        wrong-dtb/missing-modules-dep/missing-marker/identity-mismatch)."""
        return f'''
import hashlib, json, pathlib, shutil, sys

mutate = {mutate!r}
work = pathlib.Path({work!r})
if work.exists():
    shutil.rmtree(work)
release = {RELEASE!r}
bundle = {BUNDLE!r}
marker = f"eaidk_ota_trial={{release}}@{{bundle[:12]}}"
boot = work / "boot"
staging = work / "state" / "staging" / release
mods = work / "modules" / release
for d in (boot / "extlinux", boot / "dtb/rockchip", boot / "eaidk-ota",
          staging / "boot" / "dtb/rockchip", staging / "deploy",
          staging / "root/lib/modules" / release, mods / "kernel"):
    d.mkdir(parents=True, exist_ok=True)
(boot / "eaidk-ota" / "BACKEND").write_text("RawBootstateBackend 1" + chr(10))

def blob(path):
    path.write_bytes(release.encode() + b"\\x00" * 4096)

img, uin = boot / f"Image-{{release}}", boot / f"uInitrd-{{release}}"
blob(img); blob(uin)
dtb = boot / "dtb/rockchip" / {DTB!r}
dtb.write_bytes(b"\\0" * 256 + b"openailab,eaidk-310\\0")
shutil.copy(img, staging / "boot" / img.name)
shutil.copy(uin, staging / "boot" / uin.name)
shutil.copy(dtb, staging / "boot/dtb/rockchip" / dtb.name)
shutil.copy(dtb, staging / "deploy" / "dtb.copy")
(mods / "modules.dep").write_text("kernel/board_test.ko: \\n")
(mods / ".eaidk-ota-installed").write_text(f"release={{release}}\\nbundle={{bundle}}\\n")
(mods / "kernel" / "board_test.ko").write_bytes(b"fake")
shutil.copy(mods / "modules.dep", staging / "root/lib/modules" / release / "modules.dep")

stable_append = {STABLE_APPEND!r}
conf_text = (
    f"default rockchip-kernel-{{release}}-test\\n"
    "timeout 5\\n"
    f"label rockchip-kernel-{{release}}-test\\n"
    f"    LINUX  /Image-{{release}}\\n"
    f"    FDT    /dtb/rockchip/{DTB}\\n"
    f"    INITRD /uInitrd-{{release}}\\n"
    f"    APPEND {{stable_append}} {{marker}}\\n")
(boot / "extlinux" / "extlinux.conf").write_text(
    conf_text.replace(marker + " ", "").replace(
        f"rockchip-kernel-{{release}}-test", "rockchip-kernel-6.18.54-eaidk310-zramfix1"))
identity = {{
    "release": release, "bundle_sha256": bundle, "backend": "RAW_REDUNDANT",
    "marker": marker, "conf": str(boot / "extlinux/extlinux-candidate.conf"),
    "parsed": {{"label": f"rockchip-kernel-{{release}}-test",
               "linux": f"/Image-{{release}}", "initrd": f"/uInitrd-{{release}}",
               "fdt": f"/dtb/rockchip/{DTB}",
               "append": f"{{stable_append}} {{marker}}", "marker": marker}},
}}
if mutate != "missing-conf":
    conf_path = boot / "extlinux" / "extlinux-candidate.conf"
    text = conf_text
    if mutate == "wrong-image":
        text = text.replace(f"/Image-{{release}}", "/Image-other")
    if mutate == "wrong-uinitrd":
        text = text.replace(f"/uInitrd-{{release}}", "/uInitrd-other")
    if mutate == "wrong-dtb":
        text = text.replace({DTB!r}, "rk3328-eaidk-310-other.dtb")
    if mutate == "missing-marker":
        text = text.replace(" " + marker, "")
    conf_path.write_text(text)
if mutate == "missing-modules-dep":
    (mods / "modules.dep").unlink()
identity["conf_sha256"] = hashlib.sha256(
    (boot / "extlinux" / "extlinux-candidate.conf").read_bytes()).hexdigest() \\
    if (boot / "extlinux" / "extlinux-candidate.conf").exists() else "missing"
if mutate == "identity-mismatch":
    identity["marker"] = "eaidk_ota_trial=other@000000000000"
(work / "state" / "state.json").write_text(json.dumps({{
    "state": "STAGED", "release": release, "bundle_sha256": bundle,
    "candidate_identity": identity}}))
run_dir = work / "run"
run_dir.mkdir(parents=True, exist_ok=True)
(run_dir / "local-authorization").write_text(json.dumps(
    {{"issued": "now", "expires": "2999-01-01T00:00:00+00:00",
     "tty": "/dev/ttyS2", "ttl_seconds": 900}}))
cfg_dir = work / "config"
cfg_dir.mkdir(parents=True, exist_ok=True)
(cfg_dir / "allow-boot-mutation").write_text("1")
print("FIXTURE_OK")
'''

    def run_case(self, work: str, mutate: str) -> dict:
        script = f'''
import json, pathlib
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
loader = SourceFileLoader("ota", {self.wsl_tool!r})
spec = spec_from_loader("ota", loader)
m = module_from_spec(spec); loader.exec_module(m)
work = pathlib.Path({work!r})
raw = {{"bootcount": 0, "upgrade_available": 0, "fail_closed": False}}
checks = m.prearm_contract(work / "boot", work / "state", {RELEASE!r},
                           {BUNDLE!r}, "RawBootstateBackend", raw,
                           modules_root=str(work / "modules"))
print("RESULTS=" + json.dumps([{{"name": n, "ok": o, "detail": d}}
      for n, o, d in checks]))
'''
        out = self.wsl_python(self.build_fixture_script(work, mutate) + "\n" + script)
        for line in out.splitlines():
            if line.startswith("RESULTS="):
                return {c["name"]: c["ok"] for c in json.loads(line[8:])}
        self.fail(f"no RESULTS in output: {out[-500:]}")

    def expect_refusal(self, results: dict, failed_check: str):
        self.assertFalse(all(results.values()),
                         f"expected refusal, all checks passed: {results}")
        self.assertFalse(results.get(failed_check, True),
                         f"expected {failed_check} to be the failing check")

    WORK = "/tmp/eaidk-prearm-tests"

    def test_case8_all_consistent_allows(self):
        r = self.run_case(f"{self.WORK}-case8", "none")
        self.assertTrue(all(r.values()),
                        {k: v for k, v in r.items() if not v})

    def test_case1_missing_conf_refused(self):
        r = self.run_case(f"{self.WORK}-case1", "missing-conf")
        self.expect_refusal(r, "candidate_conf_present")

    def test_case2_wrong_image_refused(self):
        r = self.run_case(f"{self.WORK}-case2", "wrong-image")
        self.expect_refusal(r, "candidate_conf_refs_expected_image")

    def test_case3_wrong_uinitrd_refused(self):
        r = self.run_case(f"{self.WORK}-case3", "wrong-uinitrd")
        self.expect_refusal(r, "candidate_conf_refs_expected_uinitrd")

    def test_case4_wrong_dtb_refused(self):
        r = self.run_case(f"{self.WORK}-case4", "wrong-dtb")
        self.expect_refusal(r, "candidate_conf_refs_expected_dtb")

    def test_case5_missing_modules_dep_refused(self):
        r = self.run_case(f"{self.WORK}-case5", "missing-modules-dep")
        self.expect_refusal(r, "modules_dep_present")

    def test_case6_missing_marker_refused(self):
        r = self.run_case(f"{self.WORK}-case6", "missing-marker")
        self.expect_refusal(r, "trial_marker_present_in_append")

    def test_case7_identity_mismatch_refused(self):
        r = self.run_case(f"{self.WORK}-case7", "identity-mismatch")
        self.expect_refusal(r, "candidate_conf_identity_matches_plan")

    def test_install_candidate_conf_generates_and_readbacks(self):
        out = self.wsl_python(self.build_fixture_script(f"{self.WORK}-gen", "none") + '''
import json, pathlib
from importlib.machinery import SourceFileLoader
from importlib.util import module_from_spec, spec_from_loader
loader = SourceFileLoader("ota", %(tool)r)
spec = spec_from_loader("ota", loader)
m = module_from_spec(spec); loader.exec_module(m)
work = pathlib.Path(%(work)r)
identity = m.install_candidate_conf(work / "boot", %(release)r, %(bundle)r,
                                    %(dtb)r)
assert identity["marker"] in identity["parsed"]["append"]
assert identity["parsed"]["linux"] == "/Image-" + %(release)r
stable = m.current_default_entry(work / "boot")
root_of = lambda s: [t for t in s.split() if t.startswith("root=")][0]
assert root_of(identity["parsed"]["append"]) == root_of(stable["append"]), \\
    "candidate APPEND must inherit the running stable root="
print("GEN_OK")
''' % {"tool": self.wsl_tool, "work": f"{self.WORK}-gen",
       "release": RELEASE, "bundle": BUNDLE, "dtb": DTB})
        self.assertIn("GEN_OK", out, out[-600:])

    def test_cli_arm_refusal_leaves_raw_untouched(self):
        work = f"{self.WORK}-cli"
        rawfile = f"{work}/raw.img"
        setup = self.build_fixture_script(work, "missing-conf") + f'''
import pathlib, subprocess
raw = pathlib.Path({rawfile!r})
with raw.open("wb") as fh:
    fh.truncate(15729152 + 512)
'''
        pre = self.wsl_python(setup)
        self.assertIn("FIXTURE_OK", pre, pre[-400:])
        digest = self.wsl_python(f"import hashlib; print(hashlib.sha256(open({rawfile!r},'rb').read()).hexdigest())").strip()
        before = digest.splitlines()[-1]
        cli = (f"{self.wsl_tool} --state-dir {work}/state --boot-dir {work}/boot "
               f"--modules-dir {work}/modules --run-dir {work}/run "
               f"--config-dir {work}/config "
               f"--bootstate-tool {self.wsl_tool.rsplit('/', 1)[0]}/eaidk-bootstate "
               f"--backend raw --raw-device {rawfile} arm")
        proc = subprocess.run(
            ["wsl", "-d", "Ubuntu-24.04", "--", "bash", "-c", cli],
            text=True, capture_output=True, timeout=120)
        self.assertNotEqual(proc.returncode, 0, "arm must fail on missing conf")
        self.assertIn("ARM_REFUSED", proc.stdout + proc.stderr)
        after = self.wsl_python(f"import hashlib; print(hashlib.sha256(open({rawfile!r},'rb').read()).hexdigest())").strip().splitlines()[-1]
        self.assertEqual(before, after, "raw bootstate must be untouched on refusal")


if __name__ == "__main__":
    unittest.main()
