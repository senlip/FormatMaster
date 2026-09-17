"""离屏渲染主窗口并截图，用于 UI 改版的前后对比。

用法：
    python tools/shot_ui.py --out ui.png [--scenario busy|empty] [--scale 2] [--w 1360] [--h 860]

为什么不用真实屏幕截图：offscreen 平台不弹窗、不抢焦点，也不会因为
用户正在用电脑而截到别的窗口；同一份代码在任何机器上渲染结果一致。
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from ui_probe import apply_app_style, register_fonts              # noqa: E402


def _demo_files(work: Path) -> list[Path]:
    """凑一批"像真的"样本，让信息列/类型徽标都有内容可显示。"""
    src = work / "src"
    src.mkdir(parents=True, exist_ok=True)

    pool = ROOT / "tests" / "_work"
    plan = [
        ("宣传片_4K.mp4", pool / "sample.mp4"),
        ("产品主图.png", pool / "sample.png"),
        ("季度质量报告.docx", pool / "sample.docx"),
        ("素材归档.zip", pool / "sample.zip"),
        ("数据表.csv", pool / "sample.csv"),
        ("说明书.txt", pool / "sample.txt"),
    ]
    made: list[Path] = []
    for name, origin in plan:
        target = src / name
        if origin.is_file():
            shutil.copyfile(origin, target)
            made.append(target)

    # 音频：优先拿真实 FLAC；没有就退化成 mp4 改名（只求视觉真实）
    flac = pool / "舍得_decr.flac"
    audio_src = flac if flac.is_file() else pool / "sample.mp4"
    if audio_src.is_file():
        for name in ("背景音乐.flac", "录音_会议.mp3"):
            target = src / name
            shutil.copyfile(audio_src, target)
            made.append(target)

    # 平台加密音乐：内容不必真能解开，界面只关心"识别出是哪个平台"
    for name in ("夜曲_周杰伦.ncm", "老歌.kwm", "乐队.kgm"):
        target = src / name
        if audio_src.is_file():
            shutil.copyfile(audio_src, target)
            made.append(target)

    return made


def _fabricate_states(win) -> None:
    """把一部分任务伪造成"已完成/失败/进行中"，让状态色与进度条都能看到。"""
    from app.core.jobs import JobStatus

    done = (0, 3, 5)
    running = (1, 6)
    failed = (7,)
    for idx in done:
        if idx < len(win.jobs):
            win.jobs[idx].status = JobStatus.DONE
            win.jobs[idx].progress = 1.0
            win.jobs[idx].elapsed = 3.4 + idx * 0.7
            win.jobs[idx].message = "完成"
    for idx in running:
        if idx < len(win.jobs):
            win.jobs[idx].status = JobStatus.RUNNING
            win.jobs[idx].progress = 0.62 if idx == 1 else 0.35
            win.jobs[idx].message = "编码中"
    for idx in failed:
        if idx < len(win.jobs):
            win.jobs[idx].status = JobStatus.FAILED
            win.jobs[idx].progress = 0.18
            win.jobs[idx].message = "源文件损坏，无法解码"
    for job in win.jobs:
        win._update_row(job)
    win._refresh_summary()
    win._update_total_progress()


def _register_fonts() -> None:
    """offscreen 平台不会枚举系统字体，必须手动注册，否则中文全变豆腐块。"""
    register_fonts()


def _apply_app_style(app) -> None:
    """复刻 app/main.py 里的初始化。

    少这一步，截出来的窗口是"没穿衣服"的：全局样式表只挂在 main() 里，
    如果直接 new MainWindow()，顶栏不会是深蓝、选中行会退回系统高亮色。
    """
    apply_app_style(app)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, help="输出 PNG 路径")
    parser.add_argument("--scenario", default="busy", choices=["busy", "empty", "done"])
    parser.add_argument("--scale", type=float, default=2.0, help="设备像素比（2 = 高清）")
    parser.add_argument("--w", type=int, default=1360)
    parser.add_argument("--h", type=int, default=880)
    args = parser.parse_args()

    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    if args.scale != 1:
        # 逻辑尺寸不变、物理像素翻倍，出图更锐利（不然 13px 中文字看不清）
        os.environ["QT_SCALE_FACTOR"] = str(args.scale)

    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    QApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    app = QApplication.instance() or QApplication(sys.argv)
    _register_fonts()
    _apply_app_style(app)

    from app.ui.main_window import MainWindow

    work = Path(tempfile.gettempdir()) / "fm_shot_ui"
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)
    (work / "out").mkdir(parents=True, exist_ok=True)

    win = MainWindow()
    win.resize(args.w, args.h)

    if args.scenario != "empty":
        win.add_paths(_demo_files(work))
        # 等后台探测把分辨率/时长填进"信息"列
        deadline = time.time() + 12
        while time.time() < deadline:
            app.processEvents()
            time.sleep(0.02)
            if all(j.info is not None or j.platform for j in win.jobs):
                break
        app.processEvents()
        if args.scenario == "busy":
            _fabricate_states(win)
        win.table.selectRow(2)

    win.show()
    for _ in range(6):
        app.processEvents()
        time.sleep(0.05)

    pixmap = win.grab()
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    pixmap.save(str(out), "PNG")
    print(f"已保存 {out}  {pixmap.width()}×{pixmap.height()}")

    win.scheduler.shutdown()
    win.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
