import hashlib
import json
import os
import pathlib
import re
import shutil
import struct
import subprocess
import tarfile
import tempfile
import unittest


PROJECT_DIR = pathlib.Path(__file__).resolve().parents[1]
SCRIPTS = PROJECT_DIR / "scripts"
HELPER = PROJECT_DIR / "tools" / "kernel_artifacts.py"
BUNDLE_NAME = "eaidk310-linux-6.12.108-eaidk310-zramfix1"
RELEASE = "6.12.108-eaidk310-zramfix1"
EMMC_ROOT_UUID = "781e1dc3-166b-46e9-8578-4b9c003d7305"
EMMC_BOOT_UUID = "cbdb447a-125d-4d30-bc3d-07807a1f4578"
EMMC_LABEL = "rockchip-kernel-6.12.108-eaidk310-zramfix1"


@unittest.skipUnless(os.name == "nt", "Windows WSL integration tests")
class RescueTfDeploymentTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.work = pathlib.Path(self.temporary_directory.name)
        self.bundle = self.work / BUNDLE_NAME
        self.target = self.work / "target"
        self.fake_bin = self.work / "fake-bin"
        self.archive = self.work / f"{BUNDLE_NAME}.tar.zst"
        self._create_target()
        self._create_fake_commands()
        self._create_bundle()

    def tearDown(self):
        self.temporary_directory.cleanup()

    @staticmethod
    def wsl_path(path: pathlib.Path) -> str:
        resolved = path.resolve()
        return f"/mnt/{resolved.drive[0].lower()}{resolved.as_posix()[2:]}"

    @staticmethod
    def sha256(path: pathlib.Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def file_snapshot(root: pathlib.Path) -> dict[str, bytes]:
        return {
            path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*")
            if path.is_file()
        }

    def _write(self, root: pathlib.Path, relative: str, content: bytes) -> pathlib.Path:
        path = root.joinpath(*relative.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    def _stable_extlinux(self) -> bytes:
        return (
            "default rockchip-kernel-6.8.4\n"
            "    label rockchip-kernel-6.8.4\n"
            "    LINUX /Image\n"
            "    FDT /dtb/rockchip/rk3328-eaidk-310.dtb\n"
            "    INITRD /uInitrd\n"
            "    APPEND root=UUID=fixture rootwait\n"
        ).encode()

    def _create_target(self) -> None:
        self._write(self.target, "boot/Image", b"stable image\n")
        self._write(self.target, "boot/uInitrd", b"stable initrd\n")
        self._write(
            self.target,
            "boot/dtb/rockchip/rk3328-eaidk-310.dtb",
            b"stable dtb\n",
        )
        self._write(
            self.target,
            "boot/extlinux/extlinux.conf",
            self._stable_extlinux(),
        )
        (self.target / "lib" / "modules").mkdir(parents=True)

    def _create_fake_commands(self) -> None:
        self.fake_bin.mkdir()
        commands = {
            "findmnt": """#!/bin/sh
case "$*" in
  '-n -o SOURCE /') printf '%s\\n' "${FAKE_ROOT_SOURCE:-/dev/mmcblk0p2}" ;;
  '-n -o SOURCE /boot') printf '%s\\n' "${FAKE_BOOT_SOURCE:-/dev/mmcblk0p1}" ;;
  '-n -o SOURCE --target '*/boot) printf '%s\\n' '/dev/mmcblk2p1' ;;
  '-n -o SOURCE --target '*) printf '%s\\n' '/dev/mmcblk2p2' ;;
  '-rn -o SOURCE') [ -z "${FAKE_EMMC_MOUNTS:-}" ] || printf '%s\\n' "$FAKE_EMMC_MOUNTS" ;;
  *) printf 'unexpected findmnt args: %s\\n' "$*" >&2; exit 64 ;;
esac
""",
            "uname": """#!/bin/sh
[ "$*" = '-r' ] || exit 64
printf '%s\\n' "${FAKE_KERNEL_RELEASE:-6.8.4-rk3328}"
""",
            "df": """#!/bin/sh
printf 'Filesystem 1B-blocks Used Available Use%% Mounted on\\n'
printf 'fixture 999999999999 0 %s 0%% /fixture\\n' "${FAKE_AVAILABLE_BYTES:-999999999999}"
""",
            "date": """#!/bin/sh
[ "$*" = '-u +%Y%m%dT%H%M%SZ' ] || exit 64
printf '20260904T120000Z\\n'
""",
            "sync": "#!/bin/sh\nexit 0\n",
            "lsblk": """#!/bin/sh
[ "$*" = '-dn -o MODEL /dev/mmcblk2' ] || exit 64
printf '%s\n' "${FAKE_EMMC_MODEL:-HBD08G}"
""",
            "blkid": """#!/bin/sh
case "$*" in
  '-s UUID -o value /dev/mmcblk2p1') printf '%s\n' "${FAKE_EMMC_BOOT_UUID:-cbdb447a-125d-4d30-bc3d-07807a1f4578}" ;;
  '-s UUID -o value /dev/mmcblk2p2') printf '%s\n' "${FAKE_EMMC_ROOT_UUID:-781e1dc3-166b-46e9-8578-4b9c003d7305}" ;;
  *) exit 64 ;;
esac
""",
        }
        for name, content in commands.items():
            path = self.fake_bin / name
            path.write_text(content, encoding="utf-8", newline="\n")
            path.chmod(0o755)

    def _create_bundle(self) -> None:
        files = {
            f"boot/Image-{RELEASE}": b"new arm64 image\n",
            f"boot/uInitrd-{RELEASE}": b"new arm64 initrd\n",
            "boot/dtb/rockchip/rk3328-eaidk-310-6.12.108.dtb": b"new dtb\n",
            f"root/lib/modules/{RELEASE}/modules.dep": b"kernel/test.ko:\n",
            f"root/lib/modules/{RELEASE}/modules.builtin": b"",
            f"root/lib/modules/{RELEASE}/modules.order": b"kernel/test.ko\n",
            f"root/lib/modules/{RELEASE}/kernel/test.ko": b"arm64 module\n",
            "deploy/extlinux-entry.conf": (
                "    label rockchip-kernel-6.12.108-eaidk310-zramfix1-test\n"
                "    LINUX  /Image-6.12.108-eaidk310-zramfix1\n"
                "    FDT    /dtb/rockchip/rk3328-eaidk-310-6.12.108.dtb\n"
                "    INITRD /uInitrd-6.12.108-eaidk310-zramfix1\n"
                "    APPEND root=UUID=fixture rootwait\n"
            ).encode(),
            "verification-report.json": b'{"gate":"PASS"}\n',
        }
        for relative, content in files.items():
            self._write(self.bundle, relative, content)
        shutil.copy2(SCRIPTS / "deploy-rescue-tf.sh", self.bundle / "deploy")
        shutil.copy2(HELPER, self.bundle / "deploy" / "kernel_artifacts.py")
        stable_paths = (
            "boot/Image",
            "boot/uInitrd",
            "boot/dtb/rockchip/rk3328-eaidk-310.dtb",
            "boot/extlinux/extlinux.conf",
        )
        lock = "".join(
            f"{self.sha256(self.target.joinpath(*relative.split('/')))}  {relative}\n"
            for relative in stable_paths
        )
        self._write(
            self.bundle,
            "deploy/stable-baseline-sha256.txt",
            lock.encode(),
        )
        manifest_files = sorted(
            path.relative_to(self.bundle).as_posix()
            for path in self.bundle.rglob("*")
            if path.is_file() and path.name != "manifest.json"
        )
        manifest = {
            "schema_version": 1,
            "metadata": {
                "kernel_release": RELEASE,
                "deployment_enabled": True,
            },
            "files": [
                {
                    "path": relative,
                    "sha256": self.sha256(
                        self.bundle.joinpath(*relative.split("/"))
                    ),
                    "size": self.bundle.joinpath(*relative.split("/")).stat().st_size,
                }
                for relative in manifest_files
            ],
        }
        (self.bundle / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        tar_path = self.work / f"{BUNDLE_NAME}.tar"
        with tarfile.open(tar_path, "w") as archive:
            archive.add(self.bundle, arcname=BUNDLE_NAME)
        compressed = subprocess.run(
            [
                "wsl", "-d", "Ubuntu-24.04", "--", "zstd", "-q", "-f",
                self.wsl_path(tar_path), "-o", self.wsl_path(self.archive),
            ],
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(compressed.returncode, 0, compressed.stderr)
        tar_path.unlink()
        self.bundle_sha256 = self.sha256(self.archive)

    def run_deploy(
        self,
        *,
        apply: bool = False,
        bundle_sha256: str | None = None,
        test_mode: bool = True,
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        arguments = [
            "--root-prefix", self.wsl_path(self.target),
            "--bundle-root", self.wsl_path(self.bundle),
            "--bundle-archive", self.wsl_path(self.archive),
        ]
        if bundle_sha256 is not None:
            arguments.extend(("--bundle-sha256", bundle_sha256))
        if apply:
            arguments.append("--apply")
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.wsl_path(self.fake_bin)}:/usr/sbin:/usr/bin:/sbin:/bin",
                "EAIDK310_TEST_MODE": "1" if test_mode else "0",
            }
        )
        if environment:
            env.update(environment)
        return subprocess.run(
            [
                "wsl", "-d", "Ubuntu-24.04", "--", "env",
                *[f"{key}={value}" for key, value in env.items() if key in {
                    "PATH", "EAIDK310_TEST_MODE", "FAKE_ROOT_SOURCE",
                    "FAKE_BOOT_SOURCE", "FAKE_EMMC_MOUNTS",
                    "FAKE_KERNEL_RELEASE", "FAKE_AVAILABLE_BYTES",
                }],
                "bash", self.wsl_path(SCRIPTS / "deploy-rescue-tf.sh"),
                *arguments,
            ],
            cwd=PROJECT_DIR,
            text=True,
            capture_output=True,
            check=False,
        )

    def prepare_emmc_target(self) -> None:
        config = self.target / "boot/extlinux/extlinux.conf"
        config.write_text(
            config.read_text(encoding="utf-8").replace(
                "root=UUID=fixture", f"root=UUID={EMMC_ROOT_UUID}"
            ),
            encoding="utf-8",
            newline="\n",
        )

    def run_emmc_deploy(
        self,
        *,
        apply: bool = False,
        expected_extlinux_sha256: str | None = None,
        environment: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        arguments = [
            "--root-prefix", self.wsl_path(self.target),
            "--bundle-root", self.wsl_path(self.bundle),
            "--bundle-archive", self.wsl_path(self.archive),
            "--bundle-sha256", self.bundle_sha256,
        ]
        if expected_extlinux_sha256 is not None:
            arguments.extend(("--expected-extlinux-sha256", expected_extlinux_sha256))
        if apply:
            arguments.append("--apply")
        env = os.environ.copy()
        env.update(
            {
                "PATH": f"{self.wsl_path(self.fake_bin)}:/usr/sbin:/usr/bin:/sbin:/bin",
                "EAIDK310_TEST_MODE": "1",
                "FAKE_EMMC_MOUNTS": "/dev/mmcblk2p2",
            }
        )
        if environment:
            env.update(environment)
        passed_environment = {
            "PATH", "EAIDK310_TEST_MODE", "FAKE_ROOT_SOURCE", "FAKE_BOOT_SOURCE",
            "FAKE_EMMC_MOUNTS", "FAKE_KERNEL_RELEASE", "FAKE_AVAILABLE_BYTES",
            "FAKE_EMMC_MODEL", "FAKE_EMMC_BOOT_UUID", "FAKE_EMMC_ROOT_UUID",
        }
        return subprocess.run(
            [
                "wsl", "-d", "Ubuntu-24.04", "--", "env",
                *[f"{key}={value}" for key, value in env.items() if key in passed_environment],
                "bash", self.wsl_path(SCRIPTS / "deploy-emmc.sh"),
                *arguments,
            ],
            cwd=PROJECT_DIR,
            text=True,
            capture_output=True,
            check=False,
        )

    def assert_rejected_without_changes(
        self, result: subprocess.CompletedProcess[str], before: dict[str, bytes]
    ) -> None:
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.file_snapshot(self.target), before)

    def test_valid_dry_run_reports_plan_without_changing_target(self):
        before = self.file_snapshot(self.target)
        result = self.run_deploy(bundle_sha256=self.bundle_sha256)

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("write=false", result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["mode"], "dry-run")
        self.assertEqual(report["bundle_sha256"], self.bundle_sha256)
        self.assertEqual(report["current_kernel"], "6.8.4-rk3328")
        self.assertEqual(self.file_snapshot(self.target), before)

    def test_valid_apply_installs_versioned_files_and_preserves_stable_boot(self):
        stable = {
            relative: self.target.joinpath(*relative.split("/")).read_bytes()
            for relative in (
                "boot/Image",
                "boot/uInitrd",
                "boot/dtb/rockchip/rk3328-eaidk-310.dtb",
                "boot/extlinux/extlinux.conf",
            )
        }
        result = self.run_deploy(
            apply=True, bundle_sha256=self.bundle_sha256
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("write=true", result.stderr)
        self.assertEqual((self.target / "boot" / "Image").read_bytes(), stable["boot/Image"])
        self.assertEqual((self.target / "boot" / "uInitrd").read_bytes(), stable["boot/uInitrd"])
        self.assertEqual(
            (self.target / "boot/dtb/rockchip/rk3328-eaidk-310.dtb").read_bytes(),
            stable["boot/dtb/rockchip/rk3328-eaidk-310.dtb"],
        )
        self.assertEqual(
            (self.target / f"boot/Image-{RELEASE}").read_bytes(),
            (self.bundle / f"boot/Image-{RELEASE}").read_bytes(),
        )
        self.assertEqual(
            (self.target / f"lib/modules/{RELEASE}/kernel/test.ko").read_bytes(),
            b"arm64 module\n",
        )
        backup = self.target / "boot/eaidk310-backups/20260904T120000Z"
        self.assertEqual((backup / "extlinux.conf").read_bytes(), stable["boot/extlinux/extlinux.conf"])
        self.assertTrue((backup / "baseline-sha256.txt").is_file())
        report = json.loads((backup / "deployment-report.json").read_text(encoding="utf-8"))
        self.assertEqual(report["mode"], "apply")
        self.assertEqual(
            report["stable_before_sha256"],
            {
                relative.removeprefix("boot/"): hashlib.sha256(content).hexdigest()
                for relative, content in stable.items()
            },
        )
        self.assertEqual(
            report["versioned_after_sha256"][f"Image-{RELEASE}"],
            self.sha256(self.target / f"boot/Image-{RELEASE}"),
        )
        self.assertEqual(
            report["versioned_after_sha256"][f"uInitrd-{RELEASE}"],
            self.sha256(self.target / f"boot/uInitrd-{RELEASE}"),
        )
        self.assertEqual(
            report["versioned_after_sha256"][
                "dtb/rockchip/rk3328-eaidk-310-6.12.108.dtb"
            ],
            self.sha256(
                self.target
                / "boot/dtb/rockchip/rk3328-eaidk-310-6.12.108.dtb"
            ),
        )
        self.assertEqual(
            report["extlinux_after_sha256"],
            self.sha256(self.target / "boot/extlinux/extlinux.conf"),
        )
        deployed_extlinux = (self.target / "boot/extlinux/extlinux.conf").read_text(encoding="utf-8")
        self.assertTrue(deployed_extlinux.startswith("default rockchip-kernel-6.8.4\n"))
        self.assertEqual(deployed_extlinux.count("rockchip-kernel-6.12.108-eaidk310-zramfix1-test"), 1)
        self.assertFalse(any(self.target.rglob("*.tmp")))

    def test_wrong_root_source_is_rejected(self):
        before = self.file_snapshot(self.target)
        result = self.run_deploy(
            bundle_sha256=self.bundle_sha256,
            environment={"FAKE_ROOT_SOURCE": "/dev/mmcblk2p2"},
        )
        self.assert_rejected_without_changes(result, before)
        self.assertIn("root source", result.stderr)

    def test_mounted_emmc_is_rejected(self):
        before = self.file_snapshot(self.target)
        result = self.run_deploy(
            bundle_sha256=self.bundle_sha256,
            environment={"FAKE_EMMC_MOUNTS": "/dev/mmcblk2p1"},
        )
        self.assert_rejected_without_changes(result, before)
        self.assertIn("eMMC", result.stderr)

    def test_wrong_default_is_rejected_before_any_write(self):
        config = self.target / "boot/extlinux/extlinux.conf"
        config.write_text(
            config.read_text(encoding="utf-8").replace(
                "default rockchip-kernel-6.8.4",
                "default rockchip-kernel-6.12.108-eaidk310-zramfix1-test",
            ),
            encoding="utf-8",
        )
        before = self.file_snapshot(self.target)
        result = self.run_deploy(bundle_sha256=self.bundle_sha256)
        self.assert_rejected_without_changes(result, before)
        self.assertIn("default", result.stderr)

    def test_changed_stable_image_is_rejected(self):
        (self.target / "boot/Image").write_bytes(b"changed stable image\n")
        before = self.file_snapshot(self.target)
        result = self.run_deploy(bundle_sha256=self.bundle_sha256)
        self.assert_rejected_without_changes(result, before)
        self.assertIn("stable", result.stderr)

    def test_insufficient_space_is_rejected(self):
        before = self.file_snapshot(self.target)
        result = self.run_deploy(
            bundle_sha256=self.bundle_sha256,
            environment={"FAKE_AVAILABLE_BYTES": "1"},
        )
        self.assert_rejected_without_changes(result, before)
        self.assertIn("space", result.stderr)

    def test_conflicting_versioned_file_is_rejected(self):
        self._write(
            self.target, f"boot/Image-{RELEASE}", b"conflicting image\n"
        )
        before = self.file_snapshot(self.target)
        result = self.run_deploy(bundle_sha256=self.bundle_sha256)
        self.assert_rejected_without_changes(result, before)
        self.assertIn("conflict", result.stderr)

    def test_interrupted_temporary_copy_is_rejected(self):
        self._write(
            self.target,
            f"lib/modules/.{RELEASE}.20260904T120000Z.tmp/partial",
            b"partial\n",
        )
        before = self.file_snapshot(self.target)
        result = self.run_deploy(bundle_sha256=self.bundle_sha256)
        self.assert_rejected_without_changes(result, before)
        self.assertIn("temporary", result.stderr)

    def test_bad_bundle_hash_is_rejected(self):
        before = self.file_snapshot(self.target)
        result = self.run_deploy(bundle_sha256="0" * 64)
        self.assert_rejected_without_changes(result, before)
        self.assertIn("bundle SHA-256", result.stderr)

    def test_manifest_mismatch_is_rejected(self):
        (self.bundle / f"boot/Image-{RELEASE}").write_bytes(b"tampered bundle\n")
        before = self.file_snapshot(self.target)
        result = self.run_deploy(bundle_sha256=self.bundle_sha256)
        self.assert_rejected_without_changes(result, before)
        self.assertIn("manifest", result.stderr.lower())

    def test_apply_requires_bundle_hash_authorization(self):
        before = self.file_snapshot(self.target)
        result = self.run_deploy(apply=True)
        self.assert_rejected_without_changes(result, before)
        self.assertIn("--bundle-sha256", result.stderr)

    def test_root_prefix_is_rejected_outside_test_mode(self):
        before = self.file_snapshot(self.target)
        result = self.run_deploy(
            bundle_sha256=self.bundle_sha256, test_mode=False
        )
        self.assert_rejected_without_changes(result, before)
        self.assertIn("EAIDK310_TEST_MODE", result.stderr)

    def test_emmc_deployer_exists(self):
        self.assertTrue((SCRIPTS / "deploy-emmc.sh").is_file())

    def test_emmc_dry_run_reports_identity_without_changes(self):
        self.prepare_emmc_target()
        before = self.file_snapshot(self.target)
        result = self.run_emmc_deploy()

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertFalse(report["write"])
        self.assertEqual(report["target_model"], "HBD08G")
        self.assertEqual(report["root_uuid"], EMMC_ROOT_UUID)
        self.assertEqual(report["boot_uuid"], EMMC_BOOT_UUID)
        self.assertEqual(self.file_snapshot(self.target), before)

    def test_emmc_apply_replaces_coherent_boot_stack_and_saves_rollback(self):
        self.prepare_emmc_target()
        old_image = (self.target / "boot/Image").read_bytes()
        old_extlinux = (self.target / "boot/extlinux/extlinux.conf").read_bytes()
        expected_extlinux = hashlib.sha256(old_extlinux).hexdigest()

        result = self.run_emmc_deploy(
            apply=True, expected_extlinux_sha256=expected_extlinux
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            (self.target / "boot/Image").read_bytes(),
            (self.bundle / f"boot/Image-{RELEASE}").read_bytes(),
        )
        self.assertEqual(
            (self.target / "boot/uInitrd").read_bytes(),
            (self.bundle / f"boot/uInitrd-{RELEASE}").read_bytes(),
        )
        self.assertEqual(
            (self.target / "boot/dtb/rockchip/rk3328-eaidk-310.dtb").read_bytes(),
            (self.bundle / "boot/dtb/rockchip/rk3328-eaidk-310-6.12.108.dtb").read_bytes(),
        )
        self.assertEqual(
            (self.target / f"lib/modules/{RELEASE}/kernel/test.ko").read_bytes(),
            b"arm64 module\n",
        )
        rollback = self.target / "boot/rollback/6.8.4-pre-6.12.108"
        self.assertEqual((rollback / "Image").read_bytes(), old_image)
        self.assertEqual((rollback / "extlinux.conf").read_bytes(), old_extlinux)
        extlinux = (self.target / "boot/extlinux/extlinux.conf").read_text(encoding="utf-8")
        self.assertTrue(extlinux.startswith(f"default {EMMC_LABEL}\ntimeout 30\n"))
        self.assertEqual(extlinux.count(f"label {EMMC_LABEL}"), 1)
        self.assertIn(f"root=UUID={EMMC_ROOT_UUID}", extlinux)
        self.assertFalse(any(self.target.rglob("*.tmp")))

    def test_emmc_apply_requires_current_extlinux_hash(self):
        self.prepare_emmc_target()
        before = self.file_snapshot(self.target)
        result = self.run_emmc_deploy(apply=True)
        self.assert_rejected_without_changes(result, before)
        self.assertIn("expected-extlinux-sha256", result.stderr)

    def test_emmc_wrong_model_is_rejected(self):
        self.prepare_emmc_target()
        before = self.file_snapshot(self.target)
        result = self.run_emmc_deploy(environment={"FAKE_EMMC_MODEL": "OTHER"})
        self.assert_rejected_without_changes(result, before)
        self.assertIn("model", result.stderr.lower())

    def test_emmc_wrong_root_uuid_is_rejected(self):
        self.prepare_emmc_target()
        before = self.file_snapshot(self.target)
        result = self.run_emmc_deploy(environment={"FAKE_EMMC_ROOT_UUID": "wrong"})
        self.assert_rejected_without_changes(result, before)
        self.assertIn("UUID", result.stderr)


class ScriptContractTests(unittest.TestCase):
    def test_emmc_model_uses_the_mmc_sysfs_identity(self):
        text = (SCRIPTS / "deploy-emmc.sh").read_text(encoding="utf-8")
        self.assertIn("/sys/block/mmcblk2/device/name", text)

    def test_deploy_script_defaults_to_a_non_writing_run(self):
        path = SCRIPTS / "deploy-rescue-tf.sh"
        self.assertTrue(path.is_file(), "missing Task 7 deployment script")
        if os.name == "nt":
            drive = path.drive[0].lower()
            script_path = "/mnt/" + drive + path.as_posix()[2:]
            command = ["wsl", "-d", "Ubuntu-24.04", "--", "bash", script_path]
        else:
            command = ["bash", str(path)]
        result = subprocess.run(
            command,
            cwd=PROJECT_DIR,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertIn("write=false", result.stderr)

    def test_current_scripts_are_strict_and_have_no_raw_disk_write_surface(self):
        current_scripts = (
            "verify-inputs.sh",
            "install-board-inputs.sh",
            "build-kernel.sh",
        )
        future_scripts = (
            "build-initramfs-arm64.sh",
            "package-artifacts.sh",
            "verify-bundle.sh",
            "deploy-rescue-tf.sh",
        )

        for name in current_scripts:
            self.assertTrue((SCRIPTS / name).is_file(), f"missing required script: {name}")

        for path in [*(SCRIPTS / name for name in current_scripts),
                     *(SCRIPTS / name for name in future_scripts if (SCRIPTS / name).is_file())]:
            with self.subTest(script=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn("set -euo pipefail", text)
                self.assertIsNone(re.search(r"\bdd\s+.*of=/dev/", text))
                self.assertIsNone(re.search(r">\s*/dev/mmcblk2", text))
                self.assertNotIn("/mnt/d/Project/EAIDK310", text)

    def test_build_contract_is_out_of_tree_release_locked_and_stages_modules(self):
        text = (SCRIPTS / "build-kernel.sh").read_text(encoding="utf-8")

        self.assertIn('O="$BUILD_DIR"', text)
        self.assertIn("6.12.108-eaidk310-zramfix1", text)
        self.assertIn("rk3328-eaidk-310.dtb", text)
        self.assertIn("modules_install", text)
        self.assertIn('INSTALL_MOD_PATH="$STAGE_ROOT"', text)

    def test_cross_built_module_checks_select_the_target_release_and_real_module_name(self):
        text = (SCRIPTS / "build-kernel.sh").read_text(encoding="utf-8")

        self.assertIn('modinfo -b "$STAGE_ROOT" -k "$EXPECTED_RELEASE"', text)
        self.assertIn("dwmac-rk", text)
        self.assertNotIn("dwmac-rockchip", text)

    def test_build_identity_is_fully_fixed_for_reproducible_outputs(self):
        text = (SCRIPTS / "build-kernel.sh").read_text(encoding="utf-8")

        self.assertIn("KBUILD_BUILD_VERSION=1", text)
        self.assertIn("KBUILD_BUILD_USER=Fog", text)
        self.assertIn("KBUILD_BUILD_HOST=eaidk310-wsl", text)
        self.assertIn("KBUILD_BUILD_TIMESTAMP", text)

    def test_task6_scripts_expose_a_non_mutating_help_interface(self):
        for name in (
            "build-initramfs-arm64.sh",
            "package-artifacts.sh",
            "verify-bundle.sh",
        ):
            path = SCRIPTS / name
            with self.subTest(script=name):
                self.assertTrue(path.is_file(), f"missing required script: {name}")
                if os.name == "nt":
                    drive = path.drive[0].lower()
                    wsl_path = "/mnt/" + drive + path.as_posix()[2:]
                    command = [
                        "wsl", "-d", "Ubuntu-24.04", "--", "bash", wsl_path, "--help"
                    ]
                else:
                    command = ["bash", str(path), "--help"]
                result = subprocess.run(
                    command,
                    cwd=PROJECT_DIR,
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("usage:", result.stdout.lower())

    def test_initramfs_policy_is_exact_and_has_no_fake_builtin_modules(self):
        initramfs_dir = PROJECT_DIR / "initramfs"
        policy_path = initramfs_dir / "initramfs.conf"
        modules_path = initramfs_dir / "modules"
        self.assertTrue(policy_path.is_file(), "missing initramfs policy")
        self.assertTrue(modules_path.is_file(), "missing explicit module policy")
        policy = policy_path.read_text(encoding="utf-8")
        self.assertEqual(
            policy,
            "MODULES=most\n"
            "BUSYBOX=auto\n"
            "COMPRESS=zstd\n"
            "DEVICE=\n"
            "NFSROOT=auto\n"
            "RUNSIZE=10%\n"
            "FSTYPE=auto\n",
        )
        explicit_modules = [
            line.strip()
            for line in modules_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertEqual(explicit_modules, [])

    def test_arm64_initramfs_builder_uses_the_locked_foreign_chroot_flow(self):
        text = (SCRIPTS / "build-initramfs-arm64.sh").read_text(encoding="utf-8")

        for required in (
            "debootstrap",
            "--arch=arm64",
            "trixie",
            "qemu-aarch64-static",
            "/proc/sys/fs/binfmt_misc/qemu-aarch64",
            "mkinitramfs",
            "6.12.108-eaidk310-zramfix1",
        ):
            with self.subTest(required=required):
                self.assertIn(required, text)
        self.assertIn('chroot "$CHROOT_DIR" env SOURCE_DATE_EPOCH=', text)

    def test_initramfs_preflight_accepts_the_verified_task5_outputs(self):
        if os.name != "nt":
            self.skipTest("Windows WSL integration test")
        path = SCRIPTS / "build-initramfs-arm64.sh"
        drive = path.drive[0].lower()
        wsl_path = "/mnt/" + drive + path.as_posix()[2:]
        result = subprocess.run(
            [
                "wsl", "-d", "Ubuntu-24.04", "-u", "root", "--",
                "bash", wsl_path, "--preflight-only",
            ],
            cwd=PROJECT_DIR,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("INITRAMFS_PREFLIGHT_GATE=PASS", result.stdout)

    def test_current_initramfs_chroot_has_the_complete_policy_context(self):
        if os.name != "nt":
            self.skipTest("Windows WSL integration test")
        chroot = "/home/Fog/eaidk310-kernel/chroot-trixie-arm64-zramfix1"
        command = (
            "set -e; "
            f"test -d {chroot}/etc/eaidk310-initramfs/scripts; "
            f"test -d {chroot}/etc/eaidk310-initramfs/hooks; "
            f"test -d {chroot}/etc/eaidk310-initramfs/conf.d; "
            f"cmp -s /home/Fog/eaidk310-kernel/build-zramfix1/.config "
            f"{chroot}/boot/config-6.12.108-eaidk310-zramfix1"
        )
        result = subprocess.run(
            ["wsl", "-d", "Ubuntu-24.04", "-u", "root", "--", "bash", "-lc", command],
            cwd=PROJECT_DIR,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)

    def test_current_uinitrd_header_uses_the_fixed_source_date_epoch(self):
        if os.name != "nt":
            self.skipTest("Windows WSL integration test")
        uinitrd = "/home/Fog/eaidk310-kernel/initramfs-zramfix1/uInitrd-6.12.108-eaidk310-zramfix1"
        result = subprocess.run(
            ["wsl", "-d", "Ubuntu-24.04", "--", "cat", uinitrd],
            cwd=PROJECT_DIR,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr.decode(errors="replace"))
        self.assertGreaterEqual(len(result.stdout), 12)
        timestamp = struct.unpack(">I", result.stdout[8:12])[0]
        self.assertEqual(timestamp, 1788352262)

    def test_uinitrd_metadata_name_fits_the_legacy_uimage_header(self):
        build_script = (SCRIPTS / "build-initramfs-arm64.sh").read_text(encoding="utf-8")
        verify_script = (SCRIPTS / "verify-bundle.sh").read_text(encoding="utf-8")
        expected_name = "initramfs-6.12.108-zramfix1"

        self.assertLessEqual(len(expected_name.encode("ascii")), 32)
        self.assertIn(f'UIMAGE_NAME="{expected_name}"', build_script)
        self.assertIn('-n "$UIMAGE_NAME"', build_script)
        self.assertIn(f"Image Name:   {expected_name}", verify_script)

    def test_package_dry_run_reports_only_versioned_boot_targets(self):
        if os.name != "nt":
            self.skipTest("Windows WSL integration test")
        path = SCRIPTS / "package-artifacts.sh"
        drive = path.drive[0].lower()
        wsl_path = "/mnt/" + drive + path.as_posix()[2:]
        result = subprocess.run(
            ["wsl", "-d", "Ubuntu-24.04", "--", "bash", wsl_path, "--dry-run"],
            cwd=PROJECT_DIR,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        targets = {
            line.removeprefix("target=")
            for line in result.stdout.splitlines()
            if line.startswith("target=")
        }
        self.assertEqual(
            targets,
            {
                "boot/Image-6.12.108-eaidk310-zramfix1",
                "boot/uInitrd-6.12.108-eaidk310-zramfix1",
                "boot/dtb/rockchip/rk3328-eaidk-310-6.12.108.dtb",
                "root/lib/modules/6.12.108-eaidk310-zramfix1/",
            },
        )
        self.assertIn("write=false", result.stdout)

    def test_bundle_verifier_accepts_the_packaged_task6_artifacts(self):
        if os.name != "nt":
            self.skipTest("Windows WSL integration test")
        path = SCRIPTS / "verify-bundle.sh"
        drive = path.drive[0].lower()
        wsl_path = "/mnt/" + drive + path.as_posix()[2:]
        result = subprocess.run(
            [
                "wsl", "-d", "Ubuntu-24.04", "--", "bash", wsl_path,
                "--bundle-root",
                "/home/Fog/eaidk310-kernel/artifacts/eaidk310-linux-6.12.108-eaidk310-zramfix1",
                "--archive",
                "/mnt/d/Project/EAIDK310/kernel-6.12.108-zramfix1/artifacts/eaidk310-linux-6.12.108-eaidk310-zramfix1.tar.zst",
            ],
            cwd=PROJECT_DIR,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("BUNDLE_GATE=PASS", result.stdout)

    def test_packaged_deployer_defaults_to_a_non_writing_failure_without_inputs(self):
        if os.name != "nt":
            self.skipTest("Windows WSL integration test")
        placeholder = (
            "/home/Fog/eaidk310-kernel/artifacts/"
            "eaidk310-linux-6.12.108-eaidk310-zramfix1/deploy/deploy-rescue-tf.sh"
        )
        result = subprocess.run(
            ["wsl", "-d", "Ubuntu-24.04", "--", "bash", placeholder],
            cwd=PROJECT_DIR,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("write=false", result.stderr)
        self.assertIn("DEPLOY_GATE=FAIL", result.stderr)


if __name__ == "__main__":
    unittest.main()
