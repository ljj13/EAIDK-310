"""Write a one-frame RGB565 color-bar test pattern to an ST7789 framebuffer."""

import argparse
import json
from pathlib import Path


COLORS_RGB565 = (
    0x0000,  # black
    0xFFFF,  # white
    0xF800,  # red
    0x07E0,  # green
    0x001F,  # blue
    0x07FF,  # cyan
    0xF81F,  # magenta
    0xFFE0,  # yellow
)


def make_color_bars(width: int, height: int) -> bytes:
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    row = bytearray()
    for x in range(width):
        color = COLORS_RGB565[min(x * len(COLORS_RGB565) // width, 7)]
        row.extend(color.to_bytes(2, "little"))
    return bytes(row) * height


def find_framebuffer(width: int, height: int) -> Path:
    for sysfs in sorted(Path("/sys/class/graphics").glob("fb*")):
        virtual_size = (sysfs / "virtual_size").read_text().strip()
        bits_per_pixel = int((sysfs / "bits_per_pixel").read_text().strip())
        if virtual_size == f"{width},{height}" and bits_per_pixel == 16:
            return Path("/dev") / sysfs.name
    raise RuntimeError(f"no {width}x{height} 16-bpp framebuffer found")


def write_pattern(framebuffer: Path, width: int, height: int) -> int:
    frame = make_color_bars(width, height)
    with Path(framebuffer).open("r+b", buffering=0) as device:
        device.seek(0)
        written = device.write(frame)
    if written != len(frame):
        raise OSError(f"short framebuffer write: expected {len(frame)}, wrote {written}")
    return written


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--framebuffer", type=Path)
    parser.add_argument("--width", type=int, default=240)
    parser.add_argument("--height", type=int, default=320)
    args = parser.parse_args()
    framebuffer = args.framebuffer or find_framebuffer(args.width, args.height)
    written = write_pattern(framebuffer, args.width, args.height)
    print(
        json.dumps(
            {
                "framebuffer": str(framebuffer),
                "width": args.width,
                "height": args.height,
                "format": "RGB565 little-endian",
                "bytes_written": written,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
