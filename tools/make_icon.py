"""Draw the application icon of sdt and write assets/sdt.ico.

A dark tile with an amber border and the letters SDT under a uniformly
distributed load. The T is a T-beam section, flange and web, with its top and
bottom bars; the D holds the ordinates of a moment diagram. The small sizes
are simpler, since those details blur: no bars at 32 pixels and below, and
the letters alone at 24 and 16. Run it again after a change of the design:

    python tools/make_icon.py

It needs Pillow and the Bahnschrift font of Windows (the DIN typeface). The
build only reads the file it writes: `build_exe.ps1` gives `assets/sdt.ico`
to PyInstaller.
"""

from __future__ import annotations

import os

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT = r"C:\Windows\Fonts\bahnschrift.ttf"
SIZE = 1024                              # drawn large, then scaled to each icon size
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
SMALL = 32                               # at this size and below the bars are left out: they blur
TINY = 24                                # at this size and below, the letters alone
DARK, AMBER = (28, 30, 36), (255, 170, 44)
CAP = 292                                # height of the letters
K = CAP / 400.0
STROKE = 78 * K                          # thickness of the T-beam flange and web


def _font(size: int) -> ImageFont.FreeTypeFont:
    font = ImageFont.truetype(FONT, size)
    try:
        font.set_variation_by_name("Bold")
    except (OSError, AttributeError):
        pass
    return font


def _box(font: ImageFont.FreeTypeFont, letter: str) -> tuple[float, float, float, float]:
    left, top, right, bottom = font.getbbox(letter)
    return left, top, right - left, bottom - top


def draw_icon(bars: bool = True, details: bool = True) -> Image.Image:
    """The icon at ``SIZE`` pixels. ``bars`` False leaves the reinforcing bars out;
    ``details`` False leaves out the load and the moment diagram too, and the
    letters are then larger and centred."""
    mask = Image.new("L", (SIZE, SIZE), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, SIZE - 1, SIZE - 1), 224, fill=255)
    image = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    image.paste(Image.new("RGBA", (SIZE, SIZE), DARK + (255,)), (0, 0), mask)
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((26, 26, SIZE - 27, SIZE - 27), 200, outline=AMBER, width=30)

    size = 300                            # the font size whose capitals are CAP high
    while _box(_font(size), "D")[3] < CAP:
        size += 2
    font = _font(size)
    boxes = {letter: _box(font, letter) for letter in "SD"}
    t_width, gap = 300 * K, 30 * K
    total = boxes["S"][2] + gap + boxes["D"][2] + gap + t_width
    start = (SIZE - total) / 2
    top, x = (SIZE - CAP) / 2 + (62 if details else 0), start
    for letter in "SD":
        left, glyph_top, width, height = boxes[letter]
        # a round letter overshoots the cap height a little: centre it on the capitals
        draw.text((x - left, top - glyph_top - (height - CAP) / 2), letter, font=font, fill=AMBER)
        if letter == "D" and details:
            # the ordinates of a moment diagram inside the bowl: the stem is the beam
            stem = x + STROKE * 0.98
            inner_top, inner_bottom = top + STROKE * 0.98, top + CAP - STROKE * 0.98
            reach, count = width - STROKE * 1.95, 5
            for index in range(1, count + 1):
                t = index / (count + 1)
                y = inner_top + t * (inner_bottom - inner_top)
                length = reach * (4 * t * (1 - t)) ** 0.5 * 0.98
                draw.line([(stem, y), (stem + length, y)], fill=AMBER, width=round(13 * K) + 1)
        x += width + gap

    # T: a T-beam section
    draw.rounded_rectangle((x, top, x + t_width, top + STROKE), 8, fill=AMBER)
    web = x + (t_width - STROKE) / 2
    draw.rounded_rectangle((web, top, web + STROKE, top + CAP), 8, fill=AMBER)
    if bars:
        r = 15 * K
        for cx in (x + 40 * K, x + 104 * K, x + t_width - 104 * K, x + t_width - 40 * K):
            draw.ellipse((cx - r, top + STROKE / 2 - r, cx + r, top + STROKE / 2 + r), fill=DARK)
        for cy in (top + CAP - 44 * K, top + CAP - 104 * K):
            draw.ellipse((web + STROKE / 2 - r, cy - r, web + STROKE / 2 + r, cy + r), fill=DARK)
    if details:
        # a uniformly distributed load on the three letters
        left, right = start, start + total
        line_y, tip_y, arrows = top - 122, top - 24, 6
        draw.rounded_rectangle((left, line_y, right, line_y + 16), 8, fill=AMBER)
        for index in range(arrows):
            ax = left + 14 + index * (right - left - 28) / (arrows - 1)
            draw.rectangle((ax - 7, line_y, ax + 7, tip_y - 30), fill=AMBER)
            draw.polygon([(ax, tip_y), (ax - 26, tip_y - 42), (ax + 26, tip_y - 42)], fill=AMBER)
    return image


def _scaled(letters_height: int) -> None:
    """Set the height of the letters, and with it the sizes that follow from it."""
    global CAP, K, STROKE
    CAP = letters_height
    K = CAP / 400.0
    STROKE = 78 * K


def main() -> None:
    folder = os.path.join(ROOT, "assets")
    os.makedirs(folder, exist_ok=True)
    full, no_bars = draw_icon(), draw_icon(bars=False)
    _scaled(340)                          # the letters alone can be larger
    letters = draw_icon(bars=False, details=False)
    _scaled(292)

    def drawing(size: int) -> Image.Image:
        return letters if size <= TINY else (no_bars if size <= SMALL else full)

    frames = [drawing(size).resize((size, size), Image.LANCZOS) for size in ICON_SIZES]
    frames[-1].save(os.path.join(folder, "sdt.ico"), format="ICO",
                    sizes=[(size, size) for size in ICON_SIZES], append_images=frames[:-1])
    print("written:", os.path.join(folder, "sdt.ico"))


if __name__ == "__main__":
    main()
