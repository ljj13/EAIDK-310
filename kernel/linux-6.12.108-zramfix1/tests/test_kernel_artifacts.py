import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile
import unittest


PROJECT_DIR = pathlib.Path(__file__).resolve().parents[1]
CLI = PROJECT_DIR / "tools" / "kernel_artifacts.py"
LOCK = PROJECT_DIR / "source-lock.json"
PORT_MATRIX = PROJECT_DIR / "analysis" / "board-port-matrix.json"

REQUIRED_SUBSYSTEMS = {
    "identity",
    "aliases",
    "chosen",
    "cpu-supply",
    "gpu-supply",
    "rk805",
    "regulators",
    "io-domains",
    "sdmmc",
    "sdio",
    "emmc",
    "gmac2io",
    "integrated-phy",
    "usb2",
    "usb3",
    "hdmi",
    "drm",
    "audio",
    "thermal",
    "gpio-leds",
    "uart2",
    "bluetooth",
    "i2c",
    "spi",
    "pwm",
    "known-failures",
}


class KernelArtifactCliTests(unittest.TestCase):
    def run_cli(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(CLI), *args],
            cwd=PROJECT_DIR,
            text=True,
            capture_output=True,
            check=False,
        )

    def create_bundle_fixture(self, root: pathlib.Path) -> pathlib.Path:
        files = {
            "boot/Image-6.12.108-eaidk310-zramfix1": b"arm64 image\n",
            "boot/uInitrd-6.12.108-eaidk310-zramfix1": b"uboot initrd\n",
            "boot/dtb/rockchip/rk3328-eaidk-310-6.12.108.dtb": b"dtb\n",
            "root/lib/modules/6.12.108-eaidk310-zramfix1/modules.dep": b"kernel/test.ko:\n",
            "root/lib/modules/6.12.108-eaidk310-zramfix1/modules.builtin": b"kernel/builtin.ko\n",
            "root/lib/modules/6.12.108-eaidk310-zramfix1/modules.order": b"kernel/test.ko\n",
            "root/lib/modules/6.12.108-eaidk310-zramfix1/kernel/test.ko": b"module\n",
            "deploy/deploy-rescue-tf.sh": b"#!/bin/sh\nexit 1\n",
            "deploy/kernel_artifacts.py": b"#!/usr/bin/env python3\n",
            "deploy/stable-baseline-sha256.txt": b"0" * 64 + b"  boot/Image\n",
            "deploy/extlinux-entry.conf": b"versioned extlinux entry\n",
            "verification-report.json": b'{"gate":"PASS"}\n',
        }
        for relative_path, content in files.items():
            path = root.joinpath(*relative_path.split("/"))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        metadata = root / "metadata.json"
        metadata.write_text(
            json.dumps({"kernel_release": "6.12.108-eaidk310-zramfix1"}),
            encoding="utf-8",
        )
        manifest = root / "manifest.json"
        result = self.run_cli(
            "manifest",
            "--root",
            str(root),
            "--output",
            str(manifest),
            "--metadata-json",
            str(metadata),
            *[item for path in files for item in ("--file", path)],
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        metadata.unlink()
        return manifest

    def test_render_extlinux_emits_the_exact_non_default_test_entry(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = pathlib.Path(temporary_directory) / "entry.conf"
            append_line = (
                "root=UUID=bd2a6dbf-c55f-4d5e-8738-797e581ee7c9 rootwait "
                "rootfstype=ext4 net.ifnames=0 earlycon console=ttyS2,1500000n8 "
                "console=tty1 consoleblank=0 loglevel=4"
            )
            result = self.run_cli(
                "render-extlinux",
                "--append-line",
                append_line,
                "--output",
                str(output),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                "    label rockchip-kernel-6.12.108-eaidk310-zramfix1-test\n"
                "    LINUX  /Image-6.12.108-eaidk310-zramfix1\n"
                "    FDT    /dtb/rockchip/rk3328-eaidk-310-6.12.108.dtb\n"
                "    INITRD /uInitrd-6.12.108-eaidk310-zramfix1\n"
                f"    APPEND {append_line}\n",
            )

    def test_render_extlinux_rejects_an_embedded_second_directive(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output = pathlib.Path(temporary_directory) / "entry.conf"
            result = self.run_cli(
                "render-extlinux",
                "--append-line",
                "root=UUID=valid\n    DEFAULT malicious",
                "--output",
                str(output),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("single line", result.stderr)
            self.assertFalse(output.exists())

    def test_bundle_layout_requires_manifest_completeness_and_versioned_paths(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            manifest = self.create_bundle_fixture(root)

            result = self.run_cli(
                "verify-bundle-layout",
                "--root",
                str(root),
                "--manifest",
                str(manifest),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["gate"], "PASS")
            self.assertEqual(report["kernel_release"], "6.12.108-eaidk310-zramfix1")
            self.assertEqual(report["manifest_files"], 12)

            extra = root / "untracked"
            extra.write_bytes(b"not in manifest\n")
            rejected = self.run_cli(
                "verify-bundle-layout",
                "--root",
                str(root),
                "--manifest",
                str(manifest),
            )
            self.assertNotEqual(rejected.returncode, 0)
            self.assertIn("unmanifested", rejected.stderr)

    def test_bundle_layout_rejects_a_stable_boot_path(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            manifest_path = self.create_bundle_fixture(root)
            stable_image = root / "boot" / "Image"
            stable_image.write_bytes(b"forbidden stable image\n")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"].append(
                {
                    "path": "boot/Image",
                    "sha256": hashlib.sha256(stable_image.read_bytes()).hexdigest(),
                    "size": stable_image.stat().st_size,
                }
            )
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            result = self.run_cli(
                "verify-bundle-layout",
                "--root",
                str(root),
                "--manifest",
                str(manifest_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("forbidden stable boot path", result.stderr)

    def test_bundle_layout_requires_the_deployment_validator(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            manifest_path = self.create_bundle_fixture(root)
            validator = root / "deploy" / "kernel_artifacts.py"
            validator.unlink()
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"] = [
                entry
                for entry in manifest["files"]
                if entry["path"] != "deploy/kernel_artifacts.py"
            ]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            result = self.run_cli(
                "verify-bundle-layout",
                "--root",
                str(root),
                "--manifest",
                str(manifest_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("deploy/kernel_artifacts.py", result.stderr)

    def test_bundle_layout_requires_the_stable_baseline_lock(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            manifest_path = self.create_bundle_fixture(root)
            baseline_lock = root / "deploy" / "stable-baseline-sha256.txt"
            baseline_lock.unlink()
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["files"] = [
                entry
                for entry in manifest["files"]
                if entry["path"] != "deploy/stable-baseline-sha256.txt"
            ]
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            result = self.run_cli(
                "verify-bundle-layout",
                "--root",
                str(root),
                "--manifest",
                str(manifest_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("deploy/stable-baseline-sha256.txt", result.stderr)

    def test_verify_extlinux_deployment_requires_stable_default_and_real_files(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            boot = pathlib.Path(temporary_directory) / "boot"
            for relative_path in (
                "Image",
                "uInitrd",
                "dtb/rockchip/rk3328-eaidk-310.dtb",
                "Image-6.12.108-eaidk310-zramfix1",
                "uInitrd-6.12.108-eaidk310-zramfix1",
                "dtb/rockchip/rk3328-eaidk-310-6.12.108.dtb",
            ):
                path = boot.joinpath(*relative_path.split("/"))
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"fixture\n")
            config = pathlib.Path(temporary_directory) / "extlinux.conf"
            config.write_text(
                "default rockchip-kernel-6.8.4\n"
                "label rockchip-kernel-6.8.4\n"
                "  linux /Image\n"
                "  fdt /dtb/rockchip/rk3328-eaidk-310.dtb\n"
                "  initrd /uInitrd\n"
                "label rockchip-kernel-6.12.108-eaidk310-zramfix1-test\n"
                "  linux /Image-6.12.108-eaidk310-zramfix1\n"
                "  fdt /dtb/rockchip/rk3328-eaidk-310-6.12.108.dtb\n"
                "  initrd /uInitrd-6.12.108-eaidk310-zramfix1\n",
                encoding="utf-8",
            )

            result = self.run_cli(
                "verify-extlinux-deployment",
                "--config",
                str(config),
                "--boot-root",
                str(boot),
                "--expected-default",
                "rockchip-kernel-6.8.4",
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                json.loads(result.stdout),
                {
                    "default": "rockchip-kernel-6.8.4",
                    "gate": "PASS",
                    "labels": [
                        "rockchip-kernel-6.8.4",
                        "rockchip-kernel-6.12.108-eaidk310-zramfix1-test",
                    ],
                    "referenced_files": 6,
                },
            )

            config.write_text(
                config.read_text(encoding="utf-8").replace(
                    "default rockchip-kernel-6.8.4",
                    "default rockchip-kernel-6.12.108-eaidk310-zramfix1-test",
                ),
                encoding="utf-8",
            )
            wrong_default = self.run_cli(
                "verify-extlinux-deployment",
                "--config",
                str(config),
                "--boot-root",
                str(boot),
                "--expected-default",
                "rockchip-kernel-6.8.4",
            )
            self.assertNotEqual(wrong_default.returncode, 0)
            self.assertIn("default", wrong_default.stderr)

    def test_early_boot_dependency_gate_accepts_builtin_and_modular_providers(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            module_dir = pathlib.Path(temporary_directory)
            (module_dir / "modules.builtin").write_text(
                "kernel/fs/ext4/ext4.ko\n"
                "kernel/drivers/pinctrl/pinctrl-rockchip.ko\n"
                "kernel/drivers/mmc/core/mmc_block.ko\n"
                "kernel/drivers/mmc/host/dw_mmc-rockchip.ko\n",
                encoding="utf-8",
            )
            regulator = "kernel/drivers/regulator/rk808-regulator.ko"
            (module_dir / "modules.dep").write_text(f"{regulator}:\n", encoding="utf-8")
            regulator_path = module_dir.joinpath(*regulator.split("/"))
            regulator_path.parent.mkdir(parents=True)
            regulator_path.write_bytes(b"module\n")

            result = self.run_cli(
                "verify-early-deps", "--module-dir", str(module_dir)
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["gate"], "PASS")
            self.assertEqual(report["providers"]["ext4"]["kind"], "builtin")
            self.assertEqual(report["providers"]["rk805-regulator"]["kind"], "module")

    def test_early_boot_dependency_gate_rejects_a_missing_mmc_host(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            module_dir = pathlib.Path(temporary_directory)
            (module_dir / "modules.builtin").write_text(
                "kernel/fs/ext4/ext4.ko\n"
                "kernel/drivers/pinctrl/pinctrl-rockchip.ko\n"
                "kernel/drivers/regulator/rk808-regulator.ko\n"
                "kernel/drivers/mmc/core/mmc_block.ko\n",
                encoding="utf-8",
            )
            (module_dir / "modules.dep").write_text("", encoding="utf-8")

            result = self.run_cli(
                "verify-early-deps", "--module-dir", str(module_dir)
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("mmc-host", result.stderr)

    def test_verify_lock_reports_the_pinned_release_and_signer(self):
        result = self.run_cli("verify-lock", str(LOCK))

        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(result.stdout)
        self.assertEqual(report["kernel_version"], "6.12.108")
        self.assertEqual(report["localversion"], "-eaidk310-zramfix1")
        self.assertEqual(report["expected_release"], "6.12.108-eaidk310-zramfix1")
        self.assertEqual(
            report["archive_sha256"],
            "e1d1ea200d22d55c9f5d5fae59e69bb3b494515705fc3390cd54231ee4f4baaf",
        )
        self.assertEqual(
            report["signer_fingerprint"],
            "647F28654894E3BD457199BE38DBBDC86092693E",
        )

    def test_input_report_verifies_locked_files_and_observed_build_inputs(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            baseline = root / "baseline" / "config"
            baseline.parent.mkdir()
            baseline.write_bytes(b"locked baseline\n")
            archive = root / "linux.tar.xz"
            archive.write_bytes(b"locked archive\n")
            lock = {
                "kernel_version": "6.12.108",
                "localversion": "-eaidk310-zramfix1",
                "expected_release": "6.12.108-eaidk310-zramfix1",
                "archive_url": "https://example.invalid/linux.tar.xz",
                "signature_url": "https://example.invalid/linux.tar.sign",
                "archive_sha256": "26664720fd9d0cde94d4af417b678e20162418c543fc24e15164c0ab5d4d8230",
                "signer_fingerprint": "647F28654894E3BD457199BE38DBBDC86092693E",
                "wsl_root": "/home/Fog/eaidk310-kernel",
                "baseline_files": {
                    "baseline/config": "2e04891c45258a95558b33b9a67948714dbf4151efd97353d552e772dc892432"
                },
            }
            lock_path = root / "source-lock.json"
            lock_path.write_text(json.dumps(lock), encoding="utf-8")
            observations = {
                "source_dir": "/home/Fog/eaidk310-kernel/src/linux-6.12.108",
                "build_dir": "/home/Fog/eaidk310-kernel/build-zramfix1",
                "kernel_version": "6.12.108",
                "gcc_target": "aarch64-linux-gnu",
                "validsig_fingerprint": "647F28654894E3BD457199BE38DBBDC86092693E",
                "tool_versions": {"gcc": "13.3.0", "make": "4.3", "gpg": "2.4.4"},
            }
            observations_path = root / "observations.json"
            observations_path.write_text(json.dumps(observations), encoding="utf-8")
            output = root / "input-gate.json"

            result = self.run_cli(
                "input-report",
                "--lock",
                str(lock_path),
                "--project-root",
                str(root),
                "--archive",
                str(archive),
                "--observations-json",
                str(observations_path),
                "--output",
                str(output),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(report["gate"], "PASS")
            self.assertEqual(report["kernel_release"], "6.12.108-eaidk310-zramfix1")
            self.assertEqual(report["baseline_files_verified"], 1)
            self.assertEqual(report["tool_versions"]["gcc"], "13.3.0")

    def test_input_report_rejects_a_source_outside_the_locked_wsl_root(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            baseline = root / "baseline"
            baseline.write_bytes(b"baseline\n")
            archive = root / "linux.tar.xz"
            archive.write_bytes(b"archive\n")
            lock = {
                "kernel_version": "6.12.108",
                "localversion": "-eaidk310-zramfix1",
                "expected_release": "6.12.108-eaidk310-zramfix1",
                "archive_url": "https://example.invalid/linux.tar.xz",
                "signature_url": "https://example.invalid/linux.tar.sign",
                "archive_sha256": hashlib.sha256(b"archive\n").hexdigest(),
                "signer_fingerprint": "647F28654894E3BD457199BE38DBBDC86092693E",
                "wsl_root": "/home/Fog/eaidk310-kernel",
                "baseline_files": {"baseline": hashlib.sha256(b"baseline\n").hexdigest()},
            }
            lock_path = root / "source-lock.json"
            lock_path.write_text(json.dumps(lock), encoding="utf-8")
            observations = {
                "source_dir": "/mnt/d/Project/EAIDK310/linux-6.12.108",
                "build_dir": "/home/Fog/eaidk310-kernel/build-zramfix1",
                "kernel_version": "6.12.108",
                "gcc_target": "aarch64-linux-gnu",
                "validsig_fingerprint": "647F28654894E3BD457199BE38DBBDC86092693E",
                "tool_versions": {"gcc": "13.3.0"},
            }
            observations_path = root / "observations.json"
            observations_path.write_text(json.dumps(observations), encoding="utf-8")

            result = self.run_cli(
                "input-report",
                "--lock", str(lock_path),
                "--project-root", str(root),
                "--archive", str(archive),
                "--observations-json", str(observations_path),
                "--output", str(root / "input-gate.json"),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("source_dir must remain below", result.stderr)

    def test_manifest_round_trip_reports_literal_file_hash_and_size(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            artifact = root / "boot" / "Image-6.12.108-eaidk310-zramfix1"
            artifact.parent.mkdir()
            artifact.write_bytes(b"eaidk310-image\n")
            metadata_path = root / "metadata.json"
            metadata_path.write_text(
                json.dumps({"kernel_release": "6.12.108-eaidk310-zramfix1"}),
                encoding="utf-8",
            )
            manifest_path = root / "manifest.json"

            create = self.run_cli(
                "manifest",
                "--root",
                str(root),
                "--output",
                str(manifest_path),
                "--metadata-json",
                str(metadata_path),
                "--file",
                "boot/Image-6.12.108-eaidk310-zramfix1",
            )
            self.assertEqual(create.returncode, 0, create.stderr)

            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], 1)
            self.assertEqual(manifest["metadata"]["kernel_release"], "6.12.108-eaidk310-zramfix1")
            self.assertEqual(
                manifest["files"],
                [
                    {
                        "path": "boot/Image-6.12.108-eaidk310-zramfix1",
                        "sha256": "2b3072e82f0f2bcf2114100a64a218d4c94384fd9bc529d3bc5fd9005bcfbee4",
                        "size": 15,
                    }
                ],
            )

            verify = self.run_cli(
                "verify-manifest",
                "--root",
                str(root),
                "--manifest",
                str(manifest_path),
            )
            self.assertEqual(verify.returncode, 0, verify.stderr)
            self.assertEqual(json.loads(verify.stdout)["verified_files"], 1)

    def test_verify_manifest_rejects_an_artifact_changed_after_creation(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            artifact = root / "Image"
            artifact.write_bytes(b"before")
            metadata_path = root / "metadata.json"
            metadata_path.write_text("{}\n", encoding="utf-8")
            manifest_path = root / "manifest.json"
            create = self.run_cli(
                "manifest",
                "--root",
                str(root),
                "--output",
                str(manifest_path),
                "--metadata-json",
                str(metadata_path),
                "--file",
                "Image",
            )
            self.assertEqual(create.returncode, 0, create.stderr)

            artifact.write_bytes(b"after!")
            verify = self.run_cli(
                "verify-manifest",
                "--root",
                str(root),
                "--manifest",
                str(manifest_path),
            )
            self.assertNotEqual(verify.returncode, 0)
            self.assertIn("SHA-256 mismatch", verify.stderr)

    def test_manifest_rejects_a_path_that_escapes_the_root(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            parent = pathlib.Path(temporary_directory)
            root = parent / "root"
            root.mkdir()
            (parent / "outside").write_bytes(b"outside")
            metadata_path = root / "metadata.json"
            metadata_path.write_text("{}\n", encoding="utf-8")
            manifest_path = root / "manifest.json"

            result = self.run_cli(
                "manifest",
                "--root",
                str(root),
                "--output",
                str(manifest_path),
                "--metadata-json",
                str(metadata_path),
                "--file",
                "../outside",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must not contain '..'", result.stderr)
            self.assertFalse(manifest_path.exists())

    def test_manifest_rejects_duplicate_paths(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            (root / "Image").write_bytes(b"image")
            metadata_path = root / "metadata.json"
            metadata_path.write_text("{}\n", encoding="utf-8")
            manifest_path = root / "manifest.json"

            result = self.run_cli(
                "manifest",
                "--root",
                str(root),
                "--output",
                str(manifest_path),
                "--metadata-json",
                str(metadata_path),
                "--file",
                "Image",
                "--file",
                "Image",
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("must be unique", result.stderr)
            self.assertFalse(manifest_path.exists())

    def test_board_port_matrix_resolves_every_required_subsystem(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            output_path = pathlib.Path(temporary_directory) / "matrix.md"
            result = self.run_cli(
                "render-port-matrix",
                "--input",
                str(PORT_MATRIX),
                "--output",
                str(output_path),
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            report = json.loads(result.stdout)
            self.assertEqual(report["entries"], len(REQUIRED_SUBSYSTEMS))
            self.assertEqual(set(report["subsystems"]), REQUIRED_SUBSYSTEMS)
            markdown = output_path.read_text(encoding="utf-8")
            self.assertNotIn("undecided", markdown.lower())
            for subsystem in REQUIRED_SUBSYSTEMS:
                self.assertIn(f"| {subsystem} |", markdown)

    def test_board_port_matrix_rejects_duplicate_subsystems(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            matrix = json.loads(PORT_MATRIX.read_text(encoding="utf-8"))
            matrix["entries"].append(dict(matrix["entries"][0]))
            input_path = root / "matrix.json"
            output_path = root / "matrix.md"
            input_path.write_text(json.dumps(matrix), encoding="utf-8")

            result = self.run_cli(
                "render-port-matrix",
                "--input",
                str(input_path),
                "--output",
                str(output_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("duplicate port matrix subsystem", result.stderr)
            self.assertFalse(output_path.exists())

    def test_board_port_matrix_rejects_an_invalid_disposition(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = pathlib.Path(temporary_directory)
            matrix = json.loads(PORT_MATRIX.read_text(encoding="utf-8"))
            matrix["entries"][0]["disposition"] = "migrate"
            input_path = root / "matrix.json"
            output_path = root / "matrix.md"
            input_path.write_text(json.dumps(matrix), encoding="utf-8")

            result = self.run_cli(
                "render-port-matrix",
                "--input",
                str(input_path),
                "--output",
                str(output_path),
            )

            self.assertNotEqual(result.returncode, 0)
            self.assertIn("invalid disposition", result.stderr)
            self.assertFalse(output_path.exists())

    def test_board_port_matrix_preserves_non_speculative_boundaries(self):
        matrix = json.loads(PORT_MATRIX.read_text(encoding="utf-8"))
        entries = {entry["subsystem"]: entry for entry in matrix["entries"]}

        self.assertEqual(entries["gmac2io"]["disposition"], "disable")
        self.assertEqual(entries["integrated-phy"]["disposition"], "port")
        self.assertEqual(entries["spi"]["disposition"], "disable")
        self.assertIn("no ST7789 child", entries["spi"]["properties"])
        self.assertIn("sdmmc_ext status = disabled", entries["sdio"]["properties"])
        wireless = json.dumps(entries["sdio"], sort_keys=True).lower()
        self.assertNotIn("ext_clock", wireless)
        self.assertNotIn("post-power-on-delay", wireless)
        self.assertEqual(
            entries["known-failures"]["disposition"], "omit-known-failure"
        )


if __name__ == "__main__":
    unittest.main()
