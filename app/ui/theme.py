"""界面配色与 Qt 样式表。

主色沿用 Business Authority 深蓝 #1F365D，整体走"工具软件"的克制风格：
信息密度高、边界清晰、不用花哨渐变。
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from ..core.formats import Kind

# --------------------------------------------------------------------------- #
# 调色板
# --------------------------------------------------------------------------- #
PRIMARY = "#1F365D"
PRIMARY_LIGHT = "#2C4A7C"
PRIMARY_DARK = "#152A47"
PRIMARY_SOFT = "#E8EDF5"

BG_WINDOW = "#F4F6F9"
BG_PANEL = "#FFFFFF"
BG_HOVER = "#F0F3F8"
BG_SELECT = "#E8EDF5"
BG_HEADER = "#EDF1F7"
BG_ALT = "#FAFBFD"          # 表格斑马纹：比面板白再暗一点点，够区分又不抢眼
BG_SUNKEN = "#F7F8FA"       # 凹陷面（进度条底槽等）

BORDER = "#DCE1EA"
BORDER_STRONG = "#C3CCDA"

TEXT_MAIN = "#1F2937"
TEXT_SUB = "#6B7280"
TEXT_MUTED = "#9CA3AF"
TEXT_ON_PRIMARY = "#FFFFFF"

SUCCESS = "#16A34A"
SUCCESS_SOFT = "#E7F6EC"
DANGER = "#DC2626"
DANGER_SOFT = "#FDECEC"
WARNING = "#D97706"
WARNING_SOFT = "#FDF3E3"
INFO = "#2563EB"
INFO_SOFT = "#E8F0FE"

STATUS_COLORS = {
    "pending": TEXT_MUTED,
    "running": PRIMARY,
    "done": SUCCESS,
    "failed": DANGER,
    "cancelled": WARNING,
    "skipped": TEXT_MUTED,
}

KIND_COLORS = {kind.value: kind.color for kind in Kind}


# --------------------------------------------------------------------------- #
# 样式表
#
# ⚠️ 一条铁律，踩过坑：**往容器控件上 setStyleSheet 时，选择器必须写全**
#    （例如 ``QFrame#ActionBar { ... }``）。Qt 会把控件上的样式表级联给它的
#    全部子孙，无选择器的 ``setStyleSheet("background: #FFF;")`` 等于给子按钮
#    统统刷成白底 —— 底部动作栏就这么把「开始转换」刷成了白底白字，
#    按钮上的字直接看不见了。叶子控件（QLabel、单个按钮）才可以用无选择器写法。
#    容器级的样式一律写进这份全局表，用 objectName 定位。
# --------------------------------------------------------------------------- #
STYLESHEET = f"""
QWidget {{
    font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif;
    font-size: 13px;
    color: {TEXT_MAIN};
}}

QMainWindow, QDialog {{ background: {BG_WINDOW}; }}

QToolTip {{
    background: #2B3446; color: #FFFFFF; border: none;
    padding: 5px 8px; border-radius: 4px; font-size: 12px;
}}

/* ---------------- 顶部标题栏 ---------------- */
#HeaderBar {{
    background: {PRIMARY};
    border: none;
}}
#HeaderTitle {{
    color: {TEXT_ON_PRIMARY};
    font-size: 16px;
    font-weight: 500;
    letter-spacing: 1px;
}}
#HeaderHint {{
    color: #A8B8D0;
    font-size: 12px;
}}

/* ---------------- 工具栏 ---------------- */
QToolBar {{
    background: {BG_PANEL};
    border: none;
    border-bottom: 1px solid {BORDER};
    padding: 6px 10px;
    spacing: 6px;
}}
QToolBar QToolButton {{
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 6px 11px;
    margin-right: 1px;
    color: {TEXT_MAIN};
}}
QToolBar QToolButton:hover {{
    background: {BG_HOVER};
    border-color: {BORDER};
}}
QToolBar QToolButton:pressed {{ background: {BG_SELECT}; }}
QToolBar QToolButton:disabled {{ color: {TEXT_MUTED}; }}

QToolBar::separator {{
    background: {BORDER}; width: 1px; margin: 6px 6px;
}}

/* ---------------- 按钮 ---------------- */
QPushButton {{
    background: {BG_PANEL};
    border: 1px solid {BORDER_STRONG};
    border-radius: 6px;
    padding: 7px 16px;
    color: {TEXT_MAIN};
    min-height: 18px;
}}
QPushButton:hover {{ background: {BG_HOVER}; border-color: {PRIMARY_LIGHT}; }}
QPushButton:pressed {{ background: {BG_SELECT}; }}
QPushButton:disabled {{ color: {TEXT_MUTED}; border-color: {BORDER}; background: #FAFBFC; }}

QPushButton#PrimaryButton {{
    background: {PRIMARY};
    border: 1px solid {PRIMARY};
    color: {TEXT_ON_PRIMARY};
    font-weight: 500;
    padding: 8px 22px;
}}
QPushButton#PrimaryButton:hover {{ background: {PRIMARY_LIGHT}; border-color: {PRIMARY_LIGHT}; }}
QPushButton#PrimaryButton:pressed {{ background: {PRIMARY_DARK}; }}
QPushButton#PrimaryButton:disabled {{
    background: #B9C3D3; border-color: #B9C3D3; color: #F0F3F8;
}}

QPushButton#DangerButton {{
    background: {BG_PANEL}; border: 1px solid {DANGER}; color: {DANGER};
}}
QPushButton#DangerButton:hover {{ background: {DANGER_SOFT}; }}
QPushButton#DangerButton:disabled {{ color: {TEXT_MUTED}; border-color: {BORDER}; }}

/* ---------------- 输入控件 ---------------- */
QComboBox, QLineEdit, QSpinBox, QDoubleSpinBox {{
    background: {BG_PANEL};
    border: 1px solid {BORDER_STRONG};
    border-radius: 6px;
    padding: 6px 10px;
    min-height: 18px;
    selection-background-color: {PRIMARY};
    selection-color: #FFFFFF;
}}
QComboBox:hover, QLineEdit:hover, QSpinBox:hover {{ border-color: {PRIMARY_LIGHT}; }}
QComboBox:focus, QLineEdit:focus, QSpinBox:focus {{ border-color: {PRIMARY}; }}
QComboBox:disabled, QLineEdit:disabled {{ background: #F7F8FA; color: {TEXT_MUTED}; }}

QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{
    /*ARROW_IMAGE*/
    width: 12px; height: 12px;
    margin-right: 7px;
}}
QComboBox QAbstractItemView {{
    background: {BG_PANEL};
    border: 1px solid {BORDER_STRONG};
    border-radius: 6px;
    padding: 4px;
    outline: none;
    selection-background-color: {BG_SELECT};
    selection-color: {PRIMARY};
}}
QComboBox QAbstractItemView::item {{ padding: 5px 8px; border-radius: 4px; }}

QCheckBox {{ spacing: 7px; }}
QCheckBox::indicator {{
    width: 15px; height: 15px;
    border: 1px solid {BORDER_STRONG};
    border-radius: 3px;
    background: {BG_PANEL};
}}
QCheckBox::indicator:hover {{ border-color: {PRIMARY_LIGHT}; }}
QCheckBox::indicator:checked {{
    background: {PRIMARY};
    border-color: {PRIMARY};
    /*CHECK_IMAGE*/
}}

QRadioButton {{ spacing: 7px; }}
QRadioButton::indicator {{
    width: 14px; height: 14px;
    border: 1px solid {BORDER_STRONG};
    border-radius: 7px;
    background: {BG_PANEL};
}}
QRadioButton::indicator:checked {{
    background: {PRIMARY};
    border: 1px solid {PRIMARY};
    /*DOT_IMAGE*/
}}

/* ---------------- 表格 ---------------- */
QTableView, QTableWidget {{
    background: {BG_PANEL};
    alternate-background-color: {BG_ALT};
    border: none;
    gridline-color: transparent;
    selection-background-color: {BG_SELECT};
    selection-color: {TEXT_MAIN};
    outline: none;
}}
QTableView::item {{ padding: 0px; border-bottom: 1px solid #F1F3F7; }}
QTableView::item:selected {{ background: {BG_SELECT}; color: {TEXT_MAIN}; }}
QTableView::item:hover {{ background: {BG_HOVER}; }}
QTableView::item:selected:hover {{ background: {BG_SELECT}; }}

QHeaderView {{ background: {BG_HEADER}; border: none; }}
QHeaderView::section {{
    background: {BG_HEADER};
    color: {TEXT_SUB};
    border: none;
    border-right: 1px solid {BORDER};
    border-bottom: 1px solid {BORDER};
    padding: 8px 10px;
    font-weight: 500;
    font-size: 12px;
}}
QHeaderView::section:last {{ border-right: none; }}
QHeaderView::section:hover {{ background: {BG_SELECT}; color: {PRIMARY}; }}

/* ---------------- 分组 / 卡片 ---------------- */
QGroupBox {{
    background: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 8px;
    margin-top: 12px;
    padding: 14px 12px 12px 12px;
    font-weight: 500;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 12px;
    top: 2px;
    padding: 0 6px;
    color: {PRIMARY};
    background: {BG_PANEL};
}}

QFrame#Card {{
    background: {BG_PANEL};
    border: 1px solid {BORDER};
    border-radius: 8px;
}}
QFrame#Separator {{ background: {BORDER}; max-height: 1px; border: none; }}

/* ---------------- 侧栏 ----------------
   条目外观（含选中/悬停底色与右侧数量徽标）由 widgets.NavDelegate 自绘，
   这里只留内边距，让 QListWidget 算出正确的行高。 */
QListWidget#SideNav {{
    background: {BG_PANEL};
    border: none;
    border-right: 1px solid {BORDER};
    outline: none;
    padding: 8px 6px;
}}
QListWidget#SideNav::item {{ padding: 10px 10px; }}

/* ---------------- 日志 ---------------- */
QPlainTextEdit#LogView {{
    background: #FBFCFD;
    border: 1px solid {BORDER};
    border-radius: 8px;
    font-family: "Cascadia Mono", "Consolas", "Courier New", monospace;
    font-size: 12px;
    color: #374151;
    padding: 8px;
}}

/* ---------------- 滚动条 ---------------- */
QScrollBar:vertical {{
    background: {BG_WINDOW}; width: 8px; margin: 0;
}}
QScrollBar::handle:vertical {{
    background: #C8D0DC; border-radius: 4px; min-height: 30px;
}}
QScrollBar::handle:vertical:hover {{ background: #A9B4C4; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{ background: transparent; }}

QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 0; }}
QScrollBar::handle:horizontal {{ background: #C8D0DC; border-radius: 5px; min-width: 30px; }}
QScrollBar::handle:horizontal:hover {{ background: #A9B4C4; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {{ background: transparent; }}

QScrollArea {{ border: none; background: transparent; }}
/* QScrollArea 的 viewport 是个独立子控件，只把 QScrollArea 设成透明是不管用的
   （默认会露出 Qt 自己的窗口色 #efefef，和本窗口的 {BG_WINDOW} 对不上）。
   这里直接给参数面板的容器指定底色，比去猜 viewport 的内部 objectName 可靠。 */
QWidget#ParamHolder {{ background: {BG_WINDOW}; }}

/* ---------------- 分割条 ---------------- */
QSplitter::handle {{ background: {BORDER}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
QSplitter::handle:hover {{ background: {PRIMARY_LIGHT}; }}

/* ---------------- 状态栏 ---------------- */
QStatusBar {{
    background: {BG_PANEL};
    border-top: 1px solid {BORDER};
    color: {TEXT_SUB};
    font-size: 12px;
}}
QStatusBar::item {{ border: none; }}

/* ---------------- 菜单 ---------------- */
QMenu {{
    background: {BG_PANEL};
    border: 1px solid {BORDER_STRONG};
    border-radius: 8px;
    padding: 6px;
}}
QMenu::item {{ padding: 7px 24px 7px 12px; border-radius: 5px; }}
QMenu::item:selected {{ background: {BG_SELECT}; color: {PRIMARY}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 5px 8px; }}

QProgressBar {{
    background: #EDF0F5;
    border: none;
    border-radius: 4px;
    text-align: center;
    color: {TEXT_SUB};
    font-size: 11px;
}}
QProgressBar::chunk {{ background: {PRIMARY}; border-radius: 4px; }}

QTabWidget::pane {{ border: 1px solid {BORDER}; border-radius: 8px; background: {BG_PANEL}; }}
QTabBar::tab {{
    background: transparent; padding: 8px 16px; margin-right: 4px;
    border: 1px solid transparent; border-radius: 6px; color: {TEXT_SUB};
}}
QTabBar::tab:selected {{ background: {PRIMARY_SOFT}; color: {PRIMARY}; font-weight: 500; }}
QTabBar::tab:hover {{ background: {BG_HOVER}; }}

QLabel#FieldLabel {{ color: {TEXT_SUB}; font-size: 12px; }}
QLabel#SectionTitle {{ color: {PRIMARY}; font-size: 13px; font-weight: 500; }}
QLabel#Hint {{ color: {TEXT_MUTED}; font-size: 11px; }}

/* ---------------- 底部动作栏 ----------------
   注意选择器写全：这一块是 QFrame 容器，无选择器写法会级联到里面的按钮。 */
QFrame#ActionBar {{
    background: {BG_PANEL};
    border: none;
    border-top: 1px solid {BORDER};
}}
QProgressBar#TotalProgress {{
    background: {BG_SUNKEN};
    border: none;
    border-radius: 7px;
    text-align: center;
    color: {TEXT_SUB};
    font-size: 10px;
}}
QProgressBar#TotalProgress::chunk {{ background: {PRIMARY}; border-radius: 7px; }}

/* ---------------- 空状态 ---------------- */
QFrame#EmptyState {{
    background: {BG_PANEL};
    border: 1px dashed {BORDER_STRONG};
    border-radius: 10px;
}}
QFrame#EmptyState[dragging="true"] {{
    border: 1px dashed {PRIMARY};
    background: {PRIMARY_SOFT};
}}
QLabel#EmptyTitle {{ color: {TEXT_MAIN}; font-size: 15px; }}
QLabel#EmptyHint {{ color: {TEXT_SUB}; font-size: 12px; }}
QLabel#EmptyMini {{ color: {TEXT_MUTED}; font-size: 11px; }}

/* ---------------- 卡片折叠标题 ---------------- */
QPushButton#CardHeader {{
    background: transparent;
    border: none;
    text-align: left;
    padding: 0px;
    min-height: 0px;
    color: {PRIMARY};
    font-size: 13px;
    font-weight: 500;
}}
QPushButton#CardHeader:hover {{ color: {PRIMARY_LIGHT}; }}
QPushButton#CardHeader:pressed {{ color: {PRIMARY_DARK}; }}

/* ---------------- 无边框小按钮 ---------------- */
QPushButton#LogToggle {{
    background: transparent; border: none; text-align: left;
    padding: 2px 4px; min-height: 0px; color: {TEXT_SUB}; font-size: 12px;
}}
QPushButton#LogToggle:hover {{ color: {PRIMARY}; }}
QPushButton#GhostButton {{
    background: transparent; border: none;
    padding: 3px 8px; min-height: 0px; color: {TEXT_MUTED}; font-size: 12px;
}}
QPushButton#GhostButton:hover {{ color: {PRIMARY}; background: {BG_HOVER}; border-radius: 5px; }}
QPushButton#MiniButton {{
    background: {BG_PANEL};
    border: 1px solid {BORDER_STRONG};
    border-radius: 6px;
    padding: 0px;
    min-height: 0px;
}}
QPushButton#MiniButton:hover {{ background: {BG_HOVER}; border-color: {PRIMARY_LIGHT}; }}
"""


# --------------------------------------------------------------------------- #
# 运行时生成 QSS 里画不出来的那几样东西
#
# 勾（QCheckBox 选中态）、圆点（QRadioButton 选中态）、下拉箭头
# 都是 QSS **没有形状原语**可以表达的：勾和圆点没有对应的属性，而 CSS 圈子
# 里那套"用透明边框拼三角形"的箭头写法 Qt 不支持 —— 照抄过来的结果是
# 一个实心小方块（本项目就这么错了很久）。所以只能给 QSS 喂图片。
#
# 但为三个 12px 的图形专门引入资源文件并配置打包路径不划算，于是运行时用
# QPainter 画一次、落到临时目录、把路径塞进 url()。拿不到 QApplication
# （比如纯 import 做静态检查）就返回基础样式表，最坏退化成"实心方块"。
# --------------------------------------------------------------------------- #
_INDICATORS: dict[str, tuple[str, str, int]] = {
    "/*CHECK_IMAGE*/": ("check", "#FFFFFF", 13),
    "/*DOT_IMAGE*/": ("dot", "#FFFFFF", 8),
    "/*ARROW_IMAGE*/": ("chevron_down", TEXT_SUB, 12),
}


def _emit_indicator_assets() -> dict[str, str]:
    """把指示图形画成 PNG 并返回 ``token -> url`` 映射；任何异常都退回空。"""
    from PySide6.QtWidgets import QApplication

    if QApplication.instance() is None:          # 没有 GUI 上下文，画不了
        return {}
    try:
        from . import icons

        folder = Path(tempfile.gettempdir()) / "formatmaster_ui"
        folder.mkdir(parents=True, exist_ok=True)
        urls: dict[str, str] = {}
        for token, (name, color, size) in _INDICATORS.items():
            target = folder / f"{name}_{size}.png"
            icons.render_pixmap(name, color, size, 2).save(str(target), "PNG")
            urls[token] = target.as_posix()      # QSS 的 url() 只认正斜杠
        return urls
    except Exception:
        return {}


def stylesheet() -> str:
    """最终样式表：基础表 + 运行时生成的指示图形。

    必须在 QApplication 建好之后调用（``main.py`` 里就是那个时机）。
    """
    sheet = STYLESHEET
    for token, url in _emit_indicator_assets().items():
        sheet = sheet.replace(token, f"image: url({url});")
    return sheet

