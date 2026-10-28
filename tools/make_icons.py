"""Generate the committed PWA icons: a yellow bass clef on a purple field.

The glyph outline comes from Noto Music (SIL Open Font License 1.1); the
license text is in ``tools/assets/OFL.txt``. Run this script after changing the
palette or the mark:

    python tools/make_icons.py
"""

from __future__ import annotations

from pathlib import Path

from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[1]
ASSETS = Path(__file__).resolve().parent / 'assets'
STATIC = ROOT / 'src' / 'soundsave' / 'web' / 'static'
ICONS = STATIC / 'icons'
FONT = ASSETS / 'NotoMusic-Regular.ttf'
GLYPH = '\U0001d122'
SIZE = 1024
PURPLE_TOP = (46, 16, 101)
PURPLE_BOTTOM = (124, 58, 237)
YELLOW = (250, 204, 21)


def _gradient(size: int) -> Image.Image:
    image = Image.new('RGBA', (size, size))
    draw = ImageDraw.Draw(image)
    for y in range(size):
        ratio = y / max(1, size - 1)
        color = tuple(
            round(start + (end - start) * ratio) for start, end in zip(PURPLE_TOP, PURPLE_BOTTOM, strict=True)
        )
        draw.line((0, y, size, y), fill=(*color, 255))
    return image


def draw_icon(*, maskable: bool) -> Image.Image:
    image = _gradient(SIZE)
    if not maskable:
        mask = Image.new('L', (SIZE, SIZE), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, SIZE - 1, SIZE - 1),
            radius=int(SIZE * 0.22),
            fill=255,
        )
        image.putalpha(mask)
    font_size = int(SIZE * (0.64 if maskable else 0.78))
    font = ImageFont.truetype(str(FONT), font_size)
    glyph = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(glyph)
    bbox = draw.textbbox((0, 0), GLYPH, font=font)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    draw.text(
        ((SIZE - width) / 2 - bbox[0], (SIZE - height) / 2 - bbox[1]),
        GLYPH,
        font=font,
        fill=YELLOW,
    )
    # The SVG favicon uses the font outline as-is, which is y-up and therefore
    # appears vertically mirrored in SVG coordinates. Mirror the PNG glyph too
    # so the header, favicon and PWA icon show the same upside-down clef.
    image.alpha_composite(ImageOps.flip(glyph))
    return image


def write_favicon_svg() -> None:
    font = TTFont(FONT)
    glyph_name = font.getBestCmap()[ord(GLYPH)]
    glyphs = font.getGlyphSet()
    bounds_pen = BoundsPen(glyphs)
    glyphs[glyph_name].draw(bounds_pen)
    if bounds_pen.bounds is None:
        raise RuntimeError('The bass clef glyph has no bounds')
    x0, y0, x1, y1 = bounds_pen.bounds
    path_pen = SVGPathPen(glyphs)
    glyphs[glyph_name].draw(path_pen)
    scale = 620 / max(x1 - x0, y1 - y0)
    tx = 512 - scale * (x0 + x1) / 2
    ty = 512 - scale * (y0 + y1) / 2
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#2e1065"/>
      <stop offset="1" stop-color="#7c3aed"/>
    </linearGradient>
  </defs>
  <rect width="1024" height="1024" rx="225" fill="url(#bg)"/>
  <path d="{path_pen.getCommands()}" transform="translate({tx:.3f} {ty:.3f}) scale({scale:.6f})" fill="#facc15"/>
</svg>
"""
    (STATIC / 'favicon.svg').write_text(svg, encoding='utf-8')


def main() -> None:
    ICONS.mkdir(parents=True, exist_ok=True)
    normal = draw_icon(maskable=False)
    maskable = draw_icon(maskable=True)
    normal.resize((192, 192), Image.Resampling.LANCZOS).save(ICONS / 'icon-192.png')
    normal.resize((512, 512), Image.Resampling.LANCZOS).save(ICONS / 'icon-512.png')
    maskable.resize((512, 512), Image.Resampling.LANCZOS).save(ICONS / 'maskable-512.png')
    normal.resize((180, 180), Image.Resampling.LANCZOS).save(ICONS / 'apple-touch-icon-180.png')
    write_favicon_svg()


if __name__ == '__main__':
    main()
