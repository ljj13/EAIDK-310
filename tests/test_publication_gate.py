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

    def test_rejects_literal_password_assignment(self):
        # Literal split so this source file never matches the pattern itself.
        assignment = "$Pass" + "word = '12" + "34'\n"
        (self.root / "script.ps1").write_text(
            assignment + "Send-Line $Pass" + "word\n", encoding="utf-8")
        errors = load_gate().scan_repository(self.root)
        self.assertTrue(any("password" in error.lower() for error in errors), errors)

    def test_password_prompt_with_screen_art_is_not_a_secret(self):
        # Serial logs legitimately contain a bare "Password: " prompt echo;
        # the pattern must not cross the newline into ANSI box art.
        (self.root / "console.log").write_bytes(
            b"eaidk-310 login: Fog\r\nPassword: \r\n"
            b" \x1b[0;1;34;94m_____\x1b[0m \x1b[0;34m__\x1b[0m ____\r\n")
        self.assertEqual(load_gate().scan_repository(self.root), [])

    def test_skips_gitignored_paths(self):
        import subprocess
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / ".gitignore").write_text("logs/\n", encoding="utf-8")
        evidence = self.root / "logs" / "session.log"
        evidence.parent.mkdir()
        evidence.write_text("$Pass" + "word = '12" + "34'\n", encoding="utf-8")
        (self.root / "tracked.txt").write_text("CONFIG_ARM64=y\n",
                                               encoding="utf-8")
        self.assertEqual(load_gate().scan_repository(self.root), [])

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
