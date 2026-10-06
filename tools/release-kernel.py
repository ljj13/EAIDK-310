#!/usr/bin/env python3
"""EAIDK-310 reproducible kernel release pipeline.

One clean clone plus one version number produces a verifiable release
candidate bundle ending in READY_FOR_BOARD_TRIAL:

    ./tools/release-kernel.sh 6.18.54

Stages (state machine, persisted in the per-run workspace):

    INIT -> SOURCE_VERIFIED -> MATERIALIZED -> PATCHED -> CONFIG_VERIFIED
         -> BUILT -> INITRAMFS_READY -> PACKAGED -> VERIFIED -> TESTED
         -> READY_FOR_BOARD_TRIAL

Isolation contract (docs/P7-BUILD-PIPELINE-AUDIT.md):

* cache/   shared and read-only during a run (downloads, gnupg, rootfs base)
* work/<release>-<run-id>/  ALL mutable state of exactly one build
* output/<release>/         READY_FOR_BOARD_TRIAL deliverables
* the git worktree is never written

Determinism contract (must match the reference v2026.10.06 build):

* kernel build: SOURCE_DATE_EPOCH = tarball Makefile mtime,
  KBUILD_BUILD_VERSION=1, KBUILD_BUILD_USER=Fog, KBUILD_BUILD_HOST=eaidk310-wsl
* initramfs, mkimage and the bundle tar: fixed epoch 1788352262
* bundle tar: --sort=name --mtime=@<epoch> --owner=0 --group=0 --numeric-owner
  | zstd -19 -T0

Board trust stays separate: this tool stops at READY_FOR_BOARD_TRIAL.
OTA install/arm/promotion and GitHub publishing are explicit human steps.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shlex
import shutil
import stat
import struct
import subprocess
import sys
import time
import urllib.request
import uuid
import fnmatch
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = 1
BUNDLE_TAR_EPOCH = 1788352262  # initramfs/mkimage/bundle-tar constant of every release pipeline
STAGES = [
    "INIT",
    "SOURCE_VERIFIED",
    "MATERIALIZED",
    "PATCHED",
    "CONFIG_VERIFIED",
    "BUILT",
    "INITRAMFS_READY",
    "PACKAGED",
    "VERIFIED",
    "TESTED",
    "READY_FOR_BOARD_TRIAL",
]
REQUIRED_TOOLS = (
    "make", "python3", "tar", "zstd", "sha256sum", "file", "rsync",
    "patch", "gpg", "depmod", "modinfo", "mkimage", "dumpimage", "fdtget",
)
INITRAMFS_HOST_TOOLS = ("debootstrap", "chroot", "mkinitramfs", "unmkinitramfs", "qemu-aarch64-static")
LINUX_DTS_PACKAGE_PATH = "arch/arm64/boot/dts/rockchip/rk3328-eaidk-310.dts"
PATCHED_TREE_FILES = (
    "Makefile",
    LINUX_DTS_PACKAGE_PATH,
    "arch/arm64/boot/dts/rockchip/Makefile",
    "Documentation/devicetree/bindings/arm/rockchip.yaml",
)
DRVMOUNT_PREFIXES = ("/mnt/", "/media/", "/windows/")


class Fail(Exception):
    """A gate failed; the stage is recorded and the run stops."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def tree_digest(root: Path, relative_files: list[str]) -> str:
    lines = []
    for rel in sorted(relative_files):
        lines.append(f"{sha256_file(root / rel)}  {rel}\n")
    return hashlib.sha256("".join(lines).encode()).hexdigest()


def modules_tree_digest(module_dir: Path) -> str:
    lines = []
    for path in sorted(p for p in module_dir.rglob("*") if p.is_file()):
        rel = path.relative_to(module_dir).as_posix()
        lines.append(f"{sha256_file(path)}  {rel}\n")
    return hashlib.sha256("".join(lines).encode()).hexdigest()


class Engine:
    def __init__(self, args: argparse.Namespace) -> None:
        self.repo = Path(__file__).resolve().parents[1]
        self.version = args.version
        self.release_dir = self.repo / "kernel" / f"linux-{self.version}-zramfix1"
        if not self.release_dir.is_dir():
            raise Fail(f"unknown kernel release directory: {self.release_dir}")
        meta_path = self.release_dir / "release-metadata.json"
        if not meta_path.is_file():
            raise Fail(f"missing declarative release metadata: {meta_path}")
        self.meta = json.loads(meta_path.read_text(encoding="utf-8"))
        self.release = self.meta["kernel_release"]
        if platform.system() != "Linux":
            raise Fail(
                "the release pipeline must run on Linux; from Windows use "
                "`wsl.exe -u root -e bash -c \"cd <repo> && ./tools/release-kernel.sh <version>\"`"
            )
        self.work_root = Path(args.work_root or os.environ.get("EAIDK_WORK_ROOT")
                              or Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
                              / "eaidk310" / "release").resolve()
        self.cache_root = Path(args.cache_root or os.environ.get("EAIDK_CACHE_ROOT")
                               or self.work_root / "cache").resolve()
        self.output_root = Path(args.output_dir or self.work_root / "output").resolve()
        for label, root in (("work", self.work_root), ("cache", self.cache_root),
                            ("output", self.output_root)):
            for prefix in DRVMOUNT_PREFIXES:
                if str(root).startswith(prefix):
                    raise Fail(
                        f"{label}-root {root} is on a DrvFS/9p mount ({prefix}...); "
                        "kernel builds must live on a Linux filesystem (WSL ext4)"
                    )
        self.jobs = args.jobs or os.cpu_count() or 1
        self.run_dir: Path | None = None
        self.state: dict = {}
        self.logs: dict[str, object] = {}

    # ------------------------------------------------------------------
    # run workspace + state machine
    # ------------------------------------------------------------------

    def run_paths(self) -> dict[str, Path]:
        base = self.run_dir
        return {
            "source": base / "source",
            "build": base / "build",
            "modules": base / "modules",
            "rootfs": base / "rootfs",
            "chroot": base / "rootfs" / "chroot",
            "initramfs": base / "initramfs",
            "bundle": base / "bundle",
            "verify": base / "verify",
            "logs": base / "logs",
        }

    def log_path(self, stage: str) -> Path:
        return self.run_paths()["logs"] / f"{stage.lower()}.log"

    def run(self, cmd: list[str], stage: str, cwd: Path | None = None,
            env: dict[str, str] | None = None, check: bool = True,
            capture: bool = False) -> subprocess.CompletedProcess:
        log_file = self.log_path(stage)
        log_file.parent.mkdir(parents=True, exist_ok=True)
        merged_env = dict(os.environ)
        if env:
            merged_env.update(env)
        with log_file.open("ab") as handle:
            handle.write(f"\n+ {' '.join(cmd)}\n".encode())
            handle.flush()
            proc = subprocess.run(
                cmd, cwd=str(cwd) if cwd else None, env=merged_env,
                stdout=subprocess.PIPE if capture else handle,
                stderr=subprocess.STDOUT, text=capture,
            )
        if check and proc.returncode != 0:
            tail = ""
            if capture:
                tail = (proc.stdout or "")[-2000:]
            else:
                tail = log_file.read_text(errors="replace")[-2000:]
            raise Fail(f"command failed (exit {proc.returncode}) in stage {stage}: "
                       f"{' '.join(cmd)}\n--- log tail ---\n{tail}")
        return proc

    def tool(self, name: str) -> str:
        path = shutil.which(name)
        if path is None:
            raise Fail(f"required tool is missing: {name}")
        return path

    def probe_tool_versions(self) -> dict[str, str]:
        def out(cmd: list[str]) -> str:
            try:
                return subprocess.run(cmd, capture_output=True, text=True).stdout.strip()
            except OSError:
                return "unknown"

        return {
            "gcc": out(["aarch64-linux-gnu-gcc", "-dumpfullversion", "-dumpversion"]),
            "gcc_target": out(["aarch64-linux-gnu-gcc", "-dumpmachine"]),
            "binutils": out(["aarch64-linux-gnu-ld", "--version"]).splitlines()[0] if shutil.which("aarch64-linux-gnu-ld") else "unknown",
            "make": out(["make", "--version"]).splitlines()[0],
            "python": out(["python3", "--version"]),
            "bash": out(["bash", "--version"]).splitlines()[0],
            "tar": out(["tar", "--version"]).splitlines()[0],
            "zstd": out(["zstd", "--version"]).strip("*"),
            "depmod": out(["depmod", "--version"]).splitlines()[0],
            "dtc": out(["dtc", "--version"]),
            "mkimage": out(["mkimage", "--version"]).splitlines()[0],
            "file": out(["file", "--version"]).splitlines()[0],
            "gpg": out(["gpg", "--version"]).splitlines()[0],
        }

    def load_state(self) -> None:
        path = self.run_dir / "state.json"
        self.state = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}

    def save_state(self) -> None:
        self.state["schema"] = SCHEMA_VERSION
        self.state["release"] = self.release
        self.state["run_id"] = self.run_id
        self.state["updated"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        (self.run_dir / "state.json").write_text(
            json.dumps(self.state, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def complete_stage(self, stage: str, records: dict, artifacts: dict[str, Path] | None = None) -> None:
        entry = {
            "status": "PASS",
            "ended": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "records": records,
        }
        if artifacts:
            entry["artifacts"] = {
                name: {"path": str(path), "sha256": sha256_file(path)}
                for name, path in artifacts.items()
            }
        stages = self.state.setdefault("stages", {})
        prev = stages.get(stage, {})
        entry["started"] = prev.get("started")
        if entry["started"] is None:
            entry.pop("started")
        stages[stage] = entry
        self.state["stage"] = stage
        self.state["status"] = "RUNNING"
        self.save_state()
        print(f"[{self.release}] {stage}=PASS")

    def require_stage(self, *stages: str) -> None:
        done = self.state.get("stages", {})
        for stage in stages:
            if stage not in done:
                raise Fail(f"stage {stage} not completed in this run; "
                           f"run --prepare first or use --resume (state={self.run_dir}/state.json)")

    # ------------------------------------------------------------------
    # stages
    # ------------------------------------------------------------------

    def stage_init(self) -> None:
        p = self.run_paths()
        for key in ("source", "build", "modules", "rootfs", "initramfs", "bundle", "verify", "logs"):
            p[key].mkdir(parents=True, exist_ok=True)
        self.state.setdefault("created", datetime.now(timezone.utc).isoformat(timespec="seconds"))
        self.complete_stage("INIT", {
            "work_root": str(self.work_root),
            "cache_root": str(self.cache_root),
            "output_root": str(self.output_root),
        })

    def stage_source(self) -> None:
        lock_path = self.release_dir / "source-lock.json"
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
        self.tool("python3")
        self.run(["python3", str(self.release_dir / "tools" / "kernel_artifacts.py"),
                  "verify-lock", str(lock_path)], stage="source")
        archive_url = lock["archive_url"]
        signature_url = lock["signature_url"]
        expected_sha = lock["archive_sha256"]
        downloads = self.cache_root / "downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        archive = downloads / Path(archive_url).name
        signature = downloads / Path(signature_url).name
        if archive.is_file() and sha256_file(archive) == expected_sha:
            print(f"[{self.release}] source archive cache hit: {archive.name}")
        else:
            print(f"[{self.release}] downloading {archive_url}")
            self._download(archive_url, archive)
            actual = sha256_file(archive)
            if actual != expected_sha:
                raise Fail(f"downloaded archive SHA256 mismatch: {actual} != {expected_sha}")
        if not signature.is_file():
            self._download(signature_url, signature)
        keyring = self.cache_root / "gnupg"
        keyring.mkdir(parents=True, exist_ok=True)
        os.chmod(keyring, stat.S_IRWXU)
        fingerprint = lock["signer_fingerprint"]
        listed = self.run(["gpg", "--homedir", str(keyring), "--list-keys", fingerprint],
                          stage="source", check=False, capture=True)
        if listed.returncode != 0:
            print(f"[{self.release}] fetching kernel.org release key {fingerprint}")
            self.run(["gpg", "--homedir", str(keyring), "--batch",
                      "--keyserver", "hkps://keyserver.ubuntu.com",
                      "--recv-keys", fingerprint], stage="source")
        # kernel.org signs the UNCOMPRESSED tar: stream it through xz into gpg
        verify = self.run(["bash", "-o", "pipefail", "-c",
                           f"xz -dc {shlex.quote(str(archive))} | "
                           f"gpg --homedir {shlex.quote(str(keyring))} --batch "
                           f"--status-fd=1 --verify {shlex.quote(str(signature))} -"],
                          stage="source", check=False, capture=True)
        validsig = ""
        for line in (verify.stdout or "").splitlines():
            if line.startswith("[GNUPG:] VALIDSIG "):
                validsig = line.split()[2]
        if verify.returncode != 0 or validsig != fingerprint:
            raise Fail(f"GPG verification failed (validsig={validsig or 'none'})")
        self.complete_stage("SOURCE_VERIFIED", {
            "archive": archive.name,
            "archive_sha256": expected_sha,
            "signer_fingerprint": fingerprint,
        }, artifacts={"archive": archive})

    def _download(self, url: str, target: Path) -> None:
        tmp = target.with_suffix(target.suffix + ".part")
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=120) as response, tmp.open("wb") as handle:
                    shutil.copyfileobj(response, handle)
                tmp.replace(target)
                return
            except OSError as exc:
                if attempt == 2:
                    raise Fail(f"download failed after 3 attempts: {url}: {exc}")
                time.sleep(5 * (attempt + 1))

    def stage_materialize(self) -> None:
        p = self.run_paths()
        archive = Path(self.state["stages"]["SOURCE_VERIFIED"]["artifacts"]["archive"]["path"])
        source_parent = p["source"]
        source_parent.mkdir(parents=True, exist_ok=True)
        self.run(["tar", "-xJf", str(archive), "-C", str(source_parent)], stage="materialize")
        trees = [d for d in source_parent.iterdir() if d.is_dir()]
        if len(trees) != 1:
            raise Fail(f"archive did not contain exactly one source tree: {trees}")
        source = trees[0]
        (source_parent / "linux").symlink_to(source.name)
        kernel_version = self.run(
            ["make", "-s", "-C", str(source), "kernelversion"],
            stage="materialize", capture=True).stdout.strip()
        expected = self.meta["kernel_version"]
        if kernel_version != expected:
            raise Fail(f"kernel version mismatch: {kernel_version} != {expected}")
        makefile_mtime = (source / "Makefile").stat().st_mtime
        epoch = int(makefile_mtime)
        self.complete_stage("MATERIALIZED", {
            "source_dir": source.name,
            "kernel_version": kernel_version,
            "source_date_epoch": epoch,
        }, artifacts={"source_makefile": source / "Makefile"})

    def stage_patch(self) -> None:
        p = self.run_paths()
        source = p["source"] / "linux"
        board_dts = self.release_dir / "dts" / "rk3328-eaidk-310.dts"
        patch = next(self.release_dir.glob("patches/0001-*.patch"))
        destination = source / LINUX_DTS_PACKAGE_PATH
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(board_dts, destination)
        dry = self.run(["patch", "--batch", "--forward", "--dry-run", "-p1",
                        "-d", str(source), "-i", str(patch)], stage="patch", check=False)
        reverse = self.run(["patch", "--batch", "--reverse", "--dry-run", "-p1",
                            "-d", str(source), "-i", str(patch)], stage="patch", check=False)
        if dry.returncode == 0:
            self.run(["patch", "--batch", "--forward", "-p1", "-d", str(source),
                      "-i", str(patch)], stage="patch")
        elif reverse.returncode == 0:
            print(f"[{self.release}] registration patch already applied")
        else:
            raise Fail("registration patch is neither cleanly applicable nor already applied")
        if sha256_file(destination) != sha256_file(board_dts):
            raise Fail("installed board DTS differs from the maintained source")
        registered = "dtb-$(CONFIG_ARCH_ROCKCHIP) += " + self.meta["board_dtb"]
        makefile = (source / "arch/arm64/boot/dts/rockchip/Makefile").read_text(encoding="utf-8")
        if registered not in makefile:
            raise Fail("DTB Makefile registration is missing after patching")
        binding = (source / "Documentation/devicetree/bindings/arm/rockchip.yaml").read_text(encoding="utf-8")
        if "const: openailab,eaidk-310" not in binding:
            raise Fail("root compatible registration is missing after patching")
        digest = tree_digest(source, list(PATCHED_TREE_FILES))
        self.complete_stage("PATCHED", {"patched_tree_digest": digest, "patch": patch.name})

    def stage_config(self) -> None:
        p = self.run_paths()
        source = p["source"] / "linux"
        build = p["build"]
        build.mkdir(parents=True, exist_ok=True)
        seeds = sorted((self.release_dir / "config").glob("eaidk310-*.config"))
        if len(seeds) != 1:
            raise Fail(f"expected exactly one seed config in {self.release_dir}/config/, "
                       f"found {[s.name for s in seeds]}")
        seed = seeds[0]
        shutil.copyfile(seed, build / ".config")
        ka = self.release_dir / "tools" / "kernel_artifacts.py"
        env = {"ARCH": "arm64", "CROSS_COMPILE": "aarch64-linux-gnu-",
               "SOURCE_DATE_EPOCH": str(self.state["stages"]["MATERIALIZED"]["records"]["source_date_epoch"]),
               "KBUILD_BUILD_VERSION": "1", "KBUILD_BUILD_USER": "Fog",
               "KBUILD_BUILD_HOST": "eaidk310-wsl"}
        self.run(["python3", str(ka), "check-config", "--config", str(build / ".config"),
                  "--mode", "seed"], stage="config")
        self.run(["make", "-s", "-C", str(source), f"O={build}", "olddefconfig"], stage="config", env=env)
        self.run(["make", "-s", "-C", str(source), f"O={build}", "syncconfig"], stage="config", env=env)
        self.run(["python3", str(ka), "check-config", "--config", str(build / ".config")], stage="config")
        self.run(["python3", "-m", "unittest", "discover", "-s", str(self.release_dir / "tests"),
                  "-p", "test_config_invariants.py"], stage="config")
        self.run(["python3", "-m", "unittest", "discover", "-s", str(self.release_dir / "tests"),
                  "-p", "test_dts_invariants.py"], stage="config")
        seed_text = seed.read_text(encoding="utf-8").splitlines()
        final_text = (build / ".config").read_text(encoding="utf-8").splitlines()
        import difflib
        diff = list(difflib.unified_diff(seed_text, final_text, fromfile="seed", tofile="final"))
        (p["logs"].parent / "config-diff.txt").write_text("\n".join(diff) + "\n", encoding="utf-8")
        contract = {
            "seed_sha256": sha256_file(seed),
            "final_sha256": sha256_file(build / ".config"),
            "seed_vs_final_diff_lines": len(diff),
            "invariant_tests": ["test_config_invariants", "test_dts_invariants"],
            "gate": "PASS",
        }
        (p["logs"].parent / "config-contract.json").write_text(
            json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.complete_stage("CONFIG_VERIFIED", contract,
                            artifacts={"final_config": build / ".config"})

    def stage_build(self) -> None:
        p = self.run_paths()
        source = p["source"] / "linux"
        build = p["build"]
        stage_root = p["modules"]
        expected_release = self.release
        board_dtb = self.meta["board_dtb"]
        epoch = self.state["stages"]["MATERIALIZED"]["records"]["source_date_epoch"]
        env = {"ARCH": "arm64", "CROSS_COMPILE": "aarch64-linux-gnu-",
               "SOURCE_DATE_EPOCH": str(epoch), "KBUILD_BUILD_TIMESTAMP": f"@{epoch}",
               "KBUILD_BUILD_VERSION": "1", "KBUILD_BUILD_USER": "Fog",
               "KBUILD_BUILD_HOST": "eaidk310-wsl"}
        venv_bin = self._dtschema_venv()
        if venv_bin:
            env["PATH"] = f"{venv_bin}:{os.environ.get('PATH', '')}"
        if not shutil.which("dt-validate"):
            print("[release] dt-validate missing; installing dtschema via pip --user")
            self.run(["python3", "-m", "pip", "install", "--quiet", "--user", "dtschema"],
                     stage="build")
            env["PATH"] = f"{Path.home() / '.local' / 'bin'}:{os.environ.get('PATH', '')}"
        if not shutil.which("aarch64-linux-gnu-gcc"):
            raise Fail("cross toolchain missing: install gcc-aarch64-linux-gnu")

        kernel_release = self.run(
            ["make", "-s", "-C", str(source), f"O={build}", "kernelrelease"],
            stage="build", env=env, capture=True).stdout.strip()
        if kernel_release != expected_release:
            raise Fail(f"kernel release mismatch: expected {expected_release}, got {kernel_release}")
        started = time.time()
        self.run(["make", "-C", str(source), f"O={build}", f"-j{self.jobs}",
                  "Image", "modules", f"rockchip/{board_dtb}"], stage="build", env=env)
        self.run(["make", "-C", str(source), f"O={build}", "CHECK_DTBS=y",
                  f"rockchip/{board_dtb}"], stage="build", env=env)
        self.run(["make", "-C", str(source), f"O={build}",
                  f"INSTALL_MOD_PATH={stage_root}", "modules_install"], stage="build", env=env)
        self.run(["depmod", "-b", str(stage_root), expected_release], stage="build")

        image = build / "arch/arm64/boot/Image"
        dtb = build / f"arch/arm64/boot/dts/rockchip/{board_dtb}"
        module_dir = stage_root / "lib/modules" / expected_release
        for required in (image, dtb, build / "System.map", build / "Module.symvers",
                         module_dir / "modules.dep", module_dir / "modules.builtin",
                         module_dir / "modules.order"):
            if not required.is_file() or required.stat().st_size == 0:
                raise Fail(f"required build output missing or empty: {required}")
        file_out = self.run(["file", str(image)], stage="build", capture=True).stdout
        if "ARM64" not in file_out and "ARM aarch64" not in file_out:
            raise Fail("Image is not ARM64")
        for symbol, module in (("CONFIG_DWMAC_ROCKCHIP", "dwmac-rk"),
                               ("CONFIG_DRM_LIMA", "lima"),
                               ("CONFIG_BRCMFMAC", "brcmfmac"),
                               ("CONFIG_BT_HCIUART", "hci_uart"),
                               ("CONFIG_FB_TFT_ST7789V", "fb_st7789v")):
            value = ""
            for line in (build / ".config").read_text(encoding="utf-8").splitlines():
                if line.startswith(f"{symbol}="):
                    value = line.split("=", 1)[1]
            if value == "m":
                module_path = self.run(["modinfo", "-b", str(stage_root), "-k",
                                        expected_release, "-n", module],
                                       stage="build", capture=True).stdout.strip()
                if not module_path or not Path(module_path).stat().st_size:
                    raise Fail(f"required module missing: {module}")
        records = {
            "kernel_release": kernel_release,
            "source_date_epoch": epoch,
            "jobs": self.jobs,
            "elapsed_seconds": int(time.time() - started),
            "image_sha256": sha256_file(image),
            "dtb_sha256": sha256_file(dtb),
        }
        self.complete_stage("BUILT", records, artifacts={
            "image": image, "dtb": dtb,
            "modules_dep": module_dir / "modules.dep",
        })

    def _dtschema_venv(self) -> str | None:
        candidates = [
            os.environ.get("EAIDK310_DTSCHEMA_VENV"),
            str(self.cache_root / "tools" / "dtschema-venv" / "bin"),
        ]
        for cand in candidates:
            if cand and Path(cand, "dt-validate").exists():
                return cand
        return None

    def _policy_hash(self) -> str:
        initramfs = self.meta["initramfs"]
        policy = "\n".join([
            f"debian={initramfs['suite']}", f"arch={initramfs['arch']}",
            f"release={self.release}",
            "packages=" + ",".join(initramfs["packages"]),
        ]) + "\n"
        digest = hashlib.sha256(policy.encode())
        digest.update((self.release_dir / "initramfs/initramfs.conf").read_bytes())
        digest.update((self.release_dir / "initramfs/modules").read_bytes())
        return digest.hexdigest()

    def stage_initramfs(self) -> None:
        p = self.run_paths()
        initramfs = self.meta["initramfs"]
        if os.geteuid() != 0:
            raise Fail("the initramfs stage must run as root "
                       "(wsl.exe -u root / sudo ./tools/release-kernel.sh)")
        for tool_name in INITRAMFS_HOST_TOOLS:
            self.tool(tool_name)
        binfmt = Path("/proc/sys/fs/binfmt_misc/qemu-aarch64")
        if not binfmt.is_file() or "enabled" not in binfmt.read_text():
            raise Fail("qemu-aarch64 binfmt registration is missing or disabled")

        # preflight (module policy) — identical gate to build-initramfs-arm64.sh
        ka = self.release_dir / "tools" / "kernel_artifacts.py"
        module_dir = p["modules"] / "lib/modules" / self.release
        self.run(["python3", str(ka), "verify-early-deps", "--module-dir", str(module_dir)],
                 stage="initramfs")
        for line in (self.release_dir / "initramfs" / "modules").read_text(encoding="utf-8").splitlines():
            requested = line.split("#")[0].strip()
            if requested:
                self.run(["modinfo", "-b", str(p["modules"]), "-k", self.release,
                          "-n", requested], stage="initramfs")

        # rootfs cache: immutable base, snapshotted per run
        policy_hash = self._policy_hash()
        base = self.cache_root / "rootfs" / f"{initramfs['suite']}-{initramfs['arch']}-{policy_hash[:12]}"
        marker = base / "etc" / "eaidk310-chroot.sha256"
        if base.is_dir() and marker.is_file() and marker.read_text().strip() == policy_hash:
            print(f"[{self.release}] rootfs cache hit: {base.name}")
        else:
            print(f"[{self.release}] building rootfs cache (debootstrap {initramfs['suite']})")
            if base.exists():
                shutil.rmtree(base)
            base.parent.mkdir(parents=True, exist_ok=True)
            self.run(["debootstrap", f"--arch={initramfs['arch']}", "--foreign",
                      f"--variant={initramfs.get('variant', 'minbase')}",
                      initramfs["suite"], str(base), initramfs["mirror"]], stage="initramfs")
            qemu_static = self.tool("qemu-aarch64-static")
            shutil.copyfile(qemu_static, base / "usr/bin/qemu-aarch64-static")
            self.run(["chroot", str(base), "/debootstrap/debootstrap", "--second-stage"],
                     stage="initramfs")
            (base / "etc/apt/sources.list").write_text("\n".join([
                f"deb {initramfs['mirror']} {initramfs['suite']} main",
                f"deb {initramfs['mirror']} {initramfs['suite']}-updates main",
                f"deb {initramfs['security_mirror']} {initramfs['suite']}-security main",
            ]) + "\n", encoding="utf-8")
            shutil.copyfile("/etc/resolv.conf", base / "etc/resolv.conf")
            self.run(["chroot", str(base), "env", "DEBIAN_FRONTEND=noninteractive",
                      "apt-get", "update"], stage="initramfs")
            self.run(["chroot", str(base), "env", "DEBIAN_FRONTEND=noninteractive",
                      "apt-get", "install", "-y", "--no-install-recommends",
                      *initramfs["packages"]], stage="initramfs")
            marker.write_text(policy_hash + "\n")

        # per-run snapshot: the cache base is never touched again
        chroot = p["chroot"]
        if chroot.exists():
            shutil.rmtree(chroot)
        print(f"[{self.release}] snapshotting rootfs cache into the run workspace")
        self.run(["cp", "-a", str(base), str(chroot)], stage="initramfs")
        if not (chroot / "usr/bin/qemu-aarch64-static").is_file():
            raise Fail("snapshot lacks qemu-aarch64-static")

        chroot_modules = chroot / "lib/modules" / self.release
        if chroot_modules.exists():
            shutil.rmtree(chroot_modules)
        policy_dir = chroot / "etc/eaidk310-initramfs"
        chroot_modules.mkdir(parents=True)
        (policy_dir / "conf.d").mkdir(parents=True, exist_ok=True)
        for sub in ("hooks", "scripts/init-bottom", "scripts/init-premount", "scripts/init-top",
                    "scripts/local-bottom", "scripts/local-premount", "scripts/local-top",
                    "scripts/nfs-bottom", "scripts/nfs-premount", "scripts/nfs-top",
                    "scripts/panic"):
            (policy_dir / sub).mkdir(parents=True, exist_ok=True)
        (chroot / "boot").mkdir(exist_ok=True)
        self.run(["rsync", "-a", "--delete", "--exclude", "build", "--exclude", "source",
                  str(module_dir) + "/", str(chroot_modules) + "/"], stage="initramfs")
        shutil.copyfile(self.release_dir / "initramfs/initramfs.conf", policy_dir / "initramfs.conf")
        shutil.copyfile(self.release_dir / "initramfs/modules", policy_dir / "modules")
        shutil.copyfile(p["build"] / ".config", chroot / "boot" / f"config-{self.release}")

        initrd_epoch = self.meta["initramfs_source_date_epoch"]
        raw_name = f"initrd.img-{self.release}"
        uboot_name = f"uInitrd-{self.release}"
        out = p["initramfs"]
        self.run(["chroot", str(chroot), "depmod", self.release], stage="initramfs")
        self.run(["chroot", str(chroot), "env", f"SOURCE_DATE_EPOCH={initrd_epoch}",
                  "mkinitramfs", "-d", "/etc/eaidk310-initramfs",
                  "-o", f"/tmp/{raw_name}", self.release], stage="initramfs")
        shutil.copyfile(chroot / "tmp" / raw_name, out / raw_name)
        self.run(["mkimage", "-A", "arm64", "-O", "linux", "-T", "ramdisk", "-C", "none",
                  "-n", self.meta["uimage_name"], "-d", str(out / raw_name),
                  str(out / uboot_name)], stage="initramfs",
                 env={"SOURCE_DATE_EPOCH": str(initrd_epoch)})
        for artifact in (out / raw_name, out / uboot_name):
            if not artifact.is_file() or artifact.stat().st_size == 0:
                raise Fail(f"initramfs artifact missing: {artifact}")
        self.complete_stage("INITRAMFS_READY", {
            "initramfs_source_date_epoch": initrd_epoch,
            "policy_hash": policy_hash,
            "rootfs_cache": base.name,
            "uinitrd_sha256": sha256_file(out / uboot_name),
        }, artifacts={"uinitrd": out / uboot_name, "initrd": out / raw_name})

    def stage_package(self) -> None:
        p = self.run_paths()
        bundle_name = self.meta["bundle_name"]
        bundle = p["bundle"] / bundle_name
        image = p["build"] / "arch/arm64/boot/Image"
        dtb = p["build"] / "arch/arm64/boot/dts/rockchip" / self.meta["board_dtb"]
        uinitrd = p["initramfs"] / f"uInitrd-{self.release}"
        module_dir = p["modules"] / "lib/modules" / self.release
        for source in (image, dtb, uinitrd, module_dir / "modules.dep",
                       module_dir / "modules.builtin", module_dir / "modules.order"):
            if not source.is_file() or source.stat().st_size == 0:
                raise Fail(f"required packaging input missing: {source}")
        (bundle / "boot/dtb/rockchip").mkdir(parents=True, exist_ok=True)
        (bundle / "root/lib/modules" / self.release).mkdir(parents=True, exist_ok=True)
        (bundle / "deploy").mkdir(exist_ok=True)
        shutil.copyfile(image, bundle / "boot" / f"Image-{self.release}")
        shutil.copyfile(uinitrd, bundle / "boot" / f"uInitrd-{self.release}")
        shutil.copyfile(dtb, bundle / "boot/dtb/rockchip" / self.meta["bundle_dtb_name"])
        self.run(["rsync", "-a", "--delete", "--exclude", "build", "--exclude", "source",
                  str(module_dir) + "/", str(bundle / "root/lib/modules" / self.release) + "/"],
                 stage="package")
        shutil.copyfile(self.release_dir / "scripts/deploy-rescue-tf.sh",
                        bundle / "deploy/deploy-rescue-tf.sh")
        os.chmod(bundle / "deploy/deploy-rescue-tf.sh", 0o755)
        shutil.copyfile(self.release_dir / "tools/kernel_artifacts.py",
                        bundle / "deploy/kernel_artifacts.py")
        (bundle / "deploy/stable-baseline-sha256.txt").write_text(
            "\n".join(self.meta["stable_baseline"]) + "\n", encoding="utf-8")
        ka = self.release_dir / "tools" / "kernel_artifacts.py"
        self.run(["python3", str(ka), "render-extlinux", "--append-line",
                  self.meta["append_line"], "--output",
                  str(bundle / "deploy/extlinux-entry.conf")], stage="package")

        module_files = sum(1 for f in module_dir.rglob("*")
                           if f.is_file() and fnmatch.fnmatch(f.name, "*.ko*"))
        report = {
            "gate": "PASS",
            "scope": "package-preverification",
            "kernel_release": self.release,
            "image_sha256": sha256_file(image),
            "dtb_sha256": sha256_file(dtb),
            "uinitrd_sha256": sha256_file(uinitrd),
            "module_files": module_files,
            "deployment_enabled": True,
        }
        (bundle / "verification-report.json").write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        committed = self.release_dir / "analysis" / "verify-inputs.json"
        lock = json.loads((self.release_dir / "source-lock.json").read_text(encoding="utf-8"))
        live = self.probe_tool_versions()
        if committed.is_file():
            recorded = json.loads(committed.read_text(encoding="utf-8"))["tool_versions"]
            for tool_name in ("gcc", "binutils"):
                if recorded.get(tool_name) and live.get(tool_name) != recorded[tool_name]:
                    raise Fail(
                        f"toolchain drift: live {tool_name} ({live.get(tool_name)}) differs from "
                        f"the committed reference build record ({recorded[tool_name]}); the bundle "
                        "would not be byte-reproducible against the reference"
                    )
            gcc, binutils = recorded["gcc"], recorded["binutils"]
        else:
            gcc, binutils = live["gcc"], live["binutils"]
        metadata = {
            "kernel_release": self.release,
            "source_archive_sha256": lock["archive_sha256"],
            "source_signer_fingerprint": lock["signer_fingerprint"],
            "source_date_epoch": self.state["stages"]["MATERIALIZED"]["records"]["source_date_epoch"],
            "gcc": gcc,
            "binutils": binutils,
            "bundle_policy": "versioned-test-only",
            "deployment_enabled": True,
        }
        metadata_path = p["bundle"] / "task6-metadata.json"
        metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        files = sorted(
            (f.relative_to(bundle).as_posix() for f in bundle.rglob("*")
             if f.is_file() and f.name != "manifest.json"),
            key=lambda rel: rel.encode(),
        )
        manifest_args: list[str] = []
        for rel in files:
            manifest_args += ["--file", rel]
        self.run(["python3", str(ka), "manifest", "--root", str(bundle),
                  "--output", str(bundle / "manifest.json"),
                  "--metadata-json", str(metadata_path), *manifest_args], stage="package")
        self.run(["python3", str(ka), "verify-bundle-layout", "--root", str(bundle),
                  "--manifest", str(bundle / "manifest.json")], stage="package")
        metadata_path.unlink()

        out_dir = self.output_root / self.meta["kernel_version"]
        out_dir.mkdir(parents=True, exist_ok=True)
        archive = out_dir / f"{bundle_name}.tar.zst"
        tar_epoch = self.meta["initramfs_source_date_epoch"]
        tar_cmd = f"tar --sort=name --mtime=@{tar_epoch} --owner=0 --group=0 --numeric-owner " \
                  f"-C {p['bundle']} -cf - {bundle_name} | zstd -19 -T0 -q -o {archive}"
        self.run(["bash", "-o", "pipefail", "-c", tar_cmd], stage="package")
        archive_sha = sha256_file(archive)
        (out_dir / f"{bundle_name}.tar.zst.sha256").write_text(
            f"{archive_sha}  {archive.name}\n", encoding="utf-8")
        self.complete_stage("PACKAGED", {
            "bundle": bundle_name,
            "archive": str(archive),
            "bundle_sha256": archive_sha,
        }, artifacts={"bundle_manifest": bundle / "manifest.json"})

    # ------------------------------------------------------------------
    # verification / acceptance
    # ------------------------------------------------------------------

    def stage_verify(self) -> None:
        p = self.run_paths()
        ka = self.release_dir / "tools" / "kernel_artifacts.py"
        bundle_name = self.meta["bundle_name"]
        bundle = p["bundle"] / bundle_name
        out_dir = self.output_root / self.meta["kernel_version"]
        archive = out_dir / f"{bundle_name}.tar.zst"
        archive_sha = Path(str(archive) + ".sha256").read_text().split()[0]
        if sha256_file(archive) != archive_sha:
            raise Fail("bundle archive sha256 mismatch")
        checks: list[dict] = []

        def check(name: str, condition: bool, detail: str = "") -> None:
            checks.append({"check": name, "result": "PASS" if condition else "FAIL",
                           "detail": detail})
            if not condition:
                raise Fail(f"bundle verification failed: {name} {detail}")

        check("layout", self.run(["python3", str(ka), "verify-bundle-layout", "--root",
                                  str(bundle), "--manifest", str(bundle / "manifest.json")],
                                 stage="verify", check=False).returncode == 0)
        specials = [str(f) for f in bundle.rglob("*")
                    if f.is_symlink() or (f.exists() and not f.is_file() and not f.is_dir())]
        check("no_special_files", not specials, ",".join(specials))
        dtb = bundle / "boot/dtb/rockchip" / self.meta["bundle_dtb_name"]
        model = self.run(["fdtget", "-t", "s", str(dtb), "/", "model"],
                         stage="verify", capture=True).stdout.strip()
        check("dtb_model", model == "EAIDK-310 build by lcy v2", model)
        compatible = self.run(["fdtget", "-t", "s", str(dtb), "/", "compatible"],
                              stage="verify", capture=True).stdout
        check("dtb_compatible", "openailab,eaidk-310" in compatible.split())
        uinitrd = bundle / "boot" / f"uInitrd-{self.release}"
        info = self.run(["dumpimage", "-l", str(uinitrd)], stage="verify", capture=True).stdout
        check("uinitrd_name", f"Image Name:   {self.meta['uimage_name']}" in info)
        check("uinitrd_type", "AArch64 Linux RAMDisk Image (uncompressed)" in info)
        header = uinitrd.read_bytes()[:12]
        epoch = struct.unpack(">I", header[8:12])[0]
        check("uinitrd_epoch", epoch == self.meta["initramfs_source_date_epoch"], str(epoch))

        verify_root = p["verify"]
        initramfs_dir = verify_root / "initramfs"
        archive_dir = verify_root / "archive"
        initramfs_dir.mkdir(exist_ok=True)
        archive_dir.mkdir(exist_ok=True)
        initrd_img = verify_root / "initrd.img"
        self.run(["dumpimage", "-T", "ramdisk", "-p", "0", "-o", str(initrd_img), str(uinitrd)],
                 stage="verify")
        self.run(["unmkinitramfs", str(initrd_img), str(initramfs_dir)], stage="verify")
        check("init_present", (initramfs_dir / "init").is_file())
        busybox = initramfs_dir / "usr/bin/busybox"
        check("initramfs_busybox", busybox.is_file() and "ARM aarch64" in
              self.run(["file", str(busybox)], stage="verify", capture=True).stdout)
        kmod_bin = initramfs_dir / "usr/bin/kmod"
        check("initramfs_kmod", kmod_bin.is_file())
        initramfs_modules = initramfs_dir / f"usr/lib/modules/{self.release}"
        check("initramfs_modules_dep", (initramfs_modules / "modules.dep").is_file())
        missing = []
        for line in (self.release_dir / "initramfs" / "modules").read_text(encoding="utf-8").splitlines():
            requested = line.split("#")[0].strip().replace("-", "_")
            if requested and not list(initramfs_modules.rglob(f"{requested}.ko*")):
                missing.append(requested)
        check("initramfs_explicit_modules", not missing, ",".join(missing))
        devices = [str(f) for f in initramfs_dir.rglob("*")
                   if f.is_char_device() or f.is_block_device()]
        check("initramfs_no_device_nodes", not devices)

        expected_entry = verify_root / "extlinux-entry.conf"
        self.run(["python3", str(ka), "render-extlinux", "--append-line",
                  self.meta["append_line"], "--output", str(expected_entry)], stage="verify")
        check("extlinux_entry",
              expected_entry.read_bytes() == (bundle / "deploy/extlinux-entry.conf").read_bytes())

        # archive round-trip
        extracted = archive_dir / bundle_name
        self.run(["bash", "-c", f"zstd -dc {archive} | tar -xf - -C {archive_dir}"],
                 stage="verify")
        check("archive_layout", self.run(["python3", str(ka), "verify-bundle-layout",
                                          "--root", str(extracted),
                                          "--manifest", str(extracted / "manifest.json")],
                                         stage="verify", check=False).returncode == 0)
        check("archive_manifest_identical",
              (extracted / "manifest.json").read_bytes() == (bundle / "manifest.json").read_bytes())

        # modules contract (Tier 2: modules are first-class release artifacts)
        bundle_modules = bundle / "root/lib/modules" / self.release
        check("modules_dep_present", (bundle_modules / "modules.dep").stat().st_size > 0)
        check("modules_builtin_present", (bundle_modules / "modules.builtin").is_file())
        check("modules_order_present", (bundle_modules / "modules.order").is_file())
        depmod_check = self.run(["depmod", "-b", str(bundle), "-n", "-e", self.release],
                                stage="verify", check=False, capture=True)
        check("depmod_no_unresolved", depmod_check.returncode == 0,
              (depmod_check.stdout or "")[-400:])
        ko_count = len(list(bundle_modules.rglob("*.ko*")))
        build_ko_count = len(list((p["modules"] / "lib/modules" / self.release).rglob("*.ko*")))
        check("module_count_matches_build", ko_count == build_ko_count,
              f"bundle={ko_count} build={build_ko_count}")

        # custom-source presentation gate (must hold whenever the upstream
        # source needed to compare is materialized)
        patch_compare = "NOT_APPLICABLE"
        cs = self.repo / "tools" / "verify_custom_source.py"
        if cs.is_file():
            env = {}
            uboot_src = self.cache_root / "upstream" / "u-boot"
            if (uboot_src / ".git").exists():
                env["EAIDK310_UBOOT_SRC"] = str(uboot_src)
            proc = self.run(["python3", str(cs), "--check"], stage="verify",
                            check=False, capture=True, env=env)
            if proc.returncode == 0:
                patch_compare = "PASS" if env else "REPO_ONLY"
            else:
                patch_compare = "FAIL"
                check("custom_source_compare", False, (proc.stdout or "")[-600:])
        check("custom_source_compare_gate", patch_compare != "FAIL", patch_compare)

        manifest_files = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))["files"]
        drifted = [f["path"] for f in manifest_files
                   if sha256_file(bundle / f["path"]) != f["sha256"]]
        check("manifest_files_byte_match", not drifted, ",".join(drifted[:5]))
        self.state.setdefault("records", {})["patch_source_byte_compare"] = patch_compare
        self.complete_stage("VERIFIED", {
            "checks_passed": len(checks),
            "patch_source_byte_compare": patch_compare,
        })

    def stage_test(self) -> None:
        p = self.run_paths()
        checks = []
        bundle_name = self.meta["bundle_name"]
        bundle = p["bundle"] / bundle_name
        ka = self.release_dir / "tools" / "kernel_artifacts.py"
        proc = self.run(["python3", str(ka), "verify-manifest", "--root", str(bundle),
                         "--manifest", str(bundle / "manifest.json")], stage="test", check=False)
        checks.append({"check": "kernel_artifacts_verify_manifest", "result": "PASS" if proc.returncode == 0 else "FAIL"})
        if proc.returncode != 0:
            raise Fail("verify-manifest acceptance failed")
        summary = {
            "schema": SCHEMA_VERSION,
            "kernel_release": self.release,
            "tier": "artifact-acceptance",
            "gate": "PASS",
            "checks": checks,
            "patch_source_byte_compare": self.state.get("records", {}).get(
                "patch_source_byte_compare", "NOT_APPLICABLE"),
            "repository_tier1": "enforced by make test / make verify and CI",
        }
        summary_path = p["logs"].parent / "test-summary.json"
        summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        self.complete_stage("TESTED", {"summary": str(summary_path)},
                            artifacts={"test_summary": summary_path})

    def stage_finalize(self) -> None:
        p = self.run_paths()
        out_dir = self.output_root / self.meta["kernel_version"]
        bundle_name = self.meta["bundle_name"]
        archive = out_dir / f"{bundle_name}.tar.zst"
        bundle = p["bundle"] / bundle_name

        build_env = {
            "host": {
                "distro": self._os_release(),
                "architecture": platform.machine(),
                "kernel": platform.release(),
            },
            "toolchain": self.probe_tool_versions(),
            "source_date_epoch_kernel": self.state["stages"]["MATERIALIZED"]["records"]["source_date_epoch"],
            "source_date_epoch_initramfs_bundle": self.meta["initramfs_source_date_epoch"],
            "locale": os.environ.get("LANG", "C"),
            "timezone": time.strftime("%Z"),
            "zstd_workers": "T0 (host nproc)",
        }
        build_env_path = out_dir / "BUILD-ENVIRONMENT.json"
        build_env_path.write_text(json.dumps(build_env, indent=2, sort_keys=True) + "\n",
                                  encoding="utf-8")

        lock = json.loads((self.release_dir / "source-lock.json").read_text(encoding="utf-8"))
        patches = sorted((self.release_dir / "patches").glob("*.patch"))
        custom_src_manifest = self.repo / "custom-src" / "manifest.json"
        build_env_digest = sha256_file(build_env_path)
        test_summary = json.loads(
            (p["logs"].parent / "test-summary.json").read_text(encoding="utf-8"))
        release_manifest = {
            "schema": SCHEMA_VERSION,
            "kernel_release": self.release,
            "kernel_version": self.meta["kernel_version"],
            "upstream": {
                "archive_url": lock["archive_url"],
                "archive_sha256": lock["archive_sha256"],
                "signer_fingerprint": lock["signer_fingerprint"],
            },
            "patches": [{"name": p.name, "sha256": sha256_file(p)} for p in patches],
            "config_sha256": sha256_file(p["build"] / ".config"),
            "board_dts_sha256": sha256_file(self.release_dir / "dts" / "rk3328-eaidk-310.dts"),
            "artifacts": {
                "image_sha256": self.state["stages"]["BUILT"]["records"]["image_sha256"],
                "dtb_sha256": self.state["stages"]["BUILT"]["records"]["dtb_sha256"],
                "uinitrd_sha256": self.state["stages"]["INITRAMFS_READY"]["records"]["uinitrd_sha256"],
                "modules_tree_digest": modules_tree_digest(
                    bundle / "root/lib/modules" / self.release),
            },
            "bundle": {
                "name": f"{bundle_name}.tar.zst",
                "sha256": self.state["stages"]["PACKAGED"]["records"]["bundle_sha256"],
                "bytes": archive.stat().st_size,
            },
            "custom_src_manifest_sha256": sha256_file(custom_src_manifest)
            if custom_src_manifest.is_file() else None,
            "build_environment_digest": build_env_digest,
            "git_commit": self._git_commit(),
            "test_summary": {"gate": test_summary["gate"],
                             "patch_source_byte_compare": test_summary["patch_source_byte_compare"]},
        }
        manifest_path = out_dir / "RELEASE-MANIFEST.json"
        manifest_path.write_text(json.dumps(release_manifest, indent=2, sort_keys=True) + "\n",
                                 encoding="utf-8")

        notes = [
            f"# EAIDK-310 {self.release} release candidate (draft)",
            "",
            f"- bundle: `{bundle_name}.tar.zst`",
            f"- bundle sha256: `{release_manifest['bundle']['sha256']}`",
            f"- upstream: {lock['archive_url']} (sha256 {lock['archive_sha256'][:16]}…, "
            f"signed {lock['signer_fingerprint']})",
            f"- build environment digest: `{build_env_digest[:16]}…`",
            f"- repository commit: `{release_manifest['git_commit']}`",
            "",
            "Status: READY_FOR_BOARD_TRIAL. This candidate has not been installed",
            "on any board; OTA install/arm and stable promotion are separate,",
            "explicitly approved steps (see ota/README.md).",
            "",
        ]
        (out_dir / "RELEASE-NOTES-DRAFT.md").write_text("\n".join(notes), encoding="utf-8")
        shutil.copyfile(p["logs"].parent / "test-summary.json", out_dir / "test-summary.json")

        sums = sorted([
            (out_dir / f"{bundle_name}.tar.zst").relative_to(out_dir).as_posix(),
            "BUILD-ENVIRONMENT.json", "RELEASE-MANIFEST.json", "test-summary.json",
        ])
        lines = [f"{sha256_file(out_dir / rel)}  {rel}" for rel in sums]
        (out_dir / "SHA256SUMS").write_text("\n".join(lines) + "\n", encoding="utf-8")

        previous = None
        try:
            previous = json.loads((manifest_path).read_text(encoding="utf-8"))["bundle"]["sha256"]
        except (OSError, KeyError, json.JSONDecodeError):
            pass
        current = release_manifest["bundle"]["sha256"]
        if previous:
            print(f"REPRO_COMPARE previous={previous[:16]}… current={current[:16]}… "
                  f"MATCH={'YES' if previous == current else 'NO'}")

        self.complete_stage("READY_FOR_BOARD_TRIAL", {
            "output_dir": str(out_dir),
            "bundle_sha256": release_manifest["bundle"]["sha256"],
        })
        self.state["status"] = "COMPLETE"
        self.save_state()
        print(f"[{self.release}] READY_FOR_BOARD_TRIAL")
        print(f"  output:    {out_dir}")
        print(f"  bundle:    {archive.name}")
        print(f"  sha256:    {release_manifest['bundle']['sha256']}")

    def _os_release(self) -> str:
        try:
            for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
                if line.startswith("PRETTY_NAME="):
                    return line.split("=", 1)[1].strip('"')
        except OSError:
            pass
        return "unknown"

    def _git_commit(self) -> str:
        try:
            return subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(self.repo),
                                  capture_output=True, text=True, check=True).stdout.strip()
        except subprocess.CalledProcessError:
            return "unknown"

    # ------------------------------------------------------------------
    # driver
    # ------------------------------------------------------------------

    def _verify_completed_stages(self) -> None:
        """--resume: re-verify every completed stage's recorded artifact hashes."""
        stages = self.state.get("stages", {})
        for stage, entry in stages.items():
            for name, artifact in entry.get("artifacts", {}).items():
                path = Path(artifact["path"])
                if not path.is_file():
                    raise Fail(f"resume: recorded artifact {stage}/{name} is gone ({path}); "
                               "restart without --resume")
                actual = sha256_file(path)
                if actual != artifact["sha256"]:
                    raise Fail(f"resume: recorded artifact {stage}/{name} drifted "
                               f"({actual[:12]} != {artifact['sha256'][:12]})")

    def run_pipeline(self, args: argparse.Namespace) -> int:
        self.run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ-") \
            + uuid.uuid4().hex[:8]
        self.run_dir = self.work_root / "work" / f"{self.release}-{self.run_id}"
        if args.resume:
            self.run_dir = self._latest_resumable_run()
            self.load_state()
            if self.state.get("status") == "COMPLETE":
                print(f"[{self.release}] run already COMPLETE: {self.run_dir}")
                return 0
            self._verify_completed_stages()
            print(f"[{self.release}] resuming {self.run_dir} at stage after "
                  f"{self.state.get('stage', 'INIT')}")
        else:
            if self.run_dir.exists() and args.clean:
                shutil.rmtree(self.run_dir)
            self.run_dir.mkdir(parents=True, exist_ok=True)
            self.load_state()
            if self.state.get("stages"):
                raise Fail(f"run directory already holds state: {self.run_dir} "
                           "(use --resume or --clean)")
            self.state = {"stages": {}}
        self.save_state()

        phase = args.stage
        try:
            if phase in ("all", "prepare"):
                if "INIT" not in self.state["stages"]:
                    self.stage_init()
                if "SOURCE_VERIFIED" not in self.state["stages"]:
                    self.stage_source()
                if "MATERIALIZED" not in self.state["stages"]:
                    self.stage_materialize()
                if "PATCHED" not in self.state["stages"]:
                    self.stage_patch()
                if "CONFIG_VERIFIED" not in self.state["stages"]:
                    self.stage_config()
            if phase in ("all", "build"):
                if "BUILT" not in self.state["stages"]:
                    self.require_stage("CONFIG_VERIFIED")
                    self.stage_build()
            if phase in ("all", "package"):
                if "INITRAMFS_READY" not in self.state["stages"]:
                    self.require_stage("BUILT")
                    self.stage_initramfs()
                if "PACKAGED" not in self.state["stages"]:
                    self.require_stage("INITRAMFS_READY")
                    self.stage_package()
            if phase in ("all", "verify"):
                if "VERIFIED" not in self.state["stages"]:
                    self.require_stage("PACKAGED")
                    self.stage_verify()
                if "TESTED" not in self.state["stages"]:
                    self.stage_test()
                if "READY_FOR_BOARD_TRIAL" not in self.state["stages"]:
                    self.stage_finalize()
        except Fail as exc:
            self.state["status"] = "FAILED"
            self.state["failed_stage"] = self.state.get("stage", "INIT")
            self.state["failure"] = str(exc)
            self.save_state()
            print(f"[{self.release}] FAILED(stage={self.state['failed_stage']}): {exc}",
                  file=sys.stderr)
            print(f"run workspace preserved for diagnosis: {self.run_dir}", file=sys.stderr)
            return 1
        return 0

    def _latest_resumable_run(self) -> Path:
        work_dir = self.work_root / "work"
        candidates = sorted(
            (d for d in work_dir.glob(f"{self.release}-*") if (d / "state.json").is_file()),
            key=lambda d: d.stat().st_mtime, reverse=True)
        for cand in candidates:
            state = json.loads((cand / "state.json").read_text(encoding="utf-8"))
            if state.get("status") in ("FAILED", "RUNNING"):
                return cand
        raise Fail(f"no FAILED/RUNNING run to resume for {self.release}")


def resolve_stage(args: argparse.Namespace) -> str:
    if args.all:
        return "all"
    for flag, stage in (("--prepare", "prepare"), ("--build", "build"),
                        ("--package", "package"), ("--verify", "verify")):
        if getattr(args, flag.lstrip("-")):
            return stage
    return args.stage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("version", nargs="?", help="kernel version, e.g. 6.18.54")
    parser.add_argument("--stage", default="all",
                        choices=("all", "prepare", "build", "package", "verify"),
                        help="pipeline segment to run (default: all)")
    parser.add_argument("--prepare", action="store_true", help="= --stage prepare")
    parser.add_argument("--build", action="store_true", help="= --stage build")
    parser.add_argument("--package", action="store_true", help="= --stage package")
    parser.add_argument("--verify", action="store_true", help="= --stage verify")
    parser.add_argument("--all", action="store_true", help="= --stage all (default)")
    parser.add_argument("--resume", action="store_true",
                        help="resume the latest FAILED/RUNNING run after re-verifying hashes")
    parser.add_argument("--clean", action="store_true",
                        help="delete the per-run workspace before starting (cache/output kept)")
    parser.add_argument("--purge-cache", action="store_true",
                        help="explicitly delete the shared cache (downloads/rootfs/upstream)")
    parser.add_argument("--status", action="store_true", help="show latest run state and exit")
    parser.add_argument("--work-root", help=f"workspace root (default $EAIDK_WORK_ROOT or "
                                            f"~/.cache/eaidk310/release)")
    parser.add_argument("--cache-root", help="shared cache root (default <work-root>/cache)")
    parser.add_argument("--output-dir", help="release output dir (default <work-root>/output)")
    parser.add_argument("--run-id", help="reuse/label a specific run id")
    parser.add_argument("--jobs", type=int, help="parallel build jobs (default nproc)")
    args = parser.parse_args(argv)
    args.stage = resolve_stage(args)
    if not args.version:
        parser.error("a kernel version is required, e.g. release-kernel.py 6.18.54")
    engine = Engine(args)
    if args.status:
        runs = sorted((engine.work_root / "work").glob(f"{engine.release}-*"))
        if not runs:
            print(f"no runs for {engine.release}")
            return 0
        latest = runs[-1]
        state = json.loads((latest / "state.json").read_text(encoding="utf-8")) \
            if (latest / "state.json").is_file() else {}
        print(f"run={latest.name} status={state.get('status')} stage={state.get('stage')}")
        for stage, entry in sorted(state.get("stages", {}).items(),
                                   key=lambda kv: STAGES.index(kv[0]) if kv[0] in STAGES else 99):
            print(f"  {stage}: {entry['status']}")
        return 0
    if args.purge_cache:
        cache = engine.cache_root
        if cache.is_dir():
            shutil.rmtree(cache)
            print(f"purged cache: {cache}")
        return 0
    return engine.run_pipeline(args)


if __name__ == "__main__":
    sys.exit(main())
