"""平台加密格式解密自测。

不能分发真实的平台下载文件，所以这里**按各平台的加密算法反向构造样本**，
再走完整的"探测 → 解密"链路，比对输出与原始音频是否逐字节一致。

⚠️ 关键教训：只做"自己加密、自己解密"的自洽性检查是**不够**的 —— 加解密
两边共用同一个函数时，哪怕算法整体错位，样本也照样恒真通过。酷狗漏掉
``MASK_V2_PRE_DEF`` 那一层，就是这么被掩盖过去的（99% 的字节都是错的，
自测却全绿）。所以酷狗部分另外钉了**官方实现金标**：

    tests/fixtures/kgm_official_keystream.bin      官方 KGM/KGMA 密钥流
    tests/fixtures/kgm_official_keystream_vpr.bin  官方 VPR 密钥流

这两个文件由 ``build/ref/make_kgm_fixtures.cjs`` 从 unlock-music 现役的
酷狗解密模块（``@xhacker/kgmwasm``）里抽取，是**算法常量**而非音乐内容。
测试用它们构造密文，等于让官方实现出题、本项目解答。
"""
from __future__ import annotations

import base64
import json
import struct
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.decryptors import decryptor_for_ext, all_decryptors          # noqa: E402
from app.core.decryptors import offline_reason, sanity_check              # noqa: E402
from app.core.decryptors import kuwo as kuwo_mod                           # noqa: E402
from app.core.decryptors import qmc as qmc_mod                             # noqa: E402
from app.core.decryptors._aes import AES128ECB                             # noqa: E402
from app.core.decryptors.kugou import (                                    # noqa: E402
    KGG_MAGIC, KGMA_LEGACY_MAGIC, KGM_HEADER, KGM_PREFIX, VPR_HEADER, VPR_MASK_DIFF,
    _XFORM, _repeat, _xor, decrypt_bytes, mask_stream,
)
from app.core.decryptors.kugou import classify as classify_kugou            # noqa: E402
from app.core.decryptors.ncm import (                                      # noqa: E402
    CORE_KEY, MAGIC as NCM_MAGIC, META_KEY, _IDENT_HEADER,
    _rc4_mask_table, _decrypt_stream,
)

WORK = Path(tempfile.gettempdir()) / "fm_decrypt_test"

#: 官方实现金标向量（见模块 docstring）
FIXTURES = ROOT / "tests" / "fixtures"
GOLDEN_KGM = FIXTURES / "kgm_official_keystream.bin"
GOLDEN_VPR = FIXTURES / "kgm_official_keystream_vpr.bin"


def golden_keystream(is_vpr: bool) -> bytes | None:
    """读取官方密钥流；文件缺失时返回 None。"""
    try:
        return (GOLDEN_VPR if is_vpr else GOLDEN_KGM).read_bytes()
    except OSError:
        return None


def make_kgm_from_official(audio: bytes, key: bytes, is_vpr: bool,
                           header_len: int = 0x400, magic: bytes | None = None,
                           declared: int | None = None) -> bytes:
    """用**官方密钥流**构造酷狗密文（而不是用本项目的算法）。

    解密方向是 ``out = XFORM(cipher ^ key) ^ keystream``，反解出
    ``cipher = XFORM(audio ^ keystream) ^ key``。
    这样样本的"正确答案"来自官方实现，自洽性再也不能掩盖算法错误。
    """
    ks = golden_keystream(is_vpr)
    if ks is None:
        raise RuntimeError("缺少官方金标向量，请先跑 build/ref/make_kgm_fixtures.cjs")
    if len(ks) < len(audio):
        raise RuntimeError(f"金标向量只有 {len(ks)} 字节，不够加密 {len(audio)} 字节")

    ks = ks[:len(audio)]
    cipher = _xor(_xor(audio, ks).translate(_XFORM), _repeat(key + b"\x00", len(audio)))

    header = bytearray(header_len)
    header[:16] = magic if magic is not None else (VPR_HEADER if is_vpr else KGM_HEADER)
    struct.pack_into("<I", header, 0x10, header_len if declared is None else declared)
    header[0x1C:0x2C] = key
    return bytes(header) + cipher


# --------------------------------------------------------------------------- #
def pkcs7_pad(data: bytes, block: int = 16) -> bytes:
    pad = block - len(data) % block
    return data + bytes([pad]) * pad


def make_audio(kind: str, size: int = 200_000) -> bytes:
    """造一段带真实文件头的伪音频。"""
    body = bytes(range(256)) * (size // 256)
    if kind == "flac":
        return b"fLaC\x00\x00\x00\x22" + body
    if kind == "mp3":
        return b"ID3\x04\x00\x00\x00\x00\x00\x00" + body
    if kind == "ogg":
        return b"OggS\x00\x02\x00\x00\x00\x00\x00\x00" + body
    if kind == "m4a":
        return b"\x00\x00\x00\x20ftypM4A \x00\x00\x00\x00" + body
    if kind == "wav":
        return b"RIFF\x00\x00\x00\x00WAVEfmt " + body
    raise ValueError(kind)


# ---- 各平台样本构造器（加密方向）------------------------------------------ #
def make_ncm(audio: bytes, rc4_key: bytes, meta: dict, layout: str = "compact") -> bytes:
    core, mta = AES128ECB(CORE_KEY), AES128ECB(META_KEY)

    key_blob = bytes(b ^ 0x64 for b in core.encrypt(pkcs7_pad(_IDENT_HEADER + rc4_key)))

    meta_plain = b"music:" + json.dumps(meta, ensure_ascii=False).encode("utf-8")
    meta_b64 = base64.b64encode(mta.encrypt(pkcs7_pad(meta_plain)))
    meta_blob = bytes(b ^ 0x63 for b in (b"163 key(Don't modify):" + meta_b64))

    enc_audio = _decrypt_stream(audio, _rc4_mask_table(rc4_key))

    out = bytearray(NCM_MAGIC)
    out += b"\x00\x00"
    out += struct.pack("<I", len(key_blob)) + key_blob
    out += struct.pack("<I", len(meta_blob)) + meta_blob
    if layout == "compact":
        # gap(5) + 封面区长度(4) + 封面图长度(4)
        out += b"\x00" * 5
        out += struct.pack("<I", 0)
        out += struct.pack("<I", 0)
    else:
        # 多出 4 字节校验位 —— 专门用来验证"音频起点自动校准"是否奏效
        out += b"\x00" * 4
        out += b"\x00" * 5
        out += struct.pack("<I", 0)
        out += struct.pack("<I", 0)
    out += enc_audio
    return bytes(out)


def make_qmc(audio: bytes) -> bytes:
    return qmc_mod._xor_mask(audio)


def kgm_encrypt(audio: bytes, key1: bytes, is_vpr: bool) -> bytes:
    """酷狗解密公式的逆运算。"""
    n = len(audio)
    if is_vpr:
        audio = _xor(audio, _repeat(VPR_MASK_DIFF, n))
    c = mask_stream(n).translate(_XFORM)
    return _xor(_xor(audio, c).translate(_XFORM), _repeat(key1, n))


#: 模拟"后 8 字节与已知常量不同"的酷狗文件头。
#: 真实世界里酷狗只有前 8 字节稳定，后 8 字节随客户端版本变化 ——
#: 之前用全 16 字节相等判定，这类文件会被直接判死。
VARIANT_KGM_HEADER = KGM_PREFIX + bytes((0x3C, 0x77, 0x91, 0x0D, 0x55, 0x2A, 0xEE, 0x41))


def make_kgm(audio: bytes, key: bytes, is_vpr: bool = False,
             header_len: int = 0x400, magic: bytes | None = None,
             declared: int | None = None) -> bytes:
    """构造酷狗加密样本。

    * ``magic``     —— 自定义文件头（用来模拟变体代次）
    * ``header_len``—— 头部真实长度，也就是音频真正的起点
    * ``declared``  —— 写进 0x10 字段的值；与实际不一致时，靠偏移自适应救回来
    """
    header = bytearray(header_len)
    header[:16] = magic if magic is not None else (VPR_HEADER if is_vpr else KGM_HEADER)
    struct.pack_into("<I", header, 0x10, header_len if declared is None else declared)
    header[0x1C:0x2C] = key
    return bytes(header) + kgm_encrypt(audio, key + b"\x00", is_vpr)


def make_kwm(audio: bytes, file_key: bytes) -> bytes:
    header = bytearray(0x400)
    header[:16] = kuwo_mod.MAGIC
    header[0x18:0x20] = file_key
    return bytes(header) + kuwo_mod._xor_mask(audio, kuwo_mod.build_mask(file_key))


# --------------------------------------------------------------------------- #
def run_case(tag: str, path: Path, expect_audio: bytes, expect_ext: str) -> tuple[bool, str]:
    decryptor = decryptor_for_ext(path.suffix)
    if decryptor is None:
        return False, "没有匹配的解密器"

    info = decryptor.probe(path)
    err = info.payload.get("error")
    if err:
        return False, f"探测失败：{err}"
    if info.real_ext != expect_ext:
        return False, f"真实格式判断错误：{info.real_ext} ≠ {expect_ext}"

    dst = path.with_name(path.stem + "_out." + expect_ext)
    res = decryptor.decrypt(path, dst, info)
    if not res.ok:
        return False, f"解密失败：{res.message}"
    if not dst.is_file():
        return False, "没有产出文件"

    got = dst.read_bytes()
    if got != expect_audio:
        diff = sum(1 for a, b in zip(got, expect_audio) if a != b)
        return False, f"内容不一致（{diff} / {len(expect_audio)} 字节不同，长度 {len(got)}）"

    detail = f"{decryptor.label} · {len(got) // 1024} KB"
    declared = info.payload.get("declared_offset")
    offset = info.payload.get("audio_offset")
    if declared is not None and offset is not None and declared != offset:
        detail += f"  [偏移自适应 0x{int(declared):X}→0x{int(offset):X}]"
    return True, detail


def main() -> int:
    if WORK.exists():
        for p in WORK.rglob("*"):
            if p.is_file():
                p.unlink()
    WORK.mkdir(parents=True, exist_ok=True)

    flac = make_audio("flac")
    mp3 = make_audio("mp3")
    ogg = make_audio("ogg")

    cases = [
        ("网易云 NCM (flac)", WORK / "song.ncm",
         make_ncm(flac, b"rc4-key-0123456789", {
             "musicName": "测试歌曲", "album": "测试专辑",
             "artist": [["测试歌手", 1]], "format": "flac",
         }), flac, "flac"),
        ("网易云 NCM (mp3)", WORK / "song2.ncm",
         make_ncm(mp3, b"another-key-98765", {
             "musicName": "MP3 歌曲", "album": "专辑",
             "artist": [["歌手甲", 2]], "format": "mp3",
         }), mp3, "mp3"),
        ("网易云 NCM 变体布局", WORK / "song3.ncm",
         make_ncm(mp3, b"variant-layout-key1", {
             "musicName": "变体布局", "album": "专辑",
             "artist": [["歌手乙", 3]], "format": "mp3",
         }, layout="padded"), mp3, "mp3"),
        ("QQ 音乐 QMC0 (mp3)", WORK / "track.qmc0", make_qmc(mp3), mp3, "mp3"),
        ("QQ 音乐 QMCFLAC", WORK / "track.qmcflac", make_qmc(flac), flac, "flac"),
        ("QQ 音乐 QMCOGG", WORK / "track.qmcogg", make_qmc(ogg), ogg, "ogg"),
        ("酷狗 KGM (flac)", WORK / "kugou.kgm",
         make_kgm(flac, bytes(range(16)), False), flac, "flac"),
        ("酷狗 KGM (mp3)", WORK / "kugou2.kgm",
         make_kgm(mp3, bytes(range(16, 32)), False), mp3, "mp3"),
        ("酷狗 VPR (flac)", WORK / "kugou.vpr",
         make_kgm(flac, bytes(range(32, 48)), True), flac, "flac"),
        # ---- KGMA 专项：这几条正是"kgma 不能转换"暴露出的盲区 ---- #
        ("酷狗 KGMA 标准头", WORK / "std.kgma",
         make_kgm(flac, bytes(range(48, 64)), False), flac, "flac"),
        ("酷狗 KGMA 变体头（后 8 字节不同）", WORK / "variant.kgma",
         make_kgm(mp3, bytes(range(64, 80)), False,
                  magic=VARIANT_KGM_HEADER), mp3, "mp3"),
        ("酷狗 KGMA 头部长度 0x100", WORK / "hlen.kgma",
         make_kgm(flac, bytes(range(80, 96)), False,
                  header_len=0x100), flac, "flac"),
        ("酷狗 KGMA 偏移字段为 0", WORK / "zerolen.kgma",
         make_kgm(mp3, bytes(range(96, 112)), False,
                  declared=0), mp3, "mp3"),
        ("酷狗 KGMA 变体头 + 偏移错值", WORK / "both.kgma",
         make_kgm(flac, bytes(range(112, 128)), False,
                  header_len=0x200, magic=VARIANT_KGM_HEADER, declared=0x400), flac, "flac"),
        ("酷我 KWM (mp3)", WORK / "kuwo.kwm",
         make_kwm(mp3, b"\x11\x22\x33\x44\x55\x66\x77\x88"), mp3, "mp3"),
        ("酷我 KWM (flac)", WORK / "kuwo2.kwm",
         make_kwm(flac, b"\xAA\xBB\xCC\xDD\x01\x02\x03\x04"), flac, "flac"),
    ]

    # ---- 官方金标：端到端样本，用官方密钥流出题 ---- #
    if golden_keystream(False) is not None:
        gflac = make_audio("flac", 100_000)
        gmp3 = make_audio("mp3", 90_000)
        cases += [
            ("官方金标 KGM (flac)", WORK / "gold.kgm",
             make_kgm_from_official(gflac, bytes(range(0x10, 0x20)), False), gflac, "flac"),
            ("官方金标 KGMA (mp3)", WORK / "gold.kgma",
             make_kgm_from_official(gmp3, bytes(range(0x30, 0x40)), False), gmp3, "mp3"),
            ("官方金标 VPR (flac)", WORK / "gold.vpr",
             make_kgm_from_official(gflac, bytes(range(0x50, 0x60)), True), gflac, "flac"),
            ("官方金标 KGMA 变体头+偏移错值", WORK / "gold2.kgma",
             make_kgm_from_official(gmp3, bytes(range(0x70, 0x80)), False,
                                    header_len=0x200, magic=VARIANT_KGM_HEADER,
                                    declared=0x400), gmp3, "mp3"),
        ]

    for label, path, blob, _audio, _ext in cases:
        path.write_bytes(blob)

    print("=" * 74)
    print("平台加密格式解密自测")
    print("=" * 74)

    passed = failed = 0

    # ---- 官方金标：密钥流逐字节比对（这是能抓住"整层漏算"的那道闸）---- #
    print()
    print("官方实现金标（@xhacker/kgmwasm，unlock-music 现役酷狗模块）：")
    ks_kgm, ks_vpr = golden_keystream(False), golden_keystream(True)
    if ks_kgm is None or ks_vpr is None:
        print("  [FAIL] 缺少金标向量 tests/fixtures/（跑 build/ref/make_kgm_fixtures.cjs 生成）")
        failed += 1
    else:
        n = len(ks_kgm)
        mine = mask_stream(n).translate(_XFORM)
        diff = sum(1 for a, b in zip(mine, ks_kgm) if a != b)
        ok = diff == 0 and len(mine) == n
        print(f"  [{'PASS' if ok else 'FAIL'}] KGM/KGMA 密钥流 vs 官方   {n} 字节，{diff} 处不一致")
        passed += ok
        failed += not ok

        mine_v = _xor(mine, _repeat(VPR_MASK_DIFF, n))
        diff_v = sum(1 for a, b in zip(mine_v, ks_vpr) if a != b)
        ok = diff_v == 0
        print(f"  [{'PASS' if ok else 'FAIL'}] VPR 密钥流 vs 官方        {n} 字节，{diff_v} 处不一致")
        passed += ok
        failed += not ok
    print()

    for label, path, _blob, audio, ext in cases:
        t0 = time.time()
        ok, detail = run_case(label, path, audio, ext)
        dt = time.time() - t0
        mark = "PASS" if ok else "FAIL"
        passed += ok
        failed += not ok
        print(f"  [{mark}] {label:<22} {dt * 1000:7.1f} ms  {detail}")

    # ---- 资源自检：打包后最容易漏的一环 ---- #
    print()
    print("资源自检：")
    for item in sanity_check():
        ok = bool(item["ok"])
        passed += ok
        failed += not ok
        print(f"  [{'PASS' if ok else 'FAIL'}] {item['label']:<14} {item['detail']}")

    # ---- 异常路径：应当给出明确错误而不是崩溃或产出坏文件 ---- #
    print()
    print("异常路径处理：")
    bad_km = WORK / "broken.kgm"
    bad_km.write_bytes(b"\x00" * 4096)
    d = decryptor_for_ext("kgm")
    info = d.probe(bad_km)
    ok = bool(info.payload.get("error"))
    print(f"  [{'PASS' if ok else 'FAIL'}] 伪造 KGM 头        → {str(info.payload.get('error'))[:46]}")
    passed += ok
    failed += not ok

    # 认得出代次、但如实说"解不了" —— 这比笼统的"文件头不匹配"有用得多
    kgg = WORK / "new.kgm"
    kgg_head = bytearray(0x400)
    kgg_head[:len(KGG_MAGIC)] = KGG_MAGIC
    kgg.write_bytes(bytes(kgg_head) + b"\x00" * 4096)
    info = decryptor_for_ext("kgm").probe(kgg)
    msg = str(info.payload.get("error", ""))
    ok = "KGG" in msg
    print(f"  [{'PASS' if ok else 'FAIL'}] 新版 KGG 头        → {msg[:46]}")
    passed += ok
    failed += not ok

    legacy = WORK / "old.kgma"
    legacy_head = bytearray(0x400)
    legacy_head[:len(KGMA_LEGACY_MAGIC)] = KGMA_LEGACY_MAGIC
    legacy.write_bytes(bytes(legacy_head) + b"\x00" * 4096)
    info = decryptor_for_ext("kgma").probe(legacy)
    msg = str(info.payload.get("error", ""))
    ok = "旧版 KGMA" in msg
    print(f"  [{'PASS' if ok else 'FAIL'}] 旧版 KGMA 头       → {msg[:46]}")
    passed += ok
    failed += not ok

    # KGG 应当被明确标为"离线不支持"，界面才能在添加文件时就如实告知
    ok = bool(offline_reason("kgg")) and not offline_reason("kgm")
    print(f"  [{'PASS' if ok else 'FAIL'}] KGG 标记离线不可解   → "
          f"{offline_reason('kgg')[:44]}")
    passed += ok
    failed += not ok

    # 头部家族判定：前 8 字节相同就该认作 KGM，别把 VPR 认错
    ok = (classify_kugou(KGM_HEADER)[0] == "kgm"
          and classify_kugou(VARIANT_KGM_HEADER)[0] == "kgm"
          and classify_kugou(VPR_HEADER)[0] == "vpr"
          and classify_kugou(b"\x00" * 16)[0] == "")
    print(f"  [{'PASS' if ok else 'FAIL'}] 头部家族判定        → "
          f"KGM={classify_kugou(KGM_HEADER)[0]} "
          f"变体={classify_kugou(VARIANT_KGM_HEADER)[0]} "
          f"VPR={classify_kugou(VPR_HEADER)[0]}")
    passed += ok
    failed += not ok

    v2 = WORK / "new.mflac"
    v2.write_bytes(make_qmc(flac))
    info = decryptor_for_ext("mflac").probe(v2)
    ok = "QMCv2" in str(info.payload.get("error", ""))
    print(f"  [{'PASS' if ok else 'FAIL'}] QQ 新版 mflac      → {str(info.payload.get('error'))[:46]}")
    passed += ok
    failed += not ok

    bad_ncm = WORK / "broken.ncm"
    bad_ncm.write_bytes(b"NOTANCM!" + b"\x00" * 300)
    info = decryptor_for_ext("ncm").probe(bad_ncm)
    ok = bool(info.payload.get("error"))
    print(f"  [{'PASS' if ok else 'FAIL'}] 伪造 NCM 头        → {str(info.payload.get('error'))[:46]}")
    passed += ok
    failed += not ok

    print()
    print("-" * 74)
    print(f"合计 {passed + failed} 项：通过 {passed}，失败 {failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
