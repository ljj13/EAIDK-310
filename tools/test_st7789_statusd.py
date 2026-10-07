"""Offline unit tests for tools/st7789-statusd.py (no framebuffer needed)."""

import importlib.util
import os
import sys
import unittest
from unittest import mock

_TOOLS = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_loader(
    "st7789_statusd",
    importlib.machinery.SourceFileLoader(
        "st7789_statusd", os.path.join(_TOOLS, "st7789-statusd.py")))
sd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sd)


class FontTests(unittest.TestCase):
    def test_all_glyphs_are_3x5(self):
        for ch, rows in sd.FONT.items():
            self.assertEqual(len(rows), 5, ch)
            for row in rows:
                self.assertEqual(len(row), 3, ch)
                self.assertTrue(set(row) <= {"#", ".", " "}, ch)

    def test_digit_one_has_a_centered_stem(self):
        rows = sd.FONT["1"]
        self.assertTrue(all(r[1] == "#" for r in rows))
        self.assertEqual(rows[-1], "###")

    def test_unknown_glyph_falls_back_to_space(self):
        scr = sd.Screen(32, 16)
        scr.glyph("?", 0, 0, sd.COLOR_FG)
        scr2 = sd.Screen(32, 16)
        scr2.glyph(" ", 0, 0, sd.COLOR_FG)
        self.assertEqual(scr.buf, scr2.buf)


class ScreenTests(unittest.TestCase):
    def test_text_dimensions(self):
        scr = sd.Screen(240, 240)
        end = scr.text("CPU 42%", 0, 0, sd.COLOR_FG)
        self.assertEqual(end, 7 * 4 * 2)  # (3+1)*scale per glyph

    def test_pixel_bounds_safe(self):
        scr = sd.Screen(8, 8)
        scr.pixel(-1, 0, 1)
        scr.pixel(8, 0, 1)
        scr.pixel(0, 0, 0xABCD)
        self.assertEqual(scr.buf[0:2], b"\xcd\xab")

    def test_rect_fills(self):
        scr = sd.Screen(10, 10)
        scr.rect(0, 0, 10, 10, 0xFFFF)
        self.assertEqual(scr.buf, b"\xff\xff" * 100)


class RenderTests(unittest.TestCase):
    STATUS = {
        "identity": {"loadavg_1_5_15": [0.42, 0, 0], "uptime_seconds": 90061,
                     "kernel": "6.18.55-eaidk310-wifi3"},
        "cpu": {"cores": 4, "current_freq_khz": 1200000},
        "memory": {"mem_total_kib": 996700, "mem_available_kib": 500000},
        "zram": {"algorithm": "lz4", "disksize_bytes": 402653184,
                 "usage": {"disksize_used_bytes": 40265318}},
        "thermal": [{"zone": "z0", "type": "soc", "temp_mC": 52400}],
        "disk": {"/": {"use_percent": 61, "avail_kib": 2400000}},
        "failed_units": {"count": 0, "units": []},
    }
    NETWORK = {
        "interfaces": [{"name": "eth0", "state": "UP",
                        "addresses": ["172.31.180.4/24"]}],
        "tailscale": {"backend_state": "Running",
                      "self_ip": "100.72.239.86"},
    }

    def test_page1_renders_nonempty(self):
        scr = sd.render_page1(self.STATUS, sd.Screen(240, 240))
        lit = sum(1 for i in range(0, len(scr.buf), 2)
                  if scr.buf[i:i + 2] != b"\x00\x00")
        self.assertGreater(lit, 500)

    def test_page1_temp_warn_color(self):
        hot = dict(self.STATUS, thermal=[{"temp_mC": 88000}])
        scr = sd.render_page1(hot, sd.Screen(240, 240))
        self.assertIn(b"\x00\xf8", scr.buf)  # a red pixel exists

    def test_page2_ota_warn_color(self):
        scr = sd.render_page2(self.STATUS, self.NETWORK, "STAGED",
                              sd.Screen(240, 240))
        lit = sum(1 for i in range(0, len(scr.buf), 2)
                  if scr.buf[i:i + 2] != b"\x00\x00")
        self.assertGreater(lit, 300)

    def test_missing_fields_do_not_crash(self):
        scr = sd.render_page1({}, sd.Screen(240, 240))
        self.assertEqual(len(scr.buf), 240 * 240 * 2)
        scr = sd.render_page2({}, {}, "OFFLINE", sd.Screen(240, 240))

    def test_cycle_flushes(self):
        flushed = {}

        class FakeFB:
            def flush(self, buf):
                flushed["len"] = len(buf)

        sd.cycle(FakeFB(), 240, 240, 0, self.STATUS, self.NETWORK)
        self.assertEqual(flushed["len"], 240 * 240 * 2)


class ParseHealthTests(unittest.TestCase):
    def test_missing_binary_is_empty(self):
        with mock.patch.object(sd.subprocess, "run",
                               side_effect=FileNotFoundError):
            self.assertEqual(sd.parse_health("status"), {})


if __name__ == "__main__":
    unittest.main()
