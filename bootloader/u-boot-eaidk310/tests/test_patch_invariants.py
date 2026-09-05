import json
import pathlib
import re
import unittest


PROJECT_DIR = pathlib.Path(__file__).resolve().parents[1]
PATCH_PATH = PROJECT_DIR / "patches" / "0001-arm-dts-add-eaidk310-variants.patch"
SOURCE_LOCK_PATH = PROJECT_DIR / "source-lock.json"


class Eaidk310PatchInvariantTests(unittest.TestCase):
    def test_source_lock_is_exact(self):
        self.assertEqual(
            json.loads(SOURCE_LOCK_PATH.read_text(encoding="utf-8")),
            {
                "repository": "https://github.com/u-boot/u-boot.git",
                "tag": "v2024.07-rc1",
                "commit": "38ea74d6d5c05224acdb03f799897c1bdd56f8cc",
            },
        )

    def test_patch_contains_board_hardware_invariants(self):
        patch = PATCH_PATH.read_text(encoding="utf-8")

        for expected in (
            'model = "Rockchip RK3328 EAIDK310";',
            "mmc0 = &emmc;",
            "mmc1 = &sdmmc;",
            "mmc2 = &sdio;",
            "mmc3 = &sdmmc_ext;",
            "sdmmc_ext: mmc@ff5f0000",
            "HCLK_SDMMC_EXT",
            "interrupt-parent = <&gpio2>;",
            "interrupts = <RK_PA6 IRQ_TYPE_LEVEL_LOW>;",
            "reset-gpios = <&gpio1 RK_PC2 GPIO_ACTIVE_LOW>;",
            "max-frequency = <125000000>;",
            'CONFIG_DEFAULT_DEVICE_TREE="rk3328-eaidk310-control"',
            'CONFIG_DEFAULT_DEVICE_TREE="rk3328-eaidk310-sdio-handoff"',
        ):
            self.assertIn(expected, patch)

    def test_control_and_handoff_have_one_sdio_enablement_variable(self):
        patch = PATCH_PATH.read_text(encoding="utf-8")

        control = self._added_file(patch, "arch/arm/dts/rk3328-eaidk310-control.dts")
        handoff = self._added_file(
            patch, "arch/arm/dts/rk3328-eaidk310-sdio-handoff.dts"
        )
        self.assertRegex(control, r"&sdio\s*\{\s*status = \"disabled\";\s*\};")
        self.assertNotIn('status = "okay";', control)
        self.assertIn('status = "okay";', handoff)
        self.assertIn("mmc-pwrseq = <&sdio_pwrseq>;", handoff)
        self.assertIn("RK_PC2", handoff)
        self.assertNotIn("RK_PC3", handoff)

    @staticmethod
    def _added_file(patch: str, path: str) -> str:
        pattern = re.compile(
            rf"diff --git a/{re.escape(path)} b/{re.escape(path)}\n"
            rf"(?P<body>.*?)(?=\ndiff --git |\Z)",
            re.DOTALL,
        )
        match = pattern.search(patch)
        if match is None:
            raise AssertionError(f"missing added file in patch: {path}")
        return "\n".join(
            line[1:]
            for line in match.group("body").splitlines()
            if line.startswith("+") and not line.startswith("+++")
        )


if __name__ == "__main__":
    unittest.main()
