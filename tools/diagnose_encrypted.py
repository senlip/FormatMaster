"""加密音频文件诊断工具。

用法：

    python tools/diagnose_encrypted.py <文件或目录> [更多文件...]

把文件头、识别结果、能否解密全部打出来。
某个文件"转换失败"时先用它看卡在哪一步 —— 不用打开软件，也不用重新打包。

注意：它做的是**只读**分析，不会修改或解密你的文件。
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    from app.core.decryptors import decryptor_for_ext, offline_reason, platform_of
    from app.core.decryptors.kugou import (
        KGMA_LEGACY_MAGIC, KGG_MAGIC, KGM_PREFIX, UNSUPPORTED_NOTE, VPR_PREFIX,
        classify as classify_kugou,
    )
except Exception as exc:  # noqa: BLE001
    print(f"无法加载项目模块（请在 FormatMaster 目录下运行）：{exc}")
    raise SystemExit(2)


#: 酷狗已知的头部家族。前两个能解，后两个解不了（原因见说明）。
#: 这里刻意**复用解密器里的常量与 classify()** —— 两处各写一份的话，
#: 迟早会出现"比对表认出来了、结论却说没认出来"的自相矛盾。
KUGOU_FAMILIES: tuple[tuple[bytes, str], ...] = (
    (KGM_PREFIX, "KGM / KGMA  异或掩码加密 —— 本工具支持"),
    (VPR_PREFIX, "VPR  异或掩码 + Viper 附加层 —— 本工具支持"),
    (KGG_MAGIC, "KGG / 新版 KGM  密钥由公钥表派生 —— 本工具不支持"),
    (KGMA_LEGACY_MAGIC, "旧版 KGMA  ASCII 头 + AES 封装 —— 本工具不支持"),
)


def hexdump(data: bytes, width: int = 16) -> list[str]:
    lines: list[str] = []
    for start in range(0, len(data), width):
        chunk = data[start:start + width]
        hex_part = " ".join(f"{b:02X}" for b in chunk).ljust(width * 3 - 1)
        ascii_part = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
        lines.append(f"  {start:04X}  {hex_part}  |{ascii_part}|")
    return lines


def diagnose(path: Path) -> bool:
    """分析一个文件；返回 True 表示这个文件可以解密。"""
    print("=" * 74)
    print(f"文件：{path}")
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            head = fh.read(64)
    except OSError as exc:
        print(f"  读取失败：{exc}")
        return False

    ext = path.suffix.lstrip(".").lower()
    print(f"大小：{size} 字节（{size / 1024 / 1024:.2f} MB）    扩展名：.{ext}")
    print("前 64 字节：")
    for line in hexdump(head):
        print(line)

    # 先看是不是酷狗家族 —— 这支最容易被误判，单列一张比对表
    variant, desc = classify_kugou(head)
    if variant or any(head.startswith(m) for m, _ in KUGOU_FAMILIES):
        print("酷狗头部比对（只比前 8 字节，后 8 字节随版本变）：")
        for magic, text in KUGOU_FAMILIES:
            mark = "✓" if head.startswith(magic) else " "
            print(f"  [{mark}] {magic.hex(' ').upper():<26} {text}")
        if variant:
            print(f"  → 判定代次：{desc}")

    platform = platform_of(ext)
    print(f"识别平台：{platform or '未识别 —— 这个扩展名不在加密格式列表里'}")

    decryptor = decryptor_for_ext(ext)
    if decryptor is None:
        print("结论：不是已知的加密格式，按普通文件处理即可。")
        return False

    reason = offline_reason(ext)
    if reason:
        print(f"离线可行性：✗ {reason}")

    info = decryptor.probe(path)
    err = info.payload.get("error")
    if err:
        print(f"探测结果：✗ {err}")
        print("结论：本工具无法解密这个文件（原因见上）。")
        return False

    print(f"探测结果：✓ 真实格式 = {info.real_ext.upper()}")
    for key, label in (
        ("variant", "识别到的代次"),
        ("declared_offset", "头部声明的音频起点"),
        ("audio_offset", "实际使用的音频起点"),
        ("audio_size", "音频数据长度"),
        ("note", "备注"),
    ):
        if key in info.payload:
            value = info.payload[key]
            if key in ("declared_offset", "audio_offset"):
                value = f"0x{int(value):X}（{value}）"
            print(f"    {label}：{value}")
    print("结论：可以解密。")
    return True


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        print("支持解密的格式：", end="")
        try:
            from app.core.decryptors import encrypted_extensions
            print("、".join(sorted(encrypted_extensions())))
        except Exception:  # noqa: BLE001
            print("（读取失败）")
        return 1

    targets: list[Path] = []
    for raw in argv[1:]:
        p = Path(raw)
        if p.is_dir():
            targets.extend(sorted(q for q in p.iterdir() if q.is_file()))
        else:
            targets.append(p)

    if not targets:
        print("没有找到任何文件。")
        return 1

    ok = 0
    for path in targets:
        ok += diagnose(path)
    print()
    print("-" * 74)
    print(f"合计 {len(targets)} 个文件：可解密 {ok} 个，不可解密 {len(targets) - ok} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
