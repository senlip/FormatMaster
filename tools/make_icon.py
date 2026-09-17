"""生成应用图标（深蓝底 + 双向转换箭头）。

用法：python tools/make_icon.py
输出：app/assets/app.ico 与 app/assets/app.png
"""
from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ASSETS = PROJECT_ROOT / "app" / "assets"

PRIMARY = (31, 54, 93)
ACCENT = (127, 168, 216)
WHITE = (255, 255, 255)

SS = 4                      # 超采样倍率，缩小后边缘更干净
SIZE = 256 * SS


def rounded_rect(size: int, radius: int, color) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=color)
    return img


def arrow_right(draw: ImageDraw.ImageDraw, x_start: int, x_end: int, y: int,
                thickness: int, head_w: int, head_h: int, color) -> None:
    bar_end = x_end - head_w
    draw.rounded_rectangle(
        [x_start, y - thickness // 2, bar_end + thickness // 2, y + thickness // 2],
        radius=thickness // 2, fill=color,
    )
    draw.polygon(
        [(bar_end, y - head_h // 2), (x_end, y), (bar_end, y + head_h // 2)],
        fill=color,
    )


def build_icon() -> Image.Image:
    canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    base = rounded_rect(SIZE, int(SIZE * 0.22), PRIMARY)
    canvas.alpha_composite(base)
    draw = ImageDraw.Draw(canvas)

    u = SIZE / 256.0
    thickness = int(26 * u)
    head_w = int(30 * u)
    head_h = int(44 * u)
    left = int(52 * u)
    right = int(204 * u)

    # 上面：从左到右
    arrow_right(draw, left, right, int(104 * u), thickness, head_w, head_h, WHITE)

    # 下面：从右到左（镜像绘制）
    mirror = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
    mdraw = ImageDraw.Draw(mirror)
    arrow_right(mdraw, left, right, int(152 * u), thickness, head_w, head_h, ACCENT)
    mirror = mirror.transpose(Image.FLIP_LEFT_RIGHT)
    canvas.alpha_composite(mirror)

    return canvas.resize((256, 256), Image.LANCZOS)


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    icon = build_icon()
    png_path = ASSETS / "app.png"
    ico_path = ASSETS / "app.ico"
    icon.save(png_path)
    icon.save(
        ico_path,
        sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
    )
    print(f"已生成：{png_path}")
    print(f"已生成：{ico_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
