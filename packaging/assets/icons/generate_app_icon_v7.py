"""Generate the CF Youxuan v7 icon.

The mark is deliberately text-free: a three-node route, a signal arc and a
central lightning bolt remain legible on Windows desktop thumbnails.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter


HERE = Path(__file__).resolve().parent
PNG_PATH = HERE / "app_icon_v7.png"
ICO_PATH = HERE / "app_icon_v7.ico"
ART_SIZE = 1024
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], amount: float) -> tuple[int, int, int]:
    return tuple(round(x + (y - x) * amount) for x, y in zip(a, b))  # type: ignore[return-value]


def render(size: int = ART_SIZE) -> Image.Image:
    scale = size / ART_SIZE
    background = Image.new("RGB", (size, size), "#081426")
    pixels = background.load()
    for y in range(size):
        for x in range(size):
            amount = min(1.0, (x * 0.30 + y * 0.70) / size)
            pixels[x, y] = _mix((22, 43, 74), (7, 16, 31), amount)

    tile = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(tile)
    edge = max(1, round(13 * scale))
    inset = round(22 * scale)
    radius = round(190 * scale)
    tile_box = (inset, inset, size - inset - 1, size - inset - 1)
    draw.rounded_rectangle(tile_box, radius=radius, outline="#2a466d", width=edge)

    # A restrained glow gives the hub depth without turning the icon into a
    # bright neon blob at large sizes.
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    center = (round(512 * scale), round(505 * scale))
    glow_r = round(240 * scale)
    glow_draw.ellipse(
        (center[0] - glow_r, center[1] - glow_r, center[0] + glow_r, center[1] + glow_r),
        fill=(33, 209, 184, 38),
    )
    glow = glow.filter(ImageFilter.GaussianBlur(max(1, round(46 * scale))))
    tile = Image.alpha_composite(tile, glow)
    draw = ImageDraw.Draw(tile)

    cyan = "#51dec2"
    cyan_shadow = "#1d93a5"
    violet = "#91aaff"
    violet_shadow = "#506fcb"
    orange = "#ffb65f"
    ink = "#0a1528"
    route_width = max(1, round(32 * scale))
    route_inner = max(1, round(8 * scale))
    hub = (round(512 * scale), round(505 * scale))
    nodes = [
        (round(278 * scale), round(292 * scale), cyan, cyan_shadow),
        (round(750 * scale), round(292 * scale), violet, violet_shadow),
        (round(278 * scale), round(716 * scale), cyan, cyan_shadow),
    ]

    # Triangular paths communicate both a node pool and a selected route.
    for x, y, color, shadow in nodes:
        draw.line((hub, (x, y)), fill=color, width=route_width)
        draw.line((hub, (x, y)), fill=shadow, width=route_inner)

    # Only one signal arc is kept, so the symbol does not resemble a dashboard.
    arc_box = (round(188 * scale), round(170 * scale), round(836 * scale), round(818 * scale))
    draw.arc(arc_box, start=205, end=335, fill=cyan, width=max(1, round(22 * scale)))

    # Central lightning bolt: the orange fill is enclosed by a dark keyline.
    bolt = [
        (round(542 * scale), round(290 * scale)),
        (round(405 * scale), round(510 * scale)),
        (round(493 * scale), round(510 * scale)),
        (round(454 * scale), round(704 * scale)),
        (round(612 * scale), round(445 * scale)),
        (round(526 * scale), round(445 * scale)),
    ]
    draw.polygon(bolt, fill=ink)
    bolt_inner = [
        (round(536 * scale), round(320 * scale)),
        (round(435 * scale), round(493 * scale)),
        (round(516 * scale), round(493 * scale)),
        (round(485 * scale), round(654 * scale)),
        (round(582 * scale), round(462 * scale)),
        (round(506 * scale), round(462 * scale)),
    ]
    draw.polygon(bolt_inner, fill=orange)

    # High-contrast node caps remain visible at 16px and below.
    outer_radius = round(45 * scale)
    inner_radius = round(30 * scale)
    core_radius = round(10 * scale)
    for x, y, color, _shadow in nodes:
        draw.ellipse((x - outer_radius, y - outer_radius, x + outer_radius, y + outer_radius), fill=ink)
        draw.ellipse((x - inner_radius, y - inner_radius, x + inner_radius, y + inner_radius), fill=color)
        draw.ellipse((x - core_radius, y - core_radius, x + core_radius, y + core_radius), fill="#dcfff6")

    # Composite over the opaque gradient. This avoids the black alpha fringe
    # that the first preview exposed when transparent pixels were RGB-cast.
    return Image.alpha_composite(background.convert("RGBA"), tile).resize(
        (size, size), Image.Resampling.LANCZOS
    )


def main() -> None:
    render().save(PNG_PATH, format="PNG", optimize=True)
    render().save(ICO_PATH, format="ICO", sizes=[(s, s) for s in ICON_SIZES])
    print(f"generated {PNG_PATH}")
    print(f"generated {ICO_PATH} ({', '.join(f'{s}x{s}' for s in ICON_SIZES)})")


if __name__ == "__main__":
    main()
