"""酷我音乐 .kwm 解密。

结构：
    0x00  16 魔数 "yeelion-kuwo-tme"
    0x18   8 文件密钥
    0x20 ... 加密音频数据
    音频数据实际从 0x400 开始

密钥派生：把 8 字节文件密钥当成小端 64 位整数，转成十进制字符串，
再补齐/截断到 32 个字符，最后与固定的 32 字节常量逐字符异或，
得到真正用于解密的 32 字节掩码。掩码对音频数据循环使用。
"""
from __future__ import annotations

import threading
from pathlib import Path

from .base import BaseDecryptor, DecryptInfo, DecryptResult, ProgressFn, sniff_audio_format

MAGIC = b"yeelion-kuwo-tme"
PREDEFINED_KEY = b"MoOtOiTvINGwd2E6n0E1i7L5t2IoOoNk"
KEY_OFFSET = 0x18
AUDIO_OFFSET = 0x400
MASK_LEN = 32
_CHUNK = 1 << 22


def _normalize(raw: str) -> str:
    """把密钥字符串补齐/截断到 32 字符（对齐酷我客户端的 trimKey 行为）。"""
    n = len(raw)
    if n == MASK_LEN:
        return raw
    if n > MASK_LEN:
        return raw[:MASK_LEN]
    if n == 0:
        return "0" * MASK_LEN
    reps = MASK_LEN // n + 1
    return (raw * reps)[:MASK_LEN]


def build_mask(file_key: bytes) -> bytes:
    key_str = _normalize(str(int.from_bytes(file_key, "little")))
    return bytes(PREDEFINED_KEY[i] ^ ord(key_str[i]) for i in range(MASK_LEN))


def _xor_mask(data: bytes, mask: bytes) -> bytes:
    if not data:
        return b""
    out = bytearray(len(data))
    reps = len(data) // MASK_LEN + 2
    full = (mask * reps)[:len(data)]
    for base in range(0, len(data), _CHUNK):
        end = min(base + _CHUNK, len(data))
        a = int.from_bytes(data[base:end], "little")
        b = int.from_bytes(full[base:end], "little")
        out[base:end] = (a ^ b).to_bytes(end - base, "little")
    return bytes(out)


class KuwoDecryptor(BaseDecryptor):
    name = "kwm"
    label = "酷我音乐"
    extensions = ("kwm",)
    outputs = ("mp3", "flac", "m4a", "ogg", "wav")

    # ------------------------------------------------------------------ #
    def matches(self, head: bytes) -> bool:
        return head[:16] == MAGIC

    # ------------------------------------------------------------------ #
    def probe(self, path: Path) -> DecryptInfo:
        info = DecryptInfo(platform=self.label)
        try:
            with open(path, "rb") as fh:
                head = fh.read(AUDIO_OFFSET + 4096)
        except OSError as exc:
            info.payload["error"] = f"读取失败：{exc}"
            return info

        if head[:16] != MAGIC:
            info.payload["error"] = "文件头不是 yeelion-kuwo-tme，不是有效的 KWM 文件"
            return info
        if len(head) < AUDIO_OFFSET + 16:
            info.payload["error"] = "文件不完整（没有音频数据）"
            return info

        mask = build_mask(head[KEY_OFFSET:KEY_OFFSET + 8])
        plain = _xor_mask(head[AUDIO_OFFSET:AUDIO_OFFSET + 4096], mask)
        fmt = sniff_audio_format(plain)
        if not fmt:
            info.payload["error"] = (
                "解密后不是可识别的音频流 —— 文件可能损坏，"
                "或是较新版本酷我客户端的分块加密（需要服务端鉴权）。"
            )
            return info

        info.real_ext = fmt
        info.title = path.stem
        info.payload["mask"] = mask
        return info

    # ------------------------------------------------------------------ #
    def decrypt(
        self,
        src: Path,
        dst: Path,
        info: DecryptInfo,
        on_progress: ProgressFn | None = None,
        cancel: threading.Event | None = None,
    ) -> DecryptResult:
        err = info.payload.get("error")
        if err:
            return DecryptResult(False, message=str(err))

        mask: bytes = info.payload["mask"]
        try:
            with open(src, "rb") as fh:
                fh.seek(AUDIO_OFFSET)
                payload = fh.read()
        except OSError as exc:
            return DecryptResult(False, message=f"读取失败：{exc}")

        if on_progress:
            on_progress(0.1, "解密中")
        if cancel is not None and cancel.is_set():
            return DecryptResult(False, message="已取消", cancelled=True)

        audio = _xor_mask(payload, mask)
        real_ext = sniff_audio_format(audio) or info.real_ext or "mp3"
        if dst.suffix.lstrip(".").lower() != real_ext:
            dst = dst.with_suffix("." + real_ext)

        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(audio)
        except OSError as exc:
            return DecryptResult(False, message=f"写入失败：{exc}")

        if on_progress:
            on_progress(1.0, "完成")
        return DecryptResult(True, output=dst, real_ext=real_ext, info=info, message="完成")
