import pathlib
import re
import subprocess
import unittest
import uuid


PROJECT_DIR = pathlib.Path(__file__).resolve().parents[1]
DTS = PROJECT_DIR / "dts" / "rk3328-eaidk-310.dts"
PATCH = PROJECT_DIR / "patches" / "0001-arm64-dts-rockchip-register-eaidk310.patch"
INSTALLER = PROJECT_DIR / "scripts" / "install-board-inputs.sh"


class DeviceTreeInvariantTests(unittest.TestCase):
    def read_required(self, path: pathlib.Path) -> str:
        self.assertTrue(path.is_file(), f"required file is missing: {path}")
        return path.read_text(encoding="utf-8")

    def test_native_source_shape(self):
        text = self.read_required(DTS)

        self.assertIn('#include "rk3328.dtsi"', text)
        self.assertIn('model = "EAIDK-310 build by lcy v2";', text)
        self.assertIn(
            'compatible = "openailab,eaidk-310", "rockchip,rk3328";', text
        )
        self.assertNotRegex(text, r"\bphandle\s*=")
        self.assertNotIn("st7789", text.lower())
        self.assertNotIn("fbtft", text.lower())
        self.assertNotIn("stmmac-0:", text)

    def test_required_controller_labels_are_resolved_in_source(self):
        text = self.read_required(DTS)

        for label in (
            "&sdmmc",
            "&sdio",
            "&emmc",
            "&gmac2io",
            "&gmac2phy",
            "&usb20_otg",
            "&usb_host0_ehci",
            "&usb_host0_ohci",
            "&usbdrd3",
            "&i2c1",
            "&uart0",
            "&uart2",
            "&spi0",
            "&pwm0",
            "&pwm1",
            "&pwm2",
            "&pwm3",
        ):
            self.assertIn(label, text)

    def test_wireless_and_experimental_peripheral_policy(self):
        text = self.read_required(DTS)

        self.assertNotIn("max-frequency = <1250000000>", text)
        self.assertRegex(
            text,
            r"(?s)&sdio\s*\{.*?max-frequency\s*=\s*<125000000>;.*?\};",
        )
        self.assertRegex(
            text,
            r"(?s)&sdmmc_ext\s*\{\s*status\s*=\s*\"disabled\";\s*\};",
        )
        pwrseq = re.search(r"(?s)sdio_pwrseq: sdio-pwrseq\s*\{(.*?)\n\s*\};", text)
        self.assertIsNotNone(pwrseq)
        self.assertNotIn("ext_clock", pwrseq.group(1))
        self.assertNotIn("post-power-on-delay", pwrseq.group(1))
        self.assertRegex(
            text, r"(?s)&spi0\s*\{\s*status\s*=\s*\"disabled\";\s*\};"
        )
        for pwm in range(4):
            self.assertRegex(
                text,
                rf'(?s)&pwm{pwm}\s*\{{\s*status\s*=\s*"disabled";\s*\}};',
            )

    def test_storage_power_console_and_network_wiring(self):
        text = self.read_required(DTS)

        for literal in (
            'stdout-path = "serial2:1500000n8";',
            "reset-gpios = <&gpio1 RK_PC2 GPIO_ACTIVE_LOW>;",
            "interrupts = <RK_PC3 IRQ_TYPE_LEVEL_HIGH>;",
            "bus-width = <8>;",
            "mmc-hs200-1_8v;",
            "interrupts = <RK_PA6 IRQ_TYPE_LEVEL_LOW>;",
            'clock_in_out = "output";',
            "assigned-clock-rates = <50000000>;",
        ):
            self.assertIn(literal, text)
        self.assertRegex(
            text,
            r'(?s)&gmac2io\s*\{\s*status\s*=\s*"disabled";\s*\};',
        )
        self.assertRegex(
            text, r'(?s)&gmac2phy\s*\{.*?status\s*=\s*"okay";.*?\};'
        )

    def test_registration_patch_is_narrow_and_reuses_upstream_vendor_prefix(self):
        text = self.read_required(PATCH)
        changed_paths = {
            path
            for prefix in ("--- a/", "+++ b/")
            for path in re.findall(rf"(?m)^{re.escape(prefix)}(.+)$", text)
        }

        self.assertEqual(
            changed_paths,
            {
                "arch/arm64/boot/dts/rockchip/Makefile",
                "Documentation/devicetree/bindings/arm/rockchip.yaml",
            },
        )
        self.assertIn(
            "+dtb-$(CONFIG_ARCH_ROCKCHIP) += rk3328-eaidk-310.dtb", text
        )
        self.assertNotIn("vendor-prefixes.yaml", text)
        self.assertNotIn('^openailab,.*', text)
        self.assertIn("+      - description: Open AI Lab EAIDK-310", text)
        self.assertIn("+          - const: openailab,eaidk-310", text)
        self.assertIn("+          - const: rockchip,rk3328", text)

    def test_installer_contract_is_strict_and_idempotent(self):
        text = self.read_required(INSTALLER)

        for literal in (
            "set -euo pipefail",
            'EXPECTED_VERSION="6.12.108"',
            'make -s -C "$SOURCE_DIR" kernelversion',
            'cmp -s "$BOARD_DTS" "$DTS_DESTINATION"',
            'patch --batch --forward -p1 < "$REGISTRATION_PATCH"',
            "INSTALL_BOARD_INPUTS_GATE=PASS",
        ):
            self.assertIn(literal, text)
        self.assertNotIn("/dev/mmcblk", text)
        self.assertNotIn("PhysicalDrive", text)

    def test_installer_can_run_twice_without_reject_files(self):
        if not pathlib.Path(r"C:\Windows\System32\wsl.exe").is_file():
            self.skipTest("WSL is unavailable")

        fixture_name = f"install-board-inputs-test-{uuid.uuid4().hex}"
        source_dir = f"/home/Fog/eaidk310-kernel/src/{fixture_name}"
        build_dir = f"/home/Fog/eaidk310-kernel/build-zramfix1-tests/{fixture_name}"
        project_parts = PROJECT_DIR.resolve().parts
        drive = project_parts[0][0].lower()
        wsl_project = "/mnt/" + drive + "/" + "/".join(project_parts[1:])
        installer = f"{wsl_project}/scripts/install-board-inputs.sh"
        setup = rf'''
set -euo pipefail
root="{source_dir}"
mkdir -p "$root/arch/arm64/boot/dts/rockchip"
mkdir -p "$root/Documentation/devicetree/bindings/arm"
mkdir -p "$root/Documentation/devicetree/bindings"
printf '%s\n' \
  'kernelversion:' \
  $'\t@echo 6.12.108' \
  'dtb-$(CONFIG_ARCH_ROCKCHIP) += rk3326-odroid-go2.dtb' \
  'dtb-$(CONFIG_ARCH_ROCKCHIP) += rk3326-odroid-go2-v11.dtb' \
  'dtb-$(CONFIG_ARCH_ROCKCHIP) += rk3326-odroid-go3.dtb' \
  'dtb-$(CONFIG_ARCH_ROCKCHIP) += rk3328-a1.dtb' \
  'dtb-$(CONFIG_ARCH_ROCKCHIP) += rk3328-evb.dtb' \
  'dtb-$(CONFIG_ARCH_ROCKCHIP) += rk3328-nanopi-r2c.dtb' \
  'dtb-$(CONFIG_ARCH_ROCKCHIP) += rk3328-nanopi-r2c-plus.dtb' \
  > "$root/Makefile"
cp "$root/Makefile" "$root/arch/arm64/boot/dts/rockchip/Makefile"
printf '%s\n' \
  'properties:' \
  '      - description: OPEN AI LAB EAIDK-610' \
  '        items:' \
  '          - const: openailab,eaidk-610' \
  '          - const: rockchip,rk3399' \
  '' \
  '      - description: Xunlong Orange Pi RK3399 board' \
  '        items:' \
  '          - const: xunlong,rk3399-orangepi' \
  > "$root/Documentation/devicetree/bindings/arm/rockchip.yaml"
printf '%s\n' '  "^openailab,.*":' '    description: openailab.com' \
  > "$root/Documentation/devicetree/bindings/vendor-prefixes.yaml"
'''
        try:
            prepared = subprocess.run(
                ["wsl", "-d", "Ubuntu-24.04", "--", "bash", "-s"],
                input=setup.replace("\r\n", "\n").encode("utf-8"),
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(
                prepared.returncode,
                0,
                prepared.stderr.decode("utf-8", errors="replace"),
            )

            command = [
                "wsl",
                "-d",
                "Ubuntu-24.04",
                "--",
                "bash",
                installer,
                source_dir,
                build_dir,
            ]
            first = subprocess.run(
                command,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=30,
            )
            second = subprocess.run(
                command,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
            self.assertEqual(second.returncode, 0, second.stdout + second.stderr)
            self.assertIn("INSTALL_BOARD_INPUTS_GATE=PASS", second.stdout)

            reject_check = subprocess.run(
                [
                    "wsl",
                    "-d",
                    "Ubuntu-24.04",
                    "--",
                    "find",
                    source_dir,
                    "-name",
                    "*.rej",
                    "-print",
                ],
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(reject_check.returncode, 0, reject_check.stderr)
            self.assertEqual(reject_check.stdout.strip(), "")

            config_check = subprocess.run(
                [
                    "wsl",
                    "-d",
                    "Ubuntu-24.04",
                    "--",
                    "cmp",
                    "-s",
                    f"{wsl_project}/config/eaidk310-6.12.108-zramfix1.config",
                    f"{build_dir}/.config",
                ],
                timeout=30,
            )
            self.assertEqual(config_check.returncode, 0)
        finally:
            subprocess.run(
                [
                    "wsl",
                    "-d",
                    "Ubuntu-24.04",
                    "--",
                    "rm",
                    "-rf",
                    source_dir,
                    build_dir,
                ],
                check=False,
                timeout=30,
            )


if __name__ == "__main__":
    unittest.main()
