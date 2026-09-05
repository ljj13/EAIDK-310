import importlib
import re
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PUBLIC_BASE_DTS = ROOT / "hardware" / "st7789" / "current-rk3328-eaidk-310.dts"
PRIVATE_BASE_DTS = ROOT / "wifi-test1" / "current-rk3328-eaidk-310.dts"
BASE_DTS = PUBLIC_BASE_DTS if PUBLIC_BASE_DTS.is_file() else PRIVATE_BASE_DTS


def node_block(text: str, node_name: str) -> str:
    start = text.index(f"\t{node_name} {{")
    opening = text.index("{", start)
    depth = 0
    for index in range(opening, len(text)):
        if text[index] == "{":
            depth += 1
        elif text[index] == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 2]
    raise AssertionError(f"unterminated node: {node_name}")


class BuildSt7789DtsTests(unittest.TestCase):
    def test_render_enables_spi_and_adds_wired_st7789_panel(self):
        try:
            builder = importlib.import_module("tools.build_eaidk310_st7789_test")
        except ModuleNotFoundError:
            self.fail("ST7789 DTS builder does not exist yet")

        original = BASE_DTS.read_text(encoding="utf-8")
        rendered = builder.render_test_dts(original)

        self.assertEqual(BASE_DTS.read_text(encoding="utf-8"), original)
        spi = node_block(rendered, "spi@ff190000")
        self.assertIn("pinctrl-0 = <0x33 0x34 0x36>;", spi)
        self.assertIn('status = "okay";', spi)
        panel = node_block(spi, "display@0")
        self.assertIn('compatible = "sitronix,st7789v";', panel)
        self.assertIn("reg = <0x00>;", panel)
        self.assertIn("spi-max-frequency = <0x989680>;", panel)
        self.assertIn("buswidth = <0x08>;", panel)
        self.assertIn("reset-gpios = <0x2e 0x0f 0x01>;", panel)
        self.assertIn("dc-gpios = <0x2e 0x14 0x00>;", panel)
        self.assertIn('status = "okay";', panel)

    def test_render_adds_20khz_pwm_backlight_at_about_70_percent(self):
        builder = importlib.import_module("tools.build_eaidk310_st7789_test")
        rendered = builder.render_test_dts(BASE_DTS.read_text(encoding="utf-8"))

        pwm = node_block(rendered, "pwm@ff1b0030")
        self.assertIn("phandle = <0x7b>;", pwm)
        self.assertIn('status = "okay";', pwm)

        backlight = node_block(rendered, "st7789-backlight")
        self.assertIn('compatible = "pwm-backlight";', backlight)
        self.assertIn("pwms = <0x7b 0x00 0xc350 0x00>;", backlight)
        self.assertIn("phandle = <0x7c>;", backlight)
        levels_match = re.search(r"brightness-levels = <([^>]+)>;", backlight)
        default_match = re.search(
            r"default-brightness-level = <0x([0-9a-f]+)>;", backlight
        )
        self.assertIsNotNone(levels_match)
        self.assertIsNotNone(default_match)
        levels = [int(value, 16) for value in levels_match.group(1).split()]
        default_index = int(default_match.group(1), 16)
        self.assertAlmostEqual(levels[default_index] / levels[-1], 0.70, delta=0.02)

        panel = node_block(node_block(rendered, "spi@ff190000"), "display@0")
        self.assertNotIn("backlight =", panel)

    def test_render_mode3_adds_cpol_and_cpha_only_to_the_panel(self):
        builder = importlib.import_module("tools.build_eaidk310_st7789_test")
        original = BASE_DTS.read_text(encoding="utf-8")

        mode0 = builder.render_test_dts(original)
        mode3 = builder.render_test_dts(original, spi_mode=3)

        mode0_panel = node_block(node_block(mode0, "spi@ff190000"), "display@0")
        mode3_panel = node_block(node_block(mode3, "spi@ff190000"), "display@0")
        self.assertNotIn("spi-cpol;", mode0_panel)
        self.assertNotIn("spi-cpha;", mode0_panel)
        self.assertIn("spi-cpol;", mode3_panel)
        self.assertIn("spi-cpha;", mode3_panel)

    def test_render_arduino_gfx_profile_overrides_the_panel_init_table(self):
        builder = importlib.import_module("tools.build_eaidk310_st7789_test")
        original = BASE_DTS.read_text(encoding="utf-8")

        baseline = builder.render_test_dts(original, spi_mode=3)
        profiled = builder.render_test_dts(
            original, spi_mode=3, init_profile="arduino-gfx-type1"
        )

        baseline_panel = node_block(
            node_block(baseline, "spi@ff190000"), "display@0"
        )
        profiled_panel = node_block(
            node_block(profiled, "spi@ff190000"), "display@0"
        )
        self.assertNotIn("\n\t\t\tinit = <", baseline_panel)
        self.assertIn("init = <0x01000011 0x02000078", profiled_panel)
        self.assertIn("0x0100003a 0x55", profiled_panel)
        self.assertIn("0x010000b0 0x00 0xf0", profiled_panel)
        self.assertIn("0x01000013 0x0200000a 0x01000029", profiled_panel)
        self.assertIn("0x01000020 0x02000000>;", profiled_panel)
        self.assertIn(
            'gamma = "F0 09 13 12 12 2B 3C 44 4B 1B 18 17 1D 21\\n'
            'F0 09 13 0C 0D 27 3B 44 4D 0B 17 17 1D 21";',
            profiled_panel,
        )

    def test_render_rejects_a_different_base_device_tree(self):
        builder = importlib.import_module("tools.build_eaidk310_st7789_test")
        changed = BASE_DTS.read_text(encoding="utf-8").replace(
            'model = "EAIDK-310 build by lcy v2";',
            'model = "unexpected board";',
            1,
        )

        with self.assertRaisesRegex(ValueError, "unexpected stable DTS SHA-256"):
            builder.render_test_dts(changed)

    def test_build_artifact_writes_only_the_requested_output(self):
        builder = importlib.import_module("tools.build_eaidk310_st7789_test")
        self.assertTrue(
            hasattr(builder, "build_artifact"), "build_artifact does not exist yet"
        )
        original = BASE_DTS.read_bytes()

        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "rk3328-eaidk310-st7789-test1.dts"
            result = builder.build_artifact(BASE_DTS, output)

            self.assertEqual(set(Path(temp_dir).iterdir()), {output})
            self.assertEqual(
                output.read_text(encoding="utf-8"),
                builder.render_test_dts(original.decode("utf-8")),
            )
            self.assertEqual(result["base_sha256"], builder.EXPECTED_BASE_SHA256)
            self.assertEqual(result["output_path"], str(output.resolve()))
            self.assertRegex(result["output_sha256"], r"^[0-9a-f]{64}$")

        self.assertEqual(BASE_DTS.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
