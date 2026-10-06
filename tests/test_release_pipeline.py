import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPOSITORY_ROOT / "tools" / "release-kernel.py"


def load_engine():
    spec = importlib.util.spec_from_file_location("release_kernel", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def bare_engine(module, run_dir: Path):
    """An Engine instance without __init__ (pure state-machine tests)."""
    engine = object.__new__(module.Engine)
    engine.repo = REPOSITORY_ROOT
    engine.release = "0.0.0-test-zramfix1"
    engine.run_id = "test-run"
    engine.run_dir = run_dir
    engine.work_root = run_dir
    engine.cache_root = run_dir / "cache"
    engine.output_root = run_dir / "output"
    engine.state = {}
    return engine


class ReleaseMetadataTests(unittest.TestCase):
    def test_engine_module_exists(self):
        self.assertTrue(MODULE_PATH.is_file())

    def test_stage_constants(self):
        engine = load_engine()
        self.assertEqual(
            engine.STAGES,
            ["INIT", "SOURCE_VERIFIED", "MATERIALIZED", "PATCHED", "CONFIG_VERIFIED",
             "BUILT", "INITRAMFS_READY", "PACKAGED", "VERIFIED", "TESTED",
             "READY_FOR_BOARD_TRIAL"],
        )
        self.assertEqual(engine.BUNDLE_TAR_EPOCH, 1788352262)

    def test_release_metadata_declares_engine_contract(self):
        engine = load_engine()
        required = ("kernel_version", "kernel_release", "board_dtb", "bundle_dtb_name",
                    "bundle_name", "append_line", "uimage_name",
                    "initramfs_source_date_epoch", "initramfs", "stable_baseline")
        for version in ("6.12.111", "6.18.54"):
            release_dir = REPOSITORY_ROOT / "kernel" / f"linux-{version}-zramfix1"
            meta_path = release_dir / "release-metadata.json"
            self.assertTrue(meta_path.is_file(), f"{meta_path} missing")
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            for key in required:
                self.assertIn(key, meta, f"{meta_path} missing key {key}")
            self.assertEqual(meta["kernel_version"], version)
            self.assertEqual(meta["kernel_release"], f"{version}-eaidk310-zramfix1")
            self.assertEqual(meta["bundle_name"], f"eaidk310-linux-{version}-eaidk310-zramfix1")
            self.assertIn("rk3328-eaidk-310", meta["board_dtb"])
            initramfs = meta["initramfs"]
            self.assertEqual(initramfs["suite"], "trixie")
            self.assertIn("initramfs-tools-core", initramfs["packages"])
            seeds = list((release_dir / "config").glob("eaidk310-*.config"))
            self.assertEqual(len(seeds), 1, f"{release_dir}/config seed config not unique")
            board_dts = release_dir / "dts" / "rk3328-eaidk-310.dts"
            self.assertTrue(board_dts.is_file(), f"{board_dts} missing")

    def test_resolve_stage_flags(self):
        engine = load_engine()
        ns = lambda **kw: type("Args", (), kw)()  # noqa: E731
        self.assertEqual(engine.resolve_stage(ns(all=False, prepare=False, build=False,
                                                package=False, verify=False, stage="all")), "all")
        self.assertEqual(engine.resolve_stage(ns(all=False, prepare=True, build=False,
                                                package=False, verify=False, stage="all")), "prepare")
        self.assertEqual(engine.resolve_stage(ns(all=False, prepare=False, build=False,
                                                package=False, verify=True, stage="all")), "verify")


class DigestTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "a").write_bytes(b"alpha\n")
        (self.root / "b" / "c").mkdir(parents=True)
        (self.root / "b" / "c" / "m.ko").write_bytes(b"\x7fELFko")
        (self.root / "b" / "c" / "n.o").write_bytes(b"\x7fELFo")

    def tearDown(self):
        self.temp.cleanup()

    def test_tree_digest_is_order_stable_and_content_sensitive(self):
        engine = load_engine()
        files = ["a", "b/c/m.ko"]
        first = engine.tree_digest(self.root, files)
        self.assertEqual(first, engine.tree_digest(self.root, list(reversed(files))))
        (self.root / "a").write_bytes(b"ALPHA\n")
        self.assertNotEqual(first, engine.tree_digest(self.root, files))

    def test_modules_tree_digest_counts_only_files(self):
        engine = load_engine()
        digest = engine.modules_tree_digest(self.root)
        self.assertEqual(len(digest), 64)
        (self.root / "b" / "c" / "m.ko").write_bytes(b"\x7fELFko2")
        self.assertNotEqual(digest, engine.modules_tree_digest(self.root))


class StateMachineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.run_dir = Path(self.temp.name)
        self.engine_module = load_engine()

    def tearDown(self):
        self.temp.cleanup()

    def test_complete_and_require_stage(self):
        engine = bare_engine(self.engine_module, self.run_dir)
        engine.stage_init()
        engine.complete_stage("SOURCE_VERIFIED", {"archive": "x.tar.xz"})
        self.assertEqual(engine.state["stage"], "SOURCE_VERIFIED")
        engine.require_stage("INIT", "SOURCE_VERIFIED")
        with self.assertRaises(self.engine_module.Fail):
            engine.require_stage("BUILT")
        saved = json.loads((self.run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["stages"]["SOURCE_VERIFIED"]["status"], "PASS")

    def test_artifacts_are_hashed_into_state(self):
        engine = bare_engine(self.engine_module, self.run_dir)
        artifact = self.run_dir / "out.bin"
        artifact.write_bytes(b"payload")
        engine.complete_stage("BUILT", {}, artifacts={"image": artifact})
        saved = json.loads((self.run_dir / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(
            saved["stages"]["BUILT"]["artifacts"]["image"]["sha256"],
            self.engine_module.sha256_file(artifact),
        )

    def test_resume_reverification_detects_drift(self):
        engine = bare_engine(self.engine_module, self.run_dir)
        artifact = self.run_dir / "out.bin"
        artifact.write_bytes(b"payload")
        engine.complete_stage("BUILT", {}, artifacts={"image": artifact})
        engine.state["status"] = "FAILED"
        engine.save_state()
        engine.load_state()
        artifact.write_bytes(b"TAMPERED")
        with self.assertRaises(self.engine_module.Fail):
            engine._verify_completed_stages()
        artifact.write_bytes(b"payload")
        engine._verify_completed_stages()

    def test_latest_resumable_run_prefers_failed_runs(self):
        engine = bare_engine(self.engine_module, self.run_dir)
        work = self.run_dir / "work"
        good = work / "0.0.0-test-zramfix1-20260101T000000Z-aaaa"
        bad = work / "0.0.0-test-zramfix1-20260102T000000Z-bbbb"
        good.mkdir(parents=True)
        bad.mkdir(parents=True)
        (good / "state.json").write_text(json.dumps({"status": "COMPLETE"}), encoding="utf-8")
        (bad / "state.json").write_text(json.dumps({"status": "FAILED"}), encoding="utf-8")
        import os

        os.utime(good, (1, 1))
        engine.work_root = self.run_dir
        picked = engine._latest_resumable_run()
        self.assertEqual(picked.name, bad.name)


class WorkspaceGuardTests(unittest.TestCase):
    def setUp(self):
        self.engine_module = load_engine()

    @unittest.skipUnless(sys.platform.startswith("linux"), "engine runs on Linux only")
    def test_engine_rejects_drvfs_work_root(self):
        import argparse

        ns = argparse.Namespace(version="6.18.54", work_root="/mnt/c/build", cache_root=None,
                                output_dir=None, jobs=1)
        with self.assertRaises(self.engine_module.Fail):
            self.engine_module.Engine(ns)

    def test_engine_rejects_non_linux_host(self):
        import argparse

        if sys.platform.startswith("linux"):
            self.skipTest("only meaningful off Linux")
        ns = argparse.Namespace(version="6.18.54", work_root=None, cache_root=None,
                                output_dir=None, jobs=1)
        with self.assertRaises(self.engine_module.Fail):
            self.engine_module.Engine(ns)

    def test_unknown_release_rejected_before_platform_guard(self):
        import argparse

        ns = argparse.Namespace(version="9.9.99", work_root=None, cache_root=None,
                                output_dir=None, jobs=1)
        with self.assertRaises(self.engine_module.Fail):
            self.engine_module.Engine(ns)


if __name__ == "__main__":
    unittest.main()
