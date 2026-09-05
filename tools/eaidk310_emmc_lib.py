"""Pure eMMC safety, layout, and configuration rewriting for EAIDK310 migration.

This module must stay free of any raw block-device access.  All functions here
are deterministic and safe to test against temporary files.
"""

from __future__ import annotations

import hashlib
import os
import re
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

TARGET_DEVICE = "/dev/mmcblk2"
EXPECTED_ROOT = "/dev/mmcblk0p2"
EXPECTED_BOOT = "/dev/mmcblk0p1"
EXPECTED_MODEL = "HBD08G"
SECTOR_SIZE = 512
TOTAL_SECTORS = 15269888
P1_START, P1_END = 32768, 557055
P2_START, P2_END = 557056, 15269854
BACKUP_GPT_ENTRIES_START = 15269855
BACKUP_GPT_HEADER_LBA = 15269887
MIN_ROOT_FREE_BYTES = 1073741824
BOOT_TYPE_GUID = "bc13c2ff-59e6-4262-a352-b275fd6f7172"
ROOT_TYPE_GUID = "0fc63daf-8483-4772-8e79-3d69d8477de4"
CONTROL_PREFIX_SHA256 = "C466D977606598A7C1F1613A28B99D7D937EAE9E05792D2FA82C437B5C92E0B1"
CONTROL_REGION_SHA256 = "6C11904BD33CDFB475CCD1AC8DC81C73DB507BBC735A64FF0AEE3BD2AF022EDF"
BASELINE_TAIL_SHA256 = "870A69106ADD87429598171DD582FA9E215BF843F310C3EF761E5BB59C873902"
REQUIRED_ROOT_MOUNTPOINTS = (
    ("dev", 0o755),
    ("proc", 0o555),
    ("sys", 0o555),
    ("run", 0o755),
    ("tmp", 0o1777),
    ("mnt", 0o755),
    ("media", 0o755),
)


class SafetyError(RuntimeError):
    pass


@dataclass(frozen=True)
class DeviceFacts:
    root_source: str
    boot_source: str
    target_model: str
    target_sectors: int
    target_mounts: Sequence[str]
    swap_devices: Sequence[str]


@dataclass(frozen=True)
class IdentitySet:
    disk_guid: str
    boot_partuuid: str
    root_partuuid: str
    boot_uuid: str
    root_uuid: str


def validate_device_facts(facts: DeviceFacts) -> None:
    """Reject anything but the exact, expected TF source and HBD08G target."""
    if facts.root_source != EXPECTED_ROOT:
        raise SafetyError(
            f"root source must be {EXPECTED_ROOT}, got {facts.root_source}"
        )
    if facts.boot_source != EXPECTED_BOOT:
        raise SafetyError(
            f"BOOT source must be {EXPECTED_BOOT}, got {facts.boot_source}"
        )
    if facts.target_model != EXPECTED_MODEL:
        raise SafetyError(
            f"target eMMC model must be {EXPECTED_MODEL}, got {facts.target_model}"
        )
    if facts.target_sectors != TOTAL_SECTORS:
        raise SafetyError(
            f"target eMMC must be {TOTAL_SECTORS} 512-byte sectors, "
            f"got {facts.target_sectors}"
        )
    for mount in facts.target_mounts:
        if "/dev/mmcblk2" in mount:
            raise SafetyError(f"target eMMC is mounted: {mount}")
    for swap in facts.swap_devices:
        if swap == TARGET_DEVICE or swap.startswith(TARGET_DEVICE + "p"):
            raise SafetyError(f"target eMMC is active swap: {swap}")


def validate_required_mountpoints(root: Path) -> dict[str, str]:
    """Require the empty root-level directories needed by initramfs switch_root."""
    observed = {}
    for name, expected_mode in REQUIRED_ROOT_MOUNTPOINTS:
        candidate = root / name
        try:
            metadata = os.lstat(candidate)
        except FileNotFoundError as error:
            raise SafetyError(f"required root mountpoint missing: {name}") from error
        if not stat.S_ISDIR(metadata.st_mode):
            raise SafetyError(f"required root mountpoint is not a directory: {name}")
        actual_mode = stat.S_IMODE(metadata.st_mode)
        if os.name == "posix" and actual_mode != expected_mode:
            raise SafetyError(
                f"required root mountpoint mode for {name} is {actual_mode:04o}, "
                f"expected {expected_mode:04o}"
            )
        observed[name] = f"{expected_mode:04o}"
    return observed


def ensure_required_mountpoints(root: Path) -> dict[str, str]:
    """Create and normalize the root-level mountpoints omitted by rsync excludes."""
    for name, mode in REQUIRED_ROOT_MOUNTPOINTS:
        candidate = root / name
        try:
            metadata = os.lstat(candidate)
        except FileNotFoundError:
            candidate.mkdir(parents=True)
        else:
            if not stat.S_ISDIR(metadata.st_mode):
                raise SafetyError(
                    f"required root mountpoint is not a directory: {name}"
                )
        os.chmod(candidate, mode)
    return validate_required_mountpoints(root)


def sha256_range(path: Path, offset: int, length: int, chunk_size: int = 1048576) -> str:
    """Return the uppercase SHA-256 of ``length`` bytes at ``offset`` in a file.

    The read is bounded to ``length`` bytes in ``chunk_size`` pieces and fails
    with :class:`SafetyError` on an early end of file so a short device read
    can never be mistaken for a full read-back.
    """
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        handle.seek(offset)
        remaining = length
        while remaining > 0:
            chunk = handle.read(min(chunk_size, remaining))
            if not chunk:
                raise SafetyError(
                    f"short read from {path}: expected {length} bytes at offset {offset}"
                )
            digest.update(chunk)
            remaining -= len(chunk)
    return digest.hexdigest().upper()


def generate_identities(source_identifiers: set[str]) -> IdentitySet:
    """Generate five distinct UUIDv4 values that collide with nothing."""
    source = {identifier.lower() for identifier in source_identifiers}
    while True:
        ids = IdentitySet(
            disk_guid=str(uuid.uuid4()),
            boot_partuuid=str(uuid.uuid4()),
            root_partuuid=str(uuid.uuid4()),
            boot_uuid=str(uuid.uuid4()),
            root_uuid=str(uuid.uuid4()),
        )
        values = {
            ids.disk_guid,
            ids.boot_partuuid,
            ids.root_partuuid,
            ids.boot_uuid,
            ids.root_uuid,
        }
        if len(values) == 5 and not values & source:
            return ids


def render_sfdisk(ids: IdentitySet) -> str:
    """Render the exact GPT input for the fixed HBD08G layout."""
    return (
        "label: gpt\n"
        f"label-id: {ids.disk_guid}\n"
        "unit: sectors\n"
        "first-lba: 34\n"
        "last-lba: 15269854\n"
        "sector-size: 512\n\n"
        "start=32768, size=524288, "
        f"type={BOOT_TYPE_GUID}, uuid={ids.boot_partuuid}, "
        'name="XBOOTLDR partition"\n'
        "start=557056, size=14712799, "
        f"type={ROOT_TYPE_GUID}, uuid={ids.root_partuuid}, "
        'name="Linux filesystem"\n'
    )


def validate_sfdisk_json(data: dict, ids: IdentitySet) -> None:
    """Validate ``sfdisk --json`` output byte-for-byte against the layout."""
    table = data.get("partitiontable")
    if not isinstance(table, dict):
        raise SafetyError("sfdisk JSON lacks a partitiontable")
    if table.get("label") != "gpt":
        raise SafetyError("partition table label must be gpt")
    if str(table.get("id", "")).lower() != ids.disk_guid.lower():
        raise SafetyError(
            f"disk GUID must be {ids.disk_guid}, got {table.get('id')}"
        )
    sector_size = table.get("sectorsize", table.get("sector_size"))
    try:
        sector_size_int = int(sector_size)
    except (TypeError, ValueError):
        raise SafetyError(
            f"partition table sector size must be {SECTOR_SIZE}, got {sector_size}"
        )
    if sector_size_int != SECTOR_SIZE:
        raise SafetyError(
            f"partition table sector size must be {SECTOR_SIZE}, got {sector_size}"
        )
    partitions = table.get("partitions")
    if not isinstance(partitions, list) or len(partitions) != 2:
        raise SafetyError("partition table must have exactly two partitions")
    try:
        first_lba = int(table.get("firstlba"))
    except (TypeError, ValueError):
        raise SafetyError("partition table is missing a numeric firstlba")
    if first_lba != 34:
        raise SafetyError(f"first usable LBA must be 34, got {first_lba}")
    try:
        last_lba = int(table.get("lastlba"))
    except (TypeError, ValueError):
        raise SafetyError("partition table is missing a numeric lastlba")
    if last_lba != P2_END:
        raise SafetyError(f"last usable LBA must be {P2_END}, got {last_lba}")
    expected = (
        (
            P1_START,
            P1_END,
            BOOT_TYPE_GUID,
            ids.boot_partuuid,
            "XBOOTLDR partition",
        ),
        (P2_START, P2_END, ROOT_TYPE_GUID, ids.root_partuuid, "Linux filesystem"),
    )
    for partition, (start, end, _type_guid, _part_uuid, _name) in zip(
        partitions, expected
    ):
        try:
            actual_start = int(partition.get("start"))
            actual_size = int(partition.get("size"))
        except (TypeError, ValueError):
            raise SafetyError("partition is missing numeric start/size")
        if actual_start != start:
            raise SafetyError(f"partition start LBA must be {start}, got {actual_start}")
        if actual_start + actual_size - 1 != end:
            raise SafetyError(
                f"partition end LBA must be {end}, got {actual_start + actual_size - 1}"
            )
    reported_uuids = [str(partition.get("uuid")).lower() for partition in partitions]
    disk_guid = str(table.get("id")).lower()
    for index, reported_uuid in enumerate(reported_uuids):
        if reported_uuid in reported_uuids[:index]:
            raise SafetyError(f"reused partition UUID {reported_uuid}")
        if reported_uuid == disk_guid:
            raise SafetyError(f"partition UUID {reported_uuid} equals the disk GUID")
    for partition, (_start, _end, type_guid, part_uuid, name) in zip(
        partitions, expected
    ):
        if str(partition.get("type")).lower() != type_guid.lower():
            raise SafetyError(f"partition type GUID must be {type_guid}")
        if str(partition.get("uuid")).lower() != part_uuid.lower():
            raise SafetyError(f"partition UUID must be {part_uuid}")
        if partition.get("name") != name:
            raise SafetyError(f"partition name must be {name}")


def rewrite_fstab(fstab: str, *, root_uuid: str, boot_uuid: str) -> str:
    """Rewrite only the source fields of the ``/`` and ``/boot`` fstab lines."""
    output: list[str] = []
    root_count = 0
    boot_count = 0
    for line in fstab.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            output.append(line)
            continue
        fields = stripped.split()
        if len(fields) < 2:
            output.append(line)
            continue
        mount_point = fields[1]
        if mount_point == "/":
            fields[0] = f"UUID={root_uuid}"
            output.append("\t".join(fields))
            root_count += 1
        elif mount_point == "/boot":
            fields[0] = f"UUID={boot_uuid}"
            output.append("\t".join(fields))
            boot_count += 1
        else:
            output.append(line)
    if root_count != 1 or boot_count != 1:
        raise SafetyError(
            "fstab must contain exactly one '/' entry and one '/boot' entry"
        )
    return "\n".join(output) + "\n"


_STABLE_LABEL = "rockchip-kernel-6.8.4"
_STABLE_FDT = "/dtb/rockchip/rk3328-eaidk-310.dtb"
_ROOT_TOKEN = re.compile(r"\broot=(?:UUID|PARTUUID|LABEL)=[^\s]+", re.IGNORECASE)


def rewrite_extlinux(extlinux: str, root_uuid: str) -> str:
    """Make the stable extlinux stanza the default with the new root UUID.

    Every retained ``APPEND`` line must already carry a ``root=`` token so the
    rewritten output is provably bootable, not merely free of old identifiers.
    """
    output: list[str] = []
    current_label: str | None = None
    stable_label_seen = False
    stable_fdt_seen = False
    stable_append_root_seen = False
    for line in extlinux.splitlines():
        tokens = line.strip().split()
        if not tokens:
            continue
        if tokens[0].lower() == "default":
            continue
        if tokens[0].lower() == "label":
            current_label = tokens[1]
            if current_label == _STABLE_LABEL:
                stable_label_seen = True
            output.append(line)
            continue
        if tokens[0] == "FDT" and current_label == _STABLE_LABEL:
            if tokens[1] != _STABLE_FDT:
                raise SafetyError("stable label must use the stable FDT")
            stable_fdt_seen = True
        if tokens[0].upper() == "APPEND":
            if not _ROOT_TOKEN.search(line):
                raise SafetyError("extlinux APPEND line lacks a root= token")
            if current_label == _STABLE_LABEL:
                stable_append_root_seen = True
            line = _ROOT_TOKEN.sub(f"root=UUID={root_uuid}", line)
        output.append(line)
    if not (stable_label_seen and stable_fdt_seen and stable_append_root_seen):
        raise SafetyError(
            "extlinux must define the stable label, stable FDT, and a root= APPEND"
        )
    return "default rockchip-kernel-6.8.4\n" + "\n".join(output) + "\n"


def validate_target_fstab(fstab: str, *, root_uuid: str, boot_uuid: str) -> None:
    """Require the rewritten target fstab to positively reference the identity UUIDs."""
    root_sources: list[str] = []
    boot_sources: list[str] = []
    for line in fstab.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        if len(fields) < 2:
            continue
        if fields[1] == "/":
            root_sources.append(fields[0].lower())
        elif fields[1] == "/boot":
            boot_sources.append(fields[0].lower())
    if len(root_sources) != 1 or len(boot_sources) != 1:
        raise SafetyError(
            "target fstab must contain exactly one '/' entry and one '/boot' entry"
        )
    if root_sources[0] != f"uuid={root_uuid.lower()}" or boot_sources[0] != (
        f"uuid={boot_uuid.lower()}"
    ):
        raise SafetyError(
            "target fstab does not reference the identity root and boot UUIDs"
        )


def validate_target_extlinux(extlinux: str, root_uuid: str) -> None:
    """Require one stable default stanza and identity-rooted APPEND lines."""
    defaults: list[str] = []
    current_label: str | None = None
    stable_labels = 0
    stable_fdts = 0
    stable_appends = 0
    append_tokens: list[str] = []
    expected = f"root=uuid={root_uuid.lower()}"
    for line in extlinux.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        fields = stripped.split()
        keyword = fields[0].lower()
        if keyword == "default":
            defaults.append(fields[1] if len(fields) == 2 else "")
            continue
        if keyword == "label":
            current_label = fields[1] if len(fields) >= 2 else ""
            if current_label == _STABLE_LABEL:
                stable_labels += 1
            continue
        if keyword == "fdt" and current_label == _STABLE_LABEL:
            if len(fields) != 2 or fields[1] != _STABLE_FDT:
                raise SafetyError("stable label must use the stable FDT")
            stable_fdts += 1
            continue
        if keyword != "append":
            continue
        matches = _ROOT_TOKEN.findall(stripped)
        if not matches:
            raise SafetyError("target extlinux APPEND line contains no root= token")
        if len(matches) != 1:
            raise SafetyError(
                "target extlinux APPEND line must contain exactly one root= token"
            )
        token = matches[0]
        append_tokens.append(token)
        if token.lower() != expected:
            raise SafetyError(
                f"target extlinux root token {token} differs from the identity root UUID"
            )
        if current_label == _STABLE_LABEL:
            stable_appends += 1
    if defaults != [_STABLE_LABEL]:
        raise SafetyError(f"target extlinux default must be {_STABLE_LABEL}")
    if stable_labels != 1:
        raise SafetyError("target extlinux must contain exactly one stable label")
    if stable_fdts != 1:
        raise SafetyError("stable label must use exactly one stable FDT")
    if stable_appends != 1:
        raise SafetyError("stable label must contain exactly one root= APPEND")
    if not append_tokens:
        raise SafetyError("target extlinux contains no root= token")


def root_rsync_args(source: str, target: str, exclude_file: str, dry_run: bool = False) -> list[str]:
    args = [
        "rsync",
        "-aHAXx",
        "--numeric-ids",
        "--delete",
        f"--exclude-from={exclude_file}",
    ]
    if dry_run:
        args += ["--dry-run", "--itemize-changes"]
    return args + [source.rstrip("/") + "/", target.rstrip("/") + "/"]


def boot_rsync_args(source: str, target: str, dry_run: bool = False) -> list[str]:
    args = ["rsync", "-aHAX", "--numeric-ids", "--delete"]
    if dry_run:
        args += ["--dry-run", "--itemize-changes"]
    return args + [source.rstrip("/") + "/", target.rstrip("/") + "/"]
