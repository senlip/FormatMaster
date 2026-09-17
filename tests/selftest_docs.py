"""文档转换自测：**内容正确性** 优先。

与 selftest.py 的分工：
* selftest.py 的文档用例只验"能转出 PDF、文件非空"——这正是 DOCX→PDF 乱码
  那次最大的盲区：产物是 34 页二进制垃圾也照样判 OK。
* 本文件专门盯"成功但内容是坏的"这一类：
  1. PDF 文本层必须**含源文档里的已知中文串**（编码坏就提取不出来）；
  2. 中文字体必须**嵌入**（``ext != 'n/a'``，否则换机器就掉成方框）；
  3. 不得出现替换符 / 私用区字符；
  4. 页数要合理（几十页垃圾以前就是这么冒出来的）；
  5. 临时文件（.stage / .subset）一个不剩。

样本全部自己造，并且**造完立刻验证样本本身是好的** ——
"样本坏会伪装成产品坏"这个坑在这个项目上已经踩过一次。
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.engines.base import ConvertOptions, ConvertResult          # noqa: E402
from app.core.engines.doc_engine import (                               # noqa: E402
    DocumentEngine,
    _binary_text_scavenge,
    _read_text,
    _rtf_to_text,
)

WORK = Path(tempfile.gettempdir()) / "fm_doc_test"

#: 每个样本里都埋这个串，转换后必须能在产物里找到
MARK = "氧化铝沉降槽底流密度在线检测"

PASSED = 0
FAILED = 0
FAILURES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if ok:
        PASSED += 1
        print(f"  [PASS] {name:<34} {detail}")
    else:
        FAILED += 1
        FAILURES.append(f"{name}: {detail}")
        print(f"  [FAIL] {name:<34} {detail}")


def banner(text: str) -> None:
    print(f"\n{'=' * 68}\n  {text}\n{'=' * 68}")


# --------------------------------------------------------------------------- #
# 样本制造（每个都自带"造完即验证"）
# --------------------------------------------------------------------------- #
def make_docx(path: Path) -> None:
    from docx import Document as DocxDocument
    from docx.oxml.ns import qn
    from docx.shared import Pt

    doc = DocxDocument()
    normal = doc.styles["Normal"]
    normal.font.name = "Times New Roman"
    normal.font.size = Pt(10.5)
    normal._element.rPr.rFonts.set(qn("w:eastAsia"), "宋体")
    doc.add_heading(f"技术报告：{MARK}", level=1)
    doc.add_paragraph("本报告基于 1590 批次东线氢氧化铝检测数据，涉及 8 项质量指标。")
    doc.add_paragraph("式中：ρ 为底流密度，单位 kg/m³；μ 为动力粘度。")
    doc.add_heading("关键工艺参数", level=2)
    table = doc.add_table(rows=3, cols=3)
    table.style = "Table Grid"
    for r, row in enumerate([["参数", "符号", "设计值"],
                             ["底流密度", "ρ", "1450 kg/m³"],
                             ["沉降速度", "v", "0.8 m/min"]]):
        for c, text in enumerate(row):
            table.cell(r, c).text = text
    doc.save(path)

    # 造完立刻验证：样本本身读得出中文，才算好样本
    check_text = "\n".join(p.text for p in DocxDocument(path).paragraphs)
    if MARK not in check_text:
        raise RuntimeError("docx 样本自身就是坏的（读不到中文）")


def make_rtf(path: Path) -> None:
    def esc(text: str) -> str:
        return "".join(f"\\u{ord(ch)}?" if ord(ch) > 127 else ch for ch in text)

    path.write_text(
        r"{\rtf1\ansi\ansicpg936\deff0"
        r"{\fonttbl{\f0\fnil\fcharset134 SimSun;}}"
        r"\f0\fs21 " + esc(MARK) + r"\par " + esc("第二行：测试文本") + "}",
        encoding="ascii",
    )
    parsed = _rtf_to_text(path.read_text(encoding="ascii"))
    if MARK not in parsed or "SimSun" in parsed:
        raise RuntimeError(f"rtf 样本自身解析不对：{parsed!r}")


def make_xlsx(path: Path) -> None:
    import openpyxl

    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.title = "东线指标"
    sheet.append([MARK])
    sheet.append(["批次号", "指标", "值"])
    sheet.append(["E0001", "Al2O3", 64.52])
    sheet.append(["E0002", "Na2O", 0.31])
    workbook.save(path)
    check_wb = openpyxl.load_workbook(path, read_only=True)
    try:
        rows = list(check_wb["东线指标"].iter_rows(values_only=True))
    finally:
        check_wb.close()
    if len(rows) != 4 or rows[0][0] != MARK or rows[1][0] != "批次号":
        raise RuntimeError("xlsx 样本自身就是坏的")


def make_md(path: Path) -> None:
    path.write_text(
        f"# {MARK}\n\n## 二级标题\n\n- 要点一\n- 要点二\n\n"
        "| 参数 | 值 |\n|---|---|\n| ρ | 1450 |\n",
        encoding="utf-8",
    )


def make_html(path: Path) -> None:
    path.write_text(
        "<!DOCTYPE html><html><head><meta charset='utf-8'></head><body>"
        f"<h1>{MARK}</h1><p>拜耳法工艺参数。</p>"
        "<table><tr><td>ρ</td><td>1450</td></tr></table></body></html>",
        encoding="utf-8",
    )


def make_txt(path: Path) -> None:
    path.write_text(f"{MARK}\n\n摘要：本文针对拜耳法生产中的在线检测问题。\n",
                    encoding="utf-8")


# --------------------------------------------------------------------------- #
# PDF 产物体检
# --------------------------------------------------------------------------- #
def pdf_report(path: Path) -> dict:
    import pymupdf as fitz

    with fitz.open(path) as doc:
        text = "\n".join(p.get_text() for p in doc)
        fonts = {f[3]: f[1] for p in doc for f in p.get_fonts(full=True)}
        return {
            "pages": doc.page_count,
            "text": text,
            "fonts": fonts,
            "unembedded": [n for n, ext in fonts.items() if ext == "n/a"],
            "bad_codepoints": sum(
                1 for ch in text if ch == "\ufffd" or 0xE000 <= ord(ch) <= 0xF8FF
            ),
            "size": path.stat().st_size,
        }


def run_case(engine, src: Path, dst: Path, mode: str, expect: str | None,
             max_pages: int = 40) -> tuple[bool, dict | None]:
    options = ConvertOptions(target_ext=dst.suffix.lstrip("."), overwrite=True,
                             doc_engine=mode)
    started = time.time()
    result = engine.convert(src, dst, options)
    elapsed = time.time() - started
    if not result.ok:
        print(f"    · {src.name} → {dst.suffix} [{mode}] 失败：{result.message[:100]}")
        return False, None
    if not dst.exists() or dst.stat().st_size == 0:
        print(f"    · {src.name} → {dst.suffix} [{mode}] 报告成功但产物为空")
        return False, None

    info = pdf_report(dst) if dst.suffix.lower() == ".pdf" else None
    summary = f"{dst.stat().st_size / 1024:8.1f} KB {elapsed:5.2f}s"
    if info:
        summary += f" 页={info['pages']:>3d} 字={len(info['text']):>5d} 未嵌字体={len(info['unembedded'])}"
    print(f"    · {src.name} → {dst.suffix} [{mode}] {summary}")

    if info is None:
        return True, None
    if expect and expect not in info["text"]:
        print(f"      × 文本层里找不到 {expect!r}")
        return False, info
    if info["unembedded"]:
        print(f"      × 中文字体未嵌入：{info['unembedded']}")
        return False, info
    if info["bad_codepoints"]:
        print(f"      × 文本层有 {info['bad_codepoints']} 个坏码点")
        return False, info
    if info["pages"] > max_pages:
        print(f"      × 页数异常（{info['pages']} > {max_pages}），疑似把二进制当文本排了")
        return False, info
    return True, info


# --------------------------------------------------------------------------- #
def main() -> int:
    global PASSED, FAILED
    banner("FormatMaster 文档转换自测")
    WORK.mkdir(parents=True, exist_ok=True)
    src_dir, out_dir = WORK / "src", WORK / "out"
    for d in (src_dir, out_dir):
        d.mkdir(parents=True, exist_ok=True)

    engine = DocumentEngine()
    mode = "高保真(COM)" if engine.com_ok() else "仅内置"
    print(f"  引擎：pyupdf={engine.pymupdf_ok()}  COM={engine.com_ok()}  → 优先 {mode}")
    print(f"  工作目录：{WORK}")

    # ---------------- 1. 样本 ---------------- #
    banner("1. 制造样本（并立刻验证样本本身可用）")
    samples: dict[str, Path] = {}
    makers = {"docx": make_docx, "rtf": make_rtf, "xlsx": make_xlsx,
              "md": make_md, "html": make_html, "txt": make_txt}
    for ext, maker in makers.items():
        path = src_dir / f"sample.{ext}"
        try:
            maker(path)
            samples[ext] = path
            check(f"样本 {ext} 生成且自检通过", True, f"{path.stat().st_size} 字节")
        except Exception as exc:
            check(f"样本 {ext} 生成且自检通过", False, str(exc)[:110])

    # ---------------- 2. 回归：曾经的致命 bug ---------------- #
    banner("2. 回归断言（都是这次修掉的真 bug）")
    docx = samples.get("docx")
    if docx:
        # 2.1 二进制文档绝不能被当纯文本读 —— 这是 34 页乱码的根因
        try:
            _read_text(docx)
            check("_read_text 拒绝二进制文档", False, "docx 被当成文本读了！")
        except ValueError:
            check("_read_text 拒绝二进制文档", True, "抛 ValueError ✓")

        # 2.2 抢救函数对老二进制要学会说"不行"
        fake_doc = src_dir / "ole_junk.doc"
        fake_doc.write_bytes(bytes(range(256)) * 40)
        try:
            _binary_text_scavenge(fake_doc)
            check("_binary_text_scavenge 拒绝垃圾", False, "把 ASCII 表当正文返回了")
        except ValueError:
            check("_binary_text_scavenge 拒绝垃圾", True, "抛 ValueError ✓")

    rtf = samples.get("rtf")
    if rtf:
        text = _rtf_to_text(rtf.read_text(encoding="ascii"))
        check("RTF 解析含中文且不漏字体表",
              MARK in text and "SimSun" not in text, repr(text[:48]))

    # ---------------- 3. 内置通道 ---------------- #
    banner("3. 内置通道（doc_engine=builtin，不依赖 Office）")
    for ext in ("docx", "rtf", "html", "md", "xlsx", "txt"):
        path = samples.get(ext)
        if not path:
            continue
        ok, info = run_case(engine, path, out_dir / f"builtin_{ext}.pdf",
                            "builtin", MARK)
        check(f"内置 {ext} → PDF 内容正确", ok)
        if ok and info:
            # 内置通道产物不该因为整体嵌入字体而爆掉
            check(f"内置 {ext} → PDF 体积正常", info["size"] < 2 * 1024 * 1024,
                  f"{info['size'] / 1024:.0f} KB")

    # ---------------- 4. 高保真通道 ---------------- #
    if engine.com_ok():
        banner("4. 高保真通道（Office COM）")
        for ext in ("docx", "rtf", "html", "md", "xlsx"):
            path = samples.get(ext)
            if not path:
                continue
            ok, _ = run_case(engine, path, out_dir / f"com_{ext}.pdf", "auto", MARK)
            check(f"高保真 {ext} → PDF 内容正确", ok)
    else:
        banner("4. 高保真通道")
        print("  · 本机没有可用的 Office/WPS，跳过（不算失败）")

    # ---------------- 5. 降级上报 ---------------- #
    banner("5. 降级路径必须如实上报")
    if docx:
        # 把 COM 派发打成必失败，验证降级说明会写进结果消息
        original = engine._com_dispatch

        def _always_fail(*_args, **_kwargs):
            return ConvertResult(False, message="模拟 COM 失败")

        engine._com_dispatch = _always_fail
        engine._com_failure = None
        options = ConvertOptions(target_ext="pdf", overwrite=True, doc_engine="auto")
        result = engine.convert(docx, out_dir / "fallback.pdf", options)
        engine._com_dispatch = original
        engine._com_failure = None
        ok = result.ok and "Office 高保真通道不可用" in result.message
        check("COM 失败时降级且说明原因", ok, result.message[:96])

    # ---------------- 6. 临时文件 ---------------- #
    banner("6. 临时文件清理")
    leftovers = sorted(
        list(out_dir.glob("*.stage.pdf")) + list(out_dir.glob("*.subset.pdf"))
        + list(WORK.rglob("*.stage.pdf")) + list(WORK.rglob("*.subset.pdf"))
    )
    check("无 .stage/.subset 残留", not leftovers,
          f"残留 {len(leftovers)} 个" if leftovers else "0 个 ✓")

    # ---------------- 汇总 ---------------- #
    banner("汇总")
    print(f"  合计 {PASSED + FAILED} 项：通过 {PASSED}，失败 {FAILED}")
    if FAILURES:
        print("  失败明细：")
        for item in FAILURES:
            print(f"    · {item}")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
