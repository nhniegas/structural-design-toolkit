"""Generate the Structural Design Toolkit Windows icon.

The renderer intentionally uses only the Python standard library so the icon
can be regenerated on a clean release machine without adding a runtime
dependency.
"""

from __future__ import annotations

import math
import struct
import zlib
from pathlib import Path


SIZES = (16, 24, 32, 48, 64, 128, 256)
BACKGROUND = (226, 234, 243, 255)
BLUE = (30, 112, 204, 255)
DEEP_BLUE = (16, 68, 137, 255)
LIGHT_BLUE = (71, 157, 231, 255)
WHITE = (249, 252, 255, 255)


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def png(width: int, height: int, pixels: bytearray) -> bytes:
    rows = b"".join(b"\x00" + pixels[row * width * 4 : (row + 1) * width * 4] for row in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + png_chunk(b"IHDR", header) + png_chunk(b"IDAT", zlib.compress(rows, 9)) + png_chunk(b"IEND", b"")


def draw_icon(size: int) -> bytes:
    scale = 4
    width = height = size * scale
    pixels = bytearray(BACKGROUND * (width * height))

    def set_pixel(x: int, y: int, color: tuple[int, int, int, int]) -> None:
        if 0 <= x < width and 0 <= y < height:
            offset = (y * width + x) * 4
            pixels[offset : offset + 4] = bytes(color)

    def rect(x0: float, y0: float, x1: float, y1: float, color: tuple[int, int, int, int]) -> None:
        for y in range(max(0, round(y0 * scale)), min(height, round(y1 * scale) + 1)):
            for x in range(max(0, round(x0 * scale)), min(width, round(x1 * scale) + 1)):
                set_pixel(x, y, color)

    def rounded_rect(x0: float, y0: float, x1: float, y1: float, radius: float, color: tuple[int, int, int, int]) -> None:
        for y in range(max(0, round(y0 * scale)), min(height, round(y1 * scale) + 1)):
            for x in range(max(0, round(x0 * scale)), min(width, round(x1 * scale) + 1)):
                px, py = x / scale, y / scale
                nearest_x = min(max(px, x0 + radius), x1 - radius)
                nearest_y = min(max(py, y0 + radius), y1 - radius)
                if (px - nearest_x) ** 2 + (py - nearest_y) ** 2 <= radius**2:
                    set_pixel(x, y, color)

    def line(points: list[tuple[float, float]], thickness: float, color: tuple[int, int, int, int]) -> None:
        radius = max(1, thickness * scale / 2)
        for (x0, y0), (x1, y1) in zip(points, points[1:]):
            x0, y0, x1, y1 = x0 * scale, y0 * scale, x1 * scale, y1 * scale
            distance = max(abs(x1 - x0), abs(y1 - y0), 1)
            for step in range(round(distance) + 1):
                fraction = step / distance
                cx = x0 + (x1 - x0) * fraction
                cy = y0 + (y1 - y0) * fraction
                for yy in range(math.floor(cy - radius), math.ceil(cy + radius) + 1):
                    for xx in range(math.floor(cx - radius), math.ceil(cx + radius) + 1):
                        if (xx - cx) ** 2 + (yy - cy) ** 2 <= radius**2:
                            set_pixel(xx, yy, color)

    def circle(cx: float, cy: float, radius: float, color: tuple[int, int, int, int]) -> None:
        radius *= scale
        cx *= scale
        cy *= scale
        for y in range(math.floor(cy - radius), math.ceil(cy + radius) + 1):
            for x in range(math.floor(cx - radius), math.ceil(cx + radius) + 1):
                if (x - cx) ** 2 + (y - cy) ** 2 <= radius**2:
                    set_pixel(x, y, color)

    # Layered badge inspired by engineering software marks.
    rounded_rect(11, 11, size - 11, size - 11, 12, BLUE)
    rect(11, size * 0.72, size - 11, size - 11, DEEP_BLUE)
    line([(11, size * 0.72), (size - 11, size * 0.72)], 1.2, LIGHT_BLUE)

    # Bold block lettering keeps the product name readable in Explorer.
    unit = size / 256
    def glyph_s(x: float, y: float) -> None:
        rect(x, y, x + 42 * unit, y + 10 * unit, WHITE)
        rect(x, y, x + 10 * unit, y + 31 * unit, WHITE)
        rect(x, y + 25 * unit, x + 42 * unit, y + 35 * unit, WHITE)
        rect(x + 32 * unit, y + 30 * unit, x + 42 * unit, y + 61 * unit, WHITE)
        rect(x, y + 56 * unit, x + 42 * unit, y + 66 * unit, WHITE)

    def glyph_d(x: float, y: float) -> None:
        rect(x, y, x + 10 * unit, y + 66 * unit, WHITE)
        rect(x + 8 * unit, y, x + 31 * unit, y + 10 * unit, WHITE)
        rect(x + 8 * unit, y + 56 * unit, x + 31 * unit, y + 66 * unit, WHITE)
        rect(x + 31 * unit, y + 8 * unit, x + 41 * unit, y + 58 * unit, WHITE)

    def glyph_t(x: float, y: float) -> None:
        rect(x, y, x + 44 * unit, y + 10 * unit, WHITE)
        rect(x + 17 * unit, y, x + 27 * unit, y + 66 * unit, WHITE)

    letter_y = size * 0.29
    glyph_s(size * 0.16, letter_y)
    glyph_d(size * 0.38, letter_y)
    glyph_t(size * 0.62, letter_y)

    # Beam, column, and triangulated roof accents.
    line([(size * 0.18, size * 0.86), (size * 0.82, size * 0.86)], 2.0, LIGHT_BLUE)
    line([(size * 0.30, size * 0.86), (size * 0.30, size * 0.76)], 1.4, LIGHT_BLUE)
    line([(size * 0.70, size * 0.86), (size * 0.70, size * 0.76)], 1.4, LIGHT_BLUE)
    line([(size * 0.30, size * 0.76), (size / 2, size * 0.70), (size * 0.70, size * 0.76)], 1.2, LIGHT_BLUE)

    # Average the supersampled pixels down to a clean multi-size icon.
    output = bytearray(size * size * 4)
    for y in range(size):
        for x in range(size):
            totals = [0, 0, 0, 0]
            for yy in range(y * scale, (y + 1) * scale):
                for xx in range(x * scale, (x + 1) * scale):
                    offset = (yy * width + xx) * 4
                    for channel in range(4):
                        totals[channel] += pixels[offset + channel]
            offset = (y * size + x) * 4
            output[offset : offset + 4] = bytes(value // (scale * scale) for value in totals)
    return png(size, size, output)


def write_ico(path: Path) -> None:
    images = [(size, draw_icon(size)) for size in SIZES]
    header = struct.pack("<HHH", 0, 1, len(images))
    directory_size = 6 + 16 * len(images)
    entries = bytearray()
    offset = directory_size
    payload = bytearray()
    for size, image in images:
        entries += struct.pack("<BBBBHHII", size if size < 256 else 0, size if size < 256 else 0, 0, 0, 1, 32, len(image), offset)
        payload += image
        offset += len(image)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + payload)


if __name__ == "__main__":
    write_ico(Path(__file__).resolve().parents[1] / "assets" / "sdt.ico")
