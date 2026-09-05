import dataclasses
import hashlib
import os
import pathlib
import stat
import sys
import tempfile
import types
import unittest
from unittest import mock

TOOLS_DIR = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))

import eaidk310_emmc_lib as emmc_lib  # noqa: E402
from eaidk310_emmc_lib import (  # noqa: E402
    BACKUP_GPT_HEADER_LBA,
    BASELINE_TAIL_SHA256,
    BOOT_TYPE_GUID,
    CONTROL_PREFIX_SHA256,
    CONTROL_REGION_SHA256,
    P1_END,
    P1_START,
    P2_END,
    P2_START,
    ROOT_TYPE_GUID,
    TOTAL_SECTORS,
    DeviceFacts,
    IdentitySet,
    SafetyError,
    boot_rsync_args,
    generate_identities,
    render_sfdisk,
    rewrite_extlinux,
    rewrite_fstab,
    root_rsync_args,
    sha256_range,
    validate_device_facts,
    validate_sfdisk_json,
    validate_target_extlinux,
    validate_target_fstab,
)


REQUIRED_MOUNTPOINTS = {
    "dev": "0755",
    "proc": "0555",
    "sys": "0555",
    "run": "0755",
    "tmp": "1777",
    "mnt": "0755",
    "media": "0755",
}


def valid_facts() -> DeviceFacts:
    return DeviceFacts(
        root_source="/dev/mmcblk0p2",
        boot_source="/dev/mmcblk0p1",
        target_model="HBD08G",
        target_sectors=15269888,
        target_mounts=(),
        swap_devices=(),
    )


class DeviceSafetyTests(unittest.TestCase):
    def test_exact_facts_pass(self):
        validate_device_facts(valid_facts())

    def test_wrong_root_is_rejected(self):
        facts = dataclasses.replace(valid_facts(), root_source="/dev/mmcblk2p2")
        with self.assertRaisesRegex(SafetyError, "root source"):
            validate_device_facts(facts)

    def test_wrong_boot_source_is_rejected(self):
        facts = dataclasses.replace(valid_facts(), boot_source="/dev/mmcblk2p1")
        with self.assertRaisesRegex(SafetyError, "BOOT source"):
            validate_device_facts(facts)

    def test_wrong_model_is_rejected(self):
        facts = dataclasses.replace(valid_facts(), target_model="UNKNOWN")
        with self.assertRaisesRegex(SafetyError, "HBD08G"):
            validate_device_facts(facts)

    def test_wrong_sector_count_is_rejected(self):
        facts = dataclasses.replace(valid_facts(), target_sectors=15269887)
        with self.assertRaisesRegex(SafetyError, "15269888"):
            validate_device_facts(facts)

    def test_any_target_mount_is_rejected(self):
        facts = dataclasses.replace(valid_facts(), target_mounts=("/dev/mmcblk2p1 /mnt/x",))
        with self.assertRaisesRegex(SafetyError, "mounted"):
            validate_device_facts(facts)

    def test_target_swap_is_rejected(self):
        facts = dataclasses.replace(valid_facts(), swap_devices=("/dev/mmcblk2",))
        with self.assertRaisesRegex(SafetyError, "swap"):
            validate_device_facts(facts)
        facts = dataclasses.replace(valid_facts(), swap_devices=("/dev/mmcblk2p1",))
        with self.assertRaisesRegex(SafetyError, "swap"):
            validate_device_facts(facts)
        facts = dataclasses.replace(valid_facts(), swap_devices=("/dev/zram0",))
        validate_device_facts(facts)


class LayoutGeometryTests(unittest.TestCase):
    def test_fixed_geometry(self):
        self.assertEqual(P1_END - P1_START + 1, 524288)
        self.assertEqual(P2_END - P2_START + 1, 14712799)
        self.assertEqual((P2_END - P2_START + 1) * 512, 7532953088)
        self.assertEqual(BACKUP_GPT_HEADER_LBA, TOTAL_SECTORS - 1)
        self.assertEqual(BACKUP_GPT_HEADER_LBA, 15269887)
        self.assertEqual(P2_END, 15269854)
        self.assertEqual(TOTAL_SECTORS, 15269888)


class Sha256RangeTests(unittest.TestCase):
    def test_abc_vector(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "abc.bin"
            path.write_bytes(b"abc")
            self.assertEqual(
                sha256_range(path, 0, 3), hashlib.sha256(b"abc").hexdigest().upper()
            )

    def test_offset_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "data.bin"
            path.write_bytes(b"abcdef")
            self.assertEqual(
                sha256_range(path, 2, 3), hashlib.sha256(b"cde").hexdigest().upper()
            )

    def test_early_eof_raises(self):
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "short.bin"
            path.write_bytes(b"short")
            with self.assertRaisesRegex(SafetyError, "short read"):
                sha256_range(path, 0, 10)


class IdentityTests(unittest.TestCase):
    def test_generate_identities_is_unique_and_avoids_source(self):
        source = "bd2a6dbf-c55f-4d5e-8738-797e581ee7c9"
        ids = generate_identities({source})
        values = {
            ids.disk_guid,
            ids.boot_partuuid,
            ids.root_partuuid,
            ids.boot_uuid,
            ids.root_uuid,
        }
        self.assertEqual(len(values), 5)
        self.assertNotIn(source.lower(), values)
        self.assertNotEqual(ids.root_uuid, source)

    def test_render_sfdisk_contains_exact_layout(self):
        ids = generate_identities({"bd2a6dbf-c55f-4d5e-8738-797e581ee7c9"})
        script = render_sfdisk(ids)
        self.assertIn("label: gpt", script)
        self.assertIn("last-lba: 15269854", script)
        self.assertIn("start=32768, size=524288", script)
        self.assertIn("start=557056, size=14712799", script)
        self.assertIn(BOOT_TYPE_GUID, script)
        self.assertIn(ROOT_TYPE_GUID, script)
        self.assertIn(f"uuid={ids.boot_partuuid}", script)
        self.assertIn(f"uuid={ids.root_partuuid}", script)
        self.assertIn(ids.disk_guid, script)


def sfdisk_json(ids: IdentitySet, **overrides) -> dict:
    data = {
        "partitiontable": {
            "label": "gpt",
            "id": ids.disk_guid,
            "device": "/dev/mmcblk2",
            "unit": "sectors",
            "firstlba": 34,
            "lastlba": 15269854,
            "sectorsize": 512,
            "partitions": [
                {
                    "node": "/dev/mmcblk2p1",
                    "start": 32768,
                    "size": 524288,
                    "type": BOOT_TYPE_GUID,
                    "uuid": ids.boot_partuuid,
                    "name": "XBOOTLDR partition",
                },
                {
                    "node": "/dev/mmcblk2p2",
                    "start": 557056,
                    "size": 14712799,
                    "type": ROOT_TYPE_GUID,
                    "uuid": ids.root_partuuid,
                    "name": "Linux filesystem",
                },
            ],
        }
    }
    for key, value in overrides.items():
        if key == "partitions":
            data["partitiontable"]["partitions"] = value
        elif key in ("firstlba", "lastlba", "sectorsize", "label", "id"):
            data["partitiontable"][key] = value
        else:
            raise KeyError(f"unsupported override: {key}")
    return data


class SfdiskJsonValidationTests(unittest.TestCase):
    def setUp(self):
        self.ids = IdentitySet(
            disk_guid="aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee",
            boot_partuuid="11111111-2222-4333-8444-555566667777",
            root_partuuid="22222222-3333-4333-8444-555566667777",
            boot_uuid="33333333-4444-4333-8444-555566667777",
            root_uuid="44444444-5555-4333-8444-555566667777",
        )

    def test_valid_table_passes(self):
        validate_sfdisk_json(sfdisk_json(self.ids), self.ids)

    def test_end_lba_off_by_one_is_rejected(self):
        partitions = sfdisk_json(self.ids)["partitiontable"]["partitions"]
        partitions[0]["size"] = 524289
        with self.assertRaisesRegex(SafetyError, "end LBA"):
            validate_sfdisk_json(sfdisk_json(self.ids, partitions=partitions), self.ids)

    def test_wrong_type_guid_is_rejected(self):
        partitions = sfdisk_json(self.ids)["partitiontable"]["partitions"]
        partitions[1]["type"] = "0fc63daf-8483-4772-8e79-3d69d8477de5"
        with self.assertRaisesRegex(SafetyError, "type GUID"):
            validate_sfdisk_json(sfdisk_json(self.ids, partitions=partitions), self.ids)

    def test_reused_partuuid_is_rejected(self):
        partitions = sfdisk_json(self.ids)["partitiontable"]["partitions"]
        partitions[1]["uuid"] = partitions[0]["uuid"]
        with self.assertRaisesRegex(SafetyError, "reused"):
            validate_sfdisk_json(sfdisk_json(self.ids, partitions=partitions), self.ids)

    def test_wrong_last_usable_lba_is_rejected(self):
        with self.assertRaisesRegex(SafetyError, "last usable LBA"):
            validate_sfdisk_json(sfdisk_json(self.ids, lastlba=15269853), self.ids)

    def test_wrong_first_usable_lba_is_rejected(self):
        with self.assertRaisesRegex(SafetyError, "first usable LBA"):
            validate_sfdisk_json(sfdisk_json(self.ids, firstlba=35), self.ids)

    def test_wrong_sector_size_is_rejected(self):
        with self.assertRaisesRegex(SafetyError, "sector size"):
            validate_sfdisk_json(sfdisk_json(self.ids, sectorsize=4096), self.ids)

    def test_wrong_disk_guid_is_rejected(self):
        with self.assertRaisesRegex(SafetyError, "disk GUID"):
            validate_sfdisk_json(
                sfdisk_json(self.ids, id="99999999-9999-4999-8999-999999999999"),
                self.ids,
            )

    def test_extra_partition_is_rejected(self):
        partitions = sfdisk_json(self.ids)["partitiontable"]["partitions"]
        partitions.append(
            dict(
                partitions[1],
                node="/dev/mmcblk2p3",
                uuid="99999999-9999-4999-8999-999999999999",
            )
        )
        with self.assertRaisesRegex(SafetyError, "exactly two"):
            validate_sfdisk_json(sfdisk_json(self.ids, partitions=partitions), self.ids)

    def test_wrong_partition_name_is_rejected(self):
        partitions = sfdisk_json(self.ids)["partitiontable"]["partitions"]
        partitions[0]["name"] = "BOOT"
        with self.assertRaisesRegex(SafetyError, "partition name"):
            validate_sfdisk_json(sfdisk_json(self.ids, partitions=partitions), self.ids)


FSTAB = """# source system
UUID=bd2a6dbf-c55f-4d5e-8738-797e581ee7c9 / ext4 defaults,noatime 0 1
UUID=11111111-2222-3333-4444-555555555555 /boot ext4 defaults,noatime 0 2
"""


class FstabRewriteTests(unittest.TestCase):
    def test_rewrites_only_root_and_boot_sources(self):
        fstab = rewrite_fstab(
            FSTAB,
            root_uuid="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            boot_uuid="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        )
        self.assertIn("UUID=aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa\t/\t", fstab)
        self.assertIn("UUID=bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb\t/boot\t", fstab)
        self.assertNotIn("bd2a6dbf", fstab)
        self.assertNotIn("11111111-2222-3333-4444-555555555555", fstab)

    def test_preserves_comment_and_other_mounts(self):
        fstab = rewrite_fstab(
            FSTAB,
            root_uuid="aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            boot_uuid="bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
        )
        self.assertIn("# source system", fstab)

    def test_missing_root_or_boot_entry_is_rejected(self):
        with self.assertRaisesRegex(SafetyError, "exactly one"):
            rewrite_fstab(
                "# nothing\n", root_uuid="a", boot_uuid="b"
            )


EXTLINUX = """default rockchip-kernel-6.8.4-wifi-test2
    label rockchip-kernel-6.8.4
    LINUX /Image
    FDT /dtb/rockchip/rk3328-eaidk-310.dtb
    INITRD /uInitrd
    APPEND root=UUID=bd2a6dbf-c55f-4d5e-8738-797e581ee7c9 rootwait rootfstype=ext4 console=ttyS2,1500000n8
    label rockchip-kernel-6.8.4-wifi-test2
    FDT /dtb/rockchip/rk3328-eaidk310-wifi-test2.dtb
    APPEND root=UUID=bd2a6dbf-c55f-4d5e-8738-797e581ee7c9 rootwait
"""


class ExtlinuxRewriteTests(unittest.TestCase):
    def test_rewrite_extlinux(self):
        extlinux = rewrite_extlinux(
            EXTLINUX, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
        )
        self.assertTrue(extlinux.startswith("default rockchip-kernel-6.8.4\n"))
        self.assertEqual(
            extlinux.count("root=UUID=aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"), 2
        )
        self.assertIn("FDT /dtb/rockchip/rk3328-eaidk-310.dtb", extlinux)
        self.assertIn("label rockchip-kernel-6.8.4-wifi-test2", extlinux)
        self.assertNotIn("default rockchip-kernel-6.8.4-wifi-test2", extlinux)

    def test_missing_stable_label_is_rejected(self):
        with self.assertRaisesRegex(SafetyError, "stable"):
            rewrite_extlinux(
                "label other\nAPPEND root=UUID=11111111-1111-1111-1111-111111111111 rootwait\n",
                "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
            )

    def test_wrong_stable_fdt_is_rejected(self):
        bad = """label rockchip-kernel-6.8.4
    FDT /dtb/rockchip/rk3328-eaidk310-wifi-test2.dtb
    APPEND root=UUID=11111111-1111-1111-1111-111111111111 rootwait
"""
        with self.assertRaisesRegex(SafetyError, "stable FDT"):
            rewrite_extlinux(bad, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")

    def test_append_without_root_is_rejected(self):
        bad = """default rockchip-kernel-6.8.4-wifi-test2
    label rockchip-kernel-6.8.4
    LINUX /Image
    FDT /dtb/rockchip/rk3328-eaidk-310.dtb
    INITRD /uInitrd
    APPEND console=ttyS2,1500000n8
"""
        with self.assertRaisesRegex(SafetyError, "root= token"):
            rewrite_extlinux(bad, "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


class TargetConfigValidationTests(unittest.TestCase):
    ROOT_UUID = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    BOOT_UUID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"

    def test_validate_target_fstab_accepts_identity_uuids(self):
        fstab = (
            f"UUID={self.ROOT_UUID} / ext4 defaults,noatime 0 1\n"
            f"UUID={self.BOOT_UUID} /boot ext4 defaults,noatime 0 2\n"
        )
        validate_target_fstab(
            fstab, root_uuid=self.ROOT_UUID, boot_uuid=self.BOOT_UUID
        )

    def test_validate_target_fstab_rejects_third_party_root(self):
        fstab = (
            "UUID=99999999-9999-4999-8999-999999999999 / ext4 defaults,noatime 0 1\n"
            f"UUID={self.BOOT_UUID} /boot ext4 defaults,noatime 0 2\n"
        )
        with self.assertRaisesRegex(SafetyError, "identity root and boot UUIDs"):
            validate_target_fstab(
                fstab, root_uuid=self.ROOT_UUID, boot_uuid=self.BOOT_UUID
            )

    def test_validate_target_fstab_rejects_duplicate_root_and_boot(self):
        fstab = (
            f"UUID={self.ROOT_UUID} / ext4 defaults,noatime 0 1\n"
            f"UUID={self.BOOT_UUID} /boot ext4 defaults,noatime 0 2\n"
            f"UUID={self.ROOT_UUID} / ext4 defaults,noatime 0 1\n"
            f"UUID={self.BOOT_UUID} /boot ext4 defaults,noatime 0 2\n"
        )
        with self.assertRaisesRegex(SafetyError, "exactly one"):
            validate_target_fstab(
                fstab, root_uuid=self.ROOT_UUID, boot_uuid=self.BOOT_UUID
            )

    def test_validate_target_extlinux_accepts_identity_root(self):
        extlinux = (
            "default rockchip-kernel-6.8.4\n"
            "    label rockchip-kernel-6.8.4\n"
            "    FDT /dtb/rockchip/rk3328-eaidk-310.dtb\n"
            f"    APPEND root=UUID={self.ROOT_UUID} rootwait\n"
        )
        validate_target_extlinux(extlinux, self.ROOT_UUID)

    def test_validate_target_extlinux_rejects_wrong_default(self):
        extlinux = (
            "default recovery\n"
            "    label rockchip-kernel-6.8.4\n"
            "    FDT /dtb/rockchip/rk3328-eaidk-310.dtb\n"
            f"    APPEND root=UUID={self.ROOT_UUID} rootwait\n"
        )
        with self.assertRaisesRegex(SafetyError, "default"):
            validate_target_extlinux(extlinux, self.ROOT_UUID)

    def test_validate_target_extlinux_rejects_wrong_stable_fdt(self):
        extlinux = (
            "default rockchip-kernel-6.8.4\n"
            "    label rockchip-kernel-6.8.4\n"
            "    FDT /dtb/rockchip/wrong.dtb\n"
            f"    APPEND root=UUID={self.ROOT_UUID} rootwait\n"
        )
        with self.assertRaisesRegex(SafetyError, "stable FDT"):
            validate_target_extlinux(extlinux, self.ROOT_UUID)

    def test_validate_target_extlinux_rejects_duplicate_stable_label(self):
        stanza = (
            "    label rockchip-kernel-6.8.4\n"
            "    FDT /dtb/rockchip/rk3328-eaidk-310.dtb\n"
            f"    APPEND root=UUID={self.ROOT_UUID} rootwait\n"
        )
        extlinux = "default rockchip-kernel-6.8.4\n" + stanza + stanza
        with self.assertRaisesRegex(SafetyError, "exactly one stable label"):
            validate_target_extlinux(extlinux, self.ROOT_UUID)

    def test_validate_target_extlinux_rejects_multiple_root_tokens_per_append(self):
        extlinux = (
            "default rockchip-kernel-6.8.4\n"
            "    label rockchip-kernel-6.8.4\n"
            "    FDT /dtb/rockchip/rk3328-eaidk-310.dtb\n"
            f"    APPEND root=UUID={self.ROOT_UUID} "
            "root=UUID=99999999-9999-4999-8999-999999999999 rootwait\n"
        )
        with self.assertRaisesRegex(SafetyError, "exactly one root= token"):
            validate_target_extlinux(extlinux, self.ROOT_UUID)

    def test_validate_target_extlinux_rejects_missing_root(self):
        with self.assertRaisesRegex(SafetyError, "no root= token"):
            validate_target_extlinux("label x\nAPPEND console=ttyS2,1500000n8\n", self.ROOT_UUID)

    def test_validate_target_extlinux_rejects_third_party_uuid(self):
        extlinux = (
            "default rockchip-kernel-6.8.4\n"
            "    label rockchip-kernel-6.8.4\n"
            "    FDT /dtb/rockchip/rk3328-eaidk-310.dtb\n"
            "    APPEND root=UUID=99999999-9999-4999-8999-999999999999 rootwait\n"
        )
        with self.assertRaisesRegex(SafetyError, "identity root UUID"):
            validate_target_extlinux(extlinux, self.ROOT_UUID)


class RootMountpointTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.root = pathlib.Path(self.temp_dir.name)

    def test_ensure_creates_all_required_mountpoints_with_exact_modes(self):
        with mock.patch.object(emmc_lib.os, "chmod") as chmod, mock.patch.object(
            emmc_lib,
            "validate_required_mountpoints",
            return_value=REQUIRED_MOUNTPOINTS,
        ):
            observed = emmc_lib.ensure_required_mountpoints(self.root)

        self.assertEqual(observed, REQUIRED_MOUNTPOINTS)
        for name in REQUIRED_MOUNTPOINTS:
            self.assertTrue((self.root / name).is_dir())
        self.assertEqual(
            chmod.call_args_list,
            [
                mock.call(self.root / "dev", 0o755),
                mock.call(self.root / "proc", 0o555),
                mock.call(self.root / "sys", 0o555),
                mock.call(self.root / "run", 0o755),
                mock.call(self.root / "tmp", 0o1777),
                mock.call(self.root / "mnt", 0o755),
                mock.call(self.root / "media", 0o755),
            ],
        )

    def test_validate_rejects_missing_mountpoint(self):
        for name in REQUIRED_MOUNTPOINTS:
            if name != "dev":
                (self.root / name).mkdir()
        with self.assertRaisesRegex(SafetyError, "required root mountpoint missing: dev"):
            emmc_lib.validate_required_mountpoints(self.root)

    def test_validate_rejects_non_directory_mountpoint(self):
        for name in REQUIRED_MOUNTPOINTS:
            (self.root / name).mkdir()
        (self.root / "dev").rmdir()
        (self.root / "dev").write_text("not a directory", encoding="utf-8")
        with self.assertRaisesRegex(SafetyError, "not a directory: dev"):
            emmc_lib.validate_required_mountpoints(self.root)

    def test_validate_rejects_wrong_mode(self):
        for name in REQUIRED_MOUNTPOINTS:
            (self.root / name).mkdir()

        def fake_lstat(path):
            name = pathlib.Path(path).name
            expected = int(REQUIRED_MOUNTPOINTS[name], 8)
            mode = 0o700 if name == "dev" else expected
            return types.SimpleNamespace(st_mode=stat.S_IFDIR | mode)

        with mock.patch.object(emmc_lib.os, "name", "posix"), mock.patch.object(
                emmc_lib.os, "lstat", side_effect=fake_lstat
            ), \
                self.assertRaisesRegex(SafetyError, "mode.*dev"):
            emmc_lib.validate_required_mountpoints(self.root)


class RsyncArgsTests(unittest.TestCase):
    def test_root_rsync_args(self):
        args = root_rsync_args("/", "/mnt/eaidk310-emmc/root", "/root/exclude.txt")
        self.assertEqual(
            args[:5],
            [
                "rsync",
                "-aHAXx",
                "--numeric-ids",
                "--delete",
                "--exclude-from=/root/exclude.txt",
            ],
        )
        self.assertEqual(args[5:], ["/", "/mnt/eaidk310-emmc/root/"])

    def test_root_rsync_args_dry_run(self):
        args = root_rsync_args("/", "/mnt/eaidk310-emmc/root", "/root/exclude.txt", dry_run=True)
        self.assertEqual(
            args[5:7], ["--dry-run", "--itemize-changes"]
        )
        self.assertEqual(args[7:], ["/", "/mnt/eaidk310-emmc/root/"])

    def test_boot_rsync_args(self):
        args = boot_rsync_args("/boot", "/mnt/eaidk310-emmc/root/boot")
        self.assertEqual(
            args,
            [
                "rsync",
                "-aHAX",
                "--numeric-ids",
                "--delete",
                "/boot/",
                "/mnt/eaidk310-emmc/root/boot/",
            ],
        )

    def test_boot_rsync_args_dry_run(self):
        args = boot_rsync_args("/boot", "/mnt/eaidk310-emmc/root/boot", dry_run=True)
        self.assertEqual(
            args,
            [
                "rsync",
                "-aHAX",
                "--numeric-ids",
                "--delete",
                "--dry-run",
                "--itemize-changes",
                "/boot/",
                "/mnt/eaidk310-emmc/root/boot/",
            ],
        )


if __name__ == "__main__":
    unittest.main()
