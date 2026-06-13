from __future__ import annotations

from pathlib import Path
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[1]
PNG_PATH = ROOT / "stoneage_studio" / "assets" / "app_icon.png"
ICNS_PATH = ROOT / "StoneAge Script Studio.app" / "Contents" / "Resources" / "stoneage_app.icns"


def _hex_color(value: str) -> tuple[int, int, int]:
    value = value.lstrip("#")
    return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))


def _blend(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return tuple(round(a[index] + (b[index] - a[index]) * t) for index in range(3))


def _gradient(size: int, stops: list[tuple[float, str]]) -> Image.Image:
    image = Image.new("RGBA", (size, size))
    pixels = image.load()
    colors = [(position, _hex_color(color)) for position, color in stops]
    for y in range(size):
        for x in range(size):
            t = (x * 0.54 + y * 0.76) / (size * 1.3)
            start_pos, start_color = colors[0]
            end_pos, end_color = colors[-1]
            for left, right in zip(colors, colors[1:]):
                if left[0] <= t <= right[0]:
                    start_pos, start_color = left
                    end_pos, end_color = right
                    break
            local = 0 if end_pos == start_pos else max(0.0, min(1.0, (t - start_pos) / (end_pos - start_pos)))
            pixels[x, y] = (*_blend(start_color, end_color, local), 255)
    return image


def _font(size: int, *candidates: str) -> ImageFont.FreeTypeFont:
    for candidate in candidates:
        path = Path(candidate)
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default(size=size)


def _centered_text(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], text: str, font: ImageFont.ImageFont, fill: str) -> None:
    left, top, right, bottom = box
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0]
    height = bbox[3] - bbox[1]
    x = left + (right - left - width) / 2 - bbox[0]
    y = top + (bottom - top - height) / 2 - bbox[1]
    draw.text((x, y), text, font=font, fill=fill)


def draw_icon(path: Path, size: int = 1024) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    scale = size / 1024

    bg_box = tuple(round(value * scale) for value in (76, 76, 948, 948))
    bg_radius = round(184 * scale)
    mask = Image.new("L", (size, size), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle(bg_box, radius=bg_radius, fill=255)
    canvas.alpha_composite(Image.composite(_gradient(size, [(0.0, "#F7D58A"), (0.42, "#CA7D3A"), (1.0, "#315E52")]), canvas, mask))

    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(bg_box, radius=bg_radius, outline=(255, 245, 217, 105), width=round(18 * scale))
    draw.ellipse(tuple(round(value * scale) for value in (144, 128, 420, 404)), fill="#FFE6A3")

    hill = [
        (round(74 * scale), round(728 * scale)),
        (round(242 * scale), round(628 * scale)),
        (round(420 * scale), round(638 * scale)),
        (round(578 * scale), round(696 * scale)),
        (round(742 * scale), round(754 * scale)),
        (round(842 * scale), round(704 * scale)),
        (round(950 * scale), round(636 * scale)),
        (round(950 * scale), round(948 * scale)),
        (round(74 * scale), round(948 * scale)),
    ]
    draw.polygon(hill, fill="#2F5C50")

    tablet_box = tuple(round(value * scale) for value in (260, 212, 764, 816))
    tablet_mask = Image.new("L", (size, size), 0)
    tablet_draw = ImageDraw.Draw(tablet_mask)
    tablet_draw.rounded_rectangle(tablet_box, radius=round(86 * scale), fill=255)
    tablet_gradient = _gradient(size, [(0.0, "#E1C69A"), (0.56, "#A9865C"), (1.0, "#6D513E")])
    canvas.alpha_composite(Image.composite(tablet_gradient, Image.new("RGBA", (size, size), (0, 0, 0, 0)), tablet_mask))
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle(tablet_box, radius=round(86 * scale), outline="#5C4635", width=round(22 * scale))

    crack_color = (86, 62, 44, 120)
    line_width = round(12 * scale)
    for line in ((340, 330, 586, 294), (382, 690, 688, 654), (310, 500, 464, 528), (604, 468, 724, 436)):
        draw.line(tuple(round(value * scale) for value in line), fill=crack_color, width=line_width)

    stone_font = _font(round(348 * scale), "/System/Library/Fonts/Hiragino Sans GB.ttc", "/System/Library/Fonts/STHeiti Medium.ttc")
    _centered_text(draw, tuple(round(value * scale) for value in (274, 284, 750, 656)), "石", stone_font, "#3F2E22")
    small_font = _font(round(104 * scale), "/System/Library/Fonts/Avenir Next.ttc", "/System/Library/Fonts/Helvetica.ttc")
    _centered_text(draw, tuple(round(value * scale) for value in (310, 644, 714, 764)), "SA", small_font, "#FFF1BF")

    canvas.save(path)


def build_icns(source_png: Path) -> None:
    ICNS_PATH.parent.mkdir(parents=True, exist_ok=True)
    source = Image.open(source_png).convert("RGBA")
    chunks = []
    for chunk_type, pixels in (
        (b"icp4", 16),
        (b"icp5", 32),
        (b"icp6", 64),
        (b"ic07", 128),
        (b"ic08", 256),
        (b"ic09", 512),
        (b"ic10", 1024),
    ):
        resized = source.resize((pixels, pixels), Image.Resampling.LANCZOS)
        buffer = BytesIO()
        resized.save(buffer, format="PNG")
        data = buffer.getvalue()
        chunks.append(chunk_type + (len(data) + 8).to_bytes(4, "big") + data)
    payload = b"".join(chunks)
    ICNS_PATH.write_bytes(b"icns" + (len(payload) + 8).to_bytes(4, "big") + payload)


def main() -> int:
    draw_icon(PNG_PATH)
    build_icns(PNG_PATH)
    print(PNG_PATH)
    print(ICNS_PATH)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
