"""Authorization-gated EAIDK310 eMMC migration board CLI.

Default behavior is read-only refusal: ``provision`` and ``sync`` abort unless
``--write``, the fixed authorization token, and the SHA-256 of the current
read-only preflight report are all supplied and correct.  No target device is
opened, no partition is mounted, and no command runner is invoked until that
gate passes.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from eaidk310_emmc_lib import (
    BASELINE_TAIL_SHA256,
    CONTROL_PREFIX_SHA256,
    CONTROL_REGION_SHA256,
    MIN_ROOT_FREE_BYTES,
    P1_END,
    P1_START,
    P2_END,
    P2_START,
    SECTOR_SIZE,
    DeviceFacts,
    IdentitySet,
    SafetyError,
    boot_rsync_args,
    ensure_required_mountpoints,
    generate_identities,
    render_sfdisk,
    rewrite_extlinux,
    rewrite_fstab,
    root_rsync_args,
    sha256_range,
    validate_device_facts,
    validate_required_mountpoints,
    validate_sfdisk_json,
    validate_target_extlinux,
    validate_target_fstab,
)

WRITE_TOKEN = "WRITE-HBD08G-7818182656"
BASELINE_SHA256 = "6254986C3E1E12D942D35769A8D8182422A017B6CA392237D0F284B31490A3EB"
_CAPACITY_RESERVE_BYTES = 1610612736

_REQUIRED_TOOLS = (
    "sfdisk",
    "partprobe",
    "udevadm",
    "mkfs.ext4",
    "e2fsck",
    "blkid",
    "findmnt",
    "mount",
    "umount",
    "rsync",
    "dd",
    "sha256sum",
    "systemctl",
)

_EXPECTED_BACKUP_LOCK = {
    "verified": True,
    "device_model": "HBD08G",
    "user_area_bytes": 7818182656,
    "gzip_sha256": "E0C92B0C8E2C8AFD933BF06FB7A2E8108F49D01BB00945A3A2E12581BED78601",
    "raw_sha256": "0BEA2312D1E714F527724EC6462789850C1EDEF9B2C4C1F8B1AA7F29EBD4BA68",
    "boot0_bytes": 4194304,
    "boot0_sha256": "BB9F8DF61474D25E71FA00722318CD387396CA1736605E1248821CC0DE3D3AF8",
    "boot1_bytes": 4194304,
    "boot1_sha256": "BB9F8DF61474D25E71FA00722318CD387396CA1736605E1248821CC0DE3D3AF8",
    "verification_report": "backups/emmc-factory-20260901/verification-report.md",
}

_IDENTITY_FIELDS = (
    "disk_guid",
    "boot_partuuid",
    "root_partuuid",
    "boot_uuid",
    "root_uuid",
)
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)
_TOTAL_SIZE_RE = re.compile(r"Total file size:\s*(\S+)\s*bytes")


@dataclass(frozen=True)
class SystemPaths:
    sys_root: Path = Path("/sys")
    proc_root: Path = Path("/proc")
    capacity_target: Path = Path("/run/eaidk310-emmc-capacity-target")
    sync_root: Path = Path("/mnt/eaidk310-emmc/root")
    verify_root: Path = Path("/mnt/eaidk310-emmc/verify")


class CommandRunner:
    """Runs host commands; captured mode returns stdout, visible mode inherits."""

    def run(self, argv, *, input_text=None, visible=False):
        common = {
            "input": input_text,
            "text": True,
            "check": True,
            "env": {**os.environ, "LC_ALL": "C"},
        }
        if visible:
            subprocess.run(argv, **common)
            return ""
        completed = subprocess.run(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            **common,
        )
        return completed.stdout

    def findmnt_source(self, device):
        """Return mounts for ``device``; an empty no-match result is safe."""
        argv = ["findmnt", "-rn", "-S", device]
        completed = subprocess.run(
            argv,
            text=True,
            check=False,
            env={**os.environ, "LC_ALL": "C"},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if completed.returncode == 0:
            return completed.stdout
        if (
            completed.returncode == 1
            and not completed.stdout.strip()
            and not completed.stderr.strip()
        ):
            return ""
        raise subprocess.CalledProcessError(
            completed.returncode,
            argv,
            output=completed.stdout,
            stderr=completed.stderr,
        )


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def _write_json(path, report: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )


def _parse_blkid_export(text: str) -> dict:
    result: dict = {}
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key:
            result[key.strip()] = value.strip()
    return result


def _parse_swaps(path: Path) -> list:
    devices: list = []
    if not Path(path).exists():
        return devices
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("Filename"):
            continue
        devices.append(line.split()[0])
    return devices


def _parse_total_file_size(stdout: str) -> int:
    matches = _TOTAL_SIZE_RE.findall(stdout)
    if not matches:
        raise SafetyError("rsync stats missing a 'Total file size' line")
    if len(matches) > 1:
        raise SafetyError("rsync stats contain duplicate 'Total file size' lines")
    digits = matches[0].replace(",", "")
    if not digits.isdigit():
        raise SafetyError("rsync 'Total file size' is not numeric")
    return int(digits)


def identity_to_dict(ids: IdentitySet) -> dict:
    return {field: getattr(ids, field) for field in _IDENTITY_FIELDS}


def load_identity(path) -> IdentitySet:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    try:
        values = {field: data[field] for field in _IDENTITY_FIELDS}
    except KeyError as error:
        raise SafetyError(f"identity file is missing {error}") from error
    for field in _IDENTITY_FIELDS:
        if not isinstance(values[field], str) or not _UUID_RE.match(values[field]):
            raise SafetyError(f"identity value is not a UUIDv4: {field}")
        values[field] = values[field].lower()
    ids = IdentitySet(**values)
    if len({getattr(ids, field) for field in _IDENTITY_FIELDS}) != 5:
        raise SafetyError("identity file repeats a value")
    return ids


def save_identity(path, ids: IdentitySet) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(identity_to_dict(ids), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def collect_device_facts(runner, system_paths: SystemPaths) -> DeviceFacts:
    root_source = runner.run(["findmnt", "-no", "SOURCE", "/"]).strip()
    boot_source = runner.run(["findmnt", "-no", "SOURCE", "/boot"]).strip()
    model = (
        system_paths.sys_root / "block/mmcblk2/device/name"
    ).read_text().strip()
    sectors_text = (
        system_paths.sys_root / "class/block/mmcblk2/size"
    ).read_text().strip()
    try:
        sectors = int(sectors_text)
    except ValueError as error:
        raise SafetyError(f"invalid target sector count: {sectors_text}") from error
    target_mounts = []
    for device in ("/dev/mmcblk2", "/dev/mmcblk2p1", "/dev/mmcblk2p2"):
        line = runner.findmnt_source(device).strip()
        if line:
            target_mounts.append(line)
    swap_devices = _parse_swaps(system_paths.proc_root / "swaps")
    return DeviceFacts(
        root_source=root_source,
        boot_source=boot_source,
        target_model=model,
        target_sectors=sectors,
        target_mounts=target_mounts,
        swap_devices=swap_devices,
    )


def _require_tools() -> None:
    missing = [tool for tool in _REQUIRED_TOOLS if shutil.which(tool) is None]
    if missing:
        raise SafetyError("required tool missing: " + ", ".join(missing))


def _validate_prefix_and_lock(prefix_path, prefix_manifest_path, backup_lock_path):
    prefix_sha = sha256_range(Path(prefix_path), 0, 16 * 1024 * 1024)
    if prefix_sha != CONTROL_PREFIX_SHA256:
        raise SafetyError(
            f"control prefix SHA-256 mismatch: expected {CONTROL_PREFIX_SHA256}, "
            f"got {prefix_sha}"
        )
    manifest = json.loads(Path(prefix_manifest_path).read_text(encoding="utf-8"))
    if manifest.get("writable") is not True:
        raise SafetyError("prefix manifest is not writable")
    if str(manifest.get("baseline_sha256", "")).upper() != BASELINE_SHA256:
        raise SafetyError("prefix manifest baseline SHA-256 mismatch")
    if str(manifest.get("region_sha256", "")).upper() != CONTROL_REGION_SHA256:
        raise SafetyError("prefix manifest region SHA-256 mismatch")
    if str(manifest.get("prefix_sha256", "")).upper() != prefix_sha:
        raise SafetyError("prefix manifest prefix SHA-256 mismatch")
    for start, end in manifest.get("changed_ranges", []):
        if start < 8 * 1024 * 1024 or end > 12 * 1024 * 1024:
            raise SafetyError(
                "prefix manifest changed range lies outside the U-Boot region"
            )
    lock = json.loads(Path(backup_lock_path).read_text(encoding="utf-8"))
    for key, expected in _EXPECTED_BACKUP_LOCK.items():
        if lock.get(key) != expected:
            raise SafetyError(f"factory backup lock field {key} mismatch")
    return manifest, lock


def run_preflight(
    runner,
    system_paths: SystemPaths,
    prefix_path,
    prefix_manifest_path,
    backup_lock_path,
    exclude_file,
    identity_file,
    report_path,
) -> dict:
    facts = collect_device_facts(runner, system_paths)
    validate_device_facts(facts)
    _require_tools()
    _validate_prefix_and_lock(prefix_path, prefix_manifest_path, backup_lock_path)
    boot_export = _parse_blkid_export(
        runner.run(["blkid", "-o", "export", "/dev/mmcblk0p1"])
    )
    root_export = _parse_blkid_export(
        runner.run(["blkid", "-o", "export", "/dev/mmcblk0p2"])
    )
    for export, label in ((boot_export, "BOOT"), (root_export, "ROOTFS")):
        if "UUID" not in export or "PARTUUID" not in export:
            raise SafetyError(f"source {label} blkid output lacks UUID/PARTUUID")
    source_ids = {
        boot_export["UUID"].lower(),
        boot_export["PARTUUID"].lower(),
        root_export["UUID"].lower(),
        root_export["PARTUUID"].lower(),
    }
    identity_path = Path(identity_file)
    if identity_path.exists():
        ids = load_identity(identity_path)
        values = {getattr(ids, field) for field in _IDENTITY_FIELDS}
        if len(values) != 5 or values & source_ids:
            raise SafetyError(
                "identity file collides with source identifiers or repeats"
            )
    else:
        ids = generate_identities(source_ids)
        save_identity(identity_path, ids)
    capacity_target = Path(system_paths.capacity_target)
    capacity_target.mkdir(parents=True, exist_ok=True)
    stats = runner.run(
        [
            "rsync",
            "-aHAXx",
            "--numeric-ids",
            "--dry-run",
            "--stats",
            f"--exclude-from={exclude_file}",
            "/",
            str(capacity_target) + "/",
        ]
    )
    source_bytes = _parse_total_file_size(stats)
    root_bytes = (P2_END - P2_START + 1) * SECTOR_SIZE
    if source_bytes + _CAPACITY_RESERVE_BYTES > root_bytes:
        raise SafetyError(
            f"source estimate {source_bytes} exceeds ROOTFS capacity reserve "
            f"({root_bytes} bytes)"
        )
    report = {
        "root_source": facts.root_source,
        "boot_source": facts.boot_source,
        "target_model": facts.target_model,
        "target_sectors": facts.target_sectors,
        "target_mounts": list(facts.target_mounts),
        "swap_devices": list(facts.swap_devices),
        "source_boot_uuid": boot_export["UUID"],
        "source_boot_partuuid": boot_export["PARTUUID"],
        "source_root_uuid": root_export["UUID"],
        "source_root_partuuid": root_export["PARTUUID"],
        "planned": identity_to_dict(ids),
        "prefix_sha256": CONTROL_PREFIX_SHA256,
        "prefix_manifest_sha256": _file_sha256(Path(prefix_manifest_path)),
        "backup_lock_sha256": _file_sha256(Path(backup_lock_path)),
        "exclude_file_sha256": _file_sha256(Path(exclude_file)),
        "source_byte_estimate": source_bytes,
        "layout": {
            "p1_start": P1_START,
            "p1_sectors": P1_END - P1_START + 1,
            "p2_start": P2_START,
            "p2_sectors": P2_END - P2_START + 1,
        },
        "ready_for_authorization": True,
    }
    _write_json(report_path, report)
    return report


def require_write_authorization(
    write: bool, token: str, approved_report_sha256: str, report_path
) -> dict:
    if not write:
        raise SafetyError("--write is required for destructive operations")
    if token != WRITE_TOKEN:
        raise SafetyError("invalid write authorization token")
    report_path = Path(report_path)
    actual = _file_sha256(report_path)
    if approved_report_sha256.lower() != actual.lower():
        raise SafetyError(
            "approved preflight report SHA-256 does not match the report file"
        )
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if report.get("ready_for_authorization") is not True:
        raise SafetyError("preflight report is not ready for authorization")
    return report


def _require_facts_equal_report(facts: DeviceFacts, report: dict) -> None:
    expected = DeviceFacts(
        root_source=report["root_source"],
        boot_source=report["boot_source"],
        target_model=report["target_model"],
        target_sectors=report["target_sectors"],
        target_mounts=report["target_mounts"],
        swap_devices=report["swap_devices"],
    )
    if facts != expected:
        raise SafetyError("live device facts differ from the preflight report")


def _require_identity_matches_report(ids: IdentitySet, report: dict) -> None:
    planned = report["planned"]
    for field in _IDENTITY_FIELDS:
        if getattr(ids, field) != planned[field]:
            raise SafetyError(f"identity {field} differs from the preflight report")


def _validate_blkid_identity(
    export: dict,
    *,
    role: str,
    expected_uuid: str,
    expected_partuuid: str,
    expected_label: str,
    context: str = "",
) -> None:
    prefix = f"{context} " if context else ""
    if export.get("UUID", "").lower() != expected_uuid.lower():
        raise SafetyError(f"{prefix}{role} filesystem UUID differs from identity")
    if export.get("PARTUUID", "").lower() != expected_partuuid.lower():
        raise SafetyError(f"{prefix}{role} PARTUUID differs from identity")
    if export.get("TYPE", "").lower() != "ext4":
        raise SafetyError(f"{prefix}{role} filesystem type must be ext4")
    if export.get("LABEL") != expected_label:
        raise SafetyError(
            f"{prefix}{role} filesystem label must be {expected_label}"
        )


def _collect_and_validate_target_layout(runner, ids: IdentitySet, *, context=""):
    table_output = runner.run(["sfdisk", "--json", "/dev/mmcblk2"])
    try:
        table = json.loads(table_output)
    except json.JSONDecodeError as error:
        prefix = f"{context} " if context else ""
        raise SafetyError(f"{prefix}sfdisk --json produced invalid JSON: {error}") from error
    validate_sfdisk_json(table, ids)
    boot_export = _parse_blkid_export(
        runner.run(["blkid", "-o", "export", "/dev/mmcblk2p1"])
    )
    root_export = _parse_blkid_export(
        runner.run(["blkid", "-o", "export", "/dev/mmcblk2p2"])
    )
    _validate_blkid_identity(
        boot_export,
        role="BOOT",
        expected_uuid=ids.boot_uuid,
        expected_partuuid=ids.boot_partuuid,
        expected_label="BOOT",
        context=context,
    )
    _validate_blkid_identity(
        root_export,
        role="ROOTFS",
        expected_uuid=ids.root_uuid,
        expected_partuuid=ids.root_partuuid,
        expected_label="ROOTFS",
        context=context,
    )
    return table, boot_export, root_export


def run_provision(
    runner,
    system_paths: SystemPaths,
    prefix_path,
    prefix_manifest_path,
    backup_lock_path,
    identity_file,
    preflight_report,
    approved_report_sha256,
    authorization,
    report_path,
    write,
) -> dict:
    report = require_write_authorization(
        write, authorization, approved_report_sha256, preflight_report
    )
    facts = collect_device_facts(runner, system_paths)
    validate_device_facts(facts)
    _require_facts_equal_report(facts, report)
    _validate_prefix_and_lock(prefix_path, prefix_manifest_path, backup_lock_path)
    ids = load_identity(identity_file)
    _require_identity_matches_report(ids, report)

    runner.run(
        [
            "dd",
            f"if={prefix_path}",
            "of=/dev/mmcblk2",
            "bs=4M",
            "count=4",
            "iflag=fullblock",
            "conv=fsync,notrunc",
            "status=progress",
        ],
        visible=True,
    )
    written_prefix_sha256 = sha256_range(Path("/dev/mmcblk2"), 0, 16777216)
    if written_prefix_sha256 != CONTROL_PREFIX_SHA256:
        raise SafetyError("eMMC prefix read-back SHA-256 mismatch")
    runner.run(
        [
            "sfdisk",
            "--wipe",
            "always",
            "--wipe-partitions",
            "always",
            "/dev/mmcblk2",
        ],
        input_text=render_sfdisk(ids),
        visible=True,
    )
    runner.run(["partprobe", "/dev/mmcblk2"])
    runner.run(["udevadm", "settle"])
    runner.run(
        [
            "mkfs.ext4",
            "-F",
            "-b",
            "1024",
            "-m",
            "0",
            "-O",
            "^64bit,^metadata_csum",
            "-L",
            "BOOT",
            "-U",
            ids.boot_uuid,
            "/dev/mmcblk2p1",
        ],
        visible=True,
    )
    runner.run(
        [
            "mkfs.ext4",
            "-F",
            "-b",
            "4096",
            "-m",
            "1",
            "-O",
            "^64bit,^metadata_csum",
            "-L",
            "ROOTFS",
            "-U",
            ids.root_uuid,
            "/dev/mmcblk2p2",
        ],
        visible=True,
    )
    observed_table, observed_boot, observed_root = (
        _collect_and_validate_target_layout(runner, ids, context="post-write")
    )
    result = {
        "identity": identity_to_dict(ids),
        "prefix_sha256": CONTROL_PREFIX_SHA256,
        "readback_sha256": written_prefix_sha256,
        "gpt_script": render_sfdisk(ids),
        "boot_fs_uuid": ids.boot_uuid,
        "root_fs_uuid": ids.root_uuid,
        "observed_sfdisk": observed_table,
        "observed_boot_blkid": observed_boot,
        "observed_root_blkid": observed_root,
    }
    _write_json(report_path, result)
    return result


def atomic_write_text(path: Path, text: str, *, anchor: Path) -> None:
    path = Path(path)
    anchor = Path(anchor).resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(anchor):
        raise SafetyError(f"refusing to write outside the target root: {path}")
    temp = path.with_name(path.name + ".eaidk310-new")
    temp.write_text(text, encoding="utf-8")
    os.chmod(temp, path.stat().st_mode & 0o7777)
    os.chown(temp, path.stat().st_uid, path.stat().st_gid)
    os.replace(temp, path)


def _unmount_pair(
    runner,
    boot_mount,
    root_mount,
    *,
    boot_mounted: bool,
    root_mounted: bool,
) -> None:
    """Unmount only the target mounts that actually succeeded, boot before root."""
    failed = None
    if boot_mounted:
        try:
            runner.run(["umount", str(boot_mount)])
        except subprocess.CalledProcessError as error:
            failed = error
    if root_mounted:
        try:
            runner.run(["umount", str(root_mount)])
        except subprocess.CalledProcessError as error:
            failed = error
    if failed is not None:
        raise SafetyError(f"failed to unmount target mounts: {failed}")


def run_sync(
    runner,
    system_paths: SystemPaths,
    identity_file,
    exclude_file,
    preflight_report,
    approved_report_sha256,
    authorization,
    report_path,
    write,
) -> dict:
    report = require_write_authorization(
        write, authorization, approved_report_sha256, preflight_report
    )
    facts = collect_device_facts(runner, system_paths)
    validate_device_facts(facts)
    _require_facts_equal_report(facts, report)
    ids = load_identity(identity_file)
    _require_identity_matches_report(ids, report)
    if report.get("exclude_file_sha256", "").lower() != _file_sha256(
        Path(exclude_file)
    ).lower():
        raise SafetyError("exclude file differs from the preflight report")
    _collect_and_validate_target_layout(runner, ids, context="pre-sync")

    sync_root = Path(system_paths.sync_root)
    mounted_root = False
    mounted_boot = False
    try:
        runner.run(["mkdir", "-p", str(sync_root)])
        runner.run(["mount", "/dev/mmcblk2p2", str(sync_root)])
        mounted_root = True
        runner.run(["mkdir", "-p", str(sync_root / "boot")])
        runner.run(["mount", "/dev/mmcblk2p1", str(sync_root / "boot")])
        mounted_boot = True
        runner.run(root_rsync_args("/", str(sync_root), exclude_file), visible=True)
        runner.run(boot_rsync_args("/boot", str(sync_root / "boot")), visible=True)
        runner.run(
            [
                "systemctl",
                "stop",
                "tailscaled.service",
                "apt-daily.timer",
                "apt-daily-upgrade.timer",
                "unattended-upgrades.service",
            ]
        )
        runner.run(["sync"], visible=True)
        required_mountpoints = ensure_required_mountpoints(sync_root)
        runner.run(root_rsync_args("/", str(sync_root), exclude_file), visible=True)
        runner.run(boot_rsync_args("/boot", str(sync_root / "boot")), visible=True)
        root_changes = runner.run(
            root_rsync_args("/", str(sync_root), exclude_file, dry_run=True)
        )
        boot_changes = runner.run(
            boot_rsync_args("/boot", str(sync_root / "boot"), dry_run=True)
        )
        if root_changes.strip() or boot_changes.strip():
            raise SafetyError("unexplained dry-run differences remain on the target")
        target_fstab = sync_root / "etc/fstab"
        target_extlinux = sync_root / "boot/extlinux/extlinux.conf"
        for path in (target_fstab, target_extlinux):
            backup = path.with_name(path.name + ".pre-emmc-migration")
            backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        fstab_text = target_fstab.read_text(encoding="utf-8")
        atomic_write_text(
            target_fstab,
            rewrite_fstab(
                fstab_text, root_uuid=ids.root_uuid, boot_uuid=ids.boot_uuid
            ),
            anchor=sync_root,
        )
        extlinux_text = target_extlinux.read_text(encoding="utf-8")
        atomic_write_text(
            target_extlinux,
            rewrite_extlinux(extlinux_text, ids.root_uuid),
            anchor=sync_root,
        )
        runner.run(["sync"], visible=True)
        st = os.statvfs(str(sync_root))
        free_bytes = st.f_bavail * st.f_frsize
        if free_bytes < MIN_ROOT_FREE_BYTES:
            raise SafetyError(
                f"target ROOTFS free space {free_bytes} is below "
                f"{MIN_ROOT_FREE_BYTES}"
            )
        result = {
            "identity": identity_to_dict(ids),
            "target_root": str(sync_root),
            "root_dry_run_changes": [],
            "boot_dry_run_changes": [],
            "target_free_bytes": free_bytes,
            "rewritten": ["etc/fstab", "boot/extlinux/extlinux.conf"],
            "required_mountpoints": required_mountpoints,
        }
        _write_json(report_path, result)
        return result
    finally:
        _unmount_pair(
            runner,
            sync_root / "boot",
            sync_root,
            boot_mounted=mounted_boot,
            root_mounted=mounted_root,
        )


def _run_or_raise(runner, argv, *, message: str, visible: bool = False) -> str:
    try:
        return runner.run(argv, visible=visible)
    except subprocess.CalledProcessError as error:
        raise SafetyError(f"{message}: {error}") from error


def run_verify(
    runner, system_paths: SystemPaths, prefix_path, identity_file, report_path
) -> dict:
    facts = collect_device_facts(runner, system_paths)
    validate_device_facts(facts)
    ids = load_identity(identity_file)

    table, p1_export, p2_export = _collect_and_validate_target_layout(runner, ids)
    _run_or_raise(
        runner,
        ["sfdisk", "--verify", "/dev/mmcblk2"],
        message="sfdisk --verify failed",
        visible=True,
    )
    lba34_bytes = 34 * SECTOR_SIZE
    head_length = 8 * 1024 * 1024 - lba34_bytes
    device_head = sha256_range(Path("/dev/mmcblk2"), lba34_bytes, head_length)
    prefix_head = sha256_range(Path(prefix_path), lba34_bytes, head_length)
    if device_head != prefix_head:
        raise SafetyError("LBA34..8MiB differs from the control prefix")
    device_region = sha256_range(
        Path("/dev/mmcblk2"), 8 * 1024 * 1024, 4 * 1024 * 1024
    )
    if device_region != CONTROL_REGION_SHA256:
        raise SafetyError(
            f"post-GPT U-Boot region SHA-256 mismatch: expected "
            f"{CONTROL_REGION_SHA256}, got {device_region}"
        )
    device_tail = sha256_range(
        Path("/dev/mmcblk2"), 12 * 1024 * 1024, 4 * 1024 * 1024
    )
    if device_tail != BASELINE_TAIL_SHA256:
        raise SafetyError(
            f"post-GPT 12-16MiB SHA-256 mismatch: expected {BASELINE_TAIL_SHA256}, "
            f"got {device_tail}"
        )
    _run_or_raise(
        runner,
        ["e2fsck", "-fn", "/dev/mmcblk2p1"],
        message="e2fsck failed on BOOT",
        visible=True,
    )
    _run_or_raise(
        runner,
        ["e2fsck", "-fn", "/dev/mmcblk2p2"],
        message="e2fsck failed on ROOTFS",
        visible=True,
    )

    verify_root = Path(system_paths.verify_root)
    mounted_root = False
    mounted_boot = False
    try:
        (verify_root / "boot").mkdir(parents=True, exist_ok=True)
        runner.run(["mount", "-o", "ro", "/dev/mmcblk2p2", str(verify_root)])
        mounted_root = True
        runner.run(["mount", "-o", "ro", "/dev/mmcblk2p1", str(verify_root / "boot")])
        mounted_boot = True
        required_mountpoints = validate_required_mountpoints(verify_root)
        key_files = [
            "etc/fstab",
            "etc/ssh/sshd_config",
            "etc/nftables.conf",
            "var/lib/dpkg/status",
            "home/Fog",
            "boot/Image",
            "boot/uInitrd",
            "boot/dtb/rockchip/rk3328-eaidk-310.dtb",
            "boot/extlinux/extlinux.conf",
        ]
        key_file_hashes = {}
        for relative in key_files:
            candidate = verify_root / relative
            if not candidate.exists():
                raise SafetyError(f"target key file missing: {relative}")
            if candidate.is_file():
                key_file_hashes[relative] = _file_sha256(candidate)
            else:
                key_file_hashes[relative] = "dir"
        source_boot = _parse_blkid_export(
            runner.run(["blkid", "-o", "export", "/dev/mmcblk0p1"])
        )
        source_root = _parse_blkid_export(
            runner.run(["blkid", "-o", "export", "/dev/mmcblk0p2"])
        )
        forbidden = {
            source_boot.get("UUID", "").lower(),
            source_boot.get("PARTUUID", "").lower(),
            source_root.get("UUID", "").lower(),
            source_root.get("PARTUUID", "").lower(),
        }
        forbidden.discard("")
        fstab_text = (verify_root / "etc/fstab").read_text(encoding="utf-8")
        extlinux_text = (verify_root / "boot/extlinux/extlinux.conf").read_text(
            encoding="utf-8"
        )
        for value in forbidden:
            if value in fstab_text.lower() or value in extlinux_text.lower():
                raise SafetyError(
                    f"target config still references source identifier {value}"
                )
        validate_target_fstab(
            fstab_text, root_uuid=ids.root_uuid, boot_uuid=ids.boot_uuid
        )
        validate_target_extlinux(extlinux_text, ids.root_uuid)
        st = os.statvfs(str(verify_root))
        free_bytes = st.f_bavail * st.f_frsize
        if free_bytes < MIN_ROOT_FREE_BYTES:
            raise SafetyError(
                f"target ROOTFS free space {free_bytes} is below "
                f"{MIN_ROOT_FREE_BYTES}"
            )
        result = {
            "identity": identity_to_dict(ids),
            "prefix_region_sha256": device_region,
            "prefix_tail_sha256": device_tail,
            "sfdisk_verify": "ok",
            "boot_fs_uuid": p1_export.get("UUID"),
            "boot_partuuid": p1_export.get("PARTUUID"),
            "root_fs_uuid": p2_export.get("UUID"),
            "root_partuuid": p2_export.get("PARTUUID"),
            "observed_sfdisk": table,
            "observed_boot_blkid": p1_export,
            "observed_root_blkid": p2_export,
            "key_files": key_files,
            "key_file_hashes": key_file_hashes,
            "target_fstab_sha256": _file_sha256(verify_root / "etc/fstab"),
            "target_extlinux_sha256": _file_sha256(
                verify_root / "boot/extlinux/extlinux.conf"
            ),
            "target_free_bytes": free_bytes,
            "required_mountpoints": required_mountpoints,
        }
        _write_json(report_path, result)
        return result
    finally:
        _unmount_pair(
            runner,
            verify_root / "boot",
            verify_root,
            boot_mounted=mounted_boot,
            root_mounted=mounted_root,
        )


def _handle_preflight(args) -> dict:
    runner = CommandRunner()
    system_paths = SystemPaths()
    return run_preflight(
        runner,
        system_paths,
        args.prefix,
        args.prefix_manifest,
        args.backup_lock,
        args.exclude_file,
        args.identity_file,
        args.report,
    )


def _handle_provision(args) -> dict:
    runner = CommandRunner()
    system_paths = SystemPaths()
    return run_provision(
        runner,
        system_paths,
        args.prefix,
        args.prefix_manifest,
        args.backup_lock,
        args.identity_file,
        args.preflight_report,
        args.approved_report_sha256,
        args.authorization,
        args.report,
        args.write,
    )


def _handle_sync(args) -> dict:
    runner = CommandRunner()
    system_paths = SystemPaths()
    return run_sync(
        runner,
        system_paths,
        args.identity_file,
        args.exclude_file,
        args.preflight_report,
        args.approved_report_sha256,
        args.authorization,
        args.report,
        args.write,
    )


def _handle_verify(args) -> dict:
    runner = CommandRunner()
    system_paths = SystemPaths()
    return run_verify(
        runner, system_paths, args.prefix, args.identity_file, args.report
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "EAIDK310 eMMC migration. preflight/verify are read-only; "
            "provision/sync require --write plus the authorization token and the "
            "preflight report SHA-256."
        )
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    preflight = subparsers.add_parser("preflight")
    preflight.add_argument("--prefix", required=True)
    preflight.add_argument("--prefix-manifest", required=True)
    preflight.add_argument("--backup-lock", required=True)
    preflight.add_argument("--exclude-file", required=True)
    preflight.add_argument("--identity-file", required=True)
    preflight.add_argument("--report", required=True)
    preflight.set_defaults(handler=_handle_preflight)

    provision = subparsers.add_parser("provision")
    provision.add_argument("--prefix", required=True)
    provision.add_argument("--prefix-manifest", required=True)
    provision.add_argument("--backup-lock", required=True)
    provision.add_argument("--identity-file", required=True)
    provision.add_argument("--preflight-report", required=True)
    provision.add_argument("--approved-report-sha256", required=True)
    provision.add_argument("--authorization", required=True)
    provision.add_argument("--report", required=True)
    provision.add_argument("--write", action="store_true")
    provision.set_defaults(handler=_handle_provision)

    sync = subparsers.add_parser("sync")
    sync.add_argument("--identity-file", required=True)
    sync.add_argument("--exclude-file", required=True)
    sync.add_argument("--preflight-report", required=True)
    sync.add_argument("--approved-report-sha256", required=True)
    sync.add_argument("--authorization", required=True)
    sync.add_argument("--report", required=True)
    sync.add_argument("--write", action="store_true")
    sync.set_defaults(handler=_handle_sync)

    verify = subparsers.add_parser("verify")
    verify.add_argument("--prefix", required=True)
    verify.add_argument("--identity-file", required=True)
    verify.add_argument("--report", required=True)
    verify.set_defaults(handler=_handle_verify)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    try:
        result = args.handler(args)
    except SafetyError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
