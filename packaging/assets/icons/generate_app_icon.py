"""Generate the CF Youxuan application icon in reproducible raster sizes.

The artwork is intentionally geometric: a small icon should communicate a
network route and speed without relying on text or tiny decorative details.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parent
PNG_PATH = ROOT / "app_icon_v6.png"
ICO_PATH = ROOT / "app_icon_v6.ico"
CANVAS = 1024


def lerp(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))  # type: ignore[return-value]


def rounded_mask(size: int, radius: int, inset: int = 0) -> Image.Image:
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (inset, inset, size - inset - 1, size - inset - 1),
        radius=radius,
        fill=255,
    )
    return mask


def draw_icon(size: int = CANVAS) -> Image.Image:
    scale = size / CANVAS
    image = Image.new("RGB", (size, size), "#07101f")
    pixels = image.load()

    # Subtle diagonal navy gradient: visible on large icons, quiet at 16px.
    top = (17, 34, 61)
    bottom = (7, 16, 31)
    for y in range(size):
        for x in range(size):
            t = min(1.0, (0.58 * y + 0.42 * x) / size)
            pixels[x, y] = lerp(top, bottom, t)

    draw = ImageDraw.Draw(image)
    inset = round(22 * scale)
    radius = round(190 * scale)
    mask = rounded_mask(size, radius, inset)

    # Clip all artwork to the rounded-square tile.
    artwork = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    art = ImageDraw.Draw(artwork)
    art.rounded_rectangle(
        (inset, inset, size - inset - 1, size - inset - 1),
        radius=radius,
        outline="#294266",
        width=max(1, round(14 * scale)),
    )

    # A soft halo behind the route hub keeps the mark legible on the desktop.
    halo = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    halo_draw = ImageDraw.Draw(halo)
    cx, cy = round(512 * scale), round(500 * scale)
    halo_draw.ellipse(
        (cx - round(280 * scale), cy - round(280 * scale), cx + round(280 * scale), cy + round(280 * scale)),
        fill=(19, 220, 190, 28),
    )
    halo = halo.filter(ImageFilter.GaussianBlur(max(1, round(45 * scale))))
    artwork = Image.alpha_composite(artwork, halo)
    art = ImageDraw.Draw(artwork)

    # Three robust route segments. They remain recognizable at 16/24px.
    cyan = "#53e0c2"
    cyan_dark = "#198d9f"
    violet = "#8aa7ff"
    orange = "#ffb65f"
    line_w = max(1, round(36 * scale))
    hub = (round(512 * scale), round(500 * scale))
    nodes = [
        (round(278 * scale), round(300 * scale)),
        (round(746 * scale), round(288 * scale)),
        (round(280 * scale), round(720 * scale)),
    ]
    for index, node in enumerate(nodes):
        color = cyan if index != 1 else violet
        art.line((hub, node), fill=color, width=line_w, joint="curve")
        # Dark inner edge makes routes separate from the halo and remain crisp.
        art.line((hub, node), fill=cyan_dark if index != 1 else "#4c6bc4", width=max(1, round(8 * scale)))

    # Central speed mark: a compact lightning bolt, not a text glyph.
    bolt = [
        (round(536 * scale), round(310 * scale)),
        (round(414 * scale), round(520 * scale)),
        (round(496 * scale), round(520 * scale)),
        (round(456 * scale), round(696 * scale)),
        (round(610 * scale), round(452 * scale)),
        (round(526 * scale), round(452 * scale)),
    ]
    art.polygon(bolt, fill="#111d34", outline="#07101f")
    inner_bolt = [
        (round(530 * scale), round(330 * scale)),
        (round(438 * scale), round(500 * scale)),
        (round(514 * scale), round(500 * scale)),
        (round(477 * scale), round(665 * scale)),
        (round(586 * scale), round(470 * scale)),
        (round(508 * scale), round(470 * scale)),
    ]
    art.polygon(inner_bolt, fill=orange)

    # Large circular node caps make the network idea survive Windows scaling.
    node_r = round(43 * scale)
    for index, (x, y) in enumerate(nodes):
        color = cyan if index != 1 else violet
        art.ellipse((x - node_r, y - node_r, x + node_r, y + node_r), fill="#07101f")
        art.ellipse((x - round(29 * scale), y - round(29 * scale), x + round(29 * scale), y + round(29 * scale)), fill=color)
        art.ellipse((x - round(10 * scale), y - round(10 * scale), x + round(10 * scale), y + round(10 * scale)), fill="#d9fff5")

    # A restrained speed trail anchors the mark and differentiates it from a
    # generic network icon. Rounded strokes are drawn as pills.
    trail_y = round(820 * scale)
    for x1, x2, color, width in [
        (245, 610, cyan, 28),
        (385, 760, orange, 22),
        (505, 690, violet, 18),
    ]:
        art.rounded_rectangle(
            (round(x1 * scale), trail_y - round(width * scale) // 2,
             round(x2 * scale), trail_y + round(width * scale) // 2),
            radius=round(width * scale / 2),
            fill=color,
        )
        trail_y += round(42 * scale)

    # Keep the gradient tile underneath transparent artwork.  Converting the
    # transparent layer to RGB before compositing would turn its empty pixels
    # black (the defect caught in the first v6 preview).
    artwork.putalpha(Image.composite(artwork.getchannel("A"), Image.new("L", (size, size), 0), mask))
    return Image.alpha_composite(image.convert("RGBA"), artwork)


def main() -> None:
    icon = draw_icon()
    icon.save(PNG_PATH, format="PNG", optimize=True)
    # Pillow stores each resolution independently, avoiding the old 16x16-only
    # icon problem in Explorer and shortcut thumbnails.
    sizes = (16, 24, 32, 48, 64, 128, 256)
    icon.save(ICO_PATH, format="ICO", sizes=[(s, s) for s in sizes])
    print(f"generated {PNG_PATH}")
    print(f"generated {ICO_PATH} ({', '.join(f'{s}x{s}' for s in sizes)})")


if __name__ == "__main__":
    main()
