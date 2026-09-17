"""酷狗音乐 KGM / KGMA / VPR 解密。

文件结构：

    偏移   长度  含义
    0x00   16    文件头魔数
    0x10    4    头部长度（小端）—— 音频通常从这里开始
    0x1C   16    文件私钥
    ...          文件区
    头部长度处    加密音频数据

头部代次 —— 酷狗这些年换过好几套壳，靠**前 8 字节**区分家族：

    KGM / KGMA   7C D5 32 EB 86 02 7F 4B ...   异或掩码            支持
    VPR          05 28 BC 96 E9 E4 5A 43 ...   异或掩码 + Viper 层   支持
    KGG / 新版   7F 4B 47 4D ...               密钥由公钥表派生      不支持
    旧版 KGMA    4B 47 4D 41（ASCII "KGMA"）     AES 封装            不支持

两个容易踩的坑，都在这里处理掉了：

1. **只有前 8 字节稳定**。KGM 与 VPR 的后 8 字节随客户端版本变化，
   所以判定用前缀匹配；用全 16 字节相等会把相当一部分真实文件判死。
2. **音频起点不能只信 0x10 那个字段**。不同变体里它的含义不一样，
   有的甚至是 0。所以做成自适应：声明值 → 常见值 → 声明值附近微调，
   每一步都拿"解出来像不像音频"来反验。

解密是两层异或叠加：

    第一层（私钥流，17 字节周期；第 17 个字节固定为 0）
        med8  = key1[i % 17] ^ data[i]
        med8 ^= (med8 & 0x0F) << 4

    第二层（掩码流）
        msk8  = MASK_V2_PRE_DEF[i % 272] ^ MaskV2[i >> 4]
        msk8 ^= (msk8 & 0x0F) << 4

    结果 = med8 ^ msk8

其中 MaskV2 是一张固定的 4 MB 表（随软件分发，见 assets/kgm.mask），
它按"每 16 个字节共用一个表项"的方式铺开，正好覆盖前 64 MB 音频。

VPR 在最后还要多异或一层 17 字节的 VPR_MASK_DIFF。

性能说明：`x ^= (x & 0x0F) << 4` 是纯字节映射，预计算成 256 字节的
translate 表；XOR 用大整数一次完成。这样几千万字节的解密只需要零点几秒，
而不是逐字节跑 Python 循环。
"""
from __future__ import annotations

import struct
import threading
from pathlib import Path

from ._kgm_tables import KGM_HEADER, MASK_V2_PRE_DEF, VPR_HEADER, VPR_MASK_DIFF
from .base import BaseDecryptor, DecryptInfo, DecryptResult, ProgressFn, sniff_audio_format

HEADER_LEN_OFFSET = 0x10
KEY_OFFSET = 0x1C
KEY_SIZE = 16
PERIOD = 17                      # 私钥周期：16 字节密钥 + 1 个 0
MASK_PERIOD = 16                 # MaskV2 的每个表项覆盖 16 字节音频

#: 判定头部家族只看前 8 字节 —— 后 8 字节随客户端版本变
KGM_PREFIX = KGM_HEADER[:8]
VPR_PREFIX = VPR_HEADER[:8]
#: 新版 KGG：0x7F + ASCII "KGM"
KGG_MAGIC = b"\x7fKGM"
#: 旧版 KGMA：纯 ASCII 头，音频载荷用 AES 封装
KGMA_LEGACY_MAGIC = b"KGMA"

#: 读这么长就够判定头部（0x1C + 16 字节密钥）
MIN_HEAD = 0x2C

#: 音频起点的常见取值 —— 0x400 是绝大多数文件的实际值
COMMON_OFFSETS = (0x400, 0x40, 0x80, 0x100, 0x200, 0x800, 0x1000, 0x2000)

#: 认出来但解不了的那些代次，各自给一句说人话的解释
UNSUPPORTED_NOTE: dict[str, str] = {
    "kgg": (
        "这是酷狗新版 KGG / KGM 加密：音频密钥由约 70 MB 的公钥表派生，"
        "不随文件下发，本机离线无法解密。请在酷狗里改用标准音质重新下载。"
    ),
    "kgma_legacy": (
        "这是酷狗旧版 KGMA（ASCII 头 + AES 封装）：音频载荷用 AES 加密，"
        "密钥在客户端运行时派生，本机离线无法解密。"
    ),
}

_XFORM = bytes(b ^ ((b & 0x0F) << 4) for b in range(256))
_EXPANDED = tuple(bytes([b]) * MASK_PERIOD for b in range(256))

_MASK_CACHE: dict[str, object] = {"n": 0, "data": b""}


# --------------------------------------------------------------------------- #
# 头部识别
# --------------------------------------------------------------------------- #
def classify(head: bytes) -> tuple[str, str]:
    """判断文件头属于酷狗的哪一代。

    返回 ``(代号, 人能看懂的说明)``；代号为空串表示不是酷狗文件。
    诊断工具与解密器共用它，保证两处结论一致。
    """
    if head.startswith(KGM_PREFIX):
        return "kgm", "KGM / KGMA（异或掩码加密）"
    if head.startswith(VPR_PREFIX):
        return "vpr", "VPR（异或掩码 + Viper 附加层）"
    if head.startswith(KGG_MAGIC):
        return "kgg", "KGG / 新版 KGM（密钥由公钥表派生）"
    if head.startswith(KGMA_LEGACY_MAGIC):
        return "kgma_legacy", "旧版 KGMA（ASCII 头 + AES 封装）"
    return "", ""


def is_supported(head: bytes) -> bool:
    variant, _ = classify(head)
    return variant in ("kgm", "vpr")


# --------------------------------------------------------------------------- #
# 密钥表
# --------------------------------------------------------------------------- #
def mask_table() -> bytes:
    """读取随包分发的酷狗密钥表；缺失时返回空。"""
    from ..runtime import ASSETS_DIR

    path = ASSETS_DIR / "kgm.mask"
    try:
        return path.read_bytes()
    except OSError:
        return b""


def mask_stream(n: int) -> bytes:
    """铺开完整的掩码流（**含固定修正表**）：

        mask[i] = MASK_V2_PRE_DEF[i % 272] ^ MaskV2[i >> 4]

    注意这两个部分是**乘性的两层**，缺一不可：
    ``MASK_V2_PRE_DEF`` 是 272 字节的固定修正表，``MaskV2`` 则是"每 16 字节
    共用一个表项"铺开的。曾经漏掉前者，结果每个字节都还差一层异或 ——
    而自测因为用同一个函数反向造样本，恒真通过，测不出来。修正的写法在
    注释里是对的，实现里却没有，所以现在把两者写在一起、不再分家。

    结果按最长一次请求缓存 —— 批量解密同一批歌时只付一次展开开销。
    """
    if n <= 0:
        return b""
    if int(_MASK_CACHE["n"]) >= n:
        return bytes(_MASK_CACHE["data"])[:n]

    table = mask_table()
    if not table:
        return b""

    reps = (n + MASK_PERIOD - 1) // MASK_PERIOD
    parts: list[bytes] = []
    done = 0
    tlen = len(table)
    while done < reps:
        take = min(tlen, reps - done)
        parts.append(b"".join(_EXPANDED[b] for b in table[:take]))
        done += take
    spread = b"".join(parts)                      # spread[i] = MaskV2[i >> 4]

    # 叠加 272 字节周期表。分块做异或以压住峰值内存，
    # 块大小取 272 的整数倍，保证每块都从周期起点对齐（相位不会错位）。
    chunk = 272 * 3855                            # ≈1 MB，且是 272 的整数倍
    out: list[bytes] = []
    for off in range(0, len(spread), chunk):
        piece = spread[off:off + chunk]
        out.append(_xor(piece, _repeat(MASK_V2_PRE_DEF, len(piece))))
    data = b"".join(out)[:n]

    _MASK_CACHE["n"] = n
    _MASK_CACHE["data"] = data
    return data


# --------------------------------------------------------------------------- #
# 解密核心
# --------------------------------------------------------------------------- #
def _repeat(pattern: bytes, n: int) -> bytes:
    if n <= 0 or not pattern:
        return b""
    return (pattern * (n // len(pattern) + 2))[:n]


def _xor(a: bytes, b: bytes) -> bytes:
    n = len(a)
    if not n:
        return b""
    return (int.from_bytes(a, "little") ^ int.from_bytes(b, "little")).to_bytes(n, "little")


def decrypt_bytes(data: bytes, key1: bytes, is_vpr: bool) -> bytes:
    """按酷狗算法解密一段音频（可分段调用，结果与整体解密一致）。"""
    n = len(data)
    if not n:
        return b""

    med = _xor(data, _repeat(key1, n)).translate(_XFORM)

    msk = mask_stream(n)
    if not msk:
        raise RuntimeError("缺少酷狗密钥表（assets/kgm.mask）")
    msk = msk.translate(_XFORM)

    out = _xor(med, msk)
    if is_vpr:
        out = _xor(out, _repeat(VPR_MASK_DIFF, n))
    return out


# --------------------------------------------------------------------------- #
# 音频起点定位
# --------------------------------------------------------------------------- #
def _probe_at(fh, offset: int, key1: bytes, is_vpr: bool, sample: int = 4096) -> str:
    """在指定偏移处试解一小段，返回嗅探到的音频格式（空串＝不是音频）。"""
    if offset < 0:
        return ""
    try:
        fh.seek(offset)
        blob = fh.read(sample)
    except OSError:
        return ""
    if len(blob) < 64:
        return ""
    try:
        plain = decrypt_bytes(blob, key1, is_vpr)
    except RuntimeError:
        return ""
    return sniff_audio_format(plain)


def offset_candidates(declared: int, total: int) -> list[int]:
    """候选音频起点，按可能性从高到低。

    顺序：声明值 → 常见值 → 声明值附近微调。
    微调放在最后是因为"整体偏几字节"比"字段完全是别的含义"少见得多。
    """
    out: list[int] = []
    sane = 16 <= declared <= total

    if sane:
        out.append(declared)
    for cand in COMMON_OFFSETS:
        if cand <= total and cand not in out:
            out.append(cand)
    if sane:
        for delta in range(1, 17):
            for cand in (declared - delta, declared + delta):
                if cand >= 16 and cand not in out:
                    out.append(cand)
    return out


def locate_audio_offset(fh, declared: int, total: int, key1: bytes, is_vpr: bool) -> tuple[int, str]:
    """找出音频真正的起点，并顺带确认它是可解密的。

    返回 ``(偏移, 格式)``；格式为空串表示所有候选都试过了、解不出来。
    """
    for cand in offset_candidates(declared, total):
        fmt = _probe_at(fh, cand, key1, is_vpr)
        if fmt:
            return cand, fmt
    return 0, ""


# --------------------------------------------------------------------------- #
class KugouDecryptor(BaseDecryptor):
    name = "kgm"
    label = "酷狗音乐"
    extensions = ("kgm", "kgma", "kgg", "vpr")
    outputs = ("mp3", "flac", "ogg", "m4a")
    #: KGG 是新版公钥表方案，文件里没有密钥，离线解不开
    offline_unsupported = ("kgg",)
    offline_note = (
        "酷狗新版 KGG 加密：音频密钥由约 70 MB 的公钥表派生，"
        "不随文件下发，本机离线无法解密。"
    )

    # ------------------------------------------------------------------ #
    def matches(self, head: bytes) -> bool:
        """只比前 8 字节 —— 后 8 字节随客户端版本变化。"""
        return is_supported(head)

    # ------------------------------------------------------------------ #
    def sanity(self) -> tuple[bool, str]:
        """真读一次密钥表 —— 没带 kgm.mask 的话这里必须报出来。"""
        table = mask_table()
        if not table:
            return False, "缺少密钥表 app/assets/kgm.mask"
        stream = mask_stream(MASK_PERIOD * 4)
        if len(stream) != MASK_PERIOD * 4:
            return False, "密钥表展开异常"
        return True, f"{len(table)} 字节密钥表就绪"

    # ------------------------------------------------------------------ #
    def probe(self, path: Path) -> DecryptInfo:
        info = DecryptInfo(platform=self.label)
        declared = 0
        offset = 0
        fmt = ""

        try:
            size = path.stat().st_size
            with open(path, "rb") as fh:
                head = fh.read(MIN_HEAD)
                if len(head) < MIN_HEAD:
                    info.payload["error"] = "文件不完整（头部被截断）"
                    return info

                variant, desc = classify(head)
                if not variant:
                    info.payload["error"] = (
                        "文件头不是酷狗 KGM / KGMA / VPR 特征 —— "
                        "可能不是酷狗下载的文件，或是被改过扩展名的其他格式。"
                    )
                    return info
                info.payload["variant"] = desc

                if variant in UNSUPPORTED_NOTE:
                    info.payload["error"] = UNSUPPORTED_NOTE[variant]
                    return info

                if not mask_table():
                    info.payload["error"] = (
                        "缺少酷狗密钥表（assets/kgm.mask），无法解密。"
                        "该文件需随软件一同分发。"
                    )
                    return info

                is_vpr = variant == "vpr"
                declared = struct.unpack_from("<I", head, HEADER_LEN_OFFSET)[0]
                key1 = head[KEY_OFFSET:KEY_OFFSET + KEY_SIZE]
                if len(key1) < KEY_SIZE:
                    info.payload["error"] = "文件不完整（缺少密钥字段）"
                    return info
                key1 += b"\x00"

                offset, fmt = locate_audio_offset(fh, declared, size, key1, is_vpr)
        except (OSError, struct.error) as exc:
            info.payload["error"] = f"读取失败：{exc}"
            return info

        if not fmt:
            info.payload["error"] = (
                f"头部识别为{desc}，但按已知算法解不出音频流 —— "
                "文件可能已损坏，或属于这一代里更新的加密变体。"
                "可用 tools/diagnose_encrypted.py 查看头部字节。"
            )
            return info

        total = size - offset
        info.real_ext = fmt
        info.title = path.stem
        info.payload.update(
            key1=key1, is_vpr=is_vpr,
            audio_offset=offset, audio_size=total,
            declared_offset=declared,
        )

        notes: list[str] = []
        if offset != declared:
            notes.append(
                f"头部声明音频起点 0x{declared:X}，实测应为 0x{offset:X}，已自动校正"
            )
        if total > len(mask_table()) * MASK_PERIOD:
            notes.append("音频超过 64 MB，超出密钥表覆盖范围，末尾可能异常")
        if notes:
            info.payload["note"] = "；".join(notes)
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

        key1: bytes = info.payload["key1"]      # type: ignore[assignment]
        is_vpr: bool = bool(info.payload["is_vpr"])
        offset: int = int(info.payload["audio_offset"])

        try:
            with open(src, "rb") as fh:
                fh.seek(offset)
                payload = fh.read()
        except OSError as exc:
            return DecryptResult(False, message=f"读取失败：{exc}")

        if on_progress:
            on_progress(0.05, "展开密钥表")
        if cancel is not None and cancel.is_set():
            return DecryptResult(False, message="已取消", cancelled=True)

        try:
            audio = decrypt_bytes(payload, key1, is_vpr)
        except RuntimeError as exc:
            return DecryptResult(False, message=str(exc))

        if on_progress:
            on_progress(0.9, "写出文件")

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
