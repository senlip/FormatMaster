"""把全部图标渲染成一张对照表，肉眼检查画得好不好看。"""
import os, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QColor, QPainter, QPixmap, QFont

app = QApplication(sys.argv)

from app.ui import icons                                          # noqa: E402

COLS = 6
SCALE = 4            # 放大 4 倍看细节，顺便模拟小尺寸下的可辨识度
CELL = 96
names = icons.names()
rows = (len(names) + COLS - 1) // COLS

sheet = QPixmap(COLS * CELL, rows * CELL + 30)
sheet.fill(QColor("#FFFFFF"))
p = QPainter(sheet)
p.setFont(QFont("Microsoft YaHei", 7))
p.setPen(QColor("#6B7280"))

for i, name in enumerate(names):
    cx, cy = (i % COLS) * CELL, (i // COLS) * CELL + 30
    pm = icons.render_pixmap(name, "#1F365D", 24, 2)
    p.drawPixmap(cx + 24, cy + 6, pm)
    p.drawText(cx + 4, cy + 66, name)
    # 再来一行小尺寸，检查 16px 下是否糊成一团
    pm2 = icons.render_pixmap(name, "#6B7280", 16, 2)
    p.drawPixmap(cx + 62, cy + 10, pm2)
    # 深色底上的白图标（主按钮用）
    p.fillRect(cx + 56, cy + 36, 32, 26, QColor("#1F365D"))
    pm3 = icons.render_pixmap(name, "#FFFFFF", 16, 2)
    p.drawPixmap(cx + 64, cy + 42, pm3)

p.end()
out = ROOT / "tests" / "_work" / "icon_sheet.png"
out.parent.mkdir(parents=True, exist_ok=True)
sheet.save(str(out), "PNG")
print("已保存", out, sheet.width(), "x", sheet.height(), "共", len(names), "个图标")
