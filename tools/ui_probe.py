"""离屏渲染辅助：让工具和自测都能"看到"真实界面。

offscreen 平台有两个坑，这里集中处理：

1. **不枚举系统字体**（``QFontDatabase.families()`` 返回空），中文全是豆腐块；
2. 全局样式表只挂在 ``main()`` 里，直接 new 出来的窗口是"没穿衣服"的
   —— 顶栏不是深蓝、选中行退回系统高亮色。

所以离屏截图/测量前，必须先注册字体 + 套上真实样式表，否则量出来的
像素和用户看到的根本不是一回事。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage
from PySide6.QtWidgets import QApplication

#: offscreen 平台下需要手动注册的字体（界面主字体 + 兜底 + 等宽）
FONT_FILES = (
    "C:/Windows/Fonts/msyh.ttc",     # 微软雅黑
    "C:/Windows/Fonts/simhei.ttf",   # 黑体：雅黑缺失时兜底
    "C:/Windows/Fonts/simsun.ttc",
    "C:/Windows/Fonts/segoeui.ttf",
    "C:/Windows/Fonts/consola.ttf",  # 日志区等宽
)


def register_fonts() -> None:
    """注册系统字体并设置界面主字体。需在 QApplication 之后调用。"""
    for path in FONT_FILES:
        if Path(path).is_file():
            QFontDatabase.addApplicationFont(path)
    QApplication.setFont(QFont("Microsoft YaHei UI", 9))


def apply_app_style(app: QApplication) -> None:
    """套用与 main.py 完全一致的样式表（含运行时生成的指示图形）。"""
    from app.ui import theme

    app.setStyleSheet(theme.stylesheet())
    font = app.font()
    font.setPointSize(9)
    app.setFont(font)


def pixel_at(image: QImage, win, widget, dx: int = 10, dy: int | None = None) -> QColor:
    """从**整窗口**截图里取某个控件内部的像素颜色。

    为什么不直接 ``widget.grab()``：单独 grab 一个子控件时，结果会随
    Qt 是否已完成样式 polish 而变（同一份代码两次跑出不同颜色），
    而整窗口截图 = 用户真正看到的东西，是唯一可信的来源。
    """
    dpr = image.width() / win.width()
    top_left = widget.mapTo(win, widget.rect().topLeft())
    x = int((top_left.x() + dx) * dpr)
    y = int((top_left.y() + (widget.height() // 2 if dy is None else dy)) * dpr)
    return QColor(image.pixel(x, y))


def snapshot(win) -> QImage:
    """整窗口截图（前提：widget 已 show() 且事件队列已抽干）。"""
    return win.grab().toImage()


def row_has_color(image: QImage, win, view, item_rect, color, step: int = 3) -> bool:
    """列表里某一行的横向上是否出现过指定颜色。

    比"取某一点"稳：条目是圆角矩形、里面还有文字与徽标，硬猜一个坐标
    很容易落在文字或空白上，测出来的失败是假的。
    """
    dpr = image.width() / win.width()
    top_left = view.viewport().mapTo(win, item_rect.topLeft())
    y = int((top_left.y() + item_rect.height() / 2) * dpr)
    target = QColor(color).name()
    for dx in range(2, max(3, item_rect.width() - 2), step):
        if QColor(image.pixel(int((top_left.x() + dx) * dpr), y)).name() == target:
            return True
    return False


def settle(app: QApplication, seconds: float = 0.35) -> None:
    """抽干事件队列，让布局与样式落定。"""
    import time

    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)


__all__ = [
    "Qt", "apply_app_style", "pixel_at", "register_fonts", "row_has_color",
    "settle", "snapshot",
]
