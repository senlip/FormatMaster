"""界面集成自测（无头运行，QT_QPA_PLATFORM=offscreen）。

目的：把"界面 → 注册表 → 调度器 → 解密层 → 引擎"整条链路真跑一遍，
而不是只测核心逻辑。重点覆盖加密格式带来的界面分支：
* 侧栏"平台加密音乐"筛选；
* 列表里显示平台名与剥壳后的真实格式；
* 输出格式下拉框默认落在"原始格式（仅解密）"；
* 路由提示能说清"先解密再转码"；
* 离线解不开的格式（QMCv2）在点开始时被跳过并给出可读原因。

另外还测**界面外观的硬事实**（第 9 节）：直接截窗口取像素，验证主按钮
真的画成了品牌深蓝、折叠分组默认收起、侧栏计数正确。这一节是冲着踩过的
坑来的 —— 动作栏曾经用无选择器样式表把子按钮级联成白底，"开始转换"变成
白底白字、按钮上的字完全看不见，而当时所有自测都是绿的。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from PySide6.QtCore import Qt                                                # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox                      # noqa: E402

from ui_probe import (                                                       # noqa: E402
    apply_app_style, pixel_at, register_fonts, row_has_color, settle, snapshot,
)

sys.path.insert(0, str(Path(__file__).resolve().parent))
from selftest_decrypt import make_kgm, make_ncm, make_qmc                    # noqa: E402
from selftest_pipeline import make_real_audio                                # noqa: E402

from app.core.formats import ORIGINAL_EXT                                    # noqa: E402
from app.core.jobs import JobStatus                                          # noqa: E402
from app.core.runtime import ffmpeg_path                                     # noqa: E402
from app.ui import theme                                                     # noqa: E402
from app.ui.main_window import (                                             # noqa: E402
    COL_FILE, COL_INFO, COL_TARGET, NAV_ENCRYPTED, MainWindow,
)

# 无头环境下模态框会永久阻塞事件循环，直接替掉；顺便把捕获到的文案留档
_DIALOGS: list[str] = []


def _stub_dialog(kind: str):
    def _fn(_parent=None, title="", text="", *a, **k):
        _DIALOGS.append(f"{kind}|{title}|{text}")
        return QMessageBox.Ok if kind != "question" else QMessageBox.Yes
    return staticmethod(_fn)


QMessageBox.warning = _stub_dialog("warning")
QMessageBox.information = _stub_dialog("information")
QMessageBox.question = _stub_dialog("question")
QMessageBox.critical = _stub_dialog("critical")

WORK = Path(tempfile.gettempdir()) / "fm_ui_test"


def pump(app: QApplication, seconds: float) -> None:
    """抽干事件队列（含跨线程信号与后台探测任务）。"""
    deadline = time.time() + seconds
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)


def cell(win: MainWindow, row: int, col: int) -> str:
    item = win.table.item(row, col)
    return item.text() if item is not None else ""


def main() -> int:
    if WORK.exists():
        for p in WORK.rglob("*"):
            if p.is_file():
                p.unlink()
    (WORK / "src").mkdir(parents=True, exist_ok=True)
    (WORK / "out").mkdir(parents=True, exist_ok=True)

    if not ffmpeg_path():
        print("  跳过：ffmpeg 不可用")
        return 0

    # ---- 造样本 ---- #
    real_flac = make_real_audio(WORK / "src" / "_real.flac", "flac")
    ncm = WORK / "src" / "云音乐.ncm"
    ncm.write_bytes(make_ncm(real_flac, b"ui-test-key-000001", {
        "musicName": "界面测试", "album": "专辑",
        "artist": [["歌手", 1]], "format": "flac",
    }))
    kgm = WORK / "src" / "kugou.kgm"
    kgm.write_bytes(make_kgm(real_flac, bytes(range(16)), False))
    v2 = WORK / "src" / "new.mflac"
    v2.write_bytes(make_qmc(real_flac))

    app = QApplication.instance() or QApplication(sys.argv)
    # 离屏平台不会枚举系统字体、也不会自动套样式表，这两个都得补上，
    # 否则第 9 节量到的像素跟用户看到的完全不是一回事。
    register_fonts()
    apply_app_style(app)

    print("=" * 84)
    print("界面集成自测（offscreen）")
    print("=" * 84)

    win = MainWindow()
    win.show()                                    # 截图前必须已显示，否则抓不到内容
    settle(app, 0.3)
    win.out_dir = WORK / "out"                    # 先定目录，再切模式（切模式会回灌路径）
    win.out_dir_edit.setText(str(win.out_dir))
    win.out_combo.setCurrentIndex(1)              # 统一输出到指定目录
    win._push_out_dir()

    passed = failed = 0

    def check(tag: str, ok: bool, detail: str = "") -> None:
        nonlocal passed, failed
        passed += ok
        failed += not ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {tag:<34} {detail}")

    def nav_has_encrypted() -> bool:
        return any(win.nav.item(i).data(Qt.UserRole) == NAV_ENCRYPTED
                   for i in range(win.nav.count()))

    # ---- 1. 导入加密文件 ---- #
    win.add_paths([ncm, kgm, v2])
    pump(app, 2.0)

    check("三个加密文件都进了列表", win.table.rowCount() == 3,
          f"行数={win.table.rowCount()}")
    check("侧栏出现平台加密音乐", nav_has_encrypted(), f"侧栏项={win.nav.count()}")

    # ---- 2. 默认目标是"仅解密" ---- #
    targets = [win.table.item(r, COL_TARGET).text() for r in range(win.table.rowCount())]
    check("默认目标为原始格式", all(t == "原始格式" for t in targets), str(targets))

    # ---- 3. 信息列显示了平台名 ---- #
    infos = [cell(win, r, COL_INFO) for r in range(win.table.rowCount())]
    check("信息列显示平台名", any("网易云音乐" in i for i in infos), str(infos))

    # ---- 4. 选中网易云文件：下拉框与路由提示 ---- #
    win.table.selectRow(0)
    pump(app, 0.4)
    first = win.format_combo.itemText(0)
    check("下拉首项为原始格式", win.format_combo.itemData(0) == ORIGINAL_EXT, first)
    check("路由提示含平台名", "网易云音乐" in win.route_label.text(), win.route_label.text())
    check("仅解密时隐藏音频参数", win.audio_card.isHidden())

    # ---- 5. 曲线：改成 WAV，路由提示应变成两段式 ---- #
    wav_idx = win.format_combo.findData("wav")
    check("下拉里能找到 WAV", wav_idx >= 0, f"index={wav_idx}")
    if wav_idx >= 0:
        win.format_combo.setCurrentIndex(wav_idx)
        pump(app, 0.4)
        txt = win.route_label.text()
        check("路由提示说明先解密再转码",
              "先解密" in txt and "WAV" in txt, txt)
        check("转码时恢复音频参数", not win.audio_card.isHidden())
        check("目标列已更新为 WAV", cell(win, 0, COL_TARGET) == "WAV",
              cell(win, 0, COL_TARGET))

    # ---- 6. 侧栏筛选：只看加密音乐 ---- #
    for i in range(win.nav.count()):
        if win.nav.item(i).data(Qt.UserRole) == NAV_ENCRYPTED:
            win.nav.setCurrentRow(i)
            break
    pump(app, 0.3)
    hidden = sum(1 for r in range(win.table.rowCount()) if win.table.isRowHidden(r))
    check("加密筛选不隐藏任何加密文件", hidden == 0, f"隐藏={hidden}")
    win.nav.setCurrentRow(0)
    pump(app, 0.2)

    # ---- 7. 真跑一遍：NCM → WAV ---- #
    win._on_start()
    deadline = time.time() + 60
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
        if win.jobs[0].is_finished and win.jobs[1].is_finished and win.jobs[2].is_finished:
            break
    pump(app, 0.5)

    j0 = win.jobs[0]
    check("NCM → WAV 完成", j0.status is JobStatus.DONE,
          f"{j0.status.label} / {j0.message}")
    check("产物存在且为 WAV", bool(j0.output and j0.output.is_file() and
                                   j0.output.suffix.lower() == ".wav"),
          str(j0.output))
    check("任务记录了平台与真实格式", j0.platform == "网易云音乐" and j0.real_ext == "flac",
          f"{j0.platform} / {j0.real_ext}")

    j2 = win.jobs[2]
    check("QMCv2 文件被跳过且原因可读",
          j2.status is JobStatus.SKIPPED and "QMCv2" in j2.message,
          f"{j2.status.label} / {j2.message[:40]}")

    # ---- 8. 仅解密一条：只剥壳 ---- #
    win.table.selectRow(1)
    pump(app, 0.3)
    win.format_combo.setCurrentIndex(win.format_combo.findData(ORIGINAL_EXT))
    pump(app, 0.2)
    win.jobs[1].reset()
    win._on_start()
    deadline = time.time() + 30
    while time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
        if win.jobs[1].is_finished:
            break
    j1 = win.jobs[1]
    check("KGM → 仅解密完成", j1.status is JobStatus.DONE, f"{j1.status.label} / {j1.message}")
    check("仅解密产物为 FLAC", bool(j1.output and j1.output.suffix.lower() == ".flac"),
          str(j1.output))
    if j1.status is JobStatus.DONE and j1.output:
        check("仅解密结果逐字节等于原始音频", j1.output.read_bytes() == real_flac,
              f"{j1.output.stat().st_size} 字节")

    # ---- 9. 界面外观硬事实（截图像素级） ---- #
    print()
    print("-" * 84)
    print("9. 界面外观：截真实窗口取像素，不看代码看结果")
    print("-" * 84)

    settle(app, 0.4)
    shot = snapshot(win)

    primary = pixel_at(shot, win, win.start_btn)
    check("主按钮底色为品牌深蓝",
          primary.name() == theme.PRIMARY.lower(),
          f"{primary.name()}（期望 {theme.PRIMARY.lower()}；白底 = 被容器样式级联盖掉了）")

    nav_all = row_has_color(shot, win, win.nav,
                           win.nav.visualItemRect(win.nav.item(0)), theme.PRIMARY_SOFT)
    check("侧栏选中项为浅蓝底", nav_all, f"选中行是否出现 {theme.PRIMARY_SOFT}")

    by_kind: dict[str, int] = {}
    for job in win.jobs:
        if job.src_kind is not None:
            by_kind[job.src_kind.value] = by_kind.get(job.src_kind.value, 0) + 1
    nav_counts = {win.nav.item(i).text(): win.nav.item(i).data(Qt.UserRole + 1)
                  for i in range(win.nav.count())}
    check("侧栏「全部文件」计数=列表行数",
          nav_counts.get("全部文件") == win.table.rowCount(),
          f"{nav_counts}")
    check("侧栏加密音乐计数正确",
          nav_counts.get("平台加密音乐") == sum(1 for j in win.jobs if j.src_ext in
                                                ("ncm", "kgm", "mflac")),
          f"{nav_counts.get('平台加密音乐')}")

    check("视频参数分组默认收起", win.video_card.collapsed)
    check("输出格式分组默认展开", not win.format_card.collapsed)
    check("运行日志默认收起", win.log_view.isHidden())
    check("有文件时表格可见、空状态隐藏",
          not win.table.isHidden() and win.empty_tip.isHidden())

    win.scheduler.shutdown()
    win.close()

    print()
    print("-" * 84)
    print(f"合计 {passed + failed} 项：通过 {passed}，失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
