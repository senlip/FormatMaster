"""截图的局部裁剪 + 主色统计，用来客观确认"看起来有问题"的地方。

用法：
    python tools/ui_crop.py 源图.png [x y w h 输出.png]

不给坐标就整图另存；给了坐标则裁剪该区域并打印该区域出现最多的几种颜色。
配合 tools/shot_ui.py 使用：先出整窗截图，再裁可疑区域放大看，
最后按色值下结论 —— 别凭缩略图猜。
"""
import sys
from pathlib import Path
from PySide6.QtGui import QImage, QColor

src = Path(sys.argv[1])
img = QImage(str(src))
if len(sys.argv) >= 7:
    x, y, w, h = (int(v) for v in sys.argv[2:6])
    out = Path(sys.argv[6])
else:
    x, y, w, h = 0, 0, img.width(), img.height()
    out = src.with_name(src.stem + "_crop.png")

print(f"{src.name} {img.width()}x{img.height()} 裁 {x},{y} {w}x{h}")
img.copy(x, y, w, h).save(str(out), "PNG")

# 顺带统计这一块的主色，避免"看着像"的误判
sub = img.copy(x, y, w, h)
buckets: dict[str, int] = {}
for py in range(0, sub.height(), 2):
    for px in range(0, sub.width(), 2):
        name = QColor(sub.pixel(px, py)).name()
        buckets[name] = buckets.get(name, 0) + 1
for name, n in sorted(buckets.items(), key=lambda kv: -kv[1])[:8]:
    print(f"   {name}  ×{n}")
print("已保存", out)
