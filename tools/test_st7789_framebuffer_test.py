import importlib
import struct
import unittest


def load_pattern_module(test_case: unittest.TestCase):
    try:
        return importlib.import_module("tools.st7789_framebuffer_test")
    except ModuleNotFoundError:
        test_case.fail("ST7789 framebuffer test program does not exist yet")


class St7789FramebufferPatternTests(unittest.TestCase):
    def test_eight_color_bars_are_rgb565_little_endian(self):
        pattern = load_pattern_module(self)

        frame = pattern.make_color_bars(8, 2)
        expected_row = b"".join(
            struct.pack("<H", value)
            for value in (
                0x0000,
                0xFFFF,
                0xF800,
                0x07E0,
                0x001F,
                0x07FF,
                0xF81F,
                0xFFE0,
            )
        )

        self.assertEqual(frame, expected_row * 2)

    def test_width_that_is_not_divisible_by_eight_fills_every_pixel(self):
        pattern = load_pattern_module(self)
        frame = pattern.make_color_bars(11, 3)

        self.assertEqual(len(frame), 11 * 3 * 2)
        self.assertEqual(frame[-2:], struct.pack("<H", 0xFFE0))


if __name__ == "__main__":
    unittest.main()
