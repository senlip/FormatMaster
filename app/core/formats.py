"""格式目录。

这里定义软件"认识"的所有格式。上层 UI、路由注册表、引擎适配器
全部以本模块为唯一事实来源（single source of truth）。
新增格式 = 在这里加一行 + 对应引擎确认能处理，其他地方不用动。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Kind(str, Enum):
    """媒体大类。决定走哪个引擎、UI 上归到哪个分组。"""

    VIDEO = "video"
    AUDIO = "audio"
    IMAGE = "image"
    DOCUMENT = "document"
    ARCHIVE = "archive"

    @property
    def label(self) -> str:
        return {
            Kind.VIDEO: "视频",
            Kind.AUDIO: "音频",
            Kind.IMAGE: "图像",
            Kind.DOCUMENT: "文档",
            Kind.ARCHIVE: "压缩包",
        }[self]

    @property
    def color(self) -> str:
        """UI 分组标记色。"""
        return {
            Kind.VIDEO: "#185FA5",
            Kind.AUDIO: "#534AB7",
            Kind.IMAGE: "#0F6E56",
            Kind.DOCUMENT: "#854F0B",
            Kind.ARCHIVE: "#993C1D",
        }[self]


@dataclass(frozen=True)
class FormatSpec:
    """单个格式的描述。"""

    ext: str
    kind: Kind
    label: str
    note: str = ""
    #: 只能作为输入、不能作为输出（如 rmvb 解码器有、编码器无）
    input_only: bool = False
    #: 平台自研加密封装，转换前必须先剥壳（见 core.decryptors）
    encrypted: bool = False

    @property
    def display(self) -> str:
        if self.note:
            return f"{self.label} · {self.note}"
        return self.label


def _f(ext: str, kind: Kind, label: str, note: str = "",
       input_only: bool = False, encrypted: bool = False) -> FormatSpec:
    return FormatSpec(ext=ext.lower(), kind=kind, label=label, note=note,
                      input_only=input_only, encrypted=encrypted)


_CATALOG: tuple[FormatSpec, ...] = (
    # ---------------- 视频 ----------------
    _f("mp4", Kind.VIDEO, "MP4", "H.264/H.265，最通用"),
    _f("mkv", Kind.VIDEO, "MKV", "Matroska，多轨容器"),
    _f("avi", Kind.VIDEO, "AVI", "老牌容器，兼容性好"),
    _f("mov", Kind.VIDEO, "MOV", "QuickTime，剪辑常用"),
    _f("wmv", Kind.VIDEO, "WMV", "Windows Media"),
    _f("flv", Kind.VIDEO, "FLV", "Flash 视频"),
    _f("webm", Kind.VIDEO, "WebM", "VP9/AV1，网页友好"),
    _f("m4v", Kind.VIDEO, "M4V", "Apple 容器"),
    _f("mpg", Kind.VIDEO, "MPEG", "MPEG-1/2 程序流"),
    _f("ts", Kind.VIDEO, "TS", "传输流，录制常用"),
    _f("m2ts", Kind.VIDEO, "M2TS", "蓝光传输流"),
    _f("3gp", Kind.VIDEO, "3GP", "手机旧格式"),
    _f("ogv", Kind.VIDEO, "OGV", "Ogg 视频"),
    _f("vob", Kind.VIDEO, "VOB", "DVD 视频对象"),
    _f("mxf", Kind.VIDEO, "MXF", "广播级封装"),
    _f("rmvb", Kind.VIDEO, "RMVB", "RealMedia", input_only=True),
    _f("rm", Kind.VIDEO, "RM", "RealMedia", input_only=True),
    _f("gif", Kind.VIDEO, "GIF 动图", "无音频动图"),
    _f("wmv", Kind.VIDEO, "WMV", "Windows Media"),
    # ---------------- 音频 ----------------
    _f("mp3", Kind.AUDIO, "MP3", "最通用有损"),
    _f("wav", Kind.AUDIO, "WAV", "PCM 无损"),
    _f("flac", Kind.AUDIO, "FLAC", "无损压缩"),
    _f("aac", Kind.AUDIO, "AAC", "高级音频编码"),
    _f("m4a", Kind.AUDIO, "M4A", "AAC 封装"),
    _f("ogg", Kind.AUDIO, "OGG", "Vorbis"),
    _f("opus", Kind.AUDIO, "OPUS", "低码率最优"),
    _f("wma", Kind.AUDIO, "WMA", "Windows Media"),
    _f("ac3", Kind.AUDIO, "AC3", "杜比数字"),
    _f("aiff", Kind.AUDIO, "AIFF", "Apple 无损"),
    _f("amr", Kind.AUDIO, "AMR", "窄带语音"),
    _f("mka", Kind.AUDIO, "MKA", "Matroska 音频"),
    # ---------------- 图像 ----------------
    _f("jpg", Kind.IMAGE, "JPG", "有损，体积小"),
    _f("png", Kind.IMAGE, "PNG", "无损，支持透明"),
    _f("webp", Kind.IMAGE, "WebP", "现代网页格式"),
    _f("bmp", Kind.IMAGE, "BMP", "无压缩位图"),
    _f("tiff", Kind.IMAGE, "TIFF", "印刷/扫描常用"),
    _f("gif", Kind.IMAGE, "GIF", "索引色动图"),
    _f("ico", Kind.IMAGE, "ICO", "图标"),
    _f("tga", Kind.IMAGE, "TGA", "游戏纹理"),
    _f("avif", Kind.IMAGE, "AVIF", "新一代压缩"),
    _f("heic", Kind.IMAGE, "HEIC", "苹果相机"),
    _f("ppm", Kind.IMAGE, "PPM", "无格式头位图"),
    _f("jp2", Kind.IMAGE, "JP2", "JPEG 2000"),
    _f("pcx", Kind.IMAGE, "PCX", "老式位图"),
    # ---------------- 文档 ----------------
    _f("pdf", Kind.DOCUMENT, "PDF", "固定版式"),
    _f("docx", Kind.DOCUMENT, "DOCX", "Word 2007+"),
    _f("doc", Kind.DOCUMENT, "DOC", "Word 97-2003"),
    _f("odt", Kind.DOCUMENT, "ODT", "OpenDocument 文本"),
    _f("rtf", Kind.DOCUMENT, "RTF", "富文本"),
    _f("txt", Kind.DOCUMENT, "TXT", "纯文本"),
    _f("html", Kind.DOCUMENT, "HTML", "网页"),
    _f("md", Kind.DOCUMENT, "Markdown", "轻量标记"),
    _f("xlsx", Kind.DOCUMENT, "XLSX", "Excel 2007+"),
    _f("xls", Kind.DOCUMENT, "XLS", "Excel 97-2003"),
    _f("csv", Kind.DOCUMENT, "CSV", "逗号分隔"),
    _f("pptx", Kind.DOCUMENT, "PPTX", "PowerPoint"),
    _f("epub", Kind.DOCUMENT, "EPUB", "电子书"),
    _f("mobi", Kind.DOCUMENT, "MOBI", "Kindle 电子书"),
    # ---------------- 压缩包 ----------------
    _f("zip", Kind.ARCHIVE, "ZIP", "最通用压缩"),
    _f("7z", Kind.ARCHIVE, "7Z", "压缩率最高"),
    _f("tar", Kind.ARCHIVE, "TAR", "仅打包不压缩"),
    _f("gz", Kind.ARCHIVE, "GZ", "单文件压缩"),
    _f("bz2", Kind.ARCHIVE, "BZ2", "bzip2"),
    _f("xz", Kind.ARCHIVE, "XZ", "LZMA2"),
    _f("iso", Kind.ARCHIVE, "ISO", "光盘镜像"),
    _f("cab", Kind.ARCHIVE, "CAB", "Windows 归档", input_only=True),
    _f("rar", Kind.ARCHIVE, "RAR", "需外部工具解压", input_only=True),
)

# --------------------------------------------------------------------------- #
# 国内平台加密格式（仅作为输入）
#
# 这些是各平台"下载/缓存"文件的自研加密封装 —— 音频本体仍是标准格式，
# 只是外面套了一层平台自己的壳。转换前由 core.decryptors 剥壳，
# 再走正常引擎流程。扩展名本身不能作为输出目标。
# --------------------------------------------------------------------------- #
_ENCRYPTED_EXT: tuple[tuple[str, str, str], ...] = (
    # ---- 网易云音乐 ----
    ("ncm", "NCM", "网易云音乐"),
    # ---- QQ 音乐 ----
    ("qmc0", "QMC0", "QQ 音乐"),
    ("qmc2", "QMC2", "QQ 音乐"),
    ("qmc3", "QMC3", "QQ 音乐"),
    ("qmc4", "QMC4", "QQ 音乐"),
    ("qmc6", "QMC6", "QQ 音乐"),
    ("qmc8", "QMC8", "QQ 音乐"),
    ("qmcflac", "QMCFLAC", "QQ 音乐无损"),
    ("qmcogg", "QMCOGG", "QQ 音乐"),
    ("mflac", "MFLAC", "QQ 音乐新版无损"),
    ("mflac0", "MFLAC0", "QQ 音乐新版无损"),
    ("mgg", "MGG", "QQ 音乐新版"),
    ("mgg0", "MGG0", "QQ 音乐新版"),
    ("mgg1", "MGG1", "QQ 音乐新版"),
    ("mggl", "MGGL", "QQ 音乐新版"),
    ("mmp4", "MMP4", "QQ 音乐视频"),
    ("tkm", "TKM", "QQ 音乐"),
    ("bkcmp3", "BKCMP3", "QQ 音乐"),
    ("bkcm4a", "BKCM4A", "QQ 音乐"),
    ("bkcflac", "BKCFLAC", "QQ 音乐"),
    ("bkcwav", "BKCWAV", "QQ 音乐"),
    ("bkcape", "BKCAPE", "QQ 音乐"),
    ("bkcogg", "BKCOGG", "QQ 音乐"),
    ("bkcwma", "BKCWMA", "QQ 音乐"),
    ("666c6163", "FLAC(hex)", "QQ 音乐内部名"),
    ("6d3461", "M4A(hex)", "QQ 音乐内部名"),
    ("6d7033", "MP3(hex)", "QQ 音乐内部名"),
    ("6f6767", "OGG(hex)", "QQ 音乐内部名"),
    ("776176", "WAV(hex)", "QQ 音乐内部名"),
    # ---- 酷狗音乐 ----
    ("kgm", "KGM", "酷狗音乐"),
    ("kgma", "KGMA", "酷狗音乐"),
    ("kgg", "KGG", "酷狗音乐"),
    ("vpr", "VPR", "酷狗音乐"),
    # ---- 酷我音乐 ----
    ("kwm", "KWM", "酷我音乐"),
)

_CATALOG += tuple(
    _f(ext, Kind.AUDIO, label, platform, input_only=True, encrypted=True)
    for ext, label, platform in _ENCRYPTED_EXT
)

#: 加密格式的"仅解密"目标 —— 保持原始音质，不做转码
ORIGINAL_EXT = "__original__"
ORIGINAL_SPEC = FormatSpec(
    ext=ORIGINAL_EXT,
    kind=Kind.AUDIO,
    label="原始格式",
    note="仅解密，保持原始音质",
)

#: 按扩展名索引（同名扩展名以首次出现为准，保证 gif 默认归视频）
BY_EXT: dict[str, FormatSpec] = {}
for _spec in _CATALOG:
    BY_EXT.setdefault(_spec.ext, _spec)


def spec_of(ext: str) -> FormatSpec | None:
    return BY_EXT.get(ext.lower().lstrip("."))


def kind_of(ext: str) -> Kind | None:
    spec = spec_of(ext)
    return spec.kind if spec else None


def label_of(ext: str) -> str:
    spec = spec_of(ext)
    return spec.label if spec else ext.upper()


def all_of_kind(kind: Kind, *, include_input_only: bool = False) -> list[FormatSpec]:
    """某大类下可选的格式列表，用于填充下拉框。"""
    seen: set[str] = set()
    out: list[FormatSpec] = []
    for spec in _CATALOG:
        if spec.kind is not kind or spec.ext in seen:
            continue
        if spec.input_only and not include_input_only:
            continue
        seen.add(spec.ext)
        out.append(spec)
    return out


def supported_extensions() -> list[str]:
    """所有可识别的输入扩展名，供文件对话框过滤用。"""
    return sorted({f".{e}" for e in BY_EXT})


def default_target(ext: str) -> str:
    """给定源格式，推荐一个常见的目标格式。"""
    kind = kind_of(ext)
    fallback = {
        Kind.VIDEO: "mp4",
        Kind.AUDIO: "mp3",
        Kind.IMAGE: "png",
        Kind.DOCUMENT: "pdf",
        Kind.ARCHIVE: "zip",
    }
    if kind is None:
        return "mp4"
    target = fallback[kind]
    if target == ext.lower():
        for spec in all_of_kind(kind):
            if spec.ext != ext.lower():
                return spec.ext
    return target
