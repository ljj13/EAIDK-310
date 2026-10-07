"""Offline unit tests for eaidk-ota prune-staging (P10 eMMC endurance).

These exercise cmd_prune_staging directly against a synthetic state dir so
they run on every platform (the WSL integration suites elsewhere in
ota/tests cover the backend paths).
"""

import argparse
import importlib.util
import json
import os
import pathlib
import sys
import tempfile
import time
import unittest


HERE = pathlib.Path(__file__).resolve().parent
_tool_path = HERE.parent / "eaidk-ota"
# eaidk-ota imports fcntl at module scope for its state lock; the prune path
# never locks, so a stub keeps these tests runnable on Windows dev hosts.
try:
    import fcntl  # noqa: F401
except ImportError:
    import types
    _fcntl = types.ModuleType("fcntl")
    _fcntl.flock = lambda *a, **k: None
    _fcntl.LOCK_EX = 2
    _fcntl.LOCK_UN = 8
    sys.modules["fcntl"] = _fcntl
_spec = importlib.util.spec_from_loader(
    "eaidk_ota_prune",
    importlib.machinery.SourceFileLoader("eaidk_ota_prune", str(_tool_path)))
ota = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ota)


def opts(**kw):
    defaults = dict(state_dir=None, keep=1, yes=False, json=False)
    defaults.update(kw)
    return argparse.Namespace(**defaults)


class PruneStagingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state_dir = pathlib.Path(self.tmp.name) / "state"
        self.staging = self.state_dir / "staging"
        self.staging.mkdir(parents=True)
        # production atomic_write fsyncs the directory via O_DIRECTORY,
        # which does not exist on Windows dev hosts; the durability
        # semantics are board-side and covered by the WSL suites.
        self._orig_atomic_write = ota.atomic_write
        ota.atomic_write = self._plain_atomic_write

    @staticmethod
    def _plain_atomic_write(path, data, mode=0o644):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8")

    def tearDown(self):
        ota.atomic_write = self._orig_atomic_write
        self.tmp.cleanup()

    def write_state(self, state, **extra):
        data = {"state": state, "updated": "2026-10-08T00:00:00Z", **extra}
        (self.state_dir / "state.json").write_text(json.dumps(data))
        return data

    def make_bundle(self, name, mtime=None, sha=None):
        rel = self.staging / name
        (rel / "boot").mkdir(parents=True)
        (rel / "boot" / "Image-x").write_bytes(b"x" * 128)
        (rel / "root").mkdir()
        (rel / "root" / "vmlinuz.stamp").write_text(name)
        if sha:
            (rel / ".bundle.sha256").write_text(sha + "\n")
        if mtime is not None:
            stamp = time.time() - mtime
            os.utime(rel, (stamp, stamp))
        return rel

    def run_prune(self, **kw):
        return ota.cmd_prune_staging(opts(state_dir=str(self.state_dir), **kw))

    def test_refuses_outside_idle_committed(self):
        self.write_state("STAGED", release="rel-new")
        self.make_bundle("rel-old", mtime=100)
        self.assertEqual(self.run_prune(yes=True), ota.EXIT_CONFLICT)
        self.assertTrue((self.staging / "rel-old").exists())

    def test_dry_run_is_default(self):
        self.write_state("COMMITTED", release="rel-new")
        self.make_bundle("rel-old", mtime=100)
        self.make_bundle("rel-older", mtime=200)
        rc = self.run_prune(keep=1)
        self.assertEqual(rc, ota.EXIT_OK)
        self.assertEqual(len(list(self.staging.iterdir())), 2,
                         "dry run must not remove anything")

    def test_keeps_newest_and_protected_release(self):
        self.write_state("COMMITTED", release="rel-new")
        self.make_bundle("rel-new", mtime=0, sha="aa" * 8)
        self.make_bundle("rel-mid", mtime=100)
        self.make_bundle("rel-old", mtime=200)
        rc = self.run_prune(keep=0, yes=True)
        self.assertEqual(rc, ota.EXIT_OK)
        self.assertTrue((self.staging / "rel-new").exists(),
                        "state.release bundle is protected")
        self.assertFalse((self.staging / "rel-old").exists())
        self.assertFalse((self.staging / "rel-mid").exists())

    def test_bundle_hash_matching_state_is_protected(self):
        self.write_state("IDLE", bundle_sha256="bb" * 8)
        self.make_bundle("mystery", mtime=999, sha="bb" * 8)
        self.make_bundle("stale", mtime=100)
        rc = self.run_prune(keep=0, yes=True)
        self.assertEqual(rc, ota.EXIT_OK)
        self.assertTrue((self.staging / "mystery").exists())
        self.assertFalse((self.staging / "stale").exists())

    def test_keep_zero_removes_all_unprotected(self):
        self.write_state("IDLE")
        self.make_bundle("a", mtime=10)
        self.make_bundle("b", mtime=20)
        rc = self.run_prune(keep=0, yes=True)
        self.assertEqual(rc, ota.EXIT_OK)
        self.assertEqual(list(self.staging.iterdir()), [])

    def test_history_entry_recorded_without_state_change(self):
        self.write_state("COMMITTED", release="rel-new")
        self.make_bundle("rel-old", mtime=100)
        self.run_prune(keep=0, yes=True)
        entries = list((self.state_dir / "history").glob("*prune*.json"))
        self.assertEqual(len(entries), 1)
        entry = json.loads(entries[0].read_text())
        self.assertEqual(entry["action"], "prune-staging")
        self.assertEqual(entry["removed"], ["rel-old"])
        state = json.loads((self.state_dir / "state.json").read_text())
        self.assertEqual(state["state"], "COMMITTED")

    def test_no_staging_dir_is_clean_pass(self):
        self.write_state("IDLE")
        self.staging.rmdir()
        rc = self.run_prune(yes=True)
        self.assertEqual(rc, ota.EXIT_OK)


if __name__ == "__main__":
    unittest.main()
