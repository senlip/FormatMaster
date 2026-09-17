"""QQ 音乐 .qmc* 解密。

QMC（旧版）不是密钥表加密，而是一串**掩码流**：解密就是把音频每个字节
与对应位置的掩码异或。掩码来自一张 8×7 的表做之字形（zigzag）扫描：

    x 从 -1 起步向右走，走到 x>6 折返向左，走到 x<0 再折返；
    每次触边插入一个固定字节（左边界 0xC3、右边界 0xD8），
    取表值的位置是 table[y][x]，y 每完成一次往返翻转。

扫描周期是 128 个字节。但每产生 0x8000 个掩码字节会**丢弃一个**，
导致此后掩码相位整体前移一位 —— 这正是很多早期实现只能解出前 32KB、
后面全是噪声的原因。

覆盖 .qmc0/.qmc3/.qmcflac/.qmcogg/.tkm 等旧版格式。
新版 .mflac/.mgg/.qmc2 把密钥换成服务端下发的 QRC 密钥，离线拿不到，
这里会明确报错而不是产出一个能播但其实坏了半个文件的结果。
"""
from __future__ import annotations

import struct
import threading
from pathlib import Path

from .base import BaseDecryptor, DecryptInfo, DecryptResult, ProgressFn, sniff_audio_format

#: 8 行 × 7 列的种子表
_SEED_MAP = (
    (0x4A, 0xD6, 0xCA, 0x90, 0x67, 0xF7, 0x52),
    (0x5E, 0x95, 0x23, 0x9F, 0x13, 0x11, 0x7E),
    (0x47, 0x74, 0x3D, 0x90, 0xAA, 0x3F, 0x51),
    (0xC6, 0x09, 0xD5, 0x9F, 0xFA, 0x66, 0xF9),
    (0xF3, 0xD6, 0xA1, 0x90, 0xA0, 0xF7, 0xF0),
    (0x1D, 0x95, 0xDE, 0x9F, 0x84, 0x11, 0xF4),
    (0x0E, 0x74, 0xBB, 0x90, 0xBC, 0x3F, 0x92),
    (0x00, 0x09, 0x5B, 0x9F, 0x62, 0x66, 0xA1),
)

_CYCLE_LEN = 128
_SEG = 0x8000
_CHUNK = 1 << 22          # 分段异或，避免超大文件吃掉过多内存

#: 扩展名 → 期望的明文格式（用于界面提示）
EXPECTED = {
    "qmcflac": "flac", "bkcflac": "flac", "666c6163": "flac",
    "qmcogg": "ogg", "bkcogg": "ogg", "qmc2": "ogg", "6f6767": "ogg",
    "qmc0": "mp3", "qmc3": "mp3", "bkcmp3": "mp3", "6d7033": "mp3",
    "qmc4": "ogg", "qmc6": "ogg", "qmc8": "ogg",
    "tkm": "m4a", "bkcm4a": "m4a", "6d3461": "m4a",
    "bkcwav": "wav", "776176": "wav",
    "bkcape": "ape",
    "bkcwma": "wma",
    "mflac": "flac", "mflac0": "flac",
    "mgg": "ogg", "mgg0": "ogg", "mgg1": "ogg", "mggl": "ogg",
    "mmp4": "mp4",
}

#: 这些扩展名是新版（QMCv2），需要服务端密钥
V2_EXTS = frozenset({
    "mflac", "mflac0", "mgg", "mgg0", "mgg1", "mggl", "mmp4",
})

_V1_EXTS = frozenset(EXPECTED) - V2_EXTS


def _build_cycle() -> bytes:
    """跑满一个 128 字节周期。"""
    x, y, dx = -1, 8, 1
    out = bytearray()
    for _ in range(_CYCLE_LEN):
        if x < 0:
            dx = 1
            y = (8 - y) % 8
            value = 0xC3
        elif x > 6:
            dx = -1
            y = 7 - y
            value = 0xD8
        else:
            value = _SEED_MAP[y][x]
        x += dx
        out.append(value)
    return bytes(out)


_CYCLE = _build_cycle()


def _segment_start(k: int) -> int:
    """第 k 段掩码的起始输出位置（0-based）。

    每段内部掩码严格 128 周期；段的分界由"每 0x8000 个 index 丢弃一个"
    这条规则推导而来：
        a_k = disc[k-1] - (k-1)
        disc[0] = 0x8000, disc[m] = 0xFFFF + 0x8000*(m-1)  (m >= 1)
    """
    if k <= 0:
        return 0
    m = k - 1
    disc = _SEG if m == 0 else 0xFFFF + _SEG * (m - 1)
    return disc - m


def _xor_mask(data: bytes) -> bytes:
    """用 QMC 掩码流异或数据。"""
    total = len(data)
    if not total:
        return b""
    out = bytearray(total)
    n = 0
    k = 0
    while n < total:
        seg_end = _segment_start(k + 1)
        take = min(seg_end - n, total - n)
        if take <= 0:                      # 理论上不会发生，兜底防死循环
            take = min(_CHUNK, total - n)
        base = (n + k) % _CYCLE_LEN
        reps = take // _CYCLE_LEN + 2
        mask = (_CYCLE * reps)[base:base + take]
        a = int.from_bytes(data[n:n + take], "little")
        b = int.from_bytes(mask, "little")
        out[n:n + take] = (a ^ b).to_bytes(take, "little")
        n += take
        k += 1
    return bytes(out)


class QmcDecryptor(BaseDecryptor):
    name = "qmc"
    label = "QQ 音乐"
    extensions = tuple(sorted(EXPECTED))
    outputs = ("mp3", "flac", "ogg", "m4a")
    offline_unsupported = tuple(sorted(V2_EXTS))
    offline_note = (
        "这是 QQ 音乐新版加密（QMCv2），密钥由服务端下发，离线无法解密。"
        "请在 QQ 音乐里重新下载为标准格式，或使用较旧版本的客户端下载后再试。"
    )

    # ------------------------------------------------------------------ #
    def matches(self, head: bytes) -> bool:
        """旧版 QMC 没有魔数，只能靠"解出来像不像音频"判断。"""
        if len(head) < 16:
            return False
        return bool(sniff_audio_format(_xor_mask(head[:64])))

    # ------------------------------------------------------------------ #
    def probe(self, path: Path) -> DecryptInfo:
        ext = path.suffix.lstrip(".").lower()
        info = DecryptInfo(platform=self.label)

        if ext in V2_EXTS:
            info.payload["error"] = self.offline_note
            return info

        try:
            with open(path, "rb") as fh:
                head = fh.read(512)
        except OSError as exc:
            info.payload["error"] = f"读取失败：{exc}"
            return info

        plain = _xor_mask(head)
        fmt = sniff_audio_format(plain)
        if not fmt:
            expected = EXPECTED.get(ext, "")
            if expected and sniff_audio_format(_xor_mask(head[:64])) == expected:
                fmt = expected
        if not fmt:
            info.payload["error"] = (
                "解密后不是可识别的音频流 —— 可能是新版 QMCv2 加密，"
                "或是文件已损坏/下载不完整。"
            )
            return info

        info.real_ext = fmt
        info.title = path.stem
        info.artist = ""
        info.payload["size"] = path.stat().st_size
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

        try:
            data = src.read_bytes()
        except OSError as exc:
            return DecryptResult(False, message=f"读取失败：{exc}")

        if on_progress:
            on_progress(0.05, "解密中")

        size = len(data)
        out = bytearray(size)
        n = 0
        k = 0
        while n < size:
            if cancel is not None and cancel.is_set():
                return DecryptResult(False, message="已取消", cancelled=True)
            seg_end = _segment_start(k + 1)
            take = min(seg_end - n, size - n)
            if take <= 0:
                take = min(_CHUNK, size - n)
            base = (n + k) % _CYCLE_LEN
            reps = take // _CYCLE_LEN + 2
            mask = (_CYCLE * reps)[base:base + take]
            a = int.from_bytes(data[n:n + take], "little")
            b = int.from_bytes(mask, "little")
            out[n:n + take] = (a ^ b).to_bytes(take, "little")
            n += take
            k += 1
            if on_progress and size:
                on_progress(0.05 + 0.9 * n / size, f"解密 {n * 100 // size}%")

        audio = bytes(out)
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
