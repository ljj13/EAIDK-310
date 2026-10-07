"""Unit tests for tools/eaidk-health (offline: fixture roots, no board)."""

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_loader(
    "eaidk_health",
    importlib.machinery.SourceFileLoader(
        "eaidk_health", os.path.join(_TOOLS_DIR, "eaidk-health")))
h = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(h)


def _touch(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        fh.write(content)


class TestParseMmStat(unittest.TestCase):
    def test_normal(self):
        stats = h.parse_mm_stat("4194304 65 20480 384 0 0\n")
        self.assertEqual(stats["data_bytes"], 4194304)
        self.assertEqual(stats["compr_bytes"], 65)
        self.assertEqual(stats["mem_used_bytes"], 20480)
        self.assertEqual(stats["disksize_used_bytes"], 384)
        self.assertAlmostEqual(stats["compression_ratio"], 64527.75, places=1)

    def test_zero_compressed(self):
        stats = h.parse_mm_stat("4096 0 0 0 0 0")
        self.assertIsNone(stats["compression_ratio"])

    def test_garbage(self):
        self.assertIsNone(h.parse_mm_stat(""))
        self.assertIsNone(h.parse_mm_stat("1 2"))
        self.assertIsNone(h.parse_mm_stat("a b c d"))


class TestParseAnalyze(unittest.TestCase):
    def test_kernel_userspace_total(self):
        line = ("Startup finished in 5.163s (kernel) + 26.381s (userspace) "
                "= 31.544s")
        res = h.parse_systemd_analyze(line)
        self.assertEqual(res["kernel_s"], 5.163)
        self.assertEqual(res["userspace_s"], 26.381)
        self.assertEqual(res["total_s"], 31.544)
        self.assertIsNone(res["initrd_s"])

    def test_with_initrd_no_total(self):
        line = ("Startup finished in 2.0s (kernel) + 1.5s (initrd) "
                "+ 8.25s (userspace)")
        res = h.parse_systemd_analyze(line)
        self.assertEqual(res["initrd_s"], 1.5)
        self.assertAlmostEqual(res["total_s"], 11.75, places=2)


class TestCollectZram(unittest.TestCase):
    def test_fixture(self):
        with tempfile.TemporaryDirectory() as root:
            zram = os.path.join(root, "block", "zram0")
            _touch(os.path.join(zram, "queue", "disksize"), "402653184\n")
            _touch(os.path.join(zram, "comp_algorithm"),
                   "lzo lzo-rle [lz4] zstd\n")
            _touch(os.path.join(zram, "max_comp_streams"), "4\n")
            _touch(os.path.join(zram, "mm_stat"), "20480 512 1024 4 0 0\n")
            with mock.patch.object(h, "SYS", root):
                data = h.collect_zram()
        self.assertEqual(data["device"], "zram0")
        self.assertEqual(data["disksize_bytes"], 402653184)
        self.assertEqual(data["algorithm"], "lz4")
        self.assertEqual(data["streams"], 4)
        self.assertEqual(data["usage"]["data_bytes"], 20480)
        self.assertEqual(data["usage"]["compr_bytes"], 512)
        self.assertEqual(data["usage"]["compression_ratio"], 40.0)

    def test_absent(self):
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(h, "SYS", root):
                data = h.collect_zram()
        self.assertIsNone(data["device"])


class TestCollectEmmcSysfs(unittest.TestCase):
    def test_fixture(self):
        with tempfile.TemporaryDirectory() as root:
            # Windows forbids ':' in directory names, so the fixture device
            # uses a colon-free alias; the collector globs mmc* anyway.
            dev = os.path.join(root, "class", "mmc_host", "mmc2", "mmc2a0001")
            _touch(os.path.join(dev, "pre_eol_info"), "0x01\n")
            _touch(os.path.join(dev, "device_life_time_est_typ_a"), "0x01\n")
            _touch(os.path.join(dev, "device_life_time_est_typ_b"), "0x02\n")
            _touch(os.path.join(dev, "cid"), "13014b4a\n")
            _touch(os.path.join(dev, "name"), "HBD08G\n")
            blk = os.path.join(root, "block", "mmcblk2")
            _touch(os.path.join(blk, "size"), "15269888\n")
            # fake the device backlink with a plain file + mocked readlink
            _touch(os.path.join(blk, "device"), "")
            with mock.patch.object(h, "SYS", root), \
                 mock.patch.object(os.path, "islink", return_value=True), \
                 mock.patch.object(os, "readlink",
                                   return_value="mmc2a0001"):
                data = h.collect_emmc_sysfs()
        self.assertEqual(data["device"], "mmc2a0001")
        self.assertEqual(data["health"]["pre_eol_info"], "0x01")
        self.assertEqual(data["health"]["device_life_time_est_typ_a"], "0x01")
        self.assertEqual(data["health"]["device_life_time_est_typ_b"], "0x02")
        self.assertEqual(data["name"], "HBD08G")
        self.assertEqual(data["sectors"], 15269888)
        self.assertEqual(data["block_dev"], "/dev/mmcblk2")

    def test_no_emmc(self):
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(h, "SYS", root):
                data = h.collect_emmc_sysfs()
        self.assertIsNone(data["device"])
        self.assertEqual(data["health"], {})


class TestCollectDns(unittest.TestCase):
    def test_fixture(self):
        with tempfile.TemporaryDirectory() as root:
            _touch(os.path.join(root, "resolv.conf"),
                   "# comment\nnameserver 2400:3200::1\nnameserver 192.168.1.1\n")
            with mock.patch.object(h, "ETC", root):
                servers = h.collect_dns()
        self.assertEqual(servers, ["2400:3200::1", "192.168.1.1"])


class TestJournalPolicy(unittest.TestCase):
    def test_usage_parse(self):
        for text, expect in (
            ("Archived and active journals take up 4.0M in the file system.",
             4 * 1024 * 1024),
            ("Archived and active journals take up 4M in the file system.",
             4 * 1024 * 1024),
            ("Archived and active journals take up 4096.0K in the file system.",
             4 * 1024 * 1024),
        ):
            with mock.patch.object(h, "run_cmd", return_value=(0, text)):
                data = h.collect_journal_policy()
            self.assertEqual(data["disk_usage"], expect, text)

    def test_config_parse(self):
        with tempfile.TemporaryDirectory() as root:
            _touch(os.path.join(root, "systemd", "journald.conf"),
                   "[Journal]\n#Storage=auto\nStorage=persistent\n"
                   "SystemMaxUse=64M\n")
            with mock.patch.object(h, "ETC", root):
                data = h.collect_journal_policy()
        self.assertEqual(data["config"].get("Storage"), "persistent")
        self.assertEqual(data["config"].get("SystemMaxUse"), "64M")


class TestOtaState(unittest.TestCase):
    def test_present(self):
        with tempfile.TemporaryDirectory() as root:
            state = {"backend": "RawBootstateBackend",
                     "bootstate": {"action": "clear", "bootcount": 0,
                                   "upgrade_available": 0},
                     "stable": "6.18.55-eaidk310-wifi3"}
            _touch(os.path.join(root, "lib", "eaidk-ota", "state.json"),
                   json.dumps(state))
            with mock.patch.object(h, "VAR", root):
                data = h.collect_ota_state()
        self.assertTrue(data["present"])
        self.assertEqual(data["backend"], "RawBootstateBackend")
        self.assertEqual(data["boot_action"], "clear")
        self.assertEqual(data["diag_upgrade_available"], 0)

    def test_absent(self):
        with tempfile.TemporaryDirectory() as root:
            with mock.patch.object(h, "VAR", root):
                data = h.collect_ota_state()
        self.assertFalse(data["present"])


class TestRender(unittest.TestCase):
    def test_nested(self):
        out = "\n".join(h.render({"a": 1, "b": {"c": [1, 2]}}))
        self.assertIn("a: 1", out)
        self.assertIn("b:", out)
        self.assertIn("- 1", out)


class TestFmtBytes(unittest.TestCase):
    def test_scales(self):
        self.assertEqual(h.fmt_bytes(512), "512.0B")
        self.assertEqual(h.fmt_bytes(2048), "2.0KiB")
        self.assertEqual(h.fmt_bytes(5 * 1024 * 1024), "5.0MiB")
        self.assertEqual(h.fmt_bytes(3 * 1024 ** 3), "3.0GiB")
        self.assertEqual(h.fmt_bytes(None), "?")


class TestCli(unittest.TestCase):
    def test_json_status(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = h.main(["status", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(buf.getvalue())
        self.assertIn("identity", data)
        self.assertIn("memory", data)
        self.assertIn("zram", data)
        self.assertIn("failed_units", data)


if __name__ == "__main__":
    unittest.main()
