"""主窗口。

交互模型（比格式工厂更直接）：
1. 拖文件进来，或工具栏添加；
2. 左侧按类型筛选，右侧面板改参数——改一次，应用到所有选中的文件；
3. 点「开始转换」，表格里实时看进度。
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from PySide6.QtCore import QObject, QRunnable, QSettings, QSize, Qt, QThreadPool, Signal
from PySide6.QtGui import QAction, QColor, QFont, QKeySequence
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from ..core import runtime
from ..core.decryptors import (
    all_decryptors, decryptor_for_ext, is_encrypted, offline_reason, platform_of,
)
from ..core.engines.base import ConvertOptions, MediaInfo
from ..core.formats import (
    ORIGINAL_EXT,
    Kind,
    default_target,
    kind_of,
    label_of,
    spec_of,
)
from ..core.jobs import Job, JobStatus, Scheduler
from ..core.registry import EngineRegistry, get_registry
from . import icons, theme
from .widgets import (
    Card, EmptyState, EngineStatusStrip, KindBadgeDelegate, NavDelegate,
    ProgressDelegate, StatusDelegate,
)

COL_FILE, COL_KIND, COL_SIZE, COL_INFO, COL_TARGET, COL_STATUS, COL_PROGRESS = range(7)
COLUMN_TITLES = ["文件名", "类型", "大小", "信息", "目标格式", "状态", "进度"]

#: 侧栏"平台加密音乐"筛选项的标记值（不是 Kind 成员，只用于列表过滤）
NAV_ENCRYPTED = "encrypted"


def target_text(ext: str) -> str:
    """目标格式在界面上的显示文本。"""
    if ext == ORIGINAL_EXT:
        return "原始格式"
    return ext.upper()


# --------------------------------------------------------------------------- #
class ProbeSignals(QObject):
    finished = Signal(str, object)       # 普通媒体：MediaInfo
    decrypted = Signal(str, object)      # 平台加密：DecryptInfo


class ProbeTask(QRunnable):
    """后台探测媒体信息，避免拖进 30 个视频时界面卡死。

    加密格式走另一条线：用解密器 probe() 读出平台名、标题、剥壳后的真实格式，
    这样界面在"还没转换"时就能告诉用户这是哪个平台的文件。
    """

    def __init__(self, job: Job, registry: EngineRegistry, signals: ProbeSignals) -> None:
        super().__init__()
        self.job = job
        self.registry = registry
        self.signals = signals

    def run(self) -> None:
        decryptor = decryptor_for_ext(self.job.src_ext)
        if decryptor is not None:
            try:
                info = decryptor.probe(self.job.src)
            except Exception:
                return
            self.signals.decrypted.emit(self.job.id, info)
            return

        try:
            engine = self.registry.route(self.job.src_ext, self.job.dst_ext)
            info = engine.probe(self.job.src) if engine and hasattr(engine, "probe") else None
        except Exception:
            info = None
        if info is not None:
            self.signals.finished.emit(self.job.id, info)


class SchedulerBridge(QObject):
    """把调度器的多线程回调转成 Qt 信号（跨线程 emit 会被自动排队到主线程）。"""

    job_start = Signal(object)
    job_progress = Signal(object)
    job_finish = Signal(object)
    all_finished = Signal()


# --------------------------------------------------------------------------- #
class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.registry = get_registry()
        self.settings = QSettings("ErBai", "FormatMaster")
        self.jobs: list[Job] = []
        self._rows: dict[str, int] = {}
        self.out_dir = Path(self.settings.value("out_dir", str(Path.home() / "Desktop")))
        self._probe_pool = QThreadPool.globalInstance()
        self._probe_signals = ProbeSignals()
        self._probe_signals.finished.connect(self._on_probe_finished)
        self._probe_signals.decrypted.connect(self._on_decrypt_probed)

        workers = int(self.settings.value("max_workers", 2))
        self.scheduler = Scheduler(self.registry, max_workers=workers)
        self.bridge = SchedulerBridge()
        self.scheduler.on_job_start = self.bridge.job_start.emit
        self.scheduler.on_job_progress = self.bridge.job_progress.emit
        self.scheduler.on_job_finish = self.bridge.job_finish.emit
        self.scheduler.on_all_finished = self.bridge.all_finished.emit
        self.bridge.job_start.connect(self._on_job_start)
        self.bridge.job_progress.connect(self._on_job_progress)
        self.bridge.job_finish.connect(self._on_job_finish)
        self.bridge.all_finished.connect(self._on_all_finished)

        self.setWindowTitle("FormatMaster · 万能格式转换器")
        self.resize(1280, 800)
        self.setMinimumSize(1020, 640)
        self.setAcceptDrops(True)

        self._log_lines = 0
        self._build_ui()
        self._refresh_engine_strip()
        self._log(f"就绪。输出目录：{self.out_dir}")
        self._log("已内置平台解密器：" + "、".join(d.label for d in all_decryptors()))
        missing = [e["name"] for e in self.registry.health() if not e["ok"]]
        if missing:
            self._log(f"注意：以下引擎当前不可用 → {'、'.join(missing)}")

    # ====================================================================== #
    # 界面搭建
    # ====================================================================== #
    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())

        self.toolbar = self._build_toolbar()
        root.addWidget(self.toolbar)

        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(self._build_side_nav())
        splitter.addWidget(self._build_center())
        splitter.addWidget(self._build_param_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([178, 720, 348])
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter, 1)

        root.addWidget(self._build_log_area())
        root.addWidget(self._build_action_bar())
        self.setCentralWidget(central)

        self.setStatusBar(QStatusBar())
        self._update_status("等待添加文件")
        self._update_empty_tip()        # 首次启动列表是空的，别把空表格也画出来

    # ---------------------------------------------------------------- #
    def _build_header(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("HeaderBar")
        bar.setFixedHeight(56)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(12)

        mark = QLabel()
        mark.setPixmap(icons.render_pixmap("convert", "#FFFFFF", 20, 2))
        mark.setToolTip("FormatMaster · 全格式互转")
        layout.addWidget(mark)

        title = QLabel("FormatMaster")
        title.setObjectName("HeaderTitle")
        layout.addWidget(title)

        hint = QLabel("视频 · 音频 · 图像 · 文档 · 压缩包 · 平台加密音乐  全格式互转")
        hint.setObjectName("HeaderHint")
        layout.addWidget(hint)
        layout.addStretch(1)

        self.engine_strip = EngineStatusStrip()
        layout.addWidget(self.engine_strip)
        return bar

    def _refresh_engine_strip(self) -> None:
        self.engine_strip.set_engines(self.registry.health())

    # ---------------------------------------------------------------- #
    def _build_toolbar(self) -> QToolBar:
        tb = QToolBar()
        tb.setMovable(False)
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        tb.setIconSize(QSize(16, 16))

        def act(text: str, slot, shortcut: str = "", tip: str = "",
                icon_name: str = "") -> QAction:
            a = QAction(text, self)
            a.triggered.connect(slot)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            if icon_name:
                a.setIcon(icons.make_icon(icon_name, theme.TEXT_MAIN, 16))
            a.setToolTip(tip or text)
            tb.addAction(a)
            return a

        act("添加文件", self._pick_files, "Ctrl+O", "选择一个或多个文件", "add_file")
        act("添加文件夹", self._pick_folder, "Ctrl+Shift+O", "递归导入文件夹内所有可识别文件", "add_folder")
        tb.addSeparator()
        act("移除选中", self._remove_selected, "Delete", "从列表移除选中的文件", "remove")
        act("清空列表", self._clear_all, "Ctrl+L", "清空整个任务列表", "clear")
        tb.addSeparator()

        tb.addWidget(QLabel(" 批量设为 "))
        self.batch_combo = QComboBox()
        self.batch_combo.setMinimumWidth(150)
        self.batch_combo.setToolTip("把列表里所有文件的目标格式统一改成所选格式")
        self.batch_combo.addItem("（按类型自动匹配）", "")
        tb.addWidget(self.batch_combo)
        apply_btn = QPushButton("应用")
        apply_btn.clicked.connect(self._apply_batch_target)
        tb.addWidget(apply_btn)

        spacer = QWidget()
        spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        tb.addWidget(spacer)

        status_btn = QPushButton("  引擎检测")
        status_btn.setIcon(icons.make_icon("gauge", theme.TEXT_MAIN, 16))
        status_btn.setToolTip("检查 FFmpeg / Pillow / 文档 / 压缩 引擎是否就绪")
        status_btn.clicked.connect(self._show_engine_report)
        tb.addWidget(status_btn)
        return tb

    # ---------------------------------------------------------------- #
    def _build_side_nav(self) -> QWidget:
        self.nav = QListWidget()
        self.nav.setObjectName("SideNav")
        self.nav.setFixedWidth(178)
        self.nav.setItemDelegate(NavDelegate(self.nav))
        self.nav.setMouseTracking(True)                 # 悬停态要它才有
        self.nav.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.nav.addItem(self._nav_item("全部文件", None))
        for kind in Kind:
            self.nav.addItem(self._nav_item(kind.label, kind))
        # 加密格式在 Kind 上归音频，但工作流完全不同（先解锁再考虑转码），单独给一个筛选项
        enc_item = QListWidgetItem("平台加密音乐")
        enc_item.setData(Qt.UserRole, NAV_ENCRYPTED)
        enc_item.setToolTip("网易云 / QQ 音乐 / 酷狗 / 酷我 的下载文件")
        self.nav.addItem(enc_item)
        self.nav.setCurrentRow(0)
        self.nav.currentRowChanged.connect(self._apply_filter)
        return self.nav

    def _nav_item(self, text: str, kind: Kind | None) -> QListWidgetItem:
        item = QListWidgetItem(text)
        item.setData(Qt.UserRole, kind.value if kind else None)
        return item

    # ---------------------------------------------------------------- #
    def _build_center(self) -> QWidget:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(12, 12, 6, 6)
        layout.setSpacing(8)

        bar = QHBoxLayout()
        self.summary_label = QLabel("列表为空")
        self.summary_label.setObjectName("SectionTitle")
        bar.addWidget(self.summary_label)
        bar.addStretch(1)

        self.route_hint = QLabel("")
        self.route_hint.setObjectName("Hint")
        bar.addWidget(self.route_hint)
        layout.addLayout(bar)

        self.table = QTableWidget(0, len(COLUMN_TITLES))
        self.table.setHorizontalHeaderLabels(COLUMN_TITLES)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setAlternatingRowColors(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._show_context_menu)
        self.table.itemSelectionChanged.connect(self._on_selection_changed)
        self.table.verticalHeader().setDefaultSectionSize(38)
        self.table.setAlternatingRowColors(True)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(COL_FILE, QHeaderView.Stretch)
        for col, width in (
            (COL_KIND, 78), (COL_SIZE, 84), (COL_INFO, 128),
            (COL_TARGET, 104), (COL_STATUS, 84), (COL_PROGRESS, 132),
        ):
            header.setSectionResizeMode(col, QHeaderView.Fixed)
            self.table.setColumnWidth(col, width)

        self.table.setItemDelegateForColumn(COL_PROGRESS, ProgressDelegate(self.table))
        self.table.setItemDelegateForColumn(COL_STATUS, StatusDelegate(self.table))
        self.table.setItemDelegateForColumn(COL_KIND, KindBadgeDelegate(self.table))
        layout.addWidget(self.table, 1)

        self.empty_tip = EmptyState()
        self.empty_tip.pick_btn.clicked.connect(self._pick_files)
        self.empty_tip.folder_btn.clicked.connect(self._pick_folder)
        layout.addWidget(self.empty_tip, 1)
        return wrap

    # ---------------------------------------------------------------- #
    def _build_param_panel(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedWidth(352)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        holder = QWidget()
        holder.setObjectName("ParamHolder")
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(6, 12, 20, 12)      # 右侧留出滚动条的位置
        layout.setSpacing(12)

        #: 所有可折叠卡片，key 用于记住展开/收起状态
        self._cards: dict[str, Card] = {}

        def card(title: str, key: str, collapsed: bool = False) -> Card:
            item = Card(title, collapsible=True, collapsed=collapsed, key=key)
            item.collapsed_changed.connect(self._on_card_toggled)
            self._cards[key] = item
            return item

        # ---- 输出格式 ----
        self.format_card = card("输出格式", "format")
        self.format_combo = QComboBox()
        self.format_combo.setMinimumHeight(32)
        self.format_combo.currentIndexChanged.connect(self._on_format_changed)
        self.format_card.add_row(self.format_combo)
        self.format_note = QLabel("")
        self.format_note.setObjectName("Hint")
        self.format_note.setWordWrap(True)
        self.format_card.add_row(self.format_note)
        self.route_label = QLabel("")
        self.route_label.setObjectName("Hint")
        self.route_label.setWordWrap(True)
        self.format_card.add_row(self.route_label)
        layout.addWidget(self.format_card)

        # ---- 输出位置 ----
        out_card = card("输出位置", "output")
        self.out_combo = QComboBox()
        self.out_combo.addItem("与源文件同目录", "same")
        self.out_combo.addItem("统一输出到指定目录", "custom")
        self.out_combo.currentIndexChanged.connect(self._on_out_mode_changed)
        out_card.add_row(self.out_combo)
        row = QHBoxLayout()
        self.out_dir_edit = QLineEdit(str(self.out_dir))
        self.out_dir_edit.setReadOnly(True)
        self.out_dir_edit.setToolTip(str(self.out_dir))
        self.out_dir_edit.setCursorPosition(0)      # 只读框默认显示尾部，盘符会被截掉
        row.addWidget(self.out_dir_edit, 1)
        browse = QPushButton()
        browse.setObjectName("MiniButton")
        browse.setFixedSize(34, 32)
        browse.setIcon(icons.make_icon("folder", theme.TEXT_SUB, 16))
        browse.setToolTip("选择输出目录")
        browse.clicked.connect(self._pick_out_dir)
        row.addWidget(browse)
        out_card.body().addLayout(row)
        self.overwrite_check = QCheckBox("允许覆盖同名文件")
        self.overwrite_check.setChecked(True)
        out_card.add_row(self.overwrite_check)
        layout.addWidget(out_card)

        # ---- 画质 ----
        quality_card = card("画质", "quality")
        self.quality_combo = QComboBox()
        self.quality_combo.addItem("高画质（体积大）", "high")
        self.quality_combo.addItem("均衡（推荐）", "balanced")
        self.quality_combo.addItem("小体积（压得狠）", "small")
        self.quality_combo.setCurrentIndex(1)
        self.quality_combo.currentIndexChanged.connect(self._on_quality_changed)
        quality_card.add_row(self.quality_combo)
        layout.addWidget(quality_card)

        # ---- 视频参数 ----
        self.video_card = card("视频参数", "video", collapsed=True)
        self.codec_combo = self._combo_row(
            self.video_card, "编码器",
            [("自动（推荐）", "auto"), ("H.264 兼容最好", "h264"),
             ("H.265 体积更小", "h265"), ("VP9 网页用", "vp9")],
        )
        self.resolution_combo = self._combo_row(
            self.video_card, "分辨率",
            [("保持原样", "keep"), ("3840×2160 (4K)", "3840x2160"),
             ("1920×1080", "1920x1080"), ("1280×720", "1280x720"),
             ("854×480", "854x480"), ("640×360", "640x360")],
        )
        self.fps_combo = self._combo_row(
            self.video_card, "帧率",
            [("保持原样", "keep"), ("60 fps", "60"), ("30 fps", "30"), ("24 fps", "24")],
        )
        for combo in (self.codec_combo, self.resolution_combo, self.fps_combo):
            combo.currentIndexChanged.connect(self._on_advanced_changed)
        layout.addWidget(self.video_card)

        # ---- 音频参数 ----
        self.audio_card = card("音频参数", "audio", collapsed=True)
        self.abitrate_combo = self._combo_row(
            self.audio_card, "音频码率",
            [("自动（推荐）", "auto"), ("320 kbps", "320k"),
             ("192 kbps", "192k"), ("128 kbps", "128k")],
        )
        self.samplerate_combo = self._combo_row(
            self.audio_card, "采样率",
            [("保持原样", "keep"), ("48000 Hz", "48000"), ("44100 Hz", "44100")],
        )
        for combo in (self.abitrate_combo, self.samplerate_combo):
            combo.currentIndexChanged.connect(self._on_advanced_changed)
        self.cover_check = QCheckBox("音频转 MP4 时用封面图做画面")
        self.cover_check.setChecked(True)
        self.cover_check.setToolTip(
            "勾选：把音频里的内嵌封面做成静态画面，产出一个任何播放器/手机都能打开的真视频\n"
            "不勾选：只把音轨装进容器（等于把 .m4a 改名成 .mp4，部分播放器认不出）\n"
            "源文件没有内嵌封面时，两种勾法结果相同"
        )
        self.cover_check.stateChanged.connect(self._on_advanced_changed)
        self.audio_card.add_row(self.cover_check)
        layout.addWidget(self.audio_card)

        # ---- 图像参数 ----
        self.image_card = card("图像参数", "image", collapsed=True)
        self.resize_combo = self._combo_row(
            self.image_card, "尺寸",
            [("保持原样", "keep"), ("缩小到 50%", "50%"),
             ("最长边 1920px", "1920px"), ("最长边 1280px", "1280px"),
             ("最长边 800px", "800px")],
        )
        self.resize_combo.currentIndexChanged.connect(self._on_advanced_changed)
        self.exif_check = QCheckBox("保留 EXIF / 拍摄信息")
        self.exif_check.setChecked(True)
        self.exif_check.stateChanged.connect(self._on_advanced_changed)
        self.image_card.add_row(self.exif_check)
        layout.addWidget(self.image_card)

        # ---- 文档参数 ----
        self.doc_card = card("文档参数", "document", collapsed=True)
        self.doc_channel_combo = self._combo_row(
            self.doc_card, "转换通道",
            [("自动（推荐）", "auto"),
             ("优先高保真", "office"),
             ("只用内置引擎", "builtin")],
        )
        self.doc_channel_combo.currentIndexChanged.connect(self._on_advanced_changed)
        doc_hint = QLabel("高保真通道调用本机 WPS / Office，版式完整；内置通道不依赖任何外部软件")
        doc_hint.setObjectName("Hint")
        doc_hint.setWordWrap(True)
        self.doc_card.add_row(doc_hint)
        layout.addWidget(self.doc_card)

        # ---- 性能 ----
        perf_card = card("性能", "perf", collapsed=True)
        self.hw_check = QCheckBox("启用硬件加速解码（转视频更快）")
        self.hw_check.stateChanged.connect(self._on_advanced_changed)
        perf_card.add_row(self.hw_check)
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("同时转换"))
        self.worker_spin = QComboBox()
        self.worker_spin.addItems(["1 个任务", "2 个任务", "3 个任务", "4 个任务"])
        self.worker_spin.setCurrentIndex(max(0, min(3, self.scheduler.max_workers - 1)))
        self.worker_spin.currentIndexChanged.connect(self._on_workers_changed)
        row2.addWidget(self.worker_spin, 1)
        perf_card.body().addLayout(row2)
        layout.addWidget(perf_card)

        layout.addStretch(1)
        scroll.setWidget(holder)
        self._load_card_states()
        return scroll

    # ---------------------------------------------------------------- #
    def _on_card_toggled(self, key: str, collapsed: bool) -> None:
        self.settings.setValue(f"card/{key}", "1" if collapsed else "0")

    def _load_card_states(self) -> None:
        """恢复上次的展开/收起状态；没记录就保持代码里的默认值。"""
        for key, card in self._cards.items():
            saved = self.settings.value(f"card/{key}", None)
            if saved is not None:
                card.set_collapsed(str(saved) in ("1", "true", "True"))

    def _combo_row(self, card: Card, label: str, options: list[tuple[str, str]]) -> QComboBox:
        row = QHBoxLayout()
        tag = QLabel(label)
        tag.setObjectName("FieldLabel")
        tag.setFixedWidth(64)
        row.addWidget(tag)
        combo = QComboBox()
        for text, value in options:
            combo.addItem(text, value)
        row.addWidget(combo, 1)
        card.body().addLayout(row)
        return combo

    # ---------------------------------------------------------------- #
    def _build_log_area(self) -> QWidget:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(4)

        header = QHBoxLayout()
        header.setSpacing(2)
        self.log_toggle = QPushButton("▸ 运行日志")
        self.log_toggle.setObjectName("LogToggle")
        self.log_toggle.setToolTip("展开/收起运行日志，出错时看这里")
        self.log_toggle.clicked.connect(self._toggle_log)
        header.addWidget(self.log_toggle)
        header.addStretch(1)
        clear = QPushButton("清空日志")
        clear.setObjectName("GhostButton")
        clear.clicked.connect(self._clear_log)
        header.addWidget(clear)
        layout.addLayout(header)

        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("LogView")
        self.log_view.setReadOnly(True)
        self.log_view.setFixedHeight(104)
        self.log_view.setMaximumBlockCount(600)
        self.log_view.setVisible(False)          # 默认收起，把空间留给列表
        layout.addWidget(self.log_view)
        return wrap

    def _clear_log(self) -> None:
        self.log_view.clear()
        self._log_lines = 0
        self._update_log_toggle()

    def _update_log_toggle(self) -> None:
        arrow = "▾" if self.log_view.isVisible() else "▸"
        count = f"（{self._log_lines} 条）" if self._log_lines else ""
        self.log_toggle.setText(f"{arrow} 运行日志{count}")

    def _build_action_bar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("ActionBar")
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(16, 10, 16, 10)
        layout.setSpacing(12)

        self.total_progress = QProgressBar()
        self.total_progress.setObjectName("TotalProgress")
        self.total_progress.setFixedWidth(230)
        self.total_progress.setFixedHeight(16)
        self.total_progress.setTextVisible(True)
        self.total_progress.setFormat("%p%")
        self.total_progress.setValue(0)
        layout.addWidget(self.total_progress)

        self.progress_label = QLabel("尚未开始")
        self.progress_label.setObjectName("Hint")
        layout.addWidget(self.progress_label)
        layout.addStretch(1)

        self.open_out_btn = QPushButton("  打开输出目录")
        self.open_out_btn.setIcon(icons.make_icon("folder", theme.TEXT_MAIN, 16))
        self.open_out_btn.clicked.connect(self._open_output_dir)
        layout.addWidget(self.open_out_btn)

        self.stop_btn = QPushButton("  停止")
        self.stop_btn.setObjectName("DangerButton")
        self.stop_btn.setIcon(icons.make_icon("stop", theme.DANGER, 16,
                                              disabled_color=theme.TEXT_MUTED))
        self.stop_btn.setEnabled(False)
        self.stop_btn.clicked.connect(self._on_stop)
        layout.addWidget(self.stop_btn)

        self.start_btn = QPushButton("  开始转换")
        self.start_btn.setObjectName("PrimaryButton")
        self.start_btn.setMinimumWidth(140)
        self.start_btn.setMinimumHeight(34)
        self.start_btn.setIcon(icons.make_icon("play", "#FFFFFF", 16,
                                               disabled_color="#F0F3F8"))
        self.start_btn.clicked.connect(self._on_start)
        layout.addWidget(self.start_btn)
        return bar

    # ====================================================================== #
    # 文件导入
    # ====================================================================== #
    def _pick_files(self) -> None:
        exts = sorted(self.registry.supported_inputs())
        pattern = " ".join(f"*.{e}" for e in exts)
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择要转换的文件", str(Path.home()),
            f"支持的所有格式 ({pattern});;所有文件 (*)",
        )
        if files:
            self.add_paths([Path(f) for f in files])

    def _pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择文件夹", str(Path.home()))
        if not folder:
            return
        exts = self.registry.supported_inputs()
        found: list[Path] = []
        for root, _dirs, names in os.walk(folder):
            for name in names:
                path = Path(root) / name
                if path.suffix.lstrip(".").lower() in exts:
                    found.append(path)
            if len(found) > 3000:
                break
        if not found:
            QMessageBox.information(self, "没有可转换的文件", "该文件夹内没有识别出支持的格式。")
            return
        self.add_paths(found)

    def add_paths(self, paths: list[Path]) -> None:
        added = 0
        skipped = 0
        unlocked = 0
        blocked: list[str] = []
        for path in paths:
            try:
                if not path.is_file():
                    continue
            except OSError:
                continue
            ext = path.suffix.lstrip(".").lower()
            if kind_of(ext) is None:
                skipped += 1
                continue
            if any(j.src == path for j in self.jobs):
                skipped += 1
                continue

            if is_encrypted(ext):
                # 加密格式默认"仅解密"：无损拿到原始音频，要不要转码由用户决定。
                # 默认就去转码属于无谓的音质损失。
                target = ORIGINAL_EXT
                unlocked += 1
                if offline_reason(ext):
                    blocked.append(path.name)
            else:
                target = default_target(ext)
                if not self.registry.can_convert(ext, target):
                    options = self.registry.targets_for(ext)
                    if not options:
                        skipped += 1
                        continue
                    target = options[0].ext

            opts = self._collect_options(target)
            job = Job(src=path, options=opts, out_dir=self._resolve_out_dir(path))
            self.jobs.append(job)
            self._append_row(job)
            self._probe_pool.start(ProbeTask(job, self.registry, self._probe_signals))
            added += 1

        if added:
            self._refresh_batch_combo()
            self._refresh_summary()
            self._update_empty_tip()
            note = f"（其中 {unlocked} 个为平台加密音乐，默认仅解密）" if unlocked else ""
            self._log(f"添加 {added} 个文件{note}" +
                      (f"，忽略 {skipped} 个不支持或重复的文件" if skipped else ""))
            if blocked:
                shown = "、".join(blocked[:4]) + ("…" if len(blocked) > 4 else "")
                self._log(
                    f"！其中 {len(blocked)} 个是平台新版加密，密钥不随文件下发、"
                    f"本机离线解不开（{shown}）——建议在对应客户端里改选标准音质重新下载"
                )
            self.statusBar().showMessage(f"已添加 {added} 个文件", 4000)

    # ---------------------------------------------------------------- #
    def _append_row(self, job: Job) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        self._rows[job.id] = row
        self._fill_row(row, job)

    def _fill_row(self, row: int, job: Job) -> None:
        spec = spec_of(job.src_ext)
        kind_label = spec.label if spec else job.src_ext.upper()
        platform = platform_of(job.src_ext)

        name_item = QTableWidgetItem(job.src.name)
        name_item.setToolTip(str(job.src))
        name_item.setData(Qt.UserRole, job.id)
        self.table.setItem(row, COL_FILE, name_item)

        badge = QTableWidgetItem(job.src_ext.upper())
        badge.setData(Qt.UserRole, QColor(theme.KIND_COLORS.get(
            job.src_kind.value if job.src_kind else "", theme.TEXT_SUB)))
        badge.setToolTip(f"{kind_label}（{platform}）" if platform else kind_label)
        self.table.setItem(row, COL_KIND, badge)

        self.table.setItem(row, COL_SIZE, self._center(_size_text(job)))
        self.table.setItem(row, COL_INFO, self._center(self._info_text(job)))
        self.table.setItem(row, COL_TARGET, self._center(target_text(job.dst_ext)))
        self._set_status_cell(row, job)
        self._set_progress_cell(row, job)

    @staticmethod
    def _info_text(job: Job) -> str:
        """加密格式优先显示平台名，普通文件显示分辨率/时长。"""
        if job.real_ext and job.platform:
            return f"{job.platform}·{job.real_ext.upper()}"
        if is_encrypted(job.src_ext):
            return platform_of(job.src_ext) or "待解密"
        info = job.info
        if info is None:
            return "—"
        if info.error:
            return info.error[:16]
        parts = []
        if info.resolution_text != "—":
            parts.append(info.resolution_text)
        if info.duration_text != "—":
            parts.append(info.duration_text)
        if not parts and info.video_codec:
            parts.append(str(info.video_codec))
        return " · ".join(parts) or "—"

    def _refresh_row_info(self, row: int, job: Job) -> None:
        self.table.setItem(row, COL_INFO, self._center(self._info_text(job)))

    @staticmethod
    def _center(text: str) -> QTableWidgetItem:
        item = QTableWidgetItem(text)
        item.setTextAlignment(Qt.AlignCenter)
        return item

    def _set_status_cell(self, row: int, job: Job) -> None:
        item = QTableWidgetItem(job.status.label)
        item.setTextAlignment(Qt.AlignCenter)
        item.setData(Qt.UserRole, QColor(theme.STATUS_COLORS.get(job.status.value, theme.TEXT_MUTED)))
        item.setToolTip(job.message)
        self.table.setItem(row, COL_STATUS, item)

    def _set_progress_cell(self, row: int, job: Job) -> None:
        item = QTableWidgetItem(f"{job.progress * 100:.0f}%")
        item.setData(Qt.UserRole, float(job.progress))
        item.setData(Qt.UserRole + 1, QColor(theme.STATUS_COLORS.get(job.status.value, theme.PRIMARY)))
        item.setTextAlignment(Qt.AlignCenter)
        item.setToolTip(job.message)
        self.table.setItem(row, COL_PROGRESS, item)

    def _update_row(self, job: Job) -> None:
        row = self._rows.get(job.id)
        if row is None or row >= self.table.rowCount():
            return
        self._set_status_cell(row, job)
        self._set_progress_cell(row, job)
        self._refresh_row_info(row, job)

    def _on_probe_finished(self, job_id: str, info: MediaInfo) -> None:
        for job in self.jobs:
            if job.id == job_id:
                job.info = info
                row = self._rows.get(job_id)
                if row is not None:
                    self._refresh_row_info(row, job)
                return

    def _on_decrypt_probed(self, job_id: str, info) -> None:
        """加密文件探测回来了：记下平台与真实格式，顺带提示能否解锁。"""
        for job in self.jobs:
            if job.id != job_id:
                continue
            err = info.payload.get("error") if getattr(info, "payload", None) else None
            if err:
                job.message = str(err)
                job.platform = info.platform
                job.real_ext = info.real_ext
            else:
                job.platform = info.platform
                job.real_ext = info.real_ext
            row = self._rows.get(job_id)
            if row is not None:
                self._refresh_row_info(row, job)
                item = self.table.item(row, COL_INFO)
                if item is not None:
                    item.setToolTip(str(err) if err else f"{info.platform} · {info.display}")
            if err:
                self._log(f"！{job.src.name}：{err}")
            else:
                self._log(f"识别到{info.platform}文件：{job.src.name}"
                          f"（真实格式 {info.real_ext.upper()}）")
            return

    # ====================================================================== #
    # 列表操作
    # ====================================================================== #
    def _selected_jobs(self) -> list[Job]:
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()})
        out = []
        for row in rows:
            item = self.table.item(row, COL_FILE)
            if item is None:
                continue
            job_id = item.data(Qt.UserRole)
            for job in self.jobs:
                if job.id == job_id:
                    out.append(job)
                    break
        return out

    def _remove_selected(self) -> None:
        jobs = self._selected_jobs()
        if not jobs:
            return
        for job in jobs:
            if job.status is JobStatus.RUNNING:
                job.cancel_event.set()
            self.jobs.remove(job)
        self._rebuild_table()
        self._log(f"移除 {len(jobs)} 个文件")

    def _clear_all(self) -> None:
        if not self.jobs:
            return
        if QMessageBox.question(self, "清空列表", "确定要清空整个任务列表吗？") != QMessageBox.Yes:
            return
        for job in self.jobs:
            if job.status is JobStatus.RUNNING:
                job.cancel_event.set()
        self.jobs.clear()
        self._rebuild_table()
        self._log("列表已清空")

    def _rebuild_table(self) -> None:
        self.table.setRowCount(0)
        self._rows.clear()
        for job in self.jobs:
            self._append_row(job)
        self._refresh_batch_combo()
        self._refresh_summary()
        self._update_empty_tip()

    def _apply_filter(self) -> None:
        kind_value = self.nav.currentItem().data(Qt.UserRole)
        for row in range(self.table.rowCount()):
            item = self.table.item(row, COL_FILE)
            if item is None:
                continue
            job = next((j for j in self.jobs if j.id == item.data(Qt.UserRole)), None)
            if job is None:
                continue
            if kind_value == NAV_ENCRYPTED:
                visible = is_encrypted(job.src_ext)
            else:
                visible = kind_value is None or (
                    job.src_kind is not None and job.src_kind.value == kind_value
                )
            self.table.setRowHidden(row, not visible)

    def _update_empty_tip(self) -> None:
        has_rows = self.table.rowCount() > 0
        self.table.setVisible(has_rows)
        self.empty_tip.setVisible(not has_rows)

    def _refresh_summary(self) -> None:
        total = len(self.jobs)
        if not total:
            self.summary_label.setText("列表为空")
            self._refresh_nav_counts()
            return
        done = sum(1 for j in self.jobs if j.status is JobStatus.DONE)
        failed = sum(1 for j in self.jobs if j.status is JobStatus.FAILED)
        size = sum(_file_size(j.src) for j in self.jobs)
        text = f"共 {total} 个文件 · {_human(size)}"
        if done or failed:
            text += f" · 已完成 {done}"
        if failed:
            text += f" · 失败 {failed}"
        self.summary_label.setText(text)
        self._refresh_nav_counts()

    def _refresh_nav_counts(self) -> None:
        """把各类文件数量填进侧栏徽标 —— 不用点开就知道哪一类有东西。"""
        by_kind: dict[str, int] = {}
        for job in self.jobs:
            if job.src_kind is not None:
                by_kind[job.src_kind.value] = by_kind.get(job.src_kind.value, 0) + 1
        encrypted = sum(1 for j in self.jobs if is_encrypted(j.src_ext))

        for i in range(self.nav.count()):
            item = self.nav.item(i)
            value = item.data(Qt.UserRole)
            if value == NAV_ENCRYPTED:
                count = encrypted
            elif value is None:
                count = len(self.jobs)
            else:
                count = by_kind.get(str(value), 0)
            item.setData(Qt.UserRole + 1, count)
        self.nav.viewport().update()

    def _refresh_batch_combo(self) -> None:
        current = self.batch_combo.currentData()
        self.batch_combo.blockSignals(True)
        self.batch_combo.clear()
        self.batch_combo.addItem("（按类型自动匹配）", "")
        if any(is_encrypted(j.src_ext) for j in self.jobs):
            self.batch_combo.addItem("加密音乐 → 原始格式（仅解密）",
                                     f"{Kind.AUDIO.value}:{ORIGINAL_EXT}")
        kinds = {j.src_kind for j in self.jobs if j.src_kind}
        for kind in Kind:
            if kind not in kinds:
                continue
            for spec in self.registry.targets_for(_sample_ext(self.registry, kind)):
                self.batch_combo.addItem(f"{kind.label} → {spec.display}", f"{kind.value}:{spec.ext}")
        idx = self.batch_combo.findData(current)
        if idx >= 0:
            self.batch_combo.setCurrentIndex(idx)
        self.batch_combo.blockSignals(False)

    def _apply_batch_target(self) -> None:
        data = self.batch_combo.currentData()
        if not data:
            QMessageBox.information(self, "未选择", "请先在下拉框里选一个目标格式。")
            return
        kind_value, ext = data.split(":", 1)
        changed = 0
        skipped = 0
        for job in self.jobs:
            if not job.src_kind or job.src_kind.value != kind_value:
                continue
            if ext == ORIGINAL_EXT and not is_encrypted(job.src_ext):
                skipped += 1
                continue
            if job.src_ext == ext:
                continue
            if not self.registry.can_convert(job.src_ext, ext):
                skipped += 1
                continue
            job.options.target_ext = ext
            row = self._rows.get(job.id)
            if row is not None:
                self.table.setItem(row, COL_TARGET, self._center(target_text(ext)))
            changed += 1
        self._refresh_summary()
        note = f"，{skipped} 个不适用已跳过" if skipped else ""
        self._log(f"批量设置：{changed} 个文件的目标格式改为 {target_text(ext)}{note}")

    # ====================================================================== #
    # 参数面板
    # ====================================================================== #
    def _collect_options(self, target_ext: str) -> ConvertOptions:
        return ConvertOptions(
            target_ext=target_ext,
            quality=self.quality_combo.currentData(),
            video_codec=self.codec_combo.currentData(),
            resolution=self.resolution_combo.currentData(),
            fps=self.fps_combo.currentData(),
            audio_bitrate=self.abitrate_combo.currentData(),
            sample_rate=self.samplerate_combo.currentData(),
            cover_video=self.cover_check.isChecked(),
            resize=self.resize_combo.currentData(),
            keep_exif=self.exif_check.isChecked(),
            doc_engine=self.doc_channel_combo.currentData(),
            hardware_accel=self.hw_check.isChecked(),
            overwrite=self.overwrite_check.isChecked(),
        )

    def _on_selection_changed(self) -> None:
        jobs = self._selected_jobs()
        if not jobs:
            self.format_note.setText("未选中文件——参数将作为新添加文件的默认值。")
            self.route_label.setText("")
            self._update_param_visibility(None)
            return

        job = jobs[0]
        self.format_combo.blockSignals(True)
        self.format_combo.clear()
        targets = self.registry.targets_for(job.src_ext)
        for spec in targets:
            self.format_combo.addItem(spec.display, spec.ext)
        idx = self.format_combo.findData(job.dst_ext)
        if idx >= 0:
            self.format_combo.setCurrentIndex(idx)
        self.format_combo.blockSignals(False)

        multi = f"（已选中 {len(jobs)} 个文件，修改会同时应用）" if len(jobs) > 1 else ""
        src_desc = job.src_ext.upper()
        if is_encrypted(job.src_ext):
            src_desc = f"{src_desc}（{platform_of(job.src_ext)}）"
        self.format_note.setText(f"源格式 {src_desc}，可选 {len(targets)} 种目标格式{multi}")
        self._update_route_label(job.src_ext, job.dst_ext)
        self._update_param_visibility(job.src_kind, job.dst_ext)

    def _update_route_label(self, src_ext: str, dst_ext: str) -> None:
        ok, text = self.registry.explain(src_ext, dst_ext)
        self.route_label.setText(("" if ok else "⚠ ") + text)
        self.route_label.setStyleSheet(
            f"color: {theme.SUCCESS if ok else theme.DANGER}; font-size: 11px;"
        )

    def _update_param_visibility(self, kind: Kind | None, dst_ext: str = "") -> None:
        if dst_ext == ORIGINAL_EXT:
            # 仅解密：不重编码，编码参数没有意义，藏起来免得误导
            self.video_card.setVisible(False)
            self.audio_card.setVisible(False)
            self.image_card.setVisible(False)
            self.doc_card.setVisible(False)
            return
        self.video_card.setVisible(kind in (Kind.VIDEO, None))
        self.audio_card.setVisible(kind in (Kind.VIDEO, Kind.AUDIO, None))
        self.image_card.setVisible(kind in (Kind.IMAGE, None))
        self.doc_card.setVisible(kind in (Kind.DOCUMENT, None))

    def _on_format_changed(self) -> None:
        ext = self.format_combo.currentData()
        if not ext:
            return
        jobs = self._selected_jobs()
        changed = 0
        for job in jobs:
            if job.src_ext == ext:
                continue
            if not self.registry.can_convert(job.src_ext, ext):
                continue
            job.options.target_ext = ext
            row = self._rows.get(job.id)
            if row is not None:
                self.table.setItem(row, COL_TARGET, self._center(target_text(ext)))
            changed += 1
        if jobs:
            self._update_route_label(jobs[0].src_ext, ext)
            self._update_param_visibility(jobs[0].src_kind, ext)
            self._log(f"目标格式改为 {target_text(ext)}（{changed} 个文件）")
            self._refresh_summary()

    def _on_quality_changed(self) -> None:
        self._push_options()
        self._log(f"画质档：{self.quality_combo.currentText()}")

    def _on_advanced_changed(self) -> None:
        self._push_options()

    def _push_options(self) -> None:
        jobs = self._selected_jobs()
        quality = self.quality_combo.currentData()
        for job in jobs:
            if job.status is JobStatus.RUNNING:
                continue
            job.options.quality = quality
            job.options.video_codec = self.codec_combo.currentData()
            job.options.resolution = self.resolution_combo.currentData()
            job.options.fps = self.fps_combo.currentData()
            job.options.audio_bitrate = self.abitrate_combo.currentData()
            job.options.sample_rate = self.samplerate_combo.currentData()
            job.options.cover_video = self.cover_check.isChecked()
            job.options.resize = self.resize_combo.currentData()
            job.options.keep_exif = self.exif_check.isChecked()
            job.options.doc_engine = self.doc_channel_combo.currentData()
            job.options.hardware_accel = self.hw_check.isChecked()
            job.options.overwrite = self.overwrite_check.isChecked()

    def _on_out_mode_changed(self) -> None:
        custom = self.out_combo.currentData() == "custom"
        self.out_dir_edit.setEnabled(custom)
        self._push_out_dir()

    def _pick_out_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "选择输出目录", str(self.out_dir))
        if folder:
            self.out_dir = Path(folder)
            self.out_dir_edit.setText(folder)
            self.out_dir_edit.setToolTip(folder)
            self.out_dir_edit.setCursorPosition(0)
            self.out_combo.setCurrentIndex(1)
            self.settings.setValue("out_dir", folder)
            self._push_out_dir()
            self._log(f"输出目录：{folder}")

    def _push_out_dir(self) -> None:
        for job in self.jobs:
            job.out_dir = self._resolve_out_dir(job.src)

    def _resolve_out_dir(self, src: Path) -> Path:
        if self.out_combo.currentData() == "custom":
            return self.out_dir
        return src.parent

    def _on_workers_changed(self) -> None:
        count = self.worker_spin.currentIndex() + 1
        self.scheduler.max_workers = count
        self.settings.setValue("max_workers", count)
        self._log(f"并发任务数：{count}")

    # ====================================================================== #
    # 执行
    # ====================================================================== #
    def _on_start(self) -> None:
        if self.scheduler.running:
            return
        pending = [j for j in self.jobs if not j.is_finished]
        if not pending:
            done = [j for j in self.jobs if j.status is JobStatus.DONE]
            if done:
                self._log("没有待处理的任务，已完成的文件需要先「移除」或重新添加才能重跑")
            else:
                QMessageBox.information(self, "没有任务", "请先添加要转换的文件。")
            return

        unsupported = [j for j in pending if not self.registry.can_convert(j.src_ext, j.dst_ext)]
        if unsupported:
            names = "\n".join(
                f"  · {j.src.name}（{j.src_ext.upper()} → {target_text(j.dst_ext)}）"
                f"\n      {self.registry.explain(j.src_ext, j.dst_ext)[1]}"
                for j in unsupported[:5]
            )
            QMessageBox.warning(
                self, "部分任务无法执行",
                f"以下 {len(unsupported)} 个任务本机无法完成，将被跳过：\n{names}",
            )
            for job in unsupported:
                job.status = JobStatus.SKIPPED
                job.message = self.registry.explain(job.src_ext, job.dst_ext)[1] or "无可用引擎"
                self._update_row(job)

        self._push_options()
        self._push_out_dir()
        queued = self.scheduler.run(pending)
        if queued:
            self._set_running(True)
            self._log(f"开始转换，共 {queued} 个任务，并发 {self.scheduler.max_workers}")
            self._refresh_summary()

    def _on_stop(self) -> None:
        self.scheduler.cancel_all(self.jobs)
        self._log("已请求停止，正在结束当前任务…")
        self.stop_btn.setEnabled(False)

    def _set_running(self, running: bool) -> None:
        self.start_btn.setEnabled(not running)
        self.stop_btn.setEnabled(running)
        self.start_btn.setText("  转换中…" if running else "  开始转换")

    def _on_job_start(self, job: Job) -> None:
        self._update_row(job)
        if is_encrypted(job.src_ext):
            bits = [platform_of(job.src_ext) or "平台加密"]
            if job.real_ext:
                bits.append(f"剥壳为 {job.real_ext.upper()}")
            if job.dst_ext != ORIGINAL_EXT:
                bits.append(f"转 {job.dst_ext.upper()}")
            route = " → ".join(bits)
        else:
            route = job.engine_name or "选定引擎"
        self._log(f"▶ {job.src.name} → {target_text(job.dst_ext)}（{route}）")

    def _on_job_progress(self, job: Job) -> None:
        self._update_row(job)
        self._update_total_progress()

    def _on_job_finish(self, job: Job) -> None:
        self._update_row(job)
        self._update_total_progress()
        if job.status is JobStatus.DONE:
            tail = f"  [{job.platform} 解密为 {job.real_ext.upper()}]" if job.platform else ""
            self._log(f"✓ {job.src.name} → {job.output.name if job.output else ''}  "
                      f"{job.elapsed:.1f}s{tail}")
        elif job.status is JobStatus.FAILED:
            self._log(f"✗ {job.src.name} 失败：{job.message}")
        elif job.status is JobStatus.CANCELLED:
            self._log(f"⊘ {job.src.name} 已取消")

    def _on_all_finished(self) -> None:
        self._set_running(False)
        self._refresh_summary()
        done = sum(1 for j in self.jobs if j.status is JobStatus.DONE)
        failed = sum(1 for j in self.jobs if j.status is JobStatus.FAILED)
        self._log(f"全部结束：成功 {done}，失败 {failed}")
        self.statusBar().showMessage(f"转换完成 — 成功 {done}，失败 {failed}", 8000)

    def _update_total_progress(self) -> None:
        pending = [j for j in self.jobs if j.status in (JobStatus.PENDING, JobStatus.RUNNING)]
        total = len(self.jobs)
        if not total:
            self.total_progress.setValue(0)
            return
        done = sum(1 for j in self.jobs if j.status is JobStatus.DONE)
        current = sum(j.progress for j in pending)
        value = int(((done + current) / total) * 100)
        self.total_progress.setValue(max(0, min(100, value)))
        self.progress_label.setText(f"{done}/{total} 完成" if pending else f"全部 {total} 个任务已处理")

    # ====================================================================== #
    # 其他
    # ====================================================================== #
    def _show_context_menu(self, pos) -> None:
        jobs = self._selected_jobs()
        menu = QMenu(self)
        if jobs:
            open_out = menu.addAction("打开输出目录")
            open_src = menu.addAction("打开源文件位置")
            menu.addSeparator()
            retry = menu.addAction("重新排队")
            remove = menu.addAction("从列表移除")
            chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
            if chosen is open_out:
                self._open_path(jobs[0].out_dir if jobs[0].out_dir.exists() else Path.home())
            elif chosen is open_src:
                self._open_path(jobs[0].src.parent)
            elif chosen is retry:
                for job in jobs:
                    if job.status is not JobStatus.RUNNING:
                        job.reset()
                        self._update_row(job)
                self._refresh_summary()
            elif chosen is remove:
                self._remove_selected()
        else:
            menu.addAction("添加文件…", self._pick_files)
            menu.addAction("添加文件夹…", self._pick_folder)
            menu.exec(self.table.viewport().mapToGlobal(pos))

    def _open_output_dir(self) -> None:
        target = self.out_dir if self.out_combo.currentData() == "custom" else Path.home()
        if self.jobs and self.out_combo.currentData() == "same":
            target = self.jobs[0].src.parent
        self._open_path(target)

    @staticmethod
    def _open_path(path: Path) -> None:
        try:
            os.startfile(str(path))       # type: ignore[attr-defined]
        except Exception:
            subprocess.Popen(["explorer", str(path)])

    def _show_engine_report(self) -> None:
        rows = runtime.environment_report()
        lines = []
        for item in rows:
            mark = "✓" if item["ok"] else "✗"
            path = item["path"] if item["ok"] else "未找到"
            if item["path"] == "yes":
                path = "已注册"
            lines.append(f"{mark}  {item['name']}\n      {item['desc']}\n      {path}")

        lines.append("")
        lines.append("平台解密器（内置，不依赖任何外部程序）")
        for dec in all_decryptors():
            exts = "、".join(e.upper() for e in dec.extensions[:8])
            if len(dec.extensions) > 8:
                exts += f" 等 {len(dec.extensions)} 种"
            outs = "、".join(x.upper() for x in dec.outputs)
            lines.append(f"✓  {dec.label}\n      {exts}\n      剥壳后：{outs}")

        text = "\n".join(lines)
        box = QMessageBox(self)
        box.setWindowTitle("引擎检测")
        box.setText("转换引擎可用性")
        box.setDetailedText(text)
        box.setInformativeText("\n".join(l.split("\n")[0] for l in lines[:len(rows)]))
        box.exec()
        self._refresh_engine_strip()

    def _toggle_log(self) -> None:
        self.log_view.setVisible(not self.log_view.isVisible())
        self._update_log_toggle()
        if self.log_view.isVisible():
            scrollbar = self.log_view.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())

    def _update_status(self, text: str) -> None:
        self.statusBar().showMessage(text)

    def _log(self, text: str) -> None:
        from datetime import datetime

        stamp = datetime.now().strftime("%H:%M:%S")
        self.log_view.appendPlainText(f"[{stamp}] {text}")
        self._log_lines += 1
        if self.log_view.isVisible():
            scrollbar = self.log_view.verticalScrollBar()
            scrollbar.setValue(scrollbar.maximum())
        self._update_log_toggle()

    # ---- 拖拽 ---- #
    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self.empty_tip.set_dragging(True)
            self.statusBar().showMessage("松开鼠标即可添加这些文件")

    def dragLeaveEvent(self, event) -> None:
        self.empty_tip.set_dragging(False)

    def dropEvent(self, event) -> None:
        self.empty_tip.set_dragging(False)
        paths: list[Path] = []
        for url in event.mimeData().urls():
            local = url.toLocalFile()
            if not local:
                continue
            path = Path(local)
            if path.is_dir():
                exts = self.registry.supported_inputs()
                for root, _dirs, names in os.walk(path):
                    paths.extend(
                        Path(root) / n for n in names if (Path(root) / n).suffix.lstrip(".").lower() in exts
                    )
                    if len(paths) > 3000:
                        break
            else:
                paths.append(path)
        if paths:
            self.add_paths(paths)
        event.acceptProposedAction()

    def closeEvent(self, event) -> None:
        if self.scheduler.running:
            if QMessageBox.question(self, "正在转换", "还有任务在转换，确定要退出吗？") != QMessageBox.Yes:
                event.ignore()
                return
        self.scheduler.cancel_all(self.jobs)
        self.scheduler.shutdown()
        self.settings.setValue("out_dir", str(self.out_dir))
        super().closeEvent(event)


# --------------------------------------------------------------------------- #
def _file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def _human(size: int) -> str:
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def _size_text(job: Job) -> str:
    return _human(_file_size(job.src))


def _sample_ext(registry: EngineRegistry, kind: Kind) -> str:
    """取该类型下第一个有可选目标格式的格式，用于生成批量下拉框。"""
    from ..core.formats import all_of_kind

    for spec in all_of_kind(kind):
        if registry.targets_for(spec.ext):
            return spec.ext
    return ""
