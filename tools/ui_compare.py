"""把两张界面截图拼成上下对比图，并可框出重点改动处。

用法：
    python tools/ui_compare.py 旧图.png 新图.png 输出.png [--width 1000]
    python tools/ui_compare.py 旧.png 新.png 输出.png --mark 1165,800,180,50

``--mark`` 可重复，格式 ``x,y,w,h``，坐标是**新图的逻辑坐标**
（即新窗口的宽高坐标系，不是 PNG 像素坐标 —— 脚本会自己按缩放比换算）。
"""
from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = ["C:/Windows/Fonts/msyh.ttc", "C:/Windows/Fonts/simhei.ttf"]
LABEL_BG = (31, 54, 93)
LABEL_FG = (255, 255, 255)
MARK = (220, 38, 38)
GAP = 26
BAR = 34


def _font(size: int):
    for path in FONT_CANDIDATES:
        if Path(path).is_file():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
    return ImageFont.load_default()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("before")
    parser.add_argument("after")
    parser.add_argument("out")
    parser.add_argument("--width", type=int, default=1000, help="每张图在拼图里的宽度")
    parser.add_argument("--mark", action="append", default=[],
                        help="在「改版后」上框出的区域：x,y,w,h（可重复）")
    parser.add_argument("--before-label", default="改版前")
    parser.add_argument("--after-label", default="改版后")
    parser.add_argument("--logical-size", default="",
                        help="新图对应的窗口逻辑尺寸，如 1360x880；默认按原图像素推测")
    args = parser.parse_args()

    before = Image.open(args.before).convert("RGB")
    after = Image.open(args.after).convert("RGB")
    font = _font(19)

    def fit(img: Image.Image) -> Image.Image:
        ratio = args.width / img.width
        return img.resize((args.width, max(1, round(img.height * ratio))), Image.LANCZOS)

    panels = [(args.before_label, fit(before)), (args.after_label, fit(after))]
    height = sum(BAR + img.height for _, img in panels) + GAP
    canvas = Image.new("RGB", (args.width, height), (228, 232, 238))
    draw = ImageDraw.Draw(canvas)

    y = 0
    after_top = 0
    for label, img in panels:
        draw.rectangle([0, y, args.width, y + BAR], fill=LABEL_BG)
        draw.text((14, y + 6), label, font=font, fill=LABEL_FG)
        y += BAR
        canvas.paste(img, (0, y))
        y += img.height + GAP
        after_top = y - img.height          # 循环结束时正好是"改版后"那张的顶边

    if args.logical_size:
        lw, lh = (int(v) for v in args.logical_size.lower().split("x"))
    else:
        lw, lh = after.width, after.height      # 没给就按像素当逻辑尺寸（dpr=1）

    win_ratio = after.width / lw                # 原图像素 / 逻辑像素
    disp_ratio = args.width / after.width       # 拼图显示 / 原图像素
    k = win_ratio * disp_ratio                  # 逻辑坐标 → 拼图坐标

    for spec in args.mark:
        x, y0, w, h = (int(v) for v in spec.split(","))
        draw.rectangle(
            [round(x * k) - 3, round(y0 * k) + after_top - 3,
             round((x + w) * k) + 3, round((y0 + h) * k) + after_top + 3],
            outline=MARK, width=3,
        )

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out)
    print(f"已保存 {out}  {canvas.width}×{canvas.height}  标注 {len(args.mark)} 处"
          f"（逻辑→拼图 换算系数 {k:.3f}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
