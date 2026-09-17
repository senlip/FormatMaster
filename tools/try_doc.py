"""文档转换手动试跑：走完整 DocumentEngine，打印通道 + 产物体检报告。

用法：

    PY="C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
    $PY tools/try_doc.py 源.docx 目标.pdf                    # 自动择优通道
    $PY tools/try_doc.py 源.docx 目标.pdf --engine builtin   # 强制内置通道
    $PY tools/try_doc.py 源.docx 目标.pdf --expect "氧化铝"  # 断言中文没坏

产物体检（针对 PDF）：
  1. get_text() —— 文本层能不能正确提取（编码坏 = 乱）
  2. get_fonts() —— 中文字体是否嵌入（ext == 'n/a' = 没嵌入，换机器必掉字）
  3. --expect 指定串必须在文本里出现，否则退出码 1
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.core.engines.base import ConvertOptions                      # noqa: E402
from app.core.engines.doc_engine import DocumentEngine                # noqa: E402


def inspect_pdf(path: Path, expect: str | None) -> bool:
    try:
        import pymupdf as fitz
    except ImportError:
        import fitz

    print("-" * 68)
    print("产物体检")
    with fitz.open(path) as doc:
        pages = doc.page_count
        print(f"  页数            : {pages}")
        text = "\n".join(p.get_text() for p in doc)
        print(f"  文本层字符数    : {len(text)}")
        print(f"  文本层前 120 字 : {text[:120]!r}")

        fonts: dict[str, str] = {}
        for p in doc:
            for f in p.get_fonts(full=True):
                # (xref, ext, type, basefont, name, encoding, ...)
                fonts[f[3]] = f[1]
        print(f"  字体（{len(fonts)} 种）:")
        embedded_any = False
        for name, ext in sorted(fonts.items()):
            mark = "× 未嵌入" if ext == "n/a" else f"✓ 已嵌入({ext})"
            if ext != "n/a":
                embedded_any = True
            print(f"    - {name:42s} {mark}")
        if not embedded_any and fonts:
            print("    ← 全部字体未嵌入：换机器/换阅读器就会掉字成方框")

        ok = True
        if expect:
            hit = expect in text
            print(f"  断言 expect={expect!r} : {'✓ 命中' if hit else '× 缺失'}")
            ok = ok and hit

        # 文本层里若出现大量替换符/私用区字符，说明编码坏了
        bad = sum(1 for ch in text if ch == "\ufffd" or 0xE000 <= ord(ch) <= 0xF8FF)
        print(f"  坏码点(替换符/私用区): {bad}")
        if bad > 0:
            ok = False
    return ok


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("src", help="源文件")
    ap.add_argument("dst", help="目标文件")
    ap.add_argument("--engine", default="auto", choices=["auto", "office", "builtin"])
    ap.add_argument("--quality", default="balanced", choices=["high", "balanced", "small"])
    ap.add_argument("--expect", help="PDF 文本层必须包含的字符串")
    ap.add_argument("--no-inspect", action="store_true", help="跳过产物体检")
    args = ap.parse_args()

    src, dst = Path(args.src), Path(args.dst)
    if not src.is_file():
        print(f"× 源文件不存在：{src}")
        return 2

    eng = DocumentEngine()
    src_ext = src.suffix.lstrip(".").lower()
    dst_ext = dst.suffix.lstrip(".").lower()

    print("=" * 68)
    print(f"源     : {src}  ({src.stat().st_size} 字节)")
    print(f"目标   : {dst}")
    print(f"依赖   : com_ok={eng.com_ok()}  pymupdf_ok={eng.pymupdf_ok()}")
    print(f"路由   : {eng.describe(src_ext, dst_ext)}")
    print(f"doc_engine 选项 = {args.engine}")
    print("=" * 68)

    opts = ConvertOptions(target_ext=dst_ext, overwrite=True,
                          quality=args.quality, doc_engine=args.engine)

    lines: list[str] = []

    def on_progress(ratio: float, message: str) -> None:
        if not lines or lines[-1] != message:
            lines.append(message)
            print(f"  [{ratio * 100:5.1f}%] {message}")

    t0 = time.time()
    res = eng.convert(src, dst, opts, on_progress=on_progress)
    elapsed = time.time() - t0

    print("-" * 68)
    print(f"结果   : {'✓ 成功' if res.ok else '× 失败'}  耗时 {elapsed:.2f}s")
    print(f"消息   : {res.message}")
    if res.output:
        print(f"输出   : {res.output}")
    if not res.ok:
        return 1
    if not dst.is_file():
        print("× 报告成功但产物不存在")
        return 1
    print(f"产物大小: {dst.stat().st_size} 字节")

    if args.no_inspect or dst_ext != "pdf":
        return 0
    return 0 if inspect_pdf(dst, args.expect) else 1


if __name__ == "__main__":
    sys.exit(main())
