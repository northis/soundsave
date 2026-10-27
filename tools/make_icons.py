"""Generate the committed PWA PNG icons from the same simple note mark."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / 'src' / 'soundsave' / 'web' / 'static' / 'icons'
BLUE = (37, 99, 235, 255)
WHITE = (255, 255, 255, 255)
SIZE = 1024


def draw_icon(*, maskable: bool) -> Image.Image:
    image = Image.new('RGBA', (SIZE, SIZE), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if maskable:
        draw.rectangle((0, 0, SIZE, SIZE), fill=BLUE)
        scale = 0.58
    else:
        draw.rounded_rectangle((0, 0, SIZE, SIZE), radius=int(SIZE * 0.22), fill=BLUE)
        scale = 0.72
    center = SIZE / 2
    # Note head, stem and flag.
    head_radius = int(SIZE * 0.085 * scale / 0.72)
    left_head = (int(center - SIZE * 0.17 * scale / 0.72), int(center + SIZE * 0.20 * scale / 0.72))
    right_head = (int(center + SIZE * 0.13 * scale / 0.72), int(center + SIZE * 0.14 * scale / 0.72))
    draw.ellipse(
        (
            left_head[0] - head_radius,
            left_head[1] - head_radius,
            left_head[0] + head_radius,
            left_head[1] + head_radius,
        ),
        fill=WHITE,
    )
    draw.ellipse(
        (
            right_head[0] - head_radius,
            right_head[1] - head_radius,
            right_head[0] + head_radius,
            right_head[1] + head_radius,
        ),
        fill=WHITE,
    )
    stroke = int(SIZE * 0.045 * scale / 0.72)
    draw.line(
        (left_head[0], left_head[1], left_head[0], int(center - SIZE * 0.22 * scale / 0.72)),
        fill=WHITE,
        width=stroke,
    )
    draw.line(
        (right_head[0], right_head[1], right_head[0], int(center - SIZE * 0.28 * scale / 0.72)),
        fill=WHITE,
        width=stroke,
    )
    draw.line(
        (
            left_head[0],
            int(center - SIZE * 0.22 * scale / 0.72),
            right_head[0],
            int(center - SIZE * 0.28 * scale / 0.72),
        ),
        fill=WHITE,
        width=stroke,
    )
    return image


def main() -> None:
    ICONS.mkdir(parents=True, exist_ok=True)
    normal = draw_icon(maskable=False)
    maskable = draw_icon(maskable=True)
    normal.resize((192, 192), Image.Resampling.LANCZOS).save(ICONS / 'icon-192.png')
    normal.resize((512, 512), Image.Resampling.LANCZOS).save(ICONS / 'icon-512.png')
    maskable.resize((512, 512), Image.Resampling.LANCZOS).save(ICONS / 'maskable-512.png')
    normal.resize((180, 180), Image.Resampling.LANCZOS).save(ICONS / 'apple-touch-icon-180.png')


if __name__ == '__main__':
    main()
