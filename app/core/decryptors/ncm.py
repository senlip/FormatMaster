"""网易云音乐 .ncm 解密。

文件结构（字段均为小端）：

    偏移   长度  含义
    0x00   8     魔数 "CTENFDAM"
    0x08   2     版本号
    0x0A   4     密钥块长度
    0x0E   n     密钥块   → 逐字节异或 0x64 → AES-128-ECB → 去填充 → 去前 17 字节 → RC4 密钥
          4     元数据块长度
          n     元数据块 → 逐字节异或 0x63 → 去前 22 字节 → Base64 → AES-128-ECB → 去填充 → 去前 6 字节 → JSON
          4     校验值
          5     保留区
          4     封面区长度
          4     封面图长度
          n     封面图（JPEG/PNG，未加密）
          -     音频数据（RC4 变种流加密）

音频流密码是 RC4 的改良版：KSA 与标准一致，PRGA 先把整张 S 盒铺开成
256 字节掩码表，再循环取用 —— 注意取值从表内第 1 个字节开始（跳过首个）。

关于音频起点：各版本客户端在"校验值 / 保留区 / 封面区"这几个字段的长度上
有过调整，纯按字段偏移推算容易差几字节，而差几字节会让整个流密码错位、
解出来全是噪声。所以这里先按字段算一个候选位置，再用**音频容器特征**
（fLaC / ID3 / OggS / ftyp）在候选附近自动校准。
"""
from __future__ import annotations

import base64
import json
import struct
import threading
from pathlib import Path

from ._aes import AES128ECB, pkcs7_unpad
from .base import BaseDecryptor, DecryptInfo, DecryptResult, ProgressFn, sniff_audio_format

MAGIC = b"CTENFDAM"
CORE_KEY = bytes.fromhex("687a4852416d736f356b496e62617857")
META_KEY = bytes.fromhex("2331346c6a6b5f215c5d2630553c2728")
_IDENT_HEADER = b"neteasecloudmusic"

#: 大整数异或的分块粒度，兼顾速度与内存峰值
_CHUNK = 1 << 20

#: 音频起点候选偏移（相对按字段推算的位置）
_OFFSET_CANDIDATES = (0, -4, 4, -8, 8, -12, 12, -16, 16, -1, 1)


def _rc4_mask_table(key: bytes) -> bytes:
    """标准 RC4 KSA + 铺开式 PRGA，得到 256 字节掩码表。"""
    s = list(range(256))
    j = 0
    klen = len(key)
    for i in range(256):
        j = (j + s[i] + key[i % klen]) & 0xFF
        s[i], s[j] = s[j], s[i]
    return bytes(s[(s[i] + s[(i + s[i]) & 0xFF]) & 0xFF] for i in range(256))


def _decrypt_stream(data: bytes, table: bytes) -> bytes:
    """用掩码表循环异或数据。

    掩码取值整体偏移 1 个字节（stream[i] = table[(i + 1) % 256]）。
    分块用大整数异或实现 —— CPython 的大整数运算是 C 层循环，
    处理几十 MB 的音频只需要零点几秒。
    """
    n = len(data)
    if not n:
        return b""
    out = bytearray(n)
    for base in range(0, n, _CHUNK):
        end = min(base + _CHUNK, n)
        size = end - base
        start = (base + 1) % 256
        reps = size // 256 + 2
        stream = (table * reps)[start:start + size]
        a = int.from_bytes(data[base:end], "little")
        b = int.from_bytes(stream, "little")
        out[base:end] = (a ^ b).to_bytes(size, "little")
    return bytes(out)


def locate_audio_offset(fh, guess: int, table: bytes) -> int:
    """在候选位置附近，用音频容器特征校准音频数据起点。"""
    for delta in _OFFSET_CANDIDATES:
        pos = guess + delta
        if pos < 0:
            continue
        fh.seek(pos)
        head = fh.read(64)
        if len(head) < 8:
            continue
        if sniff_audio_format(_decrypt_stream(head, table)):
            return pos
    return guess


class NcmDecryptor(BaseDecryptor):
    name = "ncm"
    label = "网易云音乐"
    extensions = ("ncm",)
    outputs = ("mp3", "flac")

    # ------------------------------------------------------------------ #
    def matches(self, head: bytes) -> bool:
        return head[:8] == MAGIC

    # ------------------------------------------------------------------ #
    def probe(self, path: Path) -> DecryptInfo:
        info = DecryptInfo(platform=self.label)
        try:
            with open(path, "rb") as fh:
                if fh.read(8) != MAGIC:
                    info.payload["error"] = "文件头不是 CTENFDAM，不是有效的 NCM 文件"
                    return info

                fh.seek(2, 1)                                  # 版本号

                key_len = struct.unpack("<I", fh.read(4))[0]
                key_blob = bytes(b ^ 0x64 for b in fh.read(key_len))
                raw_key = pkcs7_unpad(AES128ECB(CORE_KEY).decrypt(key_blob))
                if not raw_key.startswith(_IDENT_HEADER):
                    info.payload["error"] = "密钥块解密异常，文件可能已损坏"
                    return info
                rc4_key = raw_key[len(_IDENT_HEADER):]

                meta_len = struct.unpack("<I", fh.read(4))[0]
                meta: dict = {}
                if meta_len:
                    blob = bytes(b ^ 0x63 for b in fh.read(meta_len))[22:]
                    plain = pkcs7_unpad(AES128ECB(META_KEY).decrypt(base64.b64decode(blob)))
                    meta = json.loads(plain[6:].decode("utf-8"))

                fh.seek(5, 1)                                  # 校验值 + 保留区
                image_space = struct.unpack("<I", fh.read(4))[0]
                image_size = struct.unpack("<I", fh.read(4))[0]
                cover = fh.read(image_size) if image_size else b""
                if image_space > image_size:
                    fh.seek(image_space - image_size, 1)

                table = _rc4_mask_table(rc4_key)
                audio_offset = locate_audio_offset(fh, fh.tell(), table)
        except (OSError, ValueError, KeyError) as exc:
            info.payload["error"] = f"NCM 解析失败：{exc}"
            return info

        fmt = str(meta.get("format", "")).lower()
        if fmt not in ("mp3", "flac", "aac", "m4a", "wav"):
            # 元数据缺失时按体积猜（无损通常明显更大）
            fmt = "flac" if path.stat().st_size > 16 * 1024 * 1024 else "mp3"

        artists = meta.get("artist") or []
        if isinstance(artists, list):
            artist = "/".join(a[0] for a in artists if isinstance(a, (list, tuple)) and a)
        else:
            artist = str(artists)

        info.real_ext = fmt
        info.title = str(meta.get("musicName") or path.stem)
        info.artist = artist
        info.album = str(meta.get("album") or "")
        info.cover = cover
        if cover[:4] == b"\x89PNG":
            info.cover_ext = "png"
        elif cover[:2] == b"\xff\xd8":
            info.cover_ext = "jpg"
        info.payload.update(
            rc4_key=rc4_key,
            audio_offset=audio_offset,
            image_space=image_space,
            image_size=image_size,
        )
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

        rc4_key: bytes = info.payload["rc4_key"]      # type: ignore[assignment]
        offset: int = int(info.payload.get("audio_offset", 0))

        try:
            with open(src, "rb") as fh:
                fh.seek(offset)
                payload = fh.read()
        except OSError as exc:
            return DecryptResult(False, message=f"读取音频数据失败：{exc}")

        if not payload:
            return DecryptResult(False, message="没有读到音频数据")

        if on_progress:
            on_progress(0.1, "解析加密流")

        table = _rc4_mask_table(rc4_key)
        audio = _decrypt_stream(payload, table)

        if cancel is not None and cancel.is_set():
            return DecryptResult(False, message="已取消", cancelled=True)

        real_ext = sniff_audio_format(audio) or info.real_ext or "mp3"
        if dst.suffix.lstrip(".").lower() != real_ext:
            dst = dst.with_suffix("." + real_ext)

        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            with open(dst, "wb") as fh:
                fh.write(audio)
        except OSError as exc:
            return DecryptResult(False, message=f"写入失败：{exc}")

        if on_progress:
            on_progress(1.0, "完成")
        return DecryptResult(True, output=dst, real_ext=real_ext, info=info, message="完成")
