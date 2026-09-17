"""端到端自测：不启动界面，直接验证四个引擎是否真的能转换。

用法：
    python tests/selftest.py
会在 tests/_work 下生成测试素材并执行转换，最后打印结果表。
"""
from __future__ import annotations

import subprocess
import sys
import time
import zipfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.core.engines.base import ConvertOptions  # noqa: E402
from app.core.registry import get_registry  # noqa: E402
from app.core.runtime import environment_report, ffmpeg_path  # noqa: E402

WORK = Path(__file__).resolve().parent / "_work"


def banner(text: str) -> None:
    print(f"\n{'=' * 66}\n  {text}\n{'=' * 66}")


def make_assets() -> dict[str, Path]:
    WORK.mkdir(parents=True, exist_ok=True)
    ffmpeg = ffmpeg_path()
    assets: dict[str, Path] = {}

    video = WORK / "sample.mp4"
    if not video.exists():
        assert ffmpeg, "需要 FFmpeg 才能生成测试素材"
        subprocess.run(
            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=3",
             "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
             "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p",
             "-c:a", "aac", "-shortest", str(video)],
            check=True,
        )
    assets["video"] = video

    from PIL import Image

    image = WORK / "sample.png"
    if not image.exists():
        img = Image.new("RGB", (800, 600), (31, 54, 93))
        for x in range(0, 800, 40):
            for y in range(0, 600, 40):
                if (x // 40 + y // 40) % 2 == 0:
                    for px in range(x, min(x + 40, 800)):
                        for py in range(y, min(y + 40, 600)):
                            img.putpixel((px, py), (232, 237, 245))
        img.save(image)
    assets["image"] = image

    text = WORK / "sample.txt"
    if not text.exists():
        text.write_text(
            "氧化铝沉降槽底流密度在线检测与自动控制\n\n"
            "摘要：本文针对拜耳法氧化铝生产中沉降槽底流密度难以在线测量的问题，"
            "提出了一种基于差压变送器与温度补偿的软测量方法。\n\n"
            "关键词：沉降槽；底流密度；在线检测；自动控制；拜耳法\n",
            encoding="utf-8",
        )
    assets["text"] = text

    table = WORK / "sample.csv"
    if not table.exists():
        table.write_text(
            "批次号,Al2O3,SiO2,Fe2O3,Na2O,灼减\n"
            "E0001,64.52,0.021,0.018,0.31,34.2\n"
            "E0002,64.31,0.024,0.019,0.33,34.5\n"
            "E0003,64.68,0.019,0.017,0.30,33.9\n",
            encoding="utf-8",
        )
    assets["table"] = table

    archive = WORK / "sample.zip"
    if not archive.exists():
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.write(text, "readme.txt")
            zf.write(table, "data/sample.csv")
    assets["archive"] = archive

    docx_file = WORK / "sample.docx"
    if not docx_file.exists():
        from docx import Document as DocxDocument

        document = DocxDocument()
        document.add_heading("氧化铝沉降槽底流密度在线检测与自动控制", level=1)
        document.add_paragraph(
            "摘要：本文针对拜耳法氧化铝生产中沉降槽底流密度难以在线测量的问题，"
            "提出了一种基于差压变送器与温度补偿的软测量方法，并给出了控制器整定步骤。"
        )
        document.add_paragraph("关键词：沉降槽；底流密度；在线检测；自动控制；拜耳法")
        grid = document.add_table(rows=3, cols=3)
        grid.style = "Table Grid"
        for r, values in enumerate(
            [["批次号", "Al2O3", "Na2O"], ["E0001", "64.52", "0.31"], ["E0002", "64.31", "0.33"]]
        ):
            for c, value in enumerate(values):
                grid.cell(r, c).text = value
        document.save(docx_file)
    assets["docx"] = docx_file

    return assets


CASES = [
    # 视频容器互转——这些最容易踩"容器不认编码器"的坑
    ("video", "mp4", "mkv", "视频转封装 MKV"),
    ("video", "mp4", "avi", "视频转 AVI"),
    ("video", "mp4", "mov", "视频转 MOV"),
    ("video", "mp4", "webm", "视频转 WebM(VP9)"),
    ("video", "mp4", "wmv", "视频转 WMV"),
    ("video", "mp4", "flv", "视频转 FLV"),
    ("video", "mp4", "mpg", "视频转 MPEG-PS"),
    ("video", "mp4", "ogv", "视频转 OGV"),
    ("video", "mp4", "ts", "视频转 TS"),
    ("video", "mp4", "3gp", "视频转 3GP"),
    ("video", "mp4", "gif", "视频转动图 GIF"),
    # 提取音轨
    ("video", "mp4", "mp3", "提取 MP3"),
    ("video", "mp4", "wav", "提取 WAV"),
    ("video", "mp4", "flac", "提取 FLAC"),
    ("video", "mp4", "m4a", "提取 M4A"),
    ("video", "mp4", "opus", "提取 OPUS"),
    # 图像
    ("image", "png", "jpg", "图像转 JPG"),
    ("image", "png", "webp", "图像转 WebP"),
    ("image", "png", "bmp", "图像转 BMP"),
    ("image", "png", "tiff", "图像转 TIFF"),
    ("image", "png", "ico", "图像转 ICO"),
    ("image", "png", "pdf", "图像转 PDF"),
    # 文档
    ("text", "txt", "pdf", "文本排版 PDF"),
    ("text", "txt", "html", "文本转 HTML"),
    ("table", "csv", "xlsx", "CSV 转 Excel"),
    ("table", "csv", "html", "CSV 转 HTML"),
    ("docx", "docx", "pdf", "DOCX→PDF 高保真"),
    ("docx", "docx", "txt", "DOCX→TXT"),
    # 压缩包
    ("archive", "zip", "7z", "重打包 7Z"),
    ("archive", "zip", "tar", "重打包 TAR"),
]


def main() -> int:
    banner("FormatMaster 引擎自测")
    print("引擎环境：")
    for item in environment_report():
        mark = "OK  " if item["ok"] else "MISS"
        print(f"  [{mark}] {item['name']:<20} {item['path']}")

    registry = get_registry()
    registry.health()
    assets = make_assets()

    outdir = WORK / "out"
    outdir.mkdir(parents=True, exist_ok=True)

    banner("执行转换")
    passed = failed = 0
    failures: list[str] = []

    for key, src_ext, dst_ext, label in CASES:
        src: Path = assets[key]
        engine = registry.route(src_ext, dst_ext)
        if engine is None:
            print(f"  [SKIP] {label:<22} {src_ext} → {dst_ext}   无可用引擎")
            failed += 1
            failures.append(f"{label}: 无可用引擎")
            continue

        dst = outdir / f"{key}_to_{dst_ext}.{dst_ext}"
        options = ConvertOptions(target_ext=dst_ext)
        if dst_ext in ("mp3", "wav", "m4a", "flac"):
            options.quality = "balanced"

        started = time.time()
        result = engine.convert(src, dst, options)
        elapsed = time.time() - started
        size = dst.stat().st_size if dst.exists() else 0

        if result.ok and size > 0:
            passed += 1
            print(f"  [ OK ] {label:<22} {src_ext} → {dst_ext:<5} "
                  f"{size / 1024:>9.1f} KB  {elapsed:5.2f}s  ({engine.name})")
        else:
            failed += 1
            failures.append(f"{label}: {result.message[:120]}")
            print(f"  [FAIL] {label:<22} {src_ext} → {dst_ext:<5} {result.message[:110]}")

    # ---- 内容正确性专项 ---- #
    # 只验"转出来了"是不够的：DOCX→PDF 曾经输出 34 页二进制垃圾照样判 OK，
    # 所以这里额外验文本层里的中文对不对、字体有没有嵌入。
    banner("内容正确性校验（防「成功但内容是乱的」）")
    docx_pdf = outdir / "docx_to_pdf.pdf"
    if not docx_pdf.exists():
        print("  [SKIP] 没有 docx→pdf 产物，跳过")
    else:
        try:
            import pymupdf as fitz
        except ImportError:
            print("  [SKIP] 未安装 PyMuPDF，无法校验文本层")
        else:
            with fitz.open(docx_pdf) as doc:
                text = "\n".join(p.get_text() for p in doc)
                fonts = {f[3]: f[1] for p in doc for f in p.get_fonts(full=True)}
                pages = doc.page_count

            ok = "底流密度在线检测" in text
            passed += ok
            failed += not ok
            if not ok:
                failures.append(f"DOCX→PDF 文本层缺少已知中文串（提取到 {text[:40]!r}）")
            print(f"  [{'PASS' if ok else 'FAIL'}] DOCX→PDF 文本层含已知中文   "
                  f"{len(text)} 字 / {pages} 页")

            unembedded = [n for n, ext in fonts.items() if ext == "n/a"]
            ok = bool(fonts) and not unembedded
            passed += ok
            failed += not ok
            if not ok:
                failures.append(f"DOCX→PDF 中文字体未嵌入：{unembedded or '（没有字体信息）'}")
            print(f"  [{'PASS' if ok else 'FAIL'}] DOCX→PDF 字体已嵌入          "
                  f"{len(fonts)} 种字体，未嵌入 {len(unembedded)} 种")

            ok = pages <= 40
            passed += ok
            failed += not ok
            if not ok:
                failures.append(f"DOCX→PDF 页数异常：{pages} 页（疑似把二进制当文本排版）")
            print(f"  [{'PASS' if ok else 'FAIL'}] DOCX→PDF 页数合理            {pages} 页")

    banner("汇总")
    print(f"  通过 {passed} / {passed + failed}")
    if failures:
        print("  失败明细：")
        for item in failures:
            print(f"    · {item}")

    produced = sorted(outdir.iterdir())
    if produced:
        print(f"\n  产物目录 {outdir}：")
        for path in produced:
            print(f"    {path.name:<28} {path.stat().st_size / 1024:>9.1f} KB")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
