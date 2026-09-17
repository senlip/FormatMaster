"""可复用的界面组件。

进度条和状态标签用「自绘委托」而不是塞 QWidget 进单元格：
表格滚动时不会有几十个控件一起重排，滚动流畅得多。
"""
from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QLabel, QPushButton, QStyle, QStyledItemDelegate,
    QVBoxLayout, QWidget,
)

from . import icons, theme


class ProgressDelegate(QStyledItemDelegate):
    """在单元格里绘制细进度条。

    值从 ``Qt.UserRole`` 取（float 0~1），状态色从 ``Qt.UserRole + 1`` 取。
    """

    def paint(self, painter: QPainter, option, index) -> None:
        ratio = index.data(Qt.UserRole)
        if ratio is None:
            super().paint(painter, option, index)
            return

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = QRectF(option.rect).adjusted(8, 11, -8, -11)
        radius = rect.height() / 2

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#E4E8EF"))
        painter.drawRoundedRect(rect, radius, radius)

        ratio = max(0.0, min(1.0, float(ratio)))
        color = QColor(index.data(Qt.UserRole + 1) or theme.PRIMARY)
        if ratio > 0:
            fill = QRectF(rect.left(), rect.top(), max(rect.height(), rect.width() * ratio), rect.height())
            painter.setBrush(color)
            painter.drawRoundedRect(fill, radius, radius)

        label = index.data(Qt.DisplayRole) or ""
        if label:
            text_color = QColor("#FFFFFF") if ratio > 0.45 else QColor(theme.TEXT_SUB)
            painter.setPen(text_color)
            font = QFont(option.font)
            font.setPointSizeF(7.6)
            painter.setFont(font)
            painter.drawText(option.rect, Qt.AlignCenter, str(label))

        painter.restore()


class StatusDelegate(QStyledItemDelegate):
    """状态列：彩色圆点 + 文字。"""

    def paint(self, painter: QPainter, option, index) -> None:
        text = index.data(Qt.DisplayRole) or ""
        color = QColor(index.data(Qt.UserRole) or theme.TEXT_MUTED)
        if not text:
            super().paint(painter, option, index)
            return

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        font = QFont(option.font)
        font.setPointSizeF(7.8)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        text_width = metrics.horizontalAdvance(str(text))
        dot = 7
        gap = 7
        total = dot + gap + text_width
        start_x = option.rect.left() + max(8, (option.rect.width() - total) / 2)
        center_y = option.rect.center().y() + 1

        painter.setPen(Qt.NoPen)
        painter.setBrush(color)
        painter.drawEllipse(QRectF(start_x, center_y - dot / 2, dot, dot))

        painter.setPen(QPen(color))
        text_rect = QRectF(start_x + dot + gap, option.rect.top(),
                           text_width + 4, option.rect.height())
        painter.drawText(text_rect, Qt.AlignVCenter | Qt.AlignLeft, str(text))
        painter.restore()


class KindBadgeDelegate(QStyledItemDelegate):
    """类型列：带底色的格式徽标，一眼看出是视频还是文档。"""

    def paint(self, painter: QPainter, option, index) -> None:
        text = index.data(Qt.DisplayRole) or ""
        color = QColor(index.data(Qt.UserRole) or theme.TEXT_SUB)
        if not text:
            super().paint(painter, option, index)
            return

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        font = QFont(option.font)
        font.setPointSizeF(7.4)
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        width = metrics.horizontalAdvance(str(text)) + 14
        height = 18
        rect = QRectF(
            option.rect.left() + 10,
            option.rect.center().y() - height / 2 + 1,
            min(width, option.rect.width() - 16),
            height,
        )

        soft = QColor(color)
        soft.setAlpha(28)
        painter.setPen(Qt.NoPen)
        painter.setBrush(soft)
        painter.drawRoundedRect(rect, 4, 4)

        painter.setPen(QPen(color))
        painter.drawText(rect, Qt.AlignCenter, str(text))
        painter.restore()


class NavDelegate(QStyledItemDelegate):
    """侧栏条目：左边名称、右边数量徽标。

    自己画而不用 QSS 的 ``::item``，是因为"名称左对齐 + 数字右对齐"这种
    两端布局用样式表根本表达不出来。
    """

    ROW_HEIGHT = 38

    def sizeHint(self, option, index) -> QSize:      # noqa: N802 (Qt 命名)
        return QSize(super().sizeHint(option, index).width(), self.ROW_HEIGHT)

    def paint(self, painter: QPainter, option, index) -> None:
        text = str(index.data(Qt.DisplayRole) or "")
        count = index.data(Qt.UserRole + 1)
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = QRectF(option.rect).adjusted(6, 3, -6, -3)
        if selected or hovered:
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(theme.PRIMARY_SOFT if selected else theme.BG_HOVER))
            painter.drawRoundedRect(rect, 6, 6)

        font = QFont(option.font)
        font.setPointSizeF(9.2)
        font.setBold(selected)
        painter.setFont(font)
        painter.setPen(QColor(theme.PRIMARY if selected else theme.TEXT_SUB))
        painter.drawText(QRectF(rect.left() + 10, rect.top(), rect.width() - 20, rect.height()),
                         Qt.AlignVCenter | Qt.AlignLeft, text)

        if count:
            badge_font = QFont(option.font)
            badge_font.setPointSizeF(7.8)
            badge_font.setBold(True)
            painter.setFont(badge_font)
            label = str(count)
            width = max(20.0, painter.fontMetrics().horizontalAdvance(label) + 13)
            badge = QRectF(rect.right() - width - 4, rect.center().y() - 9, width, 18)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(theme.PRIMARY if selected else "#E7EAF1"))
            painter.drawRoundedRect(badge, 9, 9)
            painter.setPen(QColor("#FFFFFF" if selected else theme.TEXT_SUB))
            painter.drawText(badge, Qt.AlignCenter, label)

        painter.restore()


class Card(QFrame):
    """带边框的白色卡片容器，可选折叠。

    折叠只隐藏**内容区**，卡片本体（标题）始终在 —— 这样外部
    ``card.setVisible(False)`` 那套"按文件类型显示参数"的逻辑不受影响。
    """

    collapsed_changed = Signal(str, bool)

    def __init__(self, title: str = "", parent: QWidget | None = None, *,
                 collapsible: bool = False, collapsed: bool = False,
                 key: str = "") -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self._key = key or title
        self._collapsible = collapsible

        self._body_widget = QWidget(self)
        self._body_layout = QVBoxLayout(self._body_widget)
        self._body_layout.setContentsMargins(0, 0, 0, 0)
        self._body_layout.setSpacing(10)

        self._outer = QVBoxLayout(self)
        self._outer.setContentsMargins(14, 11, 14, 13)
        self._outer.setSpacing(9)

        self._header: QWidget | None = None
        if title:
            self._header = self._make_header(title)
            self._outer.addWidget(self._header)
        self._outer.addWidget(self._body_widget)

        self._collapsed = bool(collapsed) if collapsible else False
        self._apply_collapsed()

    # ------------------------------------------------------------------ #
    def _make_header(self, title: str) -> QWidget:
        if not self._collapsible:
            label = QLabel(title)
            label.setObjectName("SectionTitle")
            return label

        button = QPushButton(title)
        button.setObjectName("CardHeader")
        button.setCursor(Qt.PointingHandCursor)
        button.setFocusPolicy(Qt.NoFocus)
        button.setIconSize(QSize(13, 13))
        button.setToolTip("点击展开 / 收起这一组参数")
        button.clicked.connect(self._toggle)
        return button

    def _apply_collapsed(self) -> None:
        self._body_widget.setVisible(not self._collapsed)
        if isinstance(self._header, QPushButton):
            arrow = "chevron_right" if self._collapsed else "chevron_down"
            self._header.setIcon(icons.make_icon(arrow, theme.PRIMARY, 13))

    def _toggle(self) -> None:
        self.set_collapsed(not self._collapsed)
        self.collapsed_changed.emit(self._key, self._collapsed)

    # ------------------------------------------------------------------ #
    def set_collapsed(self, value: bool) -> None:
        if not self._collapsible:
            return
        self._collapsed = bool(value)
        self._apply_collapsed()

    @property
    def collapsed(self) -> bool:
        return self._collapsed

    def body(self) -> QVBoxLayout:
        return self._body_layout

    def add_row(self, widget: QWidget) -> None:
        self._body_layout.addWidget(widget)


class EmptyState(QFrame):
    """列表为空时的引导面板：图形 + 主副文案 + 两个入口按钮。

    做成一个整体控件而不是一段大文字，是因为空状态是用户看到的第一屏，
    值得给足引导；顺带把「平台加密音乐」这个不显眼的能力讲清楚。
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("EmptyState")
        self.setProperty("dragging", False)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 30, 40, 30)
        layout.setSpacing(9)
        layout.setAlignment(Qt.AlignCenter)

        art = QLabel()
        art.setPixmap(icons.render_pixmap("drop", theme.BORDER_STRONG, 54, 2))
        art.setAlignment(Qt.AlignCenter)
        layout.addWidget(art)

        title = QLabel("把文件拖到这里")
        title.setObjectName("EmptyTitle")
        title.setAlignment(Qt.AlignCenter)
        layout.addWidget(title)

        hint = QLabel("支持批量拖入，自动识别类型并匹配可转换格式")
        hint.setObjectName("EmptyHint")
        hint.setAlignment(Qt.AlignCenter)
        layout.addWidget(hint)

        row = QHBoxLayout()
        row.setSpacing(10)
        row.addStretch(1)
        self.pick_btn = QPushButton("  选择文件")
        self.pick_btn.setIcon(icons.make_icon("add_file", theme.PRIMARY, 16))
        row.addWidget(self.pick_btn)
        self.folder_btn = QPushButton("  添加文件夹")
        self.folder_btn.setIcon(icons.make_icon("add_folder", theme.PRIMARY, 16))
        row.addWidget(self.folder_btn)
        row.addStretch(1)
        layout.addLayout(row)

        mini = QLabel("网易云 .ncm / QQ 音乐 .qmc* / 酷狗 .kgm·.vpr / 酷我 .kwm  可直接解锁为原始音频")
        mini.setObjectName("EmptyMini")
        mini.setAlignment(Qt.AlignCenter)
        mini.setWordWrap(True)
        layout.addWidget(mini)

    def set_dragging(self, value: bool) -> None:
        if bool(self.property("dragging")) == bool(value):
            return
        self.setProperty("dragging", bool(value))
        style = self.style()
        style.unpolish(self)
        style.polish(self)


class EngineStatusStrip(QWidget):
    """底部引擎健康状态条：FFmpeg / Pillow / 文档 / 压缩 各自的可用性。"""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(14)
        self._labels: dict[str, QLabel] = {}

    def set_engines(self, engines: list[dict]) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self._labels.clear()

        for engine in engines:
            ok = bool(engine.get("ok"))
            label = QLabel(f"{'●' if ok else '○'} {engine.get('name')}")
            label.setStyleSheet(
                f"color: {'#8FD3A6' if ok else '#F3A9A9'}; font-size: 12px;"
            )
            tip = "就绪" if ok else str(engine.get("hint") or "不可用")
            label.setToolTip(tip)
            self._layout.addWidget(label)
            self._labels[str(engine.get("name"))] = label
        self._layout.addStretch(1)
