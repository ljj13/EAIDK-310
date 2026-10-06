import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = REPOSITORY_ROOT / "tools" / "verify_custom_source.py"


def load_tool():
    spec = importlib.util.spec_from_file_location("verify_custom_source", MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


NEW_FILE_PATCH = (
    b"From: test\nSubject: [PATCH] add thing\n\n---\n"
    b" diff header\n"
    b"diff --git a/drivers/thing.c b/drivers/thing.c\n"
    b"new file mode 100644\n"
    b"index 0000000..0000000\n"
    b"--- /dev/null\n"
    b"+++ b/drivers/thing.c\n"
    b"@@ -0,0 +1,3 @@\n"
    b"+// SPDX-License-Identifier: GPL-2.0+\n"
    b"+int thing(void) { return 42; }\n"
    b"+\n"
    b"diff --git a/Makefile b/Makefile\n"
    b"index 1111111..2222222 100644\n"
    b"--- a/Makefile\n"
    b"+++ b/Makefile\n"
    b"@@ -1 +1,2 @@\n"
    b" obj-y += base.o\n"
    b"+obj-y += thing.o\n"
)
THING_BYTES = (
    b"// SPDX-License-Identifier: GPL-2.0+\n"
    b"int thing(void) { return 42; }\n"
    b"\n"
)
BOARD_DTS = b"/dts-v1/;\n#include \"rk3328.dtsi\"\n"


class CustomSourceVerifyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.tool = load_tool()
        self._old_root = self.tool.ROOT
        self._old_custom = self.tool.CUSTOM_SRC
        self._old_manifest = self.tool.MANIFEST_PATH
        self._old_inventory = self.tool.INVENTORY_PATH
        self._old_readme = self.tool.CUSTOM_README_PATH
        self._old_cache_ws = self.tool.cache_workspace
        self.tool.ROOT = self.root
        self.tool.CUSTOM_SRC = self.root / "custom-src"
        self.tool.MANIFEST_PATH = self.root / "custom-src" / "manifest.json"
        self.tool.INVENTORY_PATH = self.root / "docs" / "CUSTOM-SOURCE-INVENTORY.md"
        self.tool.CUSTOM_README_PATH = self.root / "custom-src" / "README.md"
        self.tool.cache_workspace = (
            lambda component: self.root / ".cache" / "upstream" / component
        )
        patches = self.root / "bootloader" / "u-boot-eaidk310" / "patches"
        patches.mkdir(parents=True)
        (patches / "0001-add-thing.patch").write_bytes(NEW_FILE_PATCH)
        dts_dir = self.root / "kernel" / "linux-6.18.54-zramfix1" / "dts"
        dts_dir.mkdir(parents=True)
        (dts_dir / "rk3328-eaidk-310.dts").write_bytes(BOARD_DTS)
        (self.root / "kernel" / "linux-6.12.108-zramfix1" / "dts").mkdir(parents=True)
        (self.root / "kernel" / "linux-6.12.108-zramfix1" / "dts" / "rk3328-eaidk-310.dts").write_bytes(BOARD_DTS)
        docs = self.root / "docs"
        docs.mkdir()
        (docs / "CUSTOM-SOURCE-INVENTORY.md").write_text(
            "inventory\n", encoding="utf-8"
        )
        import hashlib

        manifest = {
            "schema": 1,
            "entries": [
                {
                    "component": "u-boot",
                    "upstream_path": "drivers/thing.c",
                    "mirror_path": "custom-src/u-boot/drivers/thing.c",
                    "source_patch": "0001-add-thing.patch",
                    "method": "patch-new-file",
                    "role": "production",
                    "sha256": hashlib.sha256(THING_BYTES).hexdigest(),
                },
                {
                    "component": "linux",
                    "upstream_path": "arch/arm64/boot/dts/rockchip/rk3328-eaidk-310.dts",
                    "mirror_path": "custom-src/linux/arch/arm64/boot/dts/rockchip/rk3328-eaidk-310.dts",
                    "source_patch": None,
                    "source_path": "kernel/linux-6.18.54-zramfix1/dts/rk3328-eaidk-310.dts",
                    "method": "repo-canonical",
                    "role": "production",
                    "sha256": hashlib.sha256(BOARD_DTS).hexdigest(),
                },
            ],
        }
        self.write_manifest(manifest)
        self.write_mirror("custom-src/u-boot/drivers/thing.c", THING_BYTES)
        self.write_mirror(
            "custom-src/linux/arch/arm64/boot/dts/rockchip/rk3328-eaidk-310.dts", BOARD_DTS
        )
        (self.root / "custom-src" / "README.md").write_text(
            "Patch-only modifications\n0001-add-thing.patch\n", encoding="utf-8"
        )
        # docs reference requirement: inventory must mention mirror paths
        (docs / "CUSTOM-SOURCE-INVENTORY.md").write_text(
            "custom-src/u-boot/drivers/thing.c\n"
            "custom-src/linux/arch/arm64/boot/dts/rockchip/rk3328-eaidk-310.dts\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.tool.ROOT = self._old_root
        self.tool.CUSTOM_SRC = self._old_custom
        self.tool.MANIFEST_PATH = self._old_manifest
        self.tool.INVENTORY_PATH = self._old_inventory
        self.tool.CUSTOM_README_PATH = self._old_readme
        self.tool.cache_workspace = self._old_cache_ws
        self.temp.cleanup()

    def write_manifest(self, manifest):
        self.tool.MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
        self.tool.MANIFEST_PATH.write_text(json.dumps(manifest), encoding="utf-8")

    def read_manifest(self):
        return json.loads(self.tool.MANIFEST_PATH.read_text(encoding="utf-8"))

    def write_mirror(self, rel: str, data: bytes):
        path = self.root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def test_tier1_passes_on_consistent_fixture(self):
        ok, errors = self.tool.check_tier1()
        self.assertTrue(ok, errors)

    def test_check_reports_skip_without_workspace(self):
        ok, _ = self.tool.check_all()
        self.assertTrue(ok)

    def test_mirror_drift_fails(self):
        self.write_mirror("custom-src/u-boot/drivers/thing.c", b"tampered\n")
        ok, errors = self.tool.check_tier1()
        self.assertFalse(ok)
        self.assertTrue(any("MIRROR_DRIFT" in e for e in errors), errors)

    def test_stale_manifest_hash_fails(self):
        manifest = self.read_manifest()
        manifest["entries"][0]["sha256"] = "0" * 64
        self.write_manifest(manifest)
        ok, errors = self.tool.check_tier1()
        self.assertFalse(ok)
        self.assertTrue(any("sha256 mismatch" in e for e in errors), errors)

    def test_extra_file_in_custom_src_fails(self):
        self.write_mirror("custom-src/u-boot/drivers/sneaky.c", b"oops\n")
        ok, errors = self.tool.check_tier1()
        self.assertFalse(ok)
        self.assertTrue(any("unexpected file" in e for e in errors), errors)

    def test_kernel_dts_family_drift_fails(self):
        path = self.root / "kernel" / "linux-6.12.108-zramfix1" / "dts" / "rk3328-eaidk-310.dts"
        path.write_bytes(BOARD_DTS + b"/ { changed; };\n")
        ok, errors = self.tool.check_tier1()
        self.assertFalse(ok)
        self.assertTrue(any("build input" in e for e in errors), errors)

    def test_patch_body_extraction_is_byte_exact(self):
        patch = NEW_FILE_PATCH
        extracted = self.tool.extract_new_file_bytes(patch, "drivers/thing.c")
        self.assertEqual(extracted, THING_BYTES)
        self.assertIsNone(self.tool.extract_new_file_bytes(patch, "Makefile"))
        self.assertIsNone(self.tool.extract_new_file_bytes(patch, "absent.c"))

    def test_extraction_handles_missing_trailing_newline(self):
        patch = NEW_FILE_PATCH.replace(b"int thing(void) { return 42; }\n", b"int x;\n\\ No newline at end of file\n")
        extracted = self.tool.extract_new_file_bytes(patch, "drivers/thing.c")
        self.assertEqual(extracted, b"// SPDX-License-Identifier: GPL-2.0+\nint x;")


if __name__ == "__main__":
    unittest.main()
