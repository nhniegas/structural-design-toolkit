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
BACKGROUND = (15, 31, 48, 255)
PANEL = (24, 52, 72, 255)
STEEL = (230, 239, 242, 255)
TEAL = (54, 208, 190, 255)
ORANGE = (255, 167, 69, 255)


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

    # Technical panel and a subtle roof line.
    rect(1.5, 1.5, size - 1.5, size - 1.5, PANEL)
    line([(3, 7), (size / 2, 3), (size - 3, 7)], 1.2, TEAL)

    # Structural frame: columns, floor beams, and foundation.
    left, right = 4.5, size - 4.5
    top, base = 7.5, size - 4.5
    line([(left, top), (left, base)], 2.0, STEEL)
    line([(right, top), (right, base)], 2.0, STEEL)
    line([(left, top), (right, top)], 2.0, STEEL)
    line([(left, size * 0.42), (right, size * 0.42)], 1.5, STEEL)
    line([(left, size * 0.64), (right, size * 0.64)], 1.5, STEEL)
    line([(left - 1, base), (right + 1, base)], 2.2, STEEL)
    line([(left, size * 0.42), (size / 2, top)], 1.0, TEAL)
    line([(size / 2, top), (right, size * 0.42)], 1.0, TEAL)

    # Analysis result/check mark.
    circle(size * 0.73, size * 0.76, size * 0.15, ORANGE)
    line(
        [(size * 0.65, size * 0.76), (size * 0.71, size * 0.82), (size * 0.82, size * 0.69)],
        max(1.2, size * 0.055),
        BACKGROUND,
    )

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
