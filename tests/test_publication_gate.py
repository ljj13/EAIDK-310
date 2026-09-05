import importlib.util
import os
from pathlib import Path
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPOSITORY_ROOT / "tools" / "publication_gate.py"


def load_gate():
    spec = importlib.util.spec_from_file_location("publication_gate", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class PublicationGateBootstrapTests(unittest.TestCase):
    def test_publication_gate_module_exists(self):
        self.assertTrue(MODULE_PATH.is_file(), "tools/publication_gate.py is missing")


@unittest.skipUnless(MODULE_PATH.is_file(), "publication gate not implemented yet")
class PublicationGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_rejects_file_at_git_hard_limit(self):
        artifact = self.root / "artifact.bin"
        with artifact.open("wb") as stream:
            stream.truncate(100 * 1024 * 1024)
        errors = load_gate().scan_repository(self.root)
        self.assertTrue(any("100 MiB" in error for error in errors), errors)

    def test_rejects_private_key_material(self):
        (self.root / "notes.txt").write_text(
            "-----BEGIN OPENSSH " + "PRIVATE KEY-----\nsecret\n",
            encoding="utf-8",
        )
        errors = load_gate().scan_repository(self.root)
        self.assertTrue(any("private key" in error.lower() for error in errors), errors)

    def test_rejects_release_binary_inside_git(self):
        (self.root / "rescue.img.xz").write_bytes(b"not an image")
        errors = load_gate().scan_repository(self.root)
        self.assertTrue(any("release-only" in error.lower() for error in errors), errors)

    @unittest.skipIf(os.name == "nt", "Windows symlink creation may require Developer Mode")
    def test_rejects_symlink_that_escapes_repository(self):
        outside = self.root.parent / "outside.txt"
        outside.write_text("outside", encoding="utf-8")
        (self.root / "escape").symlink_to(outside)
        errors = load_gate().scan_repository(self.root)
        self.assertTrue(any("symlink" in error.lower() for error in errors), errors)

    def test_accepts_source_files_and_ignores_git_metadata(self):
        source = self.root / "kernel" / "config"
        source.mkdir(parents=True)
        (source / "eaidk310.config").write_text("CONFIG_ARM64=y\n", encoding="utf-8")
        metadata = self.root / ".git"
        metadata.mkdir()
        (metadata / "credentials").write_text("token=ignored-git-internal\n", encoding="utf-8")
        self.assertEqual(load_gate().scan_repository(self.root), [])


if __name__ == "__main__":
    unittest.main()
