"""Build a reversible EAIDK-310 ST7789 test device tree."""

import hashlib
import argparse
import json
from pathlib import Path


EXPECTED_BASE_SHA256 = "c0e5ad8c724bd65ff2c25663e98efa9b4fdabf56c01284684b39788744a625d5"


SPI_BASE = """\tspi@ff190000 {
\t\tcompatible = \"rockchip,rk3328-spi\\0rockchip,rk3066-spi\";
\t\treg = <0x00 0xff190000 0x00 0x1000>;
\t\tinterrupts = <0x00 0x31 0x04>;
\t\t#address-cells = <0x01>;
\t\t#size-cells = <0x00>;
\t\tclocks = <0x02 0x20 0x02 0xd1>;
\t\tclock-names = \"spiclk\\0apb_pclk\";
\t\tdmas = <0x12 0x08 0x12 0x09>;
\t\tdma-names = \"tx\\0rx\";
\t\tpinctrl-names = \"default\";
\t\tpinctrl-0 = <0x33 0x34 0x35 0x36>;
\t\tstatus = \"disabled\";
\t};"""


SPI_ST7789 = """\tspi@ff190000 {
\t\tcompatible = \"rockchip,rk3328-spi\\0rockchip,rk3066-spi\";
\t\treg = <0x00 0xff190000 0x00 0x1000>;
\t\tinterrupts = <0x00 0x31 0x04>;
\t\t#address-cells = <0x01>;
\t\t#size-cells = <0x00>;
\t\tclocks = <0x02 0x20 0x02 0xd1>;
\t\tclock-names = \"spiclk\\0apb_pclk\";
\t\tdmas = <0x12 0x08 0x12 0x09>;
\t\tdma-names = \"tx\\0rx\";
\t\tpinctrl-names = \"default\";
\t\tpinctrl-0 = <0x33 0x34 0x36>;
\t\tstatus = \"okay\";

\t\tdisplay@0 {
\t\t\tcompatible = \"sitronix,st7789v\";
\t\t\treg = <0x00>;
\t\t\tspi-max-frequency = <0x989680>;
\t\t\tbuswidth = <0x08>;
\t\t\treset-gpios = <0x2e 0x0f 0x01>;
\t\t\tdc-gpios = <0x2e 0x14 0x00>;
\t\t\tstatus = \"okay\";
\t\t};
\t};"""


PWMIR_BASE = """\tpwm@ff1b0030 {
\t\tcompatible = \"rockchip,rk3328-pwm\";
\t\treg = <0x00 0xff1b0030 0x00 0x10>;
\t\tinterrupts = <0x00 0x32 0x04>;
\t\tclocks = <0x02 0x3c 0x02 0xd6>;
\t\tclock-names = \"pwm\\0pclk\";
\t\tpinctrl-names = \"default\";
\t\tpinctrl-0 = <0x3a>;
\t\t#pwm-cells = <0x03>;
\t\tstatus = \"disabled\";
\t};"""


PWMIR_ENABLED = """\tpwm@ff1b0030 {
\t\tcompatible = \"rockchip,rk3328-pwm\";
\t\treg = <0x00 0xff1b0030 0x00 0x10>;
\t\tinterrupts = <0x00 0x32 0x04>;
\t\tclocks = <0x02 0x3c 0x02 0xd6>;
\t\tclock-names = \"pwm\\0pclk\";
\t\tpinctrl-names = \"default\";
\t\tpinctrl-0 = <0x3a>;
\t\t#pwm-cells = <0x03>;
\t\tstatus = \"okay\";
\t\tphandle = <0x7b>;
\t};"""


BACKLIGHT = """
\tst7789-backlight {
\t\tcompatible = \"pwm-backlight\";
\t\tpwms = <0x7b 0x00 0xc350 0x00>;
\t\tbrightness-levels = <0x00 0x10 0x20 0x30 0x40 0x50 0x60 0x70 0x80 0x90 0xa0 0xb0 0xc0 0xd0 0xe0 0xf0 0xff>;
\t\tdefault-brightness-level = <0x0b>;
\t\tstatus = \"okay\";
\t\tphandle = <0x7c>;
\t};
"""


ARDUINO_GFX_TYPE1_PROPERTIES = """
			init = <0x01000011 0x02000078 0x0100003a 0x55 0x01000036 0x00 0x010000b0 0x00 0xf0 0x010000b2 0x0c 0x0c 0x00 0x33 0x33 0x010000b7 0x35 0x010000bb 0x19 0x010000c0 0x2c 0x010000c2 0x01 0x010000c3 0x12 0x010000c4 0x20 0x010000c6 0x0f 0x010000d0 0xa4 0xa1 0x010000e0 0xf0 0x09 0x13 0x12 0x12 0x2b 0x3c 0x44 0x4b 0x1b 0x18 0x17 0x1d 0x21 0x010000e1 0xf0 0x09 0x13 0x0c 0x0d 0x27 0x3b 0x44 0x4d 0x0b 0x17 0x17 0x1d 0x21 0x01000013 0x0200000a 0x01000029 0x01000020 0x02000000>;
			gamma = "F0 09 13 12 12 2B 3C 44 4B 1B 18 17 1D 21\\nF0 09 13 0C 0D 27 3B 44 4D 0B 17 17 1D 21";
"""


def _replace_once(text: str, old: str, new: str, description: str) -> str:
    count = text.count(old)
    if count != 1:
        raise ValueError(f"expected exactly one {description}, found {count}")
    return text.replace(old, new, 1)


def render_test_dts(
    base_text: str, spi_mode: int = 0, init_profile: str | None = None
) -> str:
    """Return an ST7789 test DTS without modifying its stable input."""
    if spi_mode not in (0, 3):
        raise ValueError(f"unsupported SPI mode: {spi_mode}")
    if init_profile not in (None, "arduino-gfx-type1"):
        raise ValueError(f"unsupported init profile: {init_profile}")
    actual_sha256 = hashlib.sha256(base_text.encode("utf-8")).hexdigest()
    if actual_sha256 != EXPECTED_BASE_SHA256:
        raise ValueError(
            "unexpected stable DTS SHA-256: "
            f"expected {EXPECTED_BASE_SHA256}, got {actual_sha256}"
        )
    panel_node = SPI_ST7789
    if spi_mode == 3:
        panel_node = panel_node.replace(
            "\t\t\tspi-max-frequency = <0x989680>;",
            "\t\t\tspi-max-frequency = <0x989680>;\n"
            "\t\t\tspi-cpol;\n"
            "\t\t\tspi-cpha;",
            1,
        )
    if init_profile == "arduino-gfx-type1":
        panel_node = panel_node.replace(
            "\t\t\tbuswidth = <0x08>;",
            "\t\t\tbuswidth = <0x08>;" + ARDUINO_GFX_TYPE1_PROPERTIES,
            1,
        )
    rendered = _replace_once(base_text, SPI_BASE, panel_node, "stable SPI0 node")
    rendered = _replace_once(
        rendered, PWMIR_BASE, PWMIR_ENABLED, "stable PWMIR node"
    )
    return _replace_once(rendered, "\n\tchosen {", BACKLIGHT + "\n\tchosen {", "chosen anchor")


def build_artifact(
    base_path: Path,
    output_path: Path,
    spi_mode: int = 0,
    init_profile: str | None = None,
) -> dict[str, str]:
    """Write exactly one derived DTS artifact and return its provenance."""
    base_path = Path(base_path)
    output_path = Path(output_path)
    rendered = render_test_dts(
        base_path.read_text(encoding="utf-8"),
        spi_mode=spi_mode,
        init_profile=init_profile,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8", newline="\n")
    return {
        "base_sha256": EXPECTED_BASE_SHA256,
        "output_path": str(output_path.resolve()),
        "output_sha256": hashlib.sha256(rendered.encode("utf-8")).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base_dts", type=Path)
    parser.add_argument("output_dts", type=Path)
    parser.add_argument("--spi-mode", type=int, choices=(0, 3), default=0)
    parser.add_argument(
        "--init-profile", choices=("arduino-gfx-type1",), default=None
    )
    args = parser.parse_args()
    print(
        json.dumps(
            build_artifact(
                args.base_dts,
                args.output_dts,
                spi_mode=args.spi_mode,
                init_profile=args.init_profile,
            ),
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
