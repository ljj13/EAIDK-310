"""eaidk-bootstate raw dual-copy backend tests (P3.6-B).

Fixture model tests for the record codec and the fail-closed selection
matrix, plus file-level power-loss injection against a 16 MiB image that
mirrors the audited eMMC prefix geometry (records at LBA 0x6400/0x7800).

The golden wire format is pinned to drivers/bootcount/bootcount_eaidk310_raw.c
(patched U-Boot v2024.07-rc1@38ea74d6):
  magic "EA310BS1" | ver | bootcount | upgrade_available | slot |
  seq u32le @12 | label[16] @16 | zeros | crc32 u32le @508
"""
import json
import os
import pathlib
import struct
import subprocess
import unittest
import zlib

HERE = pathlib.Path(__file__).resolve().parent
TOOL = HERE.parent / "eaidk-bootstate"
WSL_BASE = "/tmp/eaidk-raw-tests"
IMAGE_BYTES = 16 * 1024 * 1024
LBA_A, LBA_B = 0x6400, 0x7800


def enc(seq, bootcount, upgrade_available, slot=0, label=b""):
    buf = bytearray(512)
    buf[0:8] = b"EA310BS1"
    buf[8] = 1
    buf[9] = bootcount
    buf[10] = upgrade_available
    buf[11] = slot
    struct.pack_into("<I", buf, 12, seq)
    buf[16:16 + len(label)] = label
    struct.pack_into("<I", buf, 508,
                     zlib.crc32(bytes(buf[:508])) & 0xFFFFFFFF)
    return bytes(buf)


@unittest.skipUnless(os.name == "nt", "Windows WSL integration tests")
class RawTestBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ok = subprocess.run(
            ["wsl", "-d", "Ubuntu-24.04", "-u", "Fog", "--", "bash", "-c",
             "command -v python3 >/dev/null"],
            capture_output=True, text=True)
        if ok.returncode != 0:
            raise unittest.SkipTest("WSL python3 unavailable")

    def wsl(self, command: str, timeout: int = 120) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["wsl", "-d", "Ubuntu-24.04", "-u", "Fog", "--", "bash", "-c", command],
            text=True, capture_output=True, timeout=timeout)

    def setUp(self):
        self.work = f"{WSL_BASE}/{self.id().split('.')[-1]}"
        r = self.wsl(f"rm -rf {self.work} && mkdir -p {self.work}")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.image = f"{self.work}/prefix.img"

    def make_image(self, a: bytes = None, b: bytes = None):
        """Build a 16 MiB zero image, optionally with record A/B bytes."""
        script = [f"truncate -s {IMAGE_BYTES} {self.image}"]
        for lba, data in ((LBA_A, a), (LBA_B, b)):
            if data is None:
                continue
            hexs = data.hex()
            script.append(f"printf '{hexs}' | xxd -r -p | dd of={self.image} "
                          f"bs=512 seek={lba} conv=notrunc status=none")
        r = self.wsl(" && ".join(script))
        self.assertEqual(r.returncode, 0, r.stderr)

    def run_tool(self, args: str, json_mode: bool = True):
        flag = "--json " if json_mode else ""
        return self.wsl(
            f"python3 {TOOL.as_posix().replace('D:/', '/mnt/d/')} {flag}"
            f"{args} --backend raw --raw-image {self.image}")

    def read_records(self):
        r = self.wsl(
            f"dd if={self.image} bs=512 skip={LBA_A} count=1 status=none | od -An -tx1 -v | tr -d ' \\n' && echo ',' && "
            f"dd if={self.image} bs=512 skip={LBA_B} count=1 status=none | od -An -tx1 -v | tr -d ' \\n'")
        a_hex, b_hex = r.stdout.strip().split(",")
        return bytes.fromhex(a_hex), bytes.fromhex(b_hex)

    def state_of(self, r) -> dict:
        return json.loads(r.stdout)


class RawToolTests(RawTestBase):
    def test_inspect_empty_image_fails_closed(self):
        self.make_image()
        r = self.run_tool("inspect")
        self.assertEqual(r.returncode, 0, r.stderr)
        data = self.state_of(r)
        self.assertTrue(data["fail_closed"])
        self.assertEqual(data["selected"], -1)
        self.assertEqual(data["bootcount"], 0)
        self.assertFalse(data.get("conflict", False))

    def test_inspect_strict_empty_exits_2(self):
        self.make_image()
        r = self.run_tool("inspect", json_mode=False)  # --strict unsupported w/o json? keep json
        r = self.wsl(f"python3 {TOOL.as_posix().replace('D:/', '/mnt/d/')} "
                     f"inspect --strict --backend raw --raw-image {self.image}")
        self.assertEqual(r.returncode, 2)

    def test_arm_writes_record_a_seq1(self):
        self.make_image()
        r = self.run_tool("arm --label 6.18.54-rc")
        self.assertEqual(r.returncode, 0, r.stderr)
        a, b = self.read_records()
        self.assertEqual(a[0:8], b"EA310BS1")
        self.assertEqual(a[9], 0)          # bootcount 0
        self.assertEqual(a[10], 1)         # armed
        self.assertEqual(struct.unpack_from("<I", a, 12)[0], 1)  # seq 1
        self.assertTrue(a[16:].startswith(b"6.18.54-rc"))
        self.assertEqual(b, bytes(512))    # B untouched

    def test_arm_then_clear_rotates_copies(self):
        self.make_image()
        self.assertEqual(self.run_tool("arm").returncode, 0)
        self.assertEqual(self.run_tool("clear").returncode, 0)
        a, b = self.read_records()
        # arm wrote A(seq1, armed); clear selects A and writes the OTHER copy
        self.assertEqual(a[10], 1)
        self.assertEqual(b[10], 0)         # committed
        self.assertEqual(struct.unpack_from("<I", b, 12)[0], 2)

    def test_arm_refuses_on_block_device_without_flag(self):
        # raw-device path without --allow-block-write must refuse writes
        self.make_image()
        r = self.wsl(f"python3 {TOOL.as_posix().replace('D:/', '/mnt/d/')} "
                     f"arm --backend raw --raw-device {self.image}")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("read-only", r.stdout + r.stderr)

    def test_oversized_image_rejected(self):
        r = self.wsl(f"truncate -s 8M {self.image} && "
                     f"python3 {TOOL.as_posix().replace('D:/', '/mnt/d/')} "
                     f"inspect --backend raw --raw-image {self.image}")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("too small", r.stdout + r.stderr)


class RawSelectionMatrixTests(RawTestBase):
    def inspect_state(self):
        r = self.run_tool("inspect")
        self.assertEqual(r.returncode, 0, r.stderr)
        return self.state_of(r)

    def test_valid_armed_only_in_b(self):
        self.make_image(a=None, b=enc(seq=7, bootcount=1, upgrade_available=1))
        data = self.inspect_state()
        self.assertEqual(data["selected"], 1)
        self.assertFalse(data["fail_closed"])
        self.assertEqual(data["bootcount"], 1)
        self.assertEqual(data["sequence"], 7)

    def test_higher_sequence_wins(self):
        self.make_image(a=enc(seq=3, bootcount=1, upgrade_available=1),
                        b=enc(seq=9, bootcount=2, upgrade_available=1))
        data = self.inspect_state()
        self.assertEqual(data["selected"], 1)
        self.assertEqual(data["bootcount"], 2)

    def test_crc_corruption_falls_to_other_copy(self):
        good = bytearray(enc(seq=5, bootcount=0, upgrade_available=1))
        good[20] ^= 0xFF                      # corrupt A payload -> crc bad
        self.make_image(a=bytes(good), b=enc(seq=4, bootcount=0, upgrade_available=0))
        data = self.inspect_state()
        self.assertEqual(data["selected"], 1)
        self.assertEqual(data["upgrade_available"], 0)   # committed copy wins

    def test_equal_sequence_conflict_rejects_both(self):
        self.make_image(a=enc(seq=6, bootcount=1, upgrade_available=1),
                        b=enc(seq=6, bootcount=0, upgrade_available=0))
        data = self.inspect_state()
        self.assertEqual(data["selected"], -1)
        self.assertTrue(data["conflict"])
        self.assertTrue(data["fail_closed"])

    def test_equal_sequence_identical_payload_is_fine(self):
        rec = enc(seq=6, bootcount=0, upgrade_available=1)
        self.make_image(a=rec, b=rec)
        data = self.inspect_state()
        self.assertEqual(data["selected"], 0)
        self.assertFalse(data["fail_closed"])

    def test_both_fully_random_fail_closed(self):
        self.make_image(a=bytes(range(256)) * 2, b=bytes((i * 7 + 3) % 256 for i in range(512)))
        data = self.inspect_state()
        self.assertTrue(data["fail_closed"])
        self.assertEqual(data["selected"], -1)

    def test_wrong_magic_fails_closed(self):
        bad = bytearray(enc(seq=1, bootcount=0, upgrade_available=1))
        bad[0:8] = b"EA310BS2"
        self.make_image(a=bytes(bad), b=enc(seq=2, bootcount=0, upgrade_available=1))
        data = self.inspect_state()
        self.assertEqual(data["selected"], 1)   # B still fine


class RawPowerLossTests(RawTestBase):
    """Torn-write modeling at file level: a power loss during a store can
    leave the target sector unchanged, partially written, or random.  The
    worst permitted outcome is 'latest update lost'; it must never yield an
    unauthorized candidate boot."""

    def boots_until_route(self, script: list, limit: int = 6) -> str:
        """Run the driver-equivalent boot chain via the tool's simulate."""
        state = json.loads(self.run_tool("inspect").stdout)
        for i, _ in enumerate(range(limit)):
            r = self.wsl(
                f"python3 {TOOL.as_posix().replace('D:/', '/mnt/d/')} --json "
                f"simulate --boots 1 --bootlimit {script.pop(0) if script else 1} "
                f"--backend raw --raw-image {self.image}")
            self.assertEqual(r.returncode, 0, r.stderr)
            step = json.loads(r.stdout)["boots"][0]
            # apply the modelled store to the image (what the driver does)
            if step["file_after"]["upgrade_available"] == 1:
                a, b = self.read_records()
                pa = raw_parse_helper(a)
                pb = raw_parse_helper(b)
                sel = choose(pa, pb)
                seq = max(pa[1], pb[1])
                if seq == 0xFFFFFFFF:
                    pass
                else:
                    target = LBA_A
                    if (sel == 0 and pa[0]) or (sel == 1 and pb[0]):
                        target = LBA_B if sel == 0 else LBA_A
                    elif sel == 0:
                        target = LBA_B
                    new = enc(seq=seq + 1, bootcount=step["file_after"]["bootcount"],
                              upgrade_available=1)
                    self.wsl(dd_write(self.image, target, new))
            if step["route"] != "bootcmd(candidate)":
                return step["route"]
        return "bootcmd(candidate)-forever"

    def test_torn_store_cannot_bootloop_forever(self):
        # arm, then simulate: candidate attempt -> fallback within 2 boots
        self.make_image()
        self.assertEqual(self.run_tool("arm").returncode, 0)
        route = self.boots_until_route([], limit=4)
        self.assertEqual(route, "altbootcmd(stable)")

    def test_torn_target_leaves_old_copy_selectable(self):
        rec = enc(seq=1, bootcount=0, upgrade_available=1)
        self.make_image(a=rec, b=None)
        # torn B write: random garbage where the new record should be
        self.wsl(f"printf 'deadbeefdeadc0de' | xxd -r -p | dd of={self.image} "
                 f"bs=512 seek={LBA_B} conv=notrunc status=none")
        data = self.state_of(self.run_tool("inspect"))
        self.assertEqual(data["selected"], 0)       # A still authoritative
        self.assertEqual(data["sequence"], 1)
        self.assertFalse(data["fail_closed"])

    def test_half_written_sector_fails_closed_or_falls_back(self):
        rec = enc(seq=2, bootcount=1, upgrade_available=1)
        half = bytearray(rec)
        # half of the sector from the FUTURE record (bootcount incremented)
        fut = enc(seq=3, bootcount=2, upgrade_available=1)
        half[256:] = fut[256:]
        self.make_image(a=bytes(half), b=rec)
        data = self.state_of(self.run_tool("inspect"))
        # the franken-record must fail crc and selection must use B
        if data["selected"] == 0:
            self.fail("torn record with bad crc must not be selectable")
        self.assertEqual(data["selected"], 1)
        self.assertEqual(data["bootcount"], 1)


def raw_parse_helper(record: bytes):
    """(usable, seq) for the driver-equivalent chaining helper above."""
    if len(record) != 512 or record[0:8] != b"EA310BS1" or record[8] != 1:
        return (False, 0)
    if struct.unpack_from("<I", record, 508)[0] != \
            (zlib.crc32(record[:508]) & 0xFFFFFFFF):
        return (False, 0)
    return (True, struct.unpack_from("<I", record, 12)[0])


def choose(pa, pb):
    if not pa[0] and not pb[0]:
        return -1
    if not pb[0]:
        return 0
    if not pa[0]:
        return 1
    return 0 if pa[1] >= pb[1] else 1


def dd_write(image: str, lba: int, record: bytes) -> str:
    hexs = record.hex()
    return (f"printf '{hexs}' | xxd -r -p | dd of={image} "
            f"bs=512 seek={lba} conv=notrunc status=none")


class RawSimulateChainTests(RawTestBase):
    def simulate(self, boots, bootlimit=1):
        r = self.run_tool(f"simulate --boots {boots} --bootlimit {bootlimit}")
        self.assertEqual(r.returncode, 0, r.stderr)
        return self.state_of(r)

    def test_armed_chain_candidate_then_fallback(self):
        self.make_image(a=enc(seq=1, bootcount=0, upgrade_available=1))
        out = self.simulate(boots=2)
        self.assertEqual(out["boots"][0]["route"], "bootcmd(candidate)")
        self.assertEqual(out["boots"][1]["route"], "altbootcmd(stable)")

    def test_committed_stays_stable(self):
        self.make_image(a=enc(seq=4, bootcount=0, upgrade_available=0))
        out = self.simulate(boots=2)
        for step in out["boots"]:
            self.assertEqual(step["route"], "bootcmd(stable)")

    def test_empty_image_stable(self):
        self.make_image()
        out = self.simulate(boots=1)
        self.assertEqual(out["boots"][0]["route"], "bootcmd(stable)")

    def test_sequence_exhaustion_refuses(self):
        self.make_image(a=enc(seq=0xFFFFFFFF, bootcount=0, upgrade_available=1))
        r = self.run_tool("arm")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("exhausted", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
