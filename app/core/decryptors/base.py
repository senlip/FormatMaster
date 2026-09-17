"""加密音频解密层的公共契约。

国内音乐平台的"下载文件"本质是**自研加密封装**：音频本体还是标准
FLAC/MP3/AAC，外面套了一层平台自己的加密壳。把壳剥掉就得到能通用播放的文件。

设计边界（重要）：
* 本层只处理"用户自己从平台下载/缓存的文件"，剥掉平台自研加密壳。
* 不处理商业 DRM（Apple Music FairPlay、Spotify、Netflix 等）—— 那是另一回事。
* 解密器是可插拔的：新增平台 = 写一个适配器 + 注册一行。
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

#: 进度回调：(0.0~1.0, 提示文本)
ProgressFn = Callable[[float, str], None]


@dataclass
class DecryptInfo:
    """探测结果：不用解密音频本体就能拿到的信息。"""

    platform: str = ""            # 平台名，如 "网易云音乐"
    real_ext: str = ""            # 剥壳后的真实格式
    title: str = ""
    artist: str = ""
    album: str = ""
    cover: bytes = b""            # 封面图原始字节
    cover_ext: str = ""           # 封面图格式
    payload: dict = field(default_factory=dict)   # 各解密器自用的中间结果

    @property
    def display(self) -> str:
        if self.title and self.artist:
            return f"{self.title} — {self.artist}"
        return self.title or self.platform


@dataclass
class DecryptResult:
    ok: bool
    output: Path | None = None
    real_ext: str = ""
    info: DecryptInfo | None = None
    message: str = ""
    cancelled: bool = False


class BaseDecryptor(ABC):
    """所有平台解密器的抽象基类。"""

    #: 内部标识
    name: str = ""
    #: 界面上显示的平台名
    label: str = ""
    #: 负责的扩展名（不含点）
    extensions: tuple[str, ...] = ()
    #: 剥壳后可能得到哪些格式（仅用于界面提示）
    outputs: tuple[str, ...] = ()
    #: 本机离线解不开的扩展名（如 QQ 音乐 QMCv2，密钥在服务端）
    #: 有了它，界面能在"添加文件"阶段就如实告知，而不是等转换时才失败
    offline_unsupported: tuple[str, ...] = ()
    #: 对应的原因说明
    offline_note: str = ""

    # ------------------------------------------------------------------ #
    @abstractmethod
    def matches(self, head: bytes) -> bool:
        """根据文件头部字节判断是否属于本平台（魔数校验）。"""

    @abstractmethod
    def probe(self, path: Path) -> DecryptInfo:
        """读取元数据、判断真实格式。不修改文件，不做全量解密。"""

    @abstractmethod
    def decrypt(
        self,
        src: Path,
        dst: Path,
        info: DecryptInfo,
        on_progress: ProgressFn | None = None,
        cancel: threading.Event | None = None,
    ) -> DecryptResult:
        """把剥壳后的音频写到 dst。"""

    # ------------------------------------------------------------------ #
    def handles(self, path: Path, head: bytes) -> bool:
        """扩展名 + 魔数双重确认，避免误判同名文件。"""
        if path.suffix.lstrip(".").lower() not in self.extensions:
            return False
        return self.matches(head)

    # ------------------------------------------------------------------ #
    def sanity(self) -> tuple[bool, str]:
        """自检：运行本解密器所需的资源是否齐备。

        默认只报告已注册的扩展名数量；依赖外部资源（如密钥表）的解密器
        应当重写它，真正去读一次资源 —— 打包后"资源忘了带"是最常见的坑。
        """
        return True, f"{len(self.extensions)} 种扩展名"

    @staticmethod
    def _read_head(path: Path, size: int = 64) -> bytes:
        try:
            with open(path, "rb") as fh:
                return fh.read(size)
        except OSError:
            return b""


# --------------------------------------------------------------------------- #
def sniff_audio_format(data: bytes) -> str:
    """从字节流头部嗅探容器格式 —— 解密后用它决定输出扩展名。"""
    if data[:4] == b"fLaC":
        return "flac"
    if data[:3] == b"ID3":
        return "mp3"
    if data[:2] in (b"\xff\xfb", b"\xff\xfa", b"\xff\xf3", b"\xff\xf2", b"\xff\xe3"):
        return "mp3"
    if data[:4] == b"OggS":
        return "ogg"
    if data[:4] == b"RIFF":
        return "wav"
    if data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand[:3] == b"M4A":
            return "m4a"
        return "mp4"
    if data[:4] == b"MAC ":
        return "ape"
    if data[:4] == b"wvpk":
        return "wv"
    return ""


MIME_BY_EXT = {
    "flac": "audio/flac",
    "mp3": "audio/mpeg",
    "ogg": "audio/ogg",
    "wav": "audio/wav",
    "m4a": "audio/mp4",
    "mp4": "video/mp4",
    "ape": "audio/ape",
}
