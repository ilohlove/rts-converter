"""Generate the deterministic RTS Converter application icon."""

from __future__ import annotations

import argparse
import io
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, __version__ as pillow_version


EXPECTED_PILLOW_VERSION = "12.3.0"
CANVAS_SIZE = 1024
ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)

NAVY = (15, 39, 60, 255)
WHITE_GRID = (255, 255, 255, 68)
TURQUOISE = (43, 211, 197, 255)
CORAL = (255, 105, 96, 255)
TRANSPARENT = (0, 0, 0, 0)


def render_icon() -> Image.Image:
    """Render the master icon without text, gradients, or shadows."""
    image = Image.new("RGBA", (CANVAS_SIZE, CANVAS_SIZE), TRANSPARENT)
    background_mask = Image.new("L", image.size, 0)
    mask_draw = ImageDraw.Draw(background_mask)
    bounds = (24, 24, CANVAS_SIZE - 24, CANVAS_SIZE - 24)
    mask_draw.rounded_rectangle(bounds, radius=208, fill=255)

    background = Image.new("RGBA", image.size, NAVY)
    image.alpha_composite(Image.composite(background, image, background_mask))

    grid = Image.new("RGBA", image.size, TRANSPARENT)
    grid_draw = ImageDraw.Draw(grid)
    grid_width = 14
    for coordinate in (190, 352, 512, 672, 834):
        grid_draw.line((coordinate, 24, coordinate, 1000), fill=WHITE_GRID, width=grid_width)
        grid_draw.line((24, coordinate, 1000, coordinate), fill=WHITE_GRID, width=grid_width)

    # Subtle curved meridians make the grid read as a nautical chart at small sizes.
    grid_draw.arc((-250, 90, 1274, 934), 204, 336, fill=WHITE_GRID, width=grid_width)
    grid_draw.arc((-250, 90, 1274, 934), 24, 156, fill=WHITE_GRID, width=grid_width)
    clipped_alpha = ImageChops.multiply(grid.getchannel("A"), background_mask)
    grid.putalpha(clipped_alpha)
    image.alpha_composite(grid)

    draw = ImageDraw.Draw(image)
    route = ((204, 742), (405, 575), (620, 640), (817, 319))
    draw.line(route, fill=NAVY, width=76, joint="curve")
    draw.line(route, fill=TURQUOISE, width=48, joint="curve")

    waypoint_radius = 48
    for x, y in route[:3]:
        draw.ellipse(
            (
                x - waypoint_radius - 12,
                y - waypoint_radius - 12,
                x + waypoint_radius + 12,
                y + waypoint_radius + 12,
            ),
            fill=NAVY,
        )
        draw.ellipse(
            (
                x - waypoint_radius,
                y - waypoint_radius,
                x + waypoint_radius,
                y + waypoint_radius,
            ),
            fill=TURQUOISE,
        )

    destination_x, destination_y = route[-1]
    destination_radius = 62
    draw.ellipse(
        (
            destination_x - destination_radius - 12,
            destination_y - destination_radius - 12,
            destination_x + destination_radius + 12,
            destination_y + destination_radius + 12,
        ),
        fill=NAVY,
    )
    draw.ellipse(
        (
            destination_x - destination_radius,
            destination_y - destination_radius,
            destination_x + destination_radius,
            destination_y + destination_radius,
        ),
        fill=CORAL,
    )
    return image


def encode_assets(image: Image.Image) -> tuple[bytes, bytes]:
    png_buffer = io.BytesIO()
    image.save(png_buffer, format="PNG", optimize=False)

    ico_buffer = io.BytesIO()
    image.save(
        ico_buffer,
        format="ICO",
        bitmap_format="png",
        sizes=[(size, size) for size in ICON_SIZES],
    )
    return png_buffer.getvalue(), ico_buffer.getvalue()


def _write_if_changed(path: Path, content: bytes) -> bool:
    if path.is_file() and path.read_bytes() == content:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent / "assets",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify that the checked-in PNG and ICO match the generator.",
    )
    args = parser.parse_args()

    if pillow_version != EXPECTED_PILLOW_VERSION:
        parser.error(
            f"Pillow {EXPECTED_PILLOW_VERSION} is required; found {pillow_version}. "
            "Install requirements-build.txt."
        )

    png_content, ico_content = encode_assets(render_icon())
    assets = {
        args.output_dir / "rts_converter.png": png_content,
        args.output_dir / "rts_converter.ico": ico_content,
    }

    if args.check:
        stale = [
            path
            for path, content in assets.items()
            if not path.is_file() or path.read_bytes() != content
        ]
        if stale:
            parser.error("stale or missing icon assets: " + ", ".join(map(str, stale)))
        print("Icon assets match the deterministic generator.")
        return 0

    changed = [path for path, content in assets.items() if _write_if_changed(path, content)]
    if changed:
        print("Generated " + ", ".join(map(str, changed)))
    else:
        print("Icon assets are already up to date.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
