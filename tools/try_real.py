# -*- coding: utf-8 -*-
"""对真实加密文件做一次解密冒烟测试（只读源文件，产物写 tests/_work）。"""
import sys, os, time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.core.decryptors import find_for, platform_of, offline_reason, sniff_audio_format  # noqa

src = Path(sys.argv[1])
out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("tests/_work/try_real_out.bin")

raw = src.read_bytes()
print("源文件:", src, len(raw), "字节")
print("平台:", platform_of(src.suffix), "| 离线原因:", offline_reason(src.suffix) or "无")

d = find_for(src)
print("解密器:", d.name)
info = d.probe(src)
print("probe -> 真实格式=%s 标题=%r 艺术家=%r 专辑=%r 封面=%d字节 payload键=%s"
      % (info.real_ext, info.title, info.artist, info.album, len(info.cover),
         list(info.payload.keys())))

t0 = time.time()
res = d.decrypt(src, out, info)
dt = time.time() - t0
print("解密耗时 %.2fs  ok=%s  real_ext=%s  msg=%s" % (dt, res.ok, res.real_ext, res.message))
print("产物:", res.output, os.path.getsize(res.output) if res.output and res.output.exists() else "-")

data = Path(res.output).read_bytes() if res.output else b""
print("前 16 字节:", data[:16].hex(" "))
print("嗅探:", sniff_audio_format(data))

b = data
if b[:4] == b"fLaC":
    pos, si = 4, None
    while pos + 4 <= len(b):
        hdr = b[pos:pos + 4]
        size = int.from_bytes(hdr[1:4], "big")
        if hdr[0] == 0x00:
            si = b[pos + 4:pos + 4 + 34]
            break
        if hdr[0] == 0xFF:
            break
        pos += 4 + size + (1 if hdr[0] & 0x02 else 0)
    if si:
        sr = (si[10] << 12) | (si[11] << 4) | (si[12] >> 4)
        ch = ((si[12] >> 1) & 0x07) + 1
        bps = (((si[12] & 0x01) << 4) | (si[13] >> 4)) + 1
        total = ((si[13] & 0x0F) << 32) | int.from_bytes(si[14:18], "big")
        print("STREAMINFO: 采样率=%d 声道=%d 位深=%d 总采样=%d 时长=%.1fs"
              % (sr, ch, bps, total, total / sr if sr else 0))
    print("元数据块类型序列:", end=" ")
    pos = 4
    while pos + 4 <= len(b):
        hdr = b[pos:pos + 4]
        size = int.from_bytes(hdr[1:4], "big")
        print("type=%d(len=%d)" % (hdr[0], size), end=" ")
        if hdr[0] == 0xFF or (hdr[0] & 0x80):
            break
        pos += 4 + size + (1 if hdr[0] & 0x02 else 0)
        if (hdr[0] & 0x7F) == 0x7F:
            break
    print()
