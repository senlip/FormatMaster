"""矢量图标 —— 用 QPainter 现画，不依赖任何图片资源。

为什么不放 .svg / .png：每多一类资源文件，打包时就多一处"路径找不到"的
风险（本项目已经在 FFmpeg 资源路径上踩过一次）。这里总共十来个图标、
几何都很简单，用代码画比维护资源清单更省事，而且天然支持任意颜色与缩放。

约定：每个 ``_draw_*`` 函数只在 **16×16 的逻辑坐标系**里画形状，
缩放与高 DPI 交给 :func:`make_icon` 统一处理。
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF

#: 逻辑画布边长。所有图标的坐标都落在这个范围内。
CANVAS = 16.0


def _stroke(color: QColor, width: float = 1.4) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def _fill(color: QColor) -> QPen:
    return QPen(Qt.NoPen)


# --------------------------------------------------------------------------- #
# 各个图标的画法
# --------------------------------------------------------------------------- #
def _draw_add_file(p: QPainter, color: QColor) -> None:
    """一页纸 + 右下角加号。"""
    path = QPainterPath()
    path.moveTo(2.6, 2.2)
    path.lineTo(7.0, 2.2)
    path.lineTo(9.4, 4.8)
    path.lineTo(9.4, 13.8)
    path.lineTo(2.6, 13.8)
    path.closeSubpath()
    p.setPen(_stroke(color))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    # 折角
    p.drawPolyline(QPolygonF([QPointF(7.0, 2.2), QPointF(7.0, 4.8), QPointF(9.4, 4.8)]))

    cx, cy, arm = 12.3, 11.5, 2.6
    p.setPen(_stroke(color, 1.6))
    p.drawLine(QPointF(cx - arm, cy), QPointF(cx + arm, cy))
    p.drawLine(QPointF(cx, cy - arm), QPointF(cx, cy + arm))


def _draw_add_folder(p: QPainter, color: QColor) -> None:
    """文件夹 + 右下角加号。"""
    path = QPainterPath()
    path.moveTo(1.8, 4.6)
    path.lineTo(5.6, 4.6)
    path.lineTo(6.6, 6.2)
    path.lineTo(10.0, 6.2)
    path.lineTo(10.0, 13.4)
    path.lineTo(1.8, 13.4)
    path.closeSubpath()
    p.setPen(_stroke(color))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)

    cx, cy, arm = 12.4, 11.3, 2.6
    p.setPen(_stroke(color, 1.6))
    p.drawLine(QPointF(cx - arm, cy), QPointF(cx + arm, cy))
    p.drawLine(QPointF(cx, cy - arm), QPointF(cx, cy + arm))


def _draw_folder(p: QPainter, color: QColor) -> None:
    path = QPainterPath()
    path.moveTo(2.0, 4.2)
    path.lineTo(6.2, 4.2)
    path.lineTo(7.3, 5.9)
    path.lineTo(14.0, 5.9)
    path.lineTo(14.0, 13.2)
    path.lineTo(2.0, 13.2)
    path.closeSubpath()
    p.setPen(_stroke(color))
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)


def _draw_remove(p: QPainter, color: QColor) -> None:
    """圆圈减号 —— 比"叉"更明确地表达"从列表移除"而不是"删除文件"。"""
    p.setPen(_stroke(color))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QPointF(8, 8), 5.6, 5.6)
    p.setPen(_stroke(color, 1.6))
    p.drawLine(QPointF(5.4, 8), QPointF(10.6, 8))


def _draw_clear(p: QPainter, color: QColor) -> None:
    """垃圾桶。"""
    p.setPen(_stroke(color))
    p.setBrush(Qt.NoBrush)
    p.drawLine(QPointF(3.0, 4.6), QPointF(13.0, 4.6))
    p.drawPolyline(QPolygonF([
        QPointF(6.3, 4.6), QPointF(6.3, 2.8), QPointF(9.7, 2.8), QPointF(9.7, 4.6),
    ]))
    p.drawPolyline(QPolygonF([
        QPointF(4.4, 4.6), QPointF(5.0, 13.4), QPointF(11.0, 13.4), QPointF(11.6, 4.6),
    ]))
    p.setPen(_stroke(color, 1.2))
    p.drawLine(QPointF(6.9, 7.0), QPointF(7.1, 11.2))
    p.drawLine(QPointF(9.1, 7.0), QPointF(8.9, 11.2))


def _draw_gauge(p: QPainter, color: QColor) -> None:
    """半圆仪表 + 指针，用来表示"引擎健康检测"。"""
    p.setPen(_stroke(color))
    p.setBrush(Qt.NoBrush)
    p.drawArc(QRectF(2.4, 4.2, 11.2, 11.2), 0, 180 * 16)
    p.setPen(_stroke(color, 1.6))
    p.drawLine(QPointF(8.0, 9.8), QPointF(10.8, 6.4))
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawEllipse(QPointF(8.0, 9.8), 1.1, 1.1)


def _draw_play(p: QPainter, color: QColor) -> None:
    path = QPainterPath()
    path.moveTo(5.4, 3.2)
    path.lineTo(12.8, 8.0)
    path.lineTo(5.4, 12.8)
    path.closeSubpath()
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawPath(path)


def _draw_stop(p: QPainter, color: QColor) -> None:
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawRoundedRect(QRectF(4.6, 4.6, 6.8, 6.8), 1.6, 1.6)


def _draw_chevron_down(p: QPainter, color: QColor) -> None:
    p.setPen(_stroke(color, 1.7))
    p.setBrush(Qt.NoBrush)
    p.drawPolyline(QPolygonF([QPointF(4.6, 6.2), QPointF(8.0, 9.8), QPointF(11.4, 6.2)]))


def _draw_chevron_right(p: QPainter, color: QColor) -> None:
    p.setPen(_stroke(color, 1.7))
    p.setBrush(Qt.NoBrush)
    p.drawPolyline(QPolygonF([QPointF(6.2, 4.6), QPointF(9.8, 8.0), QPointF(6.2, 11.4)]))


def _draw_drop(p: QPainter, color: QColor) -> None:
    """空状态用的大图：虚线框 + 下箭头（"把文件丢进来"）。"""
    pen = _stroke(color, 1.3)
    pen.setStyle(Qt.DashLine)
    pen.setDashPattern([3.0, 2.4])
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(QRectF(1.8, 3.4, 12.4, 10.0), 2.2, 2.2)

    p.setPen(_stroke(color, 1.5))
    p.drawLine(QPointF(8.0, 5.4), QPointF(8.0, 10.4))
    p.drawPolyline(QPolygonF([QPointF(5.9, 8.3), QPointF(8.0, 10.5), QPointF(10.1, 8.3)]))


def _draw_check(p: QPainter, color: QColor) -> None:
    p.setPen(_stroke(color, 2.1))
    p.setBrush(Qt.NoBrush)
    p.drawPolyline(QPolygonF([QPointF(4.0, 8.3), QPointF(6.9, 11.2), QPointF(12.0, 5.2)]))


def _draw_dot(p: QPainter, color: QColor) -> None:
    p.setPen(Qt.NoPen)
    p.setBrush(color)
    p.drawEllipse(QPointF(8.0, 8.0), 2.7, 2.7)


def _draw_convert(p: QPainter, color: QColor) -> None:
    """上下两条反向箭头 —— 顶栏品牌标记，"互转"的意思。"""
    p.setPen(_stroke(color, 1.5))
    p.setBrush(Qt.NoBrush)
    p.drawLine(QPointF(3.0, 5.4), QPointF(12.4, 5.4))
    p.drawPolyline(QPolygonF([QPointF(10.2, 3.2), QPointF(12.6, 5.4), QPointF(10.2, 7.6)]))
    p.drawLine(QPointF(13.0, 10.6), QPointF(3.6, 10.6))
    p.drawPolyline(QPolygonF([QPointF(5.8, 8.4), QPointF(3.4, 10.6), QPointF(5.8, 12.8)]))


_DRAWERS = {
    "add_file": _draw_add_file,
    "add_folder": _draw_add_folder,
    "folder": _draw_folder,
    "remove": _draw_remove,
    "clear": _draw_clear,
    "gauge": _draw_gauge,
    "play": _draw_play,
    "stop": _draw_stop,
    "chevron_down": _draw_chevron_down,
    "chevron_right": _draw_chevron_right,
    "drop": _draw_drop,
    "convert": _draw_convert,
    "check": _draw_check,
    "dot": _draw_dot,
}


# --------------------------------------------------------------------------- #
def render_pixmap(name: str, color, size: int = 16, dpr: int = 2) -> QPixmap:
    """把图标画成 QPixmap（按 ``dpr`` 超采样，保证高分屏不糊）。"""
    drawer = _DRAWERS.get(name)
    pixmap = QPixmap(size * dpr, size * dpr)
    pixmap.fill(Qt.transparent)
    if drawer is None:
        return pixmap

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing, True)
    scale = (size * dpr) / CANVAS
    painter.scale(scale, scale)
    drawer(painter, QColor(color))
    painter.end()
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def make_icon(name: str, color, size: int = 16, disabled_color=None) -> QIcon:
    """生成 QIcon。同时塞 1x / 2x，普通屏与高分屏都锐利。

    给了 ``disabled_color`` 时额外提供禁用态图元，免得按钮变灰后
    图标还是鲜亮的高饱和色（Qt 默认的置灰效果对彩色图标很糟）。
    """
    icon = QIcon()
    for dpr in (1, 2):
        icon.addPixmap(render_pixmap(name, color, size, dpr), QIcon.Normal)
    if disabled_color is not None:
        for dpr in (1, 2):
            icon.addPixmap(render_pixmap(name, disabled_color, size, dpr), QIcon.Disabled)
    return icon


def names() -> list[str]:
    return sorted(_DRAWERS)
