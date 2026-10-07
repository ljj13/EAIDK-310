#!/usr/bin/env python3
"""st7789-statusd - render EAIDK-310 health to an ST7789 panel (fbtft fb).

P10 Phase 9: everything except plugging the panel in.  Reads the two
installed views of eaidk-health (status, network), renders two alternating
pages of system state onto a 240x240 RGB565 framebuffer using an embedded
3x5 microfont, and tolerates missing devices/fields so an unattended board
never breaks on a cosmetic dependency.

Usage:
  st7789-statusd.py --device /dev/fb1 [--width 240] [--height 240]
                    [--interval 5] [--once] [--wait]
Exit codes: 0 ok/--once done, 1 usage, 2 framebuffer unavailable.
"""

import argparse
import json
import mmap
import os
import struct
import subprocess
import sys
import time

WIDTH_DEFAULT = 240
HEIGHT_DEFAULT = 240

# 3x5 microfont: each glyph is 5 rows of 3 columns, '#' = lit.
FONT = {
    " ": ["   ", "   ", "   ", "   ", "   "],
    "A": ["###", "#.#", "###", "#.#", "#.#"],
    "B": ["##.", "#.#", "##.", "#.#", "##."],
    "C": ["###", "#..", "#..", "#..", "###"],
    "D": ["##.", "#.#", "#.#", "#.#", "##."],
    "E": ["###", "#..", "##.", "#..", "###"],
    "F": ["###", "#..", "##.", "#..", "#.."],
    "G": ["###", "#..", "#.#", "#.#", "###"],
    "H": ["#.#", "#.#", "###", "#.#", "#.#"],
    "I": ["###", ".#.", ".#.", ".#.", "###"],
    "J": ["..#", "..#", "..#", "#.#", "###"],
    "K": ["#.#", "#.#", "##.", "#.#", "#.#"],
    "L": ["#..", "#..", "#..", "#..", "###"],
    "M": ["#.#", "###", "###", "#.#", "#.#"],
    "N": ["##.", "#.#", "#.#", "#.#", "#.#"],
    "O": ["###", "#.#", "#.#", "#.#", "###"],
    "P": ["###", "#.#", "###", "#..", "#.."],
    "Q": ["###", "#.#", "###", "..#", "..#"],
    "R": ["###", "#.#", "##.", "#.#", "#.#"],
    "S": ["###", "#..", "###", "..#", "###"],
    "T": ["###", ".#.", ".#.", ".#.", ".#."],
    "U": ["#.#", "#.#", "#.#", "#.#", "###"],
    "V": ["#.#", "#.#", "#.#", "#.#", ".#."],
    "W": ["#.#", "#.#", "###", "###", "#.#"],
    "X": ["#.#", "#.#", ".#.", "#.#", "#.#"],
    "Y": ["#.#", "#.#", "###", ".#.", ".#."],
    "Z": ["###", "..#", ".#.", "#..", "###"],
    "0": ["###", "#.#", "#.#", "#.#", "###"],
    "1": [".#.", "##.", ".#.", ".#.", "###"],
    "2": ["###", "..#", "###", "#..", "###"],
    "3": ["###", "..#", "###", "..#", "###"],
    "4": ["#.#", "#.#", "###", "..#", "..#"],
    "5": ["###", "#..", "###", "..#", "###"],
    "6": ["###", "#..", "###", "#.#", "###"],
    "7": ["###", "..#", ".#.", ".#.", ".#."],
    "8": ["###", "#.#", "###", "#.#", "###"],
    "9": ["###", "#.#", "###", "..#", "###"],
    "%": ["#.#", "..#", ".#.", "#..", "#.#"],
    ".": ["   ", "   ", "   ", "   ", ".#."],
    ":": ["   ", ".#.", "   ", ".#.", "   "],
    "/": ["..#", ".#.", ".#.", "#..", "#.."],
    "-": ["   ", "   ", "###", "   ", "   "],
    "*": [".#.", "#.#", ".#.", "   ", "   "],  # degree sign stand-in
}

GLYPH_W, GLYPH_H = 3, 5

# RGB565 palette
COLOR_BG = 0x0000
COLOR_FG = 0x07E0  # green
COLOR_TITLE = 0x07FF  # cyan
COLOR_WARN = 0xFFE0  # yellow
COLOR_CRIT = 0xF800  # red
COLOR_DIM = 0x4200  # dark green-grey


def rgb565(r, g, b):
    return ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)


class Screen:
    """Backbuffer of an RGB565 framebuffer with text primitives."""

    def __init__(self, width=WIDTH_DEFAULT, height=HEIGHT_DEFAULT,
                 scale=2, buf=None):
        self.width = width
        self.height = height
        self.scale = scale
        self.buf = buf if buf is not None \
            else bytearray(width * height * 2)

    def pixel(self, x, y, color):
        if 0 <= x < self.width and 0 <= y < self.height:
            off = (y * self.width + x) * 2
            self.buf[off:off + 2] = struct.pack("<H", color)

    def rect(self, x, y, w, h, color):
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                self.pixel(xx, yy, color)

    def glyph(self, ch, x, y, color, scale=None):
        rows = FONT.get(ch.upper(), FONT.get(ch))
        if rows is None:
            rows = FONT[" "]
        scale = scale or self.scale
        for ry, row in enumerate(rows):
            for rx, cell in enumerate(row):
                if cell == "#":
                    for dy in range(scale):
                        for dx in range(scale):
                            self.pixel(x + rx * scale + dx,
                                       y + ry * scale + dy, color)

    def text(self, s, x, y, color, scale=None):
        scale = scale or self.scale
        for i, ch in enumerate(s):
            self.glyph(ch, x + i * (GLYPH_W + 1) * scale, y, color, scale)
        return x + len(s) * (GLYPH_W + 1) * scale

    def text_width(self, s, scale=None):
        scale = scale or self.scale
        return len(s) * (GLYPH_W + 1) * scale


def parse_health(view):
    """Run one eaidk-health view; return dict or {} on any failure."""
    try:
        proc = subprocess.run(["eaidk-health", view, "--json"],
                              capture_output=True, text=True, timeout=30)
        if proc.returncode == 0:
            return json.loads(proc.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        pass
    return {}


def fmt_uptime(seconds):
    if not seconds:
        return "?"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    d, h = divmod(h, 24)
    if d:
        return f"{d}D {h:02d}:{m:02d}"
    return f"{h:02d}:{m:02d}:{s:02d}"


def mem_used_frac(view):
    mem = view.get("memory") or {}
    total, avail = mem.get("mem_total_kib"), mem.get("mem_available_kib")
    if not total or avail is None:
        return None
    return (total - avail) / total


def zram_usage_frac(view):
    zram = (view.get("zram") or {}).get("usage") or {}
    size = (view.get("zram") or {}).get("disksize_bytes")
    used = zram.get("disksize_used_bytes")
    if not size or used is None:
        return None
    return used / size


def temp_mc(view):
    for zone in view.get("thermal") or []:
        if zone.get("temp_mC") is not None:
            return zone["temp_mC"]
    return None


def first_addr(view, prefix):
    for iface in view.get("interfaces") or []:
        if iface.get("name") == prefix:
            addrs = iface.get("addresses") or []
            return addrs[0] if addrs else "no-addr"
    return "absent"


def render_page1(view, scr):
    scr.rect(0, 0, scr.width, scr.height, COLOR_BG)
    scr.text("EAIDK-310", 6, 6, COLOR_TITLE)
    scr.text(time.strftime("%H:%M:%S"), scr.width - scr.text_width("00:00:00") - 6,
             6, COLOR_DIM)
    y = 34
    line_h = 24
    # CPU: loadavg as a proxy share (cheap, monotonic-view-independent)
    load = ((view.get("identity") or {}).get("loadavg_1_5_15") or [None])[0]
    cores = (view.get("cpu") or {}).get("cores") or 4
    freq = (view.get("cpu") or {}).get("current_freq_khz")
    cpu_s = f"CPU {load or '?'}"[:18]
    scr.text(cpu_s, 6, y, COLOR_FG)
    if freq:
        scr.text(f"{freq / 1000:.0f}MHZ", scr.width - scr.text_width("0000MHZ") - 6,
                 y, COLOR_DIM)
    y += line_h
    frac = mem_used_frac(view)
    total = ((view.get("memory") or {}).get("mem_total_kib") or 0) / 1024
    ram_s = f"RAM {(frac or 0) * 100:3.0f}% {total:.0f}MB"
    scr.text(ram_s, 6, y, COLOR_FG if (frac or 0) < 0.85 else COLOR_WARN)
    y += line_h
    t = temp_mc(view)
    tcol = COLOR_FG
    if t is not None and t >= 85000:
        tcol = COLOR_CRIT
    elif t is not None and t >= 70000:
        tcol = COLOR_WARN
    scr.text(f"TEMP {t / 1000:.1f}*C" if t is not None else "TEMP ?",
             6, y, tcol)
    y += line_h
    zf = zram_usage_frac(view)
    algo = ((view.get("zram") or {}).get("algorithm") or "?").upper()
    scr.text(f"ZRAM {algo} {(zf or 0) * 100:3.0f}%", 6, y, COLOR_FG)
    y += line_h
    up = (view.get("identity") or {}).get("uptime_seconds")
    scr.text(f"UPT {fmt_uptime(up)}", 6, y, COLOR_FG)
    return scr


def render_page2(status, network, ota_state, scr):
    scr.rect(0, 0, scr.width, scr.height, COLOR_BG)
    scr.text("NET", 6, 6, COLOR_TITLE)
    ts = (network.get("tailscale") or {})
    ts_state = ts.get("backend_state") or "?"
    ts_ip = ts.get("self_ip") or "?"
    scr.text(f"TS {ts_state}", 6, 34,
             COLOR_FG if ts_state == "Running" else COLOR_WARN)
    scr.text(str(ts_ip), 6, 58, COLOR_DIM)
    scr.text(f"ETH {first_addr(network, 'eth0')}", 6, 82, COLOR_FG)
    disk = (status.get("disk") or {}).get("/")
    if disk:
        pct = disk.get("use_percent", 0)
        col = COLOR_CRIT if pct >= 90 else (COLOR_WARN if pct >= 80 else COLOR_FG)
        scr.text(f"DISK {pct}% {disk.get('avail_kib', 0) // 1024}MFREE",
                 6, 106, col)
    kern = (status.get("identity") or {}).get("kernel") or "?"
    scr.text(kern[:20], 6, 130, COLOR_DIM)
    scr.text(f"OTA {ota_state}", 6, 154,
             COLOR_FG if ota_state in ("COMMITTED", "IDLE") else COLOR_WARN)
    failed = ((status.get("failed_units") or {}).get("count"))
    if failed is not None:
        scr.text(f"FAILED {failed}", 6, 178,
                 COLOR_FG if failed == 0 else COLOR_CRIT)
    return scr


class Framebuffer:
    def __init__(self, device, width, height):
        self.fd = os.open(device, os.O_RDWR)
        size = width * height * 2
        try:
            self.mm = mmap.mmap(self.fd, size)
        except (ValueError, OSError):
            os.close(self.fd)
            raise

    def flush(self, buf):
        self.mm.seek(0)
        self.mm[:len(buf)] = buf

    def close(self):
        try:
            self.mm.close()
        finally:
            os.close(self.fd)


def cycle(fb, width, height, page_no, status, network):
    scr = Screen(width, height)
    ota = (status.get("ota-state") or "OFFLINE")
    if page_no % 2 == 0:
        render_page1(status, scr)
    else:
        render_page2(status, network, ota, scr)
    fb.flush(scr.buf)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", default="/dev/fb1")
    ap.add_argument("--width", type=int, default=WIDTH_DEFAULT)
    ap.add_argument("--height", type=int, default=HEIGHT_DEFAULT)
    ap.add_argument("--interval", type=float, default=5.0)
    ap.add_argument("--once", action="store_true",
                    help="render one page and exit")
    ap.add_argument("--wait", action="store_true",
                    help="retry until the framebuffer appears (daemon use)")
    args = ap.parse_args(argv)

    fb = None
    while fb is None:
        try:
            fb = Framebuffer(args.device, args.width, args.height)
        except (OSError, ValueError):
            if not args.wait:
                print(f"framebuffer {args.device} unavailable", file=sys.stderr)
                return 2
            time.sleep(15)

    page = 0
    try:
        while True:
            status = parse_health("status")
            network = parse_health("network")
            status.setdefault("ota-state", "OFFLINE")
            try:
                proc = subprocess.run(
                    ["eaidk-ota", "status", "--json"], capture_output=True,
                    text=True, timeout=20)
                if proc.returncode == 0:
                    status["ota-state"] = json.loads(proc.stdout).get(
                        "state") or "UNKNOWN"
            except (OSError, subprocess.TimeoutExpired, ValueError):
                pass
            cycle(fb, args.width, args.height, page, status, network)
            if args.once:
                return 0
            page += 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        return 0
    finally:
        fb.close()


if __name__ == "__main__":
    sys.exit(main())
