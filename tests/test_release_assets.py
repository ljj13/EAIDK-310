import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPOSITORY_ROOT / "rescue" / "verify_release_assets.py"
RELEASE_ROOT = REPOSITORY_ROOT / "releases" / "v2026.09.05"


def load_verifier():
    spec = importlib.util.spec_from_file_location("verify_release_assets", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ReleaseAssetBootstrapTests(unittest.TestCase):
    def test_release_asset_verifier_exists(self):
        self.assertTrue(MODULE_PATH.is_file(), "rescue/verify_release_assets.py is missing")


@unittest.skipUnless(MODULE_PATH.is_file(), "release verifier not implemented yet")
class ReleaseAssetTests(unittest.TestCase):
    ABC_SHA256 = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.assets = self.root / "assets"
        self.assets.mkdir()
        self.manifest = self.root / "manifest.json"

    def tearDown(self):
        self.temp.cleanup()

    def write_manifest(self, *, sha256: str = ABC_SHA256, size: int = 3, name: str = "rescue.img.xz"):
        payload = {
            "schema_version": 1,
            "assets": [
                {
                    "name": name,
                    "size": size,
                    "sha256": sha256,
                    "source_url": "https://github.com/example/project/releases/download/v1/rescue.img.xz",
                    "reproducibility": "binary-recovery",
                }
            ],
        }
        self.manifest.write_text(json.dumps(payload), encoding="utf-8")

    def test_accepts_exact_file(self):
        self.write_manifest()
        (self.assets / "rescue.img.xz").write_bytes(b"abc")
        self.assertEqual(load_verifier().verify_assets(self.manifest, self.assets), [])

    def test_rejects_hash_mismatch(self):
        self.write_manifest(sha256="0" * 64)
        (self.assets / "rescue.img.xz").write_bytes(b"abc")
        errors = load_verifier().verify_assets(self.manifest, self.assets)
        self.assertTrue(any("sha256" in error.lower() for error in errors), errors)

    def test_rejects_size_mismatch(self):
        self.write_manifest(size=4)
        (self.assets / "rescue.img.xz").write_bytes(b"abc")
        errors = load_verifier().verify_assets(self.manifest, self.assets)
        self.assertTrue(any("size" in error.lower() for error in errors), errors)

    def test_rejects_path_traversal_name(self):
        self.write_manifest(name="../rescue.img.xz")
        errors = load_verifier().verify_assets(self.manifest, self.assets)
        self.assertTrue(any("basename" in error.lower() for error in errors), errors)

    def test_rejects_unknown_reproducibility_class(self):
        self.write_manifest()
        payload = json.loads(self.manifest.read_text(encoding="utf-8"))
        payload["assets"][0]["reproducibility"] = "probably-buildable"
        self.manifest.write_text(json.dumps(payload), encoding="utf-8")
        errors = load_verifier().verify_assets(self.manifest, self.assets)
        self.assertTrue(any("reproducibility" in error.lower() for error in errors), errors)


class PublishedReleaseMetadataTests(unittest.TestCase):
    def test_sha256sums_exactly_matches_the_release_manifest(self):
        manifest = json.loads((RELEASE_ROOT / "manifest.json").read_text(encoding="utf-8"))
        checksum_lines = {
            name: digest
            for digest, name in (
                line.split("  ", 1)
                for line in (RELEASE_ROOT / "SHA256SUMS").read_text(encoding="ascii").splitlines()
                if line
            )
        }
        expected = {asset["name"]: asset["sha256"] for asset in manifest["assets"]}
        self.assertEqual(checksum_lines, expected)


if __name__ == "__main__":
    unittest.main()
