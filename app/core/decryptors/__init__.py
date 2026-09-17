"""平台加密格式解密层。

国内音乐平台下载的文件是"自研加密封装"：音频本体仍是标准 FLAC/MP3/AAC，
外面套了平台自己的壳。这里负责把壳剥掉。

新增平台：写一个 BaseDecryptor 子类 → 在 _DECRYPTORS 里加一行。
界面、格式路由、任务调度都从本模块取信息，不需要改别处。

法律边界：只处理用户自己从平台下载/缓存的文件；不涉及商业 DRM
（Apple Music FairPlay、Spotify、Netflix 等）。
"""
from __future__ import annotations

from pathlib import Path

from .base import BaseDecryptor, DecryptInfo, DecryptResult, sniff_audio_format
from .kugou import KugouDecryptor
from .kuwo import KuwoDecryptor
from .ncm import NcmDecryptor
from .qmc import QmcDecryptor

_DECRYPTORS: tuple[BaseDecryptor, ...] = (
    NcmDecryptor(),      # 网易云音乐
    QmcDecryptor(),      # QQ 音乐
    KugouDecryptor(),    # 酷狗音乐
    KuwoDecryptor(),     # 酷我音乐
)

#: 扩展名 → 解密器（同一扩展名以先注册者为准）
BY_EXT: dict[str, BaseDecryptor] = {}
for _d in _DECRYPTORS:
    for _ext in _d.extensions:
        BY_EXT.setdefault(_ext, _d)


def all_decryptors() -> tuple[BaseDecryptor, ...]:
    return _DECRYPTORS


def decryptor_for_ext(ext: str) -> BaseDecryptor | None:
    return BY_EXT.get(ext.lower().lstrip("."))


def find_for(path: Path) -> BaseDecryptor | None:
    """按扩展名定位解密器（魔数校验在 probe 阶段做）。"""
    return decryptor_for_ext(path.suffix)


def encrypted_extensions() -> set[str]:
    return set(BY_EXT)


def is_encrypted(ext: str) -> bool:
    return ext.lower().lstrip(".") in BY_EXT


def platform_of(ext: str) -> str:
    d = decryptor_for_ext(ext)
    return d.label if d else ""


def offline_reason(ext: str) -> str:
    """该扩展名本机离线解不开的原因；空串表示可以处理。"""
    ext = ext.lower().lstrip(".")
    d = decryptor_for_ext(ext)
    if d is None:
        return ""
    if ext in d.offline_unsupported:
        return d.offline_note or f"{d.label} 的这种加密格式本机无法离线解密。"
    return ""


def sanity_check() -> list[dict[str, object]]:
    """各平台解密器的可用性自检。

    冻结打包后用它确认随包资源（尤其是 4MB 的酷狗密钥表）没被漏掉 ——
    这类问题在源码环境下永远暴露不出来。
    """
    out: list[dict[str, object]] = []
    for d in _DECRYPTORS:
        try:
            ok, detail = d.sanity()
        except Exception as exc:                     # 自检本身不能把程序带崩
            ok, detail = False, f"自检异常：{exc}"
        out.append({
            "name": d.name,
            "label": d.label,
            "extensions": len(d.extensions),
            "ok": ok,
            "detail": detail,
        })
    return out


__all__ = [
    "BaseDecryptor",
    "DecryptInfo",
    "DecryptResult",
    "all_decryptors",
    "decryptor_for_ext",
    "encrypted_extensions",
    "find_for",
    "is_encrypted",
    "offline_reason",
    "platform_of",
    "sanity_check",
    "sniff_audio_format",
]
