"""文档引擎（双通道）。

通道 A「高保真」：调用本机 Office / WPS 的 COM 接口。
    几乎能完成任意 Office 格式 -> PDF / Office 互转，版式 100% 保留。
通道 B「内置」：纯 Python（PyMuPDF + python-docx + openpyxl + mammoth + markdown）。
    不依赖任何外部软件，覆盖 PDF 与图片/文本之间的转换。

引擎自动择优：能用高保真就用，否则退回内置，并如实告知用户。
"""
from __future__ import annotations

import csv
import gc
import html as html_mod
import io
import os
import re
import shutil
import tempfile
import threading
from pathlib import Path

from ..formats import Kind
from ..runtime import office_com_available
from .base import BaseEngine, ConvertOptions, ConvertResult, MediaInfo, ProgressFn

try:                           # PyMuPDF 1.24 起推荐使用 pymupdf 作为模块名
    import pymupdf as _pymupdf
except ImportError:            # 兼容旧版本与部分发行版
    try:
        import fitz as _pymupdf
    except ImportError:
        _pymupdf = None


def _require_pymupdf():
    if _pymupdf is None:
        raise RuntimeError("未安装 PyMuPDF，请执行 pip install pymupdf")
    return _pymupdf


IMAGE_EXTS = {"jpg", "jpeg", "png", "bmp", "tiff", "tif", "webp"}
OFFICE_TEXT_EXTS = {"doc", "docx", "rtf", "odt"}
OFFICE_SHEET_EXTS = {"xls", "xlsx", "ods"}
OFFICE_SLIDE_EXTS = {"ppt", "pptx", "odp"}

#: 每种源格式可用的目标格式（内置通道）
_BUILTIN_ROUTES: dict[str, set[str]] = {
    "pdf": {"pdf", "png", "jpg", "tiff", "txt", "html", "docx"},
    "docx": {"docx", "txt", "html", "md", "pdf", "rtf", "odt"},
    "doc": {"txt", "html", "pdf", "docx"},
    "odt": {"txt", "html", "pdf", "docx"},
    "rtf": {"txt", "html", "pdf", "docx"},
    "xlsx": {"xlsx", "csv", "html", "pdf", "txt"},
    "xls": {"csv", "html", "pdf", "xlsx"},
    "csv": {"csv", "xlsx", "html", "pdf", "txt"},
    "pptx": {"pdf", "png", "jpg"},
    "txt": {"txt", "html", "pdf", "md"},
    "md": {"md", "html", "pdf", "txt"},
    "html": {"html", "txt", "pdf", "md"},
    "epub": {"txt", "html", "pdf"},
    "mobi": {"txt", "html"},
}
_BUILTIN_ROUTES["jpg"] = {"pdf"}
_BUILTIN_ROUTES["jpeg"] = {"pdf"}
_BUILTIN_ROUTES["png"] = {"pdf"}
_BUILTIN_ROUTES["bmp"] = {"pdf"}
_BUILTIN_ROUTES["tiff"] = {"pdf"}
_BUILTIN_ROUTES["tif"] = {"pdf"}
_BUILTIN_ROUTES["webp"] = {"pdf"}

#: 二进制文档容器。**绝不能**按编码当"纯文本"读 ——
#: 早先 docx→pdf 的兜底路径就是这么把 DOCX 的 ZIP 流排成 34 页乱码的。
_BINARY_DOC_EXTS = frozenset({
    "pdf", "doc", "docx", "docm", "dot", "dotx", "odt", "rtf", "epub", "mobi",
    "ppt", "pptx", "pps", "xls", "xlsx", "xlsm", "ods",
})

#: 能用 mammoth 直接转成 HTML 的容器（保留标题层级 / 列表 / 表格）
_MAMMOTH_EXTS = frozenset({"docx", "docm", "dotx", "odt", "epub"})

#: PDF 版面：A4 @72dpi，页边距 56pt
_PAGE_W, _PAGE_H = 595, 842
_PAGE_MARGIN = 56

#: HTML 排版的页数上限：正常文档远到不了，纯粹防坏输入把内存吃爆
_MAX_PDF_PAGES = 3000

#: 另存为目标格式时必须**显式**给 FileFormat，否则 Office 会按自己的猜测写：
#: 实测 `doc.SaveAs2("x.txt")` 不带 FileFormat，Word 会写出一个 RTF 文件，
#: 扩展名却是 .txt —— 又是一个"成功但内容是错的"。
_WD_FORMAT = {"doc": 0, "txt": 2, "rtf": 6, "html": 8, "htm": 8,
              "docx": 12, "pdf": 17, "odt": 23}
_XL_FORMAT = {"xlsx": 51, "xls": 56, "csv": 6, "txt": -4158, "html": 44, "pdf": 57}
_PP_FORMAT = {"pptx": 24, "ppt": 1, "pdf": 32}

#: 内置 HTML 排版用的 CSS；字体族由 _cjk_font_plan() 动态填进 @font-face
_MARKUP_CSS = """
body {{ font-family: {family}; font-size: 10.5pt; line-height: 1.6;
        margin: 0; color: #1a1a1a; }}
h1 {{ font-size: 17pt; margin: 0 0 10pt 0; }}
h2 {{ font-size: 14pt; margin: 14pt 0 6pt 0; }}
h3 {{ font-size: 12pt; margin: 12pt 0 5pt 0; }}
h4, h5, h6 {{ font-size: 11pt; margin: 10pt 0 4pt 0; }}
p {{ margin: 0 0 7pt 0; }}
ul, ol {{ margin: 0 0 7pt 0; padding-left: 20pt; }}
table {{ border-collapse: collapse; margin: 6pt 0 10pt 0; }}
td, th {{ border: 0.6pt solid #9a9a9a; padding: 3pt 6pt; }}
img {{ max-width: 100%; }}
code, pre {{ font-size: 9.5pt; }}
pre {{ white-space: pre-wrap; margin: 0 0 7pt 0; }}
"""


class DocumentEngine(BaseEngine):
    name = "文档引擎"
    kinds = frozenset({Kind.DOCUMENT})
    unavailable_hint = "缺少文档处理依赖"

    def __init__(self) -> None:
        super().__init__()
        self._com_ok: bool | None = None
        self._pymupdf_ok: bool | None = None
        #: 上一次 COM 尝试的失败原因；有值说明"高保真"实际走不通
        self._com_failure: str | None = None

    # ------------------------------------------------------------------ #
    def _probe_available(self) -> bool:
        return self.pymupdf_ok() or self.com_ok()

    def pymupdf_ok(self) -> bool:
        return _pymupdf is not None

    def com_ok(self) -> bool:
        if self._com_ok is None:
            self._com_ok = office_com_available()
        return self._com_ok

    # ------------------------------------------------------------------ #
    def handles(self, src_ext: str, dst_ext: str) -> bool:
        src_ext, dst_ext = src_ext.lower(), dst_ext.lower()
        if src_ext == dst_ext:
            return False
        if self._com_channel(src_ext, dst_ext) and self.com_ok():
            return True
        return dst_ext in _BUILTIN_ROUTES.get(src_ext, set())

    def describe(self, src_ext: str, dst_ext: str) -> str:
        src_ext, dst_ext = src_ext.lower(), dst_ext.lower()
        if self._com_channel(src_ext, dst_ext) and self.com_ok() and not self._com_failure:
            return f"文档转换（高保真）{src_ext.upper()} → {dst_ext.upper()}"
        return f"文档转换（内置）{src_ext.upper()} → {dst_ext.upper()}"

    @staticmethod
    def _com_channel(src_ext: str, dst_ext: str) -> bool:
        """COM 通道能处理哪些组合。"""
        office = OFFICE_TEXT_EXTS | OFFICE_SHEET_EXTS | OFFICE_SLIDE_EXTS
        if src_ext in office and dst_ext in office | {"pdf", "txt", "csv", "html"}:
            return not (src_ext == dst_ext)
        if src_ext == "pdf" and dst_ext in office:
            return True
        # 纯文本与表格交内置排版更快：COM 启动一次 Office 要 2 秒以上，不划算
        if src_ext in ("html", "md") and dst_ext == "pdf":
            return True
        return False

    # ------------------------------------------------------------------ #
    def probe(self, path: Path) -> MediaInfo:
        ext = path.suffix.lstrip(".").lower()
        info = MediaInfo(path=path, ext=ext, kind=Kind.DOCUMENT)
        try:
            info.size = path.stat().st_size
        except OSError:
            pass
        if ext == "pdf" and self.pymupdf_ok():
            try:
                fitz = _require_pymupdf()

                with fitz.open(path) as doc:
                    pages = doc.page_count
                    info.video_codec = f"PDF {pages} 页"
                    info.audio_codec = f"{pages}p"
                    if pages:
                        rect = doc[0].rect
                        info.width, info.height = int(rect.width), int(rect.height)
            except Exception as exc:
                info.error = str(exc)
        return info

    # ------------------------------------------------------------------ #
    def convert(
        self,
        src: Path,
        dst: Path,
        options: ConvertOptions,
        on_progress: ProgressFn | None = None,
        cancel: threading.Event | None = None,
    ) -> ConvertResult:
        src_ext = src.suffix.lstrip(".").lower()
        dst_ext = dst.suffix.lstrip(".").lower()
        dst.parent.mkdir(parents=True, exist_ok=True)

        want_com = (
            options.doc_engine != "builtin"
            and self._com_channel(src_ext, dst_ext)
            and self.com_ok()
        )
        com_note = ""
        if want_com:
            result = self._convert_via_com(src, dst, src_ext, dst_ext, on_progress)
            if result.ok:
                return result
            self._com_failure = result.message
            if options.doc_engine == "office":
                return result
            # COM 失败时尝试内置通道兜底——但**必须如实告诉用户走了哪条路**，
            # 不能拿一个降级产物冒充"高保真成功"。
            if dst_ext in _BUILTIN_ROUTES.get(src_ext, set()):
                com_note = f"；Office 高保真通道不可用（{_short(result.message)}），已改用内置排版"
                if on_progress:
                    on_progress(0.0, "Office 通道不可用，改用内置排版")
            else:
                return result

        handler = self._resolve_handler(src_ext, dst_ext)
        if handler is None:
            hint = "（装了 WPS / Office 后可支持该组合）" if not self.com_ok() else ""
            return ConvertResult(
                False, message=f"暂不支持 {src_ext.upper()} → {dst_ext.upper()}{hint}"
            )

        try:
            out = handler(src, dst, options, on_progress, cancel)
        except Exception as exc:
            out = ConvertResult(False, message=f"文档转换失败：{exc}")
        if com_note:
            # 降级路径也要如实上报：成功就说走了哪条路，
            # 失败就把两条通道的原因**都**给出来，不然用户只看到一半真相。
            out = ConvertResult(
                out.ok,
                output=out.output,
                message=out.message + com_note,
                elapsed=out.elapsed,
                cancelled=out.cancelled,
            )
        return out

    def _resolve_handler(self, src_ext: str, dst_ext: str):
        table = {
            ("pdf", "png"): self._pdf_to_image,
            ("pdf", "jpg"): self._pdf_to_image,
            ("pdf", "tiff"): self._pdf_to_image,
            ("pdf", "txt"): self._pdf_to_text,
            ("pdf", "html"): self._pdf_to_html,
            ("pdf", "docx"): self._pdf_to_docx,
            ("xlsx", "csv"): self._sheet_to_csv,
            ("xls", "csv"): self._sheet_to_csv,
            ("xlsx", "html"): self._sheet_to_html,
            ("xlsx", "txt"): self._sheet_to_csv,
            ("csv", "xlsx"): self._csv_to_xlsx,
            ("csv", "html"): self._csv_to_html,
            ("csv", "txt"): self._copy_as_text,
            ("docx", "txt"): self._docx_to_text,
            ("docx", "md"): self._docx_to_text,
            ("docx", "html"): self._docx_to_html,
            ("doc", "txt"): self._docx_to_text,
            ("odt", "txt"): self._docx_to_text,
            ("rtf", "txt"): self._docx_to_text,
            ("html", "txt"): self._html_to_text,
            ("html", "md"): self._html_to_text,
            ("md", "html"): self._md_to_html,
            ("md", "txt"): self._copy_as_text,
            ("txt", "html"): self._text_to_html,
            ("txt", "md"): self._copy_as_text,
            ("txt", "pdf"): self._text_to_pdf,
            ("csv", "pdf"): self._text_to_pdf,
            # 结构化文档 → PDF：保留标题/列表/表格，并嵌入中文字体
            ("docx", "pdf"): self._document_to_pdf,
            ("doc", "pdf"): self._document_to_pdf,
            ("odt", "pdf"): self._document_to_pdf,
            ("rtf", "pdf"): self._document_to_pdf,
            ("epub", "pdf"): self._document_to_pdf,
            ("html", "pdf"): self._document_to_pdf,
            ("md", "pdf"): self._document_to_pdf,
            ("epub", "txt"): self._docx_to_text,
            ("epub", "html"): self._docx_to_html,
        }
        if (src_ext, dst_ext) in table:
            return table[(src_ext, dst_ext)]
        if src_ext in IMAGE_EXTS and dst_ext == "pdf":
            return self._images_to_pdf
        if dst_ext == "pdf":
            return self._generic_to_pdf
        if src_ext == "pdf":
            return self._pdf_to_text
        return None

    # ================= 内置通道实现 ================= #
    def _pdf_to_image(self, src, dst, options, on_progress, cancel):
        fitz = _require_pymupdf()
        from PIL import Image

        ext = dst.suffix.lstrip(".").lower()
        dpi = {"high": 220, "balanced": 150, "small": 96}.get(options.quality, 150)
        with fitz.open(src) as doc:
            total = doc.page_count
            if total == 0:
                return ConvertResult(False, message="PDF 没有页面")
            images: list[Image.Image] = []
            for idx, page in enumerate(doc):
                if cancel is not None and cancel.is_set():
                    return ConvertResult(False, message="已取消", cancelled=True)
                pix = page.get_pixmap(dpi=dpi, alpha=False)
                img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                if total > 1:
                    img.save(_page_path(dst, idx + 1), quality=90)
                images.append(img)
                if on_progress:
                    on_progress(0.1 + 0.85 * (idx + 1) / total, f"第 {idx + 1}/{total} 页")
            if total == 1:
                images[0].save(dst, **({"quality": 90} if ext in ("jpg", "jpeg") else {}))
        if on_progress:
            on_progress(1.0, "完成")
        return ConvertResult(True, output=dst, message="完成")

    def _pdf_to_text(self, src, dst, options, on_progress, cancel):
        fitz = _require_pymupdf()

        with fitz.open(src) as doc:
            total = doc.page_count
            chunks = []
            for idx, page in enumerate(doc):
                if cancel is not None and cancel.is_set():
                    return ConvertResult(False, message="已取消", cancelled=True)
                chunks.append(page.get_text())
                if on_progress:
                    on_progress(0.1 + 0.85 * (idx + 1) / max(1, total), f"第 {idx + 1}/{total} 页")
            text = "\f".join(chunks)
        dst.write_text(text, encoding="utf-8")
        if on_progress:
            on_progress(1.0, "完成")
        return ConvertResult(True, output=dst, message="完成", elapsed=0.0)

    def _pdf_to_html(self, src, dst, options, on_progress, cancel):
        fitz = _require_pymupdf()

        with fitz.open(src) as doc:
            parts = []
            total = doc.page_count
            for idx, page in enumerate(doc):
                if cancel is not None and cancel.is_set():
                    return ConvertResult(False, message="已取消", cancelled=True)
                parts.append(f'<section class="page"><pre>{html_mod.escape(page.get_text())}</pre></section>')
                if on_progress:
                    on_progress(0.1 + 0.85 * (idx + 1) / max(1, total), "提取文本")
        body = "\n".join(parts)
        dst.write_text(
            "<!DOCTYPE html><meta charset='utf-8'><title>PDF 转换结果</title>"
            "<style>body{font-family:system-ui,sans-serif;line-height:1.7;margin:40px auto;max-width:820px}"
            ".page{border-bottom:1px solid #eee;padding:16px 0}pre{white-space:pre-wrap;font-family:inherit}</style>"
            + body,
            encoding="utf-8",
        )
        if on_progress:
            on_progress(1.0, "完成")
        return ConvertResult(True, output=dst, message="完成")

    def _pdf_to_docx(self, src, dst, options, on_progress, cancel):
        try:
            from pdf2docx import Converter
        except ImportError:
            return ConvertResult(False, message="未安装 pdf2docx，无法 PDF → DOCX")
        cv = Converter(str(src))
        try:
            cv.convert(str(dst))
        finally:
            cv.close()
        if on_progress:
            on_progress(1.0, "完成")
        return ConvertResult(True, output=dst, message="完成")

    def _images_to_pdf(self, src, dst, options, on_progress, cancel):
        fitz = _require_pymupdf()
        from PIL import Image

        with Image.open(src) as im:
            if im.mode in ("RGBA", "LA", "P"):
                im = im.convert("RGBA")
                canvas = Image.new("RGB", im.size, (255, 255, 255))
                canvas.paste(im, mask=im.split()[-1])
                im = canvas
            else:
                im = im.convert("RGB")
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=95)
            data = buf.getvalue()
            w, h = im.size

        doc = fitz.open()
        page = doc.new_page(width=w, height=h)
        page.insert_image(fitz.Rect(0, 0, w, h), stream=data)
        doc.save(str(dst))
        doc.close()
        if on_progress:
            on_progress(1.0, "完成")
        return ConvertResult(True, output=dst, message="完成")

    def _docx_to_text(self, src, dst, options, on_progress, cancel):
        ext = src.suffix.lstrip(".").lower()
        if ext in ("docx", "docm", "dotx", "odt", "epub"):
            try:
                import mammoth

                with open(src, "rb") as fh:
                    text = mammoth.extract_raw_text(fh).value
            except Exception:
                text = _docx_xml_text(src)
        elif ext == "rtf":
            text = _rtf_to_text(_read_rtf(src))
        elif ext in ("xlsx", "xlsm"):
            text = re.sub(r"<[^>]+>", "\t", _sheet_html(src))
            text = re.sub(r"\t+", "\t", text).strip()
        else:
            # .doc / .xls / .ppt 这类老二进制：救不出来就明确报错（不产乱码）
            try:
                text = _binary_text_scavenge(src)
            except ValueError as exc:
                return ConvertResult(False, message=str(exc))
        dst.write_text(text, encoding="utf-8")
        if on_progress:
            on_progress(1.0, "完成")
        return ConvertResult(True, output=dst, message="完成")

    def _docx_to_html(self, src, dst, options, on_progress, cancel):
        try:
            import mammoth

            with open(src, "rb") as fh:
                result = mammoth.convert_to_html(fh)
            body = result.value
        except Exception as exc:
            return ConvertResult(False, message=f"DOCX 解析失败：{exc}")
        dst.write_text(
            "<!DOCTYPE html><meta charset='utf-8'><title>文档转换结果</title>"
            "<style>body{font-family:system-ui,sans-serif;line-height:1.75;margin:40px auto;max-width:820px;padding:0 20px}"
            "img{max-width:100%}table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:6px 10px}</style>"
            + body,
            encoding="utf-8",
        )
        if on_progress:
            on_progress(1.0, "完成")
        return ConvertResult(True, output=dst, message="完成")

    def _sheet_to_csv(self, src, dst, options, on_progress, cancel):
        ext = src.suffix.lstrip(".").lower()
        rows: list[list[str]] = []
        if ext in ("xlsx", "xlsm"):
            try:
                import openpyxl

                wb = openpyxl.load_workbook(src, read_only=True, data_only=True)
                ws = wb.active
                for row in ws.iter_rows(values_only=True):
                    rows.append(["" if c is None else str(c) for c in row])
                wb.close()
            except Exception as exc:
                return ConvertResult(False, message=f"表格读取失败：{exc}")
        else:
            return ConvertResult(False, message=f"内置通道不支持 .{ext} 表格")

        _write_csv(dst, rows)
        if on_progress:
            on_progress(1.0, "完成")
        return ConvertResult(True, output=dst, message="完成")

    def _sheet_to_html(self, src, dst, options, on_progress, cancel):
        ext = src.suffix.lstrip(".").lower()
        if ext not in ("xlsx", "xlsm"):
            return ConvertResult(False, message=f"内置通道不支持 .{ext} 表格")
        import openpyxl

        wb = openpyxl.load_workbook(src, read_only=True, data_only=True)
        ws = wb.active
        buf = ['<table>']
        for row in ws.iter_rows(values_only=True):
            buf.append("<tr>" + "".join(
                f"<td>{html_mod.escape('' if c is None else str(c))}</td>" for c in row
            ) + "</tr>")
        buf.append("</table>")
        wb.close()
        dst.write_text(
            "<!DOCTYPE html><meta charset='utf-8'><title>表格转换结果</title>"
            "<style>body{font-family:system-ui,sans-serif;margin:40px auto;max-width:1000px}"
            "table{border-collapse:collapse;width:100%}td{border:1px solid #ddd;padding:6px 10px;font-size:13px}</style>"
            + "\n".join(buf),
            encoding="utf-8",
        )
        if on_progress:
            on_progress(1.0, "完成")
        return ConvertResult(True, output=dst, message="完成")

    def _csv_to_xlsx(self, src, dst, options, on_progress, cancel):
        import openpyxl
        from openpyxl.styles import Alignment, Font, PatternFill

        rows = _read_csv(src)
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Sheet1"
        for r_i, row in enumerate(rows, start=1):
            for c_i, value in enumerate(row, start=1):
                cell = ws.cell(row=r_i, column=c_i, value=_coerce(value))
                if r_i == 1:
                    cell.font = Font(bold=True, color="FFFFFF")
                    cell.fill = PatternFill("solid", fgColor="1F365D")
                    cell.alignment = Alignment(horizontal="center", vertical="center")
        for col in ws.columns:
            width = max((len(str(c.value)) if c.value is not None else 0) for c in col)
            ws.column_dimensions[col[0].column_letter].width = min(40, max(10, width + 4))
        wb.save(dst)
        if on_progress:
            on_progress(1.0, "完成")
        return ConvertResult(True, output=dst, message="完成")

    def _csv_to_html(self, src, dst, options, on_progress, cancel):
        rows = _read_csv(src)
        body = "\n".join(
            "<tr>" + "".join(f"<td>{html_mod.escape(c)}</td>" for c in row) + "</tr>" for row in rows
        )
        dst.write_text(
            "<!DOCTYPE html><meta charset='utf-8'><title>CSV 转换结果</title>"
            "<style>body{font-family:system-ui,sans-serif;margin:40px auto;max-width:1000px}"
            "table{border-collapse:collapse;width:100%}td{border:1px solid #ddd;padding:6px 10px;font-size:13px}</style>"
            f"<table>{body}</table>",
            encoding="utf-8",
        )
        if on_progress:
            on_progress(1.0, "完成")
        return ConvertResult(True, output=dst, message="完成")

    def _html_to_text(self, src, dst, options, on_progress, cancel):
        raw = _read_text(src)
        text = re.sub(r"(?is)<(script|style).*?</\1>", "", raw)
        text = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</h[1-6]>", "\n", text)
        text = re.sub(r"(?s)<[^>]+>", "", text)
        text = html_mod.unescape(text)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        dst.write_text(text, encoding="utf-8")
        if on_progress:
            on_progress(1.0, "完成")
        return ConvertResult(True, output=dst, message="完成")

    def _md_to_html(self, src, dst, options, on_progress, cancel):
        raw = _read_text(src)
        try:
            import markdown

            body = markdown.markdown(raw, extensions=["tables", "fenced_code", "toc"])
        except ImportError:
            body = "<pre>" + html_mod.escape(raw) + "</pre>"
        dst.write_text(
            "<!DOCTYPE html><meta charset='utf-8'><title>Markdown 转换结果</title>"
            "<style>body{font-family:system-ui,sans-serif;line-height:1.8;margin:40px auto;max-width:820px;padding:0 20px}"
            "code{background:#f4f4f4;padding:2px 5px;border-radius:4px}"
            "pre{background:#f7f7f7;padding:14px;border-radius:8px;overflow-x:auto}"
            "table{border-collapse:collapse}td,th{border:1px solid #ccc;padding:6px 10px}</style>"
            + body,
            encoding="utf-8",
        )
        if on_progress:
            on_progress(1.0, "完成")
        return ConvertResult(True, output=dst, message="完成")

    def _text_to_html(self, src, dst, options, on_progress, cancel):
        raw = _read_text(src)
        dst.write_text(
            "<!DOCTYPE html><meta charset='utf-8'><title>文本转换结果</title>"
            "<style>body{font-family:system-ui,sans-serif;margin:40px auto;max-width:820px}"
            "pre{white-space:pre-wrap;font-family:inherit;line-height:1.8}</style>"
            f"<pre>{html_mod.escape(raw)}</pre>",
            encoding="utf-8",
        )
        if on_progress:
            on_progress(1.0, "完成")
        return ConvertResult(True, output=dst, message="完成")

    def _copy_as_text(self, src, dst, options, on_progress, cancel):
        raw = _read_text(src)
        dst.write_text(raw, encoding="utf-8")
        if on_progress:
            on_progress(1.0, "完成")
        return ConvertResult(True, output=dst, message="完成")

    def _text_to_pdf(self, src, dst, options, on_progress, cancel):
        """把**纯文本** / Markdown / CSV 排版成 PDF。

        只用 TextWriter + 显式嵌入的中文字体：PyMuPDF 内置的 ``china-s``
        虽然显示正常，但导出后字体 ``ext == 'n/a'``（未嵌入），换机器就掉字。
        """
        fitz = _require_pymupdf()

        raw = _read_text(src)          # 二进制容器会在这里被明确拒绝
        plan = _cjk_font_plan(fitz)
        doc = fitz.open()
        margin = _PAGE_MARGIN
        line_h = 16
        size = 10.5
        limit = int((_PAGE_W - margin * 2) / (size * 0.98))

        lines: list[str] = []
        for para in raw.splitlines():
            if not para.strip():
                lines.append("")
                continue
            lines.extend(_wrap_text(para, limit))

        per_page = int((_PAGE_H - margin * 2) / line_h)
        total_pages = max(1, (len(lines) + per_page - 1) // per_page)

        for p in range(total_pages):
            if cancel is not None and cancel.is_set():
                doc.close()
                return ConvertResult(False, message="已取消", cancelled=True)
            page = doc.new_page(width=_PAGE_W, height=_PAGE_H)
            chunk = lines[p * per_page:(p + 1) * per_page]

            if plan.font is not None:
                writer = fitz.TextWriter(page.rect)
                y = margin
                for line in chunk:
                    if line:
                        writer.append(fitz.Point(margin, y), line,
                                      font=plan.font, fontsize=size)
                    y += line_h
                writer.write_text(page)
            else:
                y = margin
                for line in chunk:
                    if line:
                        page.insert_text(fitz.Point(margin, y), line,
                                         fontsize=size, fontname="china-s")
                    y += line_h

            if on_progress:
                on_progress(0.1 + 0.85 * (p + 1) / total_pages, f"排版 {p + 1}/{total_pages} 页")

        doc.save(str(dst))
        doc.close()
        _shrink_pdf(fitz, dst)
        if on_progress:
            on_progress(1.0, "完成")
        return ConvertResult(True, output=dst, message="完成" + _plan_suffix(plan))

    def _document_to_pdf(self, src, dst, options, on_progress, cancel):
        """结构化文档 → PDF：mammoth 抽 HTML，再用 PyMuPDF Story 多页排版。

        相比"抽纯文本重排"，这条路径保留了标题层级、列表和**表格**，
        并且把中文字体显式嵌入 PDF —— 换机器/换阅读器都不会掉字。
        """
        fitz = _require_pymupdf()
        ext = src.suffix.lstrip(".").lower()
        plan = _cjk_font_plan(fitz)

        if plan.font is None:
            # 没有可嵌入的字体时退回纯文本路径（能看，且会带上提示）
            return self._text_to_pdf(src, dst, options, on_progress, cancel)

        try:
            body = _document_html(src, ext)
        except Exception as exc:
            return ConvertResult(False, message=f"文档解析失败：{exc}")
        if not body.strip():
            return ConvertResult(False, message="文档中没有可提取的内容")

        css = (
            f"@font-face {{ font-family: {plan.family}; "
            f"src: url({plan.path.name}); }}\n"
            + _MARKUP_CSS.format(family=plan.family)
        )

        if on_progress:
            on_progress(0.2, "排版文档")

        # Story 必须配 DocumentWriter 才能跨页排版，而 DocumentWriter 只能写文件。
        # 关键：**不能直接写最终产物** —— DocumentWriter 在 close() 之后仍占着
        # 文件句柄（Windows 上表现为后续 os.replace 直接 PermissionError），
        # 所以先写到 .stage 中间文件，释放句柄后再子集化、再移到位。
        stage = dst.with_name(dst.stem + ".stage.pdf")
        writer = fitz.DocumentWriter(str(stage))
        mediabox = fitz.paper_rect("a4")
        where = mediabox + (_PAGE_MARGIN, _PAGE_MARGIN, -_PAGE_MARGIN, -_PAGE_MARGIN)
        try:
            story = fitz.Story(html=body, user_css=css,
                               archive=fitz.Archive(str(plan.path.parent)))
            more, pages = True, 0
            while more:
                if cancel is not None and cancel.is_set():
                    _discard(stage)
                    return ConvertResult(False, message="已取消", cancelled=True)
                dev = writer.begin_page(mediabox)
                more, _filled = story.place(where)
                story.draw(dev)
                writer.end_page()
                pages += 1
                if on_progress:
                    on_progress(min(0.2 + 0.05 * pages, 0.95), f"排版第 {pages} 页")
                if pages >= _MAX_PDF_PAGES:       # 防止坏输入把内存吃爆
                    break
        finally:
            writer.close()
            del writer
            gc.collect()                       # 真正释放文件句柄

        if not stage.exists() or stage.stat().st_size == 0:
            return ConvertResult(False, message="排版完成但未产生有效的 PDF")
        # Story 会把**整个**中文字体塞进 PDF（simhei 近 10 MB），必须子集化：
        # 实测一页文档 10.0 MB → 66 KB，且字体仍是嵌入状态。
        _shrink_pdf(fitz, stage, dst)
        if on_progress:
            on_progress(1.0, "完成")
        return ConvertResult(True, output=dst, message="完成" + _plan_suffix(plan))

    def _generic_to_pdf(self, src, dst, options, on_progress, cancel):
        """兜底：按源格式挑最合适的排版路径，绝不把二进制当文本读。"""
        ext = src.suffix.lstrip(".").lower()
        if ext in _BINARY_DOC_EXTS - {"pdf"}:
            return self._document_to_pdf(src, dst, options, on_progress, cancel)
        if ext in ("html", "md"):
            return self._document_to_pdf(src, dst, options, on_progress, cancel)
        if ext == "pdf":
            return ConvertResult(
                False, message="PDF → PDF 无需转换（可改用「PDF → 图片 / 文本」）"
            )
        return self._text_to_pdf(src, dst, options, on_progress, cancel)

    # ================= COM 高保真通道 ================= #
    def _convert_via_com(self, src: Path, dst: Path, src_ext: str, dst_ext: str,
                         on_progress: ProgressFn | None) -> ConvertResult:
        import pythoncom
        import win32com.client

        # Office 对相对路径的处理很挑剔（相对的是它自己的工作目录，不是我们的），
        # 一律传绝对路径。
        src, dst = Path(src).resolve(), Path(dst).resolve()
        dst.parent.mkdir(parents=True, exist_ok=True)

        # 转换跑在工作线程里，COM 必须在该线程显式初始化
        pythoncom.CoInitialize()
        try:
            return self._com_dispatch(win32com.client, src, dst, src_ext, dst_ext, on_progress)
        finally:
            pythoncom.CoUninitialize()

    def _com_dispatch(self, win32com_client, src: Path, dst: Path, src_ext: str,
                      dst_ext: str, on_progress: ProgressFn | None) -> ConvertResult:
        app_specs = [
            (OFFICE_TEXT_EXTS, "Word.Application", "KWPS.Application"),
            (OFFICE_SHEET_EXTS, "Excel.Application", "KET.Application"),
            (OFFICE_SLIDE_EXTS, "PowerPoint.Application", "KWPP.Application"),
        ]
        prog_ids: list[str] = []
        for exts, ms_id, wps_id in app_specs:
            if src_ext == "pdf" or src_ext in exts or dst_ext in exts:
                prog_ids.extend([ms_id, wps_id])
        # 纯文本类源 → pdf 交给 Word 处理排版
        if src_ext in ("txt", "html", "md", "csv"):
            prog_ids = ["Word.Application", "KWPS.Application"]

        if on_progress:
            on_progress(0.2, "启动 Office 内核")

        last_error = ""
        for prog_id in dict.fromkeys(prog_ids):
            app = None
            try:
                # 注意：win32com_client 就是 win32com.client 模块本身，
                # 不能再写 win32com.client.DispatchEx —— 那会抛 AttributeError，
                # 让整条 COM 通道对**所有** Office 文档静默失败。
                app = win32com_client.DispatchEx(prog_id)
            except Exception as exc:
                last_error = f"{prog_id}: {exc}"
                continue

            exported = False
            try:
                _silence_office(app)
                self._com_export(app, prog_id, src, dst, dst_ext, on_progress)
                exported = True
            except Exception as exc:
                last_error = f"{prog_id}: {exc}"
            finally:
                try:
                    app.Quit()
                except Exception:
                    pass

            if not exported:
                continue
            # 收尾必须在 Office 完全退出之后做：文档没关时文件还被锁着，
            # 之前在这里转编码会撞 PermissionError，反而把一次成功的转换
            # 误判成"Office 通道失败"。
            if dst_ext in ("txt", "csv"):
                _ensure_utf8_text(dst)
            elif dst_ext in ("html", "htm"):
                _ensure_utf8_html(dst)
            if on_progress:
                on_progress(1.0, "完成")
            return ConvertResult(True, output=dst, message="完成（Office 高保真）")

        return ConvertResult(False, message=f"Office 通道失败：{last_error}")

    @staticmethod
    def _com_export(app, prog_id: str, src: Path, dst: Path, dst_ext: str,
                    on_progress: ProgressFn | None) -> None:
        is_sheet = prog_id in ("Excel.Application", "KET.Application")
        is_slide = prog_id in ("PowerPoint.Application", "KWPP.Application")

        if is_slide:
            presentation = app.Presentations.Open(str(src), WithWindow=False)
            try:
                if dst_ext == "pdf":
                    _first_success(
                        lambda: presentation.SaveAs(str(dst), 32),    # ppSaveAsPDF
                        lambda: presentation.ExportAsFixedFormat(
                            str(dst), 2),                             # ppFixedFormatTypePDF
                    )
                else:
                    _first_success(
                        lambda: presentation.SaveAs(str(dst),
                                                    _PP_FORMAT.get(dst_ext, 24)),
                        lambda: presentation.SaveAs(str(dst)),
                    )
            finally:
                presentation.Close()
            return

        if is_sheet:
            workbook = app.Workbooks.Open(str(src), ReadOnly=True)
            try:
                if dst_ext == "pdf":
                    _first_success(
                        lambda: workbook.ExportAsFixedFormat(0, str(dst)),   # xlTypePDF
                        lambda: workbook.SaveAs(str(dst), FileFormat=57),    # xlPDF
                    )
                else:
                    _first_success(
                        lambda: workbook.SaveAs(
                            str(dst), FileFormat=_XL_FORMAT.get(dst_ext, 51)),
                        lambda: workbook.SaveAs(str(dst)),
                    )
            finally:
                workbook.Close(SaveChanges=False)
            return

        doc = app.Documents.Open(str(src), ReadOnly=True)
        try:
            if dst_ext == "pdf":
                # ExportAsFixedFormat 需要 Office 2007 装 "另存为 PDF" 插件；
                # SaveAs2 是 2010 才有的；2007 只能用 SaveAs —— 三条都留着。
                _first_success(
                    lambda: doc.ExportAsFixedFormat(str(dst), 17),        # wdExportFormatPDF
                    lambda: doc.SaveAs2(str(dst), FileFormat=17),         # wdFormatPDF
                    lambda: doc.SaveAs(str(dst), FileFormat=17),
                )
            elif dst_ext in ("txt", "csv"):
                # 纯文本尽量指定 UTF-8（65001）；Word 2007 会忽略这个参数，
                # 所以 _com_dispatch 里还会在 Office 退出后兜一道编码转换。
                _first_success(
                    lambda: doc.SaveAs2(str(dst), FileFormat=2, Encoding=65001),
                    lambda: doc.SaveAs(str(dst), FileFormat=2, Encoding=65001),
                    lambda: doc.SaveAs(str(dst), FileFormat=2),
                )
            else:
                fmt = _WD_FORMAT.get(dst_ext)
                if fmt is None:
                    _first_success(
                        lambda: doc.SaveAs2(str(dst)),
                        lambda: doc.SaveAs(str(dst)),
                    )
                else:
                    _first_success(
                        lambda: doc.SaveAs2(str(dst), FileFormat=fmt),
                        lambda: doc.SaveAs(str(dst), FileFormat=fmt),
                    )
        finally:
            doc.Close(SaveChanges=False)


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #
def _page_path(dst: Path, index: int) -> Path:
    return dst.with_name(f"{dst.stem}_{index:03d}{dst.suffix}")


def _read_text(path: Path) -> str:
    """按编码读**纯文本**文件。

    二进制文档（docx / pdf / xls …）必须走各自的解析器，这里直接拒绝：
    以前 docx→pdf 的兜底路径就是在这个函数上把 DOCX 的 ZIP 流当正文读出来，
    排成几十页垃圾 —— 用户看到的"乱码"就是它。
    """
    ext = path.suffix.lstrip(".").lower()
    if ext in _BINARY_DOC_EXTS:
        raise ValueError(
            f".{ext} 是二进制文档容器，不能按纯文本读取；"
            f"请走对应的解析器（docx 用 mammoth，pdf 用 PyMuPDF）"
        )
    for enc in ("utf-8", "utf-8-sig", "gbk", "gb18030", "latin-1"):
        try:
            return path.read_text(encoding=enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return path.read_text(encoding="utf-8", errors="replace")


def _write_csv(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        csv.writer(fh).writerows(rows)


def _read_csv(path: Path) -> list[list[str]]:
    raw = _read_text(path)
    try:
        dialect = csv.Sniffer().sniff(raw[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return [row for row in csv.reader(io.StringIO(raw), dialect)]


def _coerce(value: str):
    """给 Excel 写入时尽量还原数字/日期语义。"""
    text = value.strip()
    if not text:
        return ""
    try:
        if "." in text or "e" in text.lower():
            return float(text)
        return int(text)
    except ValueError:
        return value


def _docx_xml_text(path: Path) -> str:
    """没有 mammoth 时直接从 docx 的 XML 里抽文字。"""
    import zipfile

    try:
        with zipfile.ZipFile(path) as zf:
            xml = zf.read("word/document.xml").decode("utf-8", errors="replace")
    except Exception:
        return _binary_text_scavenge(path)
    xml = re.sub(r"</w:p>", "\n", xml)
    xml = re.sub(r"<w:tab[^>]*/>", "\t", xml)
    xml = re.sub(r"<[^>]+>", "", xml)
    return html_mod.unescape(xml)


def _binary_text_scavenge(path: Path) -> str:
    """对二进制格式做"可见文本抢救"，救不出来就**明确报错**。

    历史上这个函数会返回一串"看着像文本"的垃圾（OLE 容器里的字体名、ASCII 表），
    被下游排成几十上百页乱码还报"成功"。现在加了严格判据：
    必须像**正文**（有中文，或有足够长且数量合理的词），否则一律抛错。
    """
    data = path.read_bytes()
    best = ""
    for enc in ("gb18030", "utf-16-le", "latin-1"):
        try:
            text = data.decode(enc, errors="ignore")
        except LookupError:
            continue
        printable = "".join(
            ch if (ch.isprintable() or ch in "\n\t") else "\n" for ch in text
        )
        printable = re.sub(r"\n{3,}", "\n\n", printable).strip()
        if len(printable) > len(best):
            best = printable
    if _looks_like_prose(best):
        return best
    raise ValueError(
        f"无法可靠解析 .{path.suffix.lstrip('.').lower()} 的二进制内容"
        f"（纯 Python 不支持该老格式）。请改用 Office 高保真通道，"
        f"或安装 WPS / Office 后重试。"
    )


_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
_WORD_RE = re.compile(r"[A-Za-z\u3400-\u9fff]{2,}")


def _looks_like_prose(text: str) -> bool:
    """判断一段文本像不像"人的正文"。

    二进制容器解码出来往往也是"可打印字符"，但形态完全不同：
    成排的单字符（ASCII 表）、大量非常见符号、几乎没有多字词。
    这里用三条低门槛判据把它们挡掉。
    """
    if len(text) < 40:
        return False
    if _CJK_RE.search(text):
        return True
    words = _WORD_RE.findall(text)
    if len(words) < 20:
        return False
    avg_len = sum(len(w) for w in words) / len(words)
    space_ratio = text.count(" ") / len(text)
    weird = sum(1 for ch in text if not (ch.isalnum() or ch.isspace() or ch in ".,;:!?'\"()-"))
    return avg_len >= 3.0 and 0.03 <= space_ratio <= 0.30 and weird / len(text) < 0.25


#: RTF 里常见的中文载体：\uNNNN?（UTF-16 十进制）与 \'hh（当前代码页字节）
_RTF_CTRL_RE = re.compile(r"\\([a-zA-Z]+)(-?\d+)?[ ]?")
_RTF_CODEPAGE_RE = re.compile(r"\\ansicpg(\d+)")

#: 这些 RTF 分组只描述样式/元数据，不是正文，整组跳过
_RTF_SKIP_DEST = (
    "fonttbl", "colortbl", "stylesheet", "info", "pict", "object",
    "listtable", "listoverridetable", "rsidtbl", "generator",
    "themedata", "colorschememapping", "latentstyles", "datastore",
    "xmlnstbl", "filetbl", "revtbl", "mmathPr", "wgrffmtfilter",
)


def _rtf_to_text(data: str) -> str:
    """把 RTF 抽成纯文本。

    直接按编码读 RTF 只能看到 ``{\\rtf1\\ansi...}`` 这类控制字，
    中文还藏在 ``\\u-1234?`` / ``\\'hh`` 里，所以必须真的解析一遍。
    """
    codepage = "cp936"
    match = _RTF_CODEPAGE_RE.search(data[:4096])
    if match:
        codepage = f"cp{match.group(1)}"

    out: list[str] = []
    stack: list[bool] = []          # True = 该分组要保留
    i, n = 0, len(data)
    uc_skip = 1                     # \ucN：\uN 后面要跳过的替代字节数

    while i < n:
        ch = data[i]

        if ch == "{":
            # \* 开头的分组是"可忽略目标"；字体表/颜色表/样式表等只描述样式，
            # 内容不是正文，必须整组跳过（否则抽出来的文本里会混进 "SimSun;" 之类）。
            keep = True
            if data.startswith("\\*", i + 1):
                keep = False
            else:
                head = data[i + 1:i + 1 + 24].lstrip("\\")
                if any(head.startswith(dest) for dest in _RTF_SKIP_DEST):
                    keep = False
            stack.append(keep)
            i += 1
            continue
        if ch == "}":
            if stack:
                stack.pop()
            i += 1
            continue

        if ch == "\\":
            ctrl = _RTF_CTRL_RE.match(data, i)
            if ctrl:
                word, arg = ctrl.group(1), ctrl.group(2)
                i = ctrl.end()
                keep = all(stack)
                if word == "u" and arg is not None:
                    code = int(arg)
                    if code < 0:
                        code += 65536
                    if keep:
                        out.append(chr(code))
                    skipped = 0                     # 跳过 \ucN 个替代字符
                    while skipped < uc_skip and i < n and data[i] not in "\\{}":
                        i += 1
                        skipped += 1
                elif word == "uc" and arg is not None:
                    uc_skip = int(arg)
                elif word in ("par", "line", "pard"):
                    if keep:
                        out.append("\n")
                elif word == "tab":
                    if keep:
                        out.append("\t")
                elif word in ("cell", "row"):
                    if keep:
                        out.append("\t")
                continue

            if data[i + 1:i + 2] == "'":
                hexs = data[i + 2:i + 4]
                i += 4 if len(hexs) == 2 else 1
                try:
                    code = int(hexs, 16)
                except ValueError:
                    continue
                if all(stack):
                    out.append(bytes([code]).decode(codepage, "replace"))
                continue

            nxt = data[i + 1:i + 2]
            if nxt in "\\{}":
                if all(stack):
                    out.append(nxt)
                i += 2
                continue
            i += 1
            continue

        if ch in "\r\n":
            i += 1
            continue
        if all(stack):
            out.append(ch)
        i += 1

    text = re.sub(r"[ \t]+\n", "\n", "".join(out))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _sheet_html(src: Path, max_rows: int = 5000) -> str:
    """xlsx → HTML 表格。内置通道下这是 xlsx→pdf 唯一正确的做法。"""
    import openpyxl

    workbook = openpyxl.load_workbook(src, read_only=True, data_only=True)
    try:
        parts: list[str] = []
        multiple = len(workbook.worksheets) > 1
        for sheet in workbook.worksheets:
            rows: list[list[str]] = []
            for index, row in enumerate(sheet.iter_rows(values_only=True)):
                if index >= max_rows:
                    break
                cells = ["" if c is None else str(c) for c in row]
                if any(c.strip() for c in cells):
                    rows.append(cells)
            if not rows:
                continue
            if multiple:
                parts.append(f"<h3>{html_mod.escape(sheet.title)}</h3>")
            body = "".join(
                "<tr>" + "".join(f"<td>{html_mod.escape(c)}</td>" for c in cells) + "</tr>"
                for cells in rows
            )
            parts.append(f"<table>{body}</table>")
        return "".join(parts)
    finally:
        workbook.close()


def _plan_suffix(plan: "FontPlan") -> str:
    """把字体方案如实写进结果消息 —— 没嵌入就必须让用户知道。"""
    return "" if plan.embedded else f"（注意：{plan.note}）"


def _ensure_utf8_text(path: Path) -> None:
    """把 Office 导出的纯文本统一成 UTF-8。

    Word 2007 不认 ``Encoding=65001``，导出 .txt 时按系统代码页（GBK）写，
    别的环境打开就是问号。这里按内容判断编码并转成 UTF-8。
    """
    try:
        data = path.read_bytes()
    except OSError:
        return
    try:
        data.decode("utf-8")
        return                                  # 已经是 UTF-8，不动
    except UnicodeDecodeError:
        pass
    for enc in ("gb18030", "utf-16"):
        try:
            text = data.decode(enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
        try:
            path.write_text(text, encoding="utf-8")
        except OSError:
            pass                                # 文件还被占用就放弃，不影响转换结果
        return


def _ensure_utf8_html(path: Path) -> None:
    """把 Office 导出的 HTML 统一成 UTF-8。

    Word 2007 写出来的是系统代码页（GBK）字节 + ``charset=gb2312`` 声明，
    浏览器能看，但和内置通道的 UTF-8 产物不一致；统一掉更省事。
    """
    try:
        data = path.read_bytes()
    except OSError:
        return
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = data.decode("gb18030")
        except UnicodeDecodeError:
            return
    head, tail = text[:2048], text[2048:]
    head = re.sub(r"(charset\s*=\s*)[\"']?[\w-]+", r"\1utf-8",
                  head, count=2, flags=re.I)
    try:
        path.write_text(head + tail, encoding="utf-8")
    except OSError:
        pass


def _short(text: str, limit: int = 90) -> str:
    """把 COM 那一长串错误压成能塞进结果消息里的一句。"""
    line = " ".join(str(text).split())
    return line if len(line) <= limit else line[: limit - 1] + "…"


def _discard(path: Path) -> None:
    """删中间文件，删不掉也不抛。"""
    try:
        Path(path).unlink()
    except OSError:
        pass


def _first_success(*thunks) -> None:
    """依次尝试若干个 COM 调用，**全部**失败才把最后一个异常抛出去。

    不同 Office/WPS 版本的导出方法不一样：
    Word 2007 没有 SaveAs2、Excel 2007 的 ExportAsFixedFormat 要先装 PDF 插件……
    逐个试比按版本号猜稳。
    """
    last: Exception | None = None
    for thunk in thunks:
        try:
            thunk()
            return
        except Exception as exc:
            last = exc
    if last is not None:
        raise last


def _shrink_pdf(fitz, src: Path, dst: Path | None = None) -> int:
    """字体子集化 + 垃圾回收，压掉内置排版整体嵌入的中文字体。

    PyMuPDF 的 Story / TextWriter 会把整个字体文件（simhei 近 10 MB）嵌进 PDF，
    一页文档就是 10 MB。``subset_fonts()`` 只保留用到的字形：
    实测 10,070,519 字节 → 66,371 字节，且 ``ext`` 仍是 ``ttf``（未失去嵌入）。

    失败时不抛（只是体积不理想，文件本身是好的），但会清掉中间文件。
    """
    target = dst if dst is not None else src
    tmp = src.with_name(src.stem + ".subset.pdf")
    try:
        with fitz.open(src) as doc:
            doc.subset_fonts()
            doc.save(str(tmp), garbage=4, deflate=True, clean=True)
        if not tmp.exists() or tmp.stat().st_size == 0:
            raise RuntimeError("子集化产物为空")
        os.replace(str(tmp), str(target))
        if dst is not None and src != dst:
            try:                                   # 中间文件用完即删
                src.unlink()
            except OSError:
                pass
        return target.stat().st_size
    except Exception:
        for leftover in (tmp,):
            try:
                leftover.unlink()
            except OSError:
                pass
        if dst is not None and src != dst:
            try:                                   # 兜底：至少把原件搬到位
                os.replace(str(src), str(dst))
            except OSError:
                pass
        return target.stat().st_size if target.exists() else 0


def _silence_office(app) -> None:
    """让 Office/WPS 在后台静默转换：不显示界面、不弹对话框。"""
    for attr, value in (
        ("Visible", False),
        ("DisplayAlerts", 0),
        ("ScreenUpdating", False),
        ("AlertBeforeOverwriting", False),
    ):
        try:
            setattr(app, attr, value)
        except Exception:
            pass


def _plain_text_to_html(text: str) -> str:
    """纯文本 → 段落 HTML：转义后按空行分段，行内换行变 <br/>。"""
    parts: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        block = block.strip("\n")
        if not block.strip():
            continue
        safe = html_mod.escape(block).replace("\n", "<br/>")
        parts.append(f"<p>{safe}</p>")
    return "".join(parts)


def _read_rtf(path: Path) -> str:
    """RTF 是"带控制字的文本"，按字节保真地读进来交给解析器。"""
    data = path.read_bytes()
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return data.decode("latin-1", "replace")


def _document_html(src: Path, ext: str) -> str:
    """把源文档取成 HTML 片段，交给 PyMuPDF Story 排版。

    每种容器走各自**真正的**解析器；解析不了就抛 ValueError 让上层报错，
    绝不退化成"把二进制当文本读"。
    """
    if ext == "html":
        raw = _read_text(src)
        match = re.search(r"<body[^>]*>(.*?)</body>", raw, re.S | re.I)
        return match.group(1) if match else raw

    if ext == "md":
        raw = _read_text(src)
        try:
            import markdown

            return markdown.markdown(
                raw, extensions=["tables", "fenced_code", "sane_lists"]
            )
        except Exception:
            return _plain_text_to_html(raw)

    if ext in _MAMMOTH_EXTS:
        try:
            import mammoth

            with open(src, "rb") as fh:
                body = mammoth.convert_to_html(fh).value
            if body.strip():
                return body
        except Exception:
            pass
        # mammoth 不可用 / 解析失败：退回 XML 抠字，虽然丢版式但不丢内容
        return _plain_text_to_html(_docx_xml_text(src))

    if ext == "rtf":
        return _plain_text_to_html(_rtf_to_text(_read_rtf(src)))

    if ext in ("xlsx", "xlsm"):
        return _sheet_html(src)

    # 剩下的老二进制（doc / xls / ppt …）：抢救得出正文就用，否则抛错
    return _plain_text_to_html(_binary_text_scavenge(src))


_FONT_PLAN: "FontPlan | None" = None

#: 优先嵌入哪个系统字体（按可用性依次尝试）。
#: 挑的都是单字重常见字体；.ttc 集合也能加载，但排在 .ttf 之后更稳。
_FONT_CANDIDATES = (
    "simhei.ttf", "msyh.ttc", "simsun.ttc", "Deng.ttf",
    "simfang.ttf", "msjh.ttc",
)


class FontPlan:
    """中文 PDF 排版的字体方案。

    设计原则：**必须知道自己在用哪个字体**。
    以前的写法是"探测内置字体失败就试系统字体，两条都失败还继续写"，
    结果就是静默输出豆腐块 —— 这类"成功但内容是坏的"最难被发现。
    """

    __slots__ = ("font", "family", "path", "embedded", "note")

    def __init__(self, font, family: str, path: Path | None, embedded: bool, note: str):
        self.font = font            # fitz.Font 实例；None 表示只能用内置字体
        self.family = family        # HTML/CSS 里引用的 font-family 名
        self.path = path            # 命中的字体文件
        self.embedded = embedded    # 是否真的把字体嵌进 PDF
        self.note = note

    def __repr__(self) -> str:      # pragma: no cover - 调试用
        return f"FontPlan(family={self.family!r}, embedded={self.embedded}, path={self.path})"


def _cjk_font_plan(fitz) -> FontPlan:
    """决定中文排版用哪个字体（结果缓存，进程内只探一次）。

    优先显式嵌入系统字体：PyMuPDF 内置的 ``china-s`` 虽然能正确显示，
    但导出后字体 ``ext == 'n/a'``（未嵌入），换机器 / 换阅读器就掉成方框，
    这正是这个项目一直以来的痛点。
    """
    global _FONT_PLAN
    if _FONT_PLAN is not None:
        return _FONT_PLAN

    font_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for name in _FONT_CANDIDATES:
        cand = font_dir / name
        if not cand.is_file():
            continue
        try:
            font = fitz.Font(fontfile=str(cand))
            if not font.name:
                continue
        except Exception:
            continue
        _FONT_PLAN = FontPlan(
            font=font,
            family=f"fm-{cand.stem.lower()}",
            path=cand,
            embedded=True,
            note=f"已嵌入中文字体 {cand.name}",
        )
        return _FONT_PLAN

    # 退路：PyMuPDF 内置 CJK 字体，视觉可用但**不嵌入**
    try:
        probe = fitz.open()
        page = probe.new_page()
        page.insert_text(fitz.Point(50, 50), "检测", fontsize=10, fontname="china-s")
        probe.close()
        _FONT_PLAN = FontPlan(
            font=None,
            family="sans-serif",
            path=None,
            embedded=False,
            note="系统未找到可嵌入的中文字体，已用内置字体渲染（换机器可能显示异常）",
        )
        return _FONT_PLAN
    except Exception:
        pass

    # 两条路都不通：必须明确报错，绝不能静默产出乱码
    raise RuntimeError(
        "系统中找不到任何可用中文字体（已试 simhei/msyh/simsun/Deng/simfang），"
        "无法排版中文 PDF。请安装至少一种中文字体后重试。"
    )


def _pick_cjk_font() -> str | None:
    """挑一个系统中文字体文件路径（给需要 fontfile 的外部调用用）。"""
    plan = _FONT_PLAN
    if plan is not None:
        return str(plan.path) if plan.path else None
    font_dir = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for name in _FONT_CANDIDATES:
        cand = font_dir / name
        if cand.is_file():
            return str(cand)
    return None


def _wrap_text(text: str, limit: int) -> list[str]:
    """按显示宽度折行：中日韩字符算 2 格。"""
    limit = max(20, limit)
    out: list[str] = []
    current = ""
    width = 0
    for ch in text:
        w = 2 if ("\u1100" <= ch <= "\u9fff" or "\uff00" <= ch <= "\uffef") else 1
        if width + w > limit:
            out.append(current)
            current, width = "", 0
        current += ch
        width += w
    if current:
        out.append(current)
    return out
