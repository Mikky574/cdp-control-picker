"""Rebuild the original vector icon and Windows icon sizes using Pillow.

Pillow is needed only for rebuilding these assets; the application uses the
generated PNG/ICO and does not need Pillow at runtime.
"""
from __future__ import annotations

from pathlib import Path
import struct

from PIL import Image, ImageDraw


ASSETS = Path(__file__).resolve().parents[1] / "assets"
SIZES = (16, 20, 24, 32, 48, 64, 128, 256)
NAVY = "#132744"
HEADER = "#1E3B60"
TEAL = "#27C4B4"
ORANGE = "#FFAD4A"
WHITE = "#FFFFFF"

SVG = '''<svg xmlns="http://www.w3.org/2000/svg" width="256" height="256" viewBox="0 0 256 256" role="img" aria-labelledby="title description">
  <title id="title">CDP 控件采集器</title>
  <desc id="description">深蓝色圆角浏览器窗口，白色鼠标指针，青色定位框和橙色定位点。</desc>
  <rect x="12" y="24" width="232" height="208" rx="38" fill="#132744"/>
  <path d="M50 24H206Q244 24 244 62V83H12V62Q12 24 50 24Z" fill="#1E3B60"/>
  <g fill="#8AA9CB">
    <circle cx="42" cy="53" r="5"/>
    <circle cx="61" cy="53" r="5"/>
    <circle cx="80" cy="53" r="5"/>
  </g>
  <path d="M87 122V101H108 M138 101H159V122 M87 144V165H108 M138 165H159V144" fill="none" stroke="#27C4B4" stroke-width="11" stroke-linecap="round" stroke-linejoin="round"/>
  <circle cx="159" cy="101" r="8" fill="#FFAD4A"/>
  <path d="M117 123V212L140 190L156 221L176 211L159 178L194 176Z" fill="#FFFFFF" stroke="#132744" stroke-width="4" stroke-linejoin="round"/>
</svg>
'''


def draw_icon(size):
    scale = 4
    canvas = Image.new("RGBA", (256 * scale, 256 * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)

    def points(values):
        return [(x * scale, y * scale) for x, y in values]

    draw.rounded_rectangle((12 * scale, 24 * scale, 244 * scale, 232 * scale), radius=38 * scale, fill=NAVY)
    # Quadratic SVG corners and circular Pillow corners meet at the same bounds.
    header_mask = Image.new("L", canvas.size, 0)
    mask_draw = ImageDraw.Draw(header_mask)
    mask_draw.rounded_rectangle((12 * scale, 24 * scale, 244 * scale, 100 * scale), radius=38 * scale, fill=255)
    mask_draw.rectangle((12 * scale, 62 * scale, 244 * scale, 83 * scale), fill=255)
    mask_draw.rectangle((0, 84 * scale, canvas.width, canvas.height), fill=0)
    canvas.paste(Image.new("RGBA", canvas.size, HEADER), (0, 0), header_mask)
    draw = ImageDraw.Draw(canvas)
    for center_x in (42, 61, 80):
        draw.ellipse(((center_x - 5) * scale, 48 * scale, (center_x + 5) * scale, 58 * scale), fill="#8AA9CB")
    for corners in [((87, 122), (87, 101), (108, 101)), ((138, 101), (159, 101), (159, 122)), ((87, 144), (87, 165), (108, 165)), ((138, 165), (159, 165), (159, 144))]:
        draw.line(points(corners), fill=TEAL, width=11 * scale, joint="curve")
        for x, y in corners:
            radius = 5.5
            draw.ellipse(((x - radius) * scale, (y - radius) * scale, (x + radius) * scale, (y + radius) * scale), fill=TEAL)
    draw.ellipse((151 * scale, 93 * scale, 167 * scale, 109 * scale), fill=ORANGE)
    pointer = points(((117, 123), (117, 212), (140, 190), (156, 221), (176, 211), (159, 178), (194, 176)))
    draw.polygon(pointer, fill=WHITE)
    draw.line(pointer + pointer[:1], fill=NAVY, width=4 * scale, joint="curve")
    return canvas.resize((size, size), Image.Resampling.LANCZOS)


def main():
    ASSETS.mkdir(parents=True, exist_ok=True)
    (ASSETS / "app-icon.svg").write_text(SVG, encoding="utf-8", newline="\n")
    frames = {size: draw_icon(size) for size in SIZES}
    frames[256].save(ASSETS / "app-icon.png")
    frames[256].save(ASSETS / "app-icon.ico", format="ICO", sizes=[(size, size) for size in SIZES], append_images=[frames[size] for size in SIZES if size != 256])
    with Image.open(ASSETS / "app-icon.png") as png:
        assert png.size == (256, 256) and png.mode == "RGBA"
    with Image.open(ASSETS / "app-icon.ico") as ico:
        actual = sorted(width for width, height in ico.ico.sizes() if width == height)
        assert actual == list(SIZES), (actual, SIZES)
        for size in SIZES:
            assert ico.ico.getimage((size, size)).size == (size, size)
    header = (ASSETS / "app-icon.ico").read_bytes()[:6]
    assert struct.unpack("<HHH", header) == (0, 1, len(SIZES))
    print("Generated app-icon.svg; PNG 256x256 RGBA; ICO sizes: " + ", ".join(map(str, SIZES)))


if __name__ == "__main__":
    main()
