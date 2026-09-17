"""调度器端到端自测：加密格式 → 剥壳 → （可选）转码。

与 selftest_decrypt.py 的分工：
* selftest_decrypt.py 校验"字节级解密正确性"（用合成样本，逐字节比对）。
* 本文件校验"调度链路正确性"：用 ffmpeg 生成**真实可解码音频**，
  包成平台加密文件后丢给 Scheduler，验证两条路径都能走通——
  1) 目标=原始格式 → 只剥壳、不转码，产物能直接播放；
  2) 目标=其它格式 → 先剥壳到临时文件、再交给引擎转码，临时文件必须清理干净。
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.engines.base import ConvertOptions                       # noqa: E402
from app.core.engines.ffmpeg_engine import FFmpegEngine                 # noqa: E402
from app.core.formats import ORIGINAL_EXT                              # noqa: E402
from app.core.jobs import Job, JobStatus, Scheduler                    # noqa: E402
from app.core.registry import get_registry                             # noqa: E402
from app.core.runtime import ffmpeg_path                               # noqa: E402

from selftest_decrypt import (                                          # noqa: E402
    VARIANT_KGM_HEADER, make_kgm, make_kwm, make_ncm, make_qmc,
)

WORK = Path(tempfile.gettempdir()) / "fm_pipeline_test"


def make_real_audio(dst: Path, fmt: str, seconds: float = 1.0) -> bytes:
    """用 ffmpeg 生成真实可解码音频，返回文件字节。"""
    ff = ffmpeg_path()
    if not ff:
        raise RuntimeError("ffmpeg 不可用")
    codec = {
        "flac": ["-c:a", "flac"],
        "mp3": ["-c:a", "libmp3lame", "-b:a", "128k"],
    }[fmt]
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [ff, "-hide_banner", "-loglevel", "error", "-y",
           "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
           *codec, str(dst)]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 生成 {fmt} 失败：{proc.stderr.decode('utf-8', 'ignore')[:200]}")
    return dst.read_bytes()


def probe_duration(path: Path) -> float:
    """用 ffprobe 读时长，确认产物是可解码的真音频。"""
    ff = Path(ffmpeg_path() or "")
    ffprobe = ff.with_name("ffprobe.exe" if ff.suffix.lower() == ".exe" else "ffprobe")
    if not ffprobe.exists():
        return -1.0
    cmd = [str(ffprobe), "-v", "error", "-show_entries", "format=duration",
           "-of", "default=nw=1:nk=1", str(path)]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        return -1.0
    try:
        return float(proc.stdout.decode("utf-8", "ignore").strip())
    except ValueError:
        return -1.0


def probe_streams(path: Path) -> list[str]:
    """返回产物里的流类型列表，如 ['video', 'audio']；探测失败返回空列表。"""
    ff = Path(ffmpeg_path() or "")
    ffprobe = ff.with_name("ffprobe.exe" if ff.suffix.lower() == ".exe" else "ffprobe")
    if not ffprobe.exists():
        return []
    cmd = [str(ffprobe), "-v", "error", "-show_entries", "stream=codec_type",
           "-of", "default=nw=1:nk=1", str(path)]
    proc = subprocess.run(cmd, capture_output=True)
    if proc.returncode != 0:
        return []
    return [x.strip() for x in proc.stdout.decode("utf-8", "ignore").split() if x.strip()]


def make_audio_with_cover(dst: Path, seconds: float = 1.0) -> bytes:
    """生成"内嵌封面的 FLAC" —— 用来验证 音频→MP4 会做成带画面的真视频。

    注意（2026-09-16 踩过的坑）：**不要**一条命令带 `-frames:v 1` 直接产出
    带封面的 FLAC。`-frames:v 1` 会连带把音频流也截断，结果是文件看着有
    8KB，实际一个采样都解不出来、时长 N/A —— 后面 ffmpeg 一律
    "Error while filtering: No space left on device"，白白怀疑产品代码半天。
    正确做法是分两步：先生成纯音频和封面图，再用 `-c copy` 合体。
    下面最后会断言音频真的能解出来，样本自身坏掉就立刻报错。
    """
    ff = ffmpeg_path()
    if not ff:
        raise RuntimeError("ffmpeg 不可用")
    dst.parent.mkdir(parents=True, exist_ok=True)
    audio = dst.with_name(f"{dst.stem}_audio.flac")
    cover = dst.with_name(f"{dst.stem}_cover.jpg")

    def run(cmd: list[str], what: str) -> None:
        proc = subprocess.run(cmd, capture_output=True)
        if proc.returncode != 0:
            raise RuntimeError(
                f"{what}失败：{proc.stderr.decode('utf-8', 'ignore')[:200]}"
            )

    run([ff, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-c:a", "flac", str(audio)], "生成纯音频")
    run([ff, "-hide_banner", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "color=c=0x1F365D:s=64x64",
         "-frames:v", "1", str(cover)], "生成封面图")
    run([ff, "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(audio), "-i", str(cover),
         "-map", "0:a", "-map", "1:v", "-c", "copy",
         "-disposition:v", "attached_pic", str(dst)], "合成带封面 FLAC")

    # 自检：音频必须真能解、时长必须探得到，否则这条件本来就是坏的
    if probe_duration(dst) < seconds * 0.5:
        raise RuntimeError(f"带封面样本自身不可解码（时长 {probe_duration(dst)}）")
    return dst.read_bytes()


def make_unknown_length(src: Path, dst: Path) -> bytes:
    """把 FLAC 的 STREAMINFO 里 total_samples 清零，做出"时长探测不出来"的脏文件。

    真实世界里不少半截下载/被音乐软件改过头的 FLAC 就是这样，ffprobe 会报
    duration=N/A。这种文件走 `-loop 1` + `-shortest` 做封面视频时，ffmpeg 没有
    "最短流"可依，会在滤镜阶段直接返回 ENOSPC（No space left on device）。
    所以这里专门钉一条回归：**时长未知也必须交出能播的产物**。
    """
    data = bytearray(src.read_bytes())
    if bytes(data[:4]) != b"fLaC":
        raise RuntimeError("样本不是 FLAC")
    # 4 字节块头之后才是 STREAMINFO 数据：8..17 是块/帧尺寸，18..25 是
    # 采样率(20) + 声道(3) + 位深(5) + 总采样数(36)
    word = int.from_bytes(data[18:26], "big")
    word &= ~((1 << 36) - 1)                     # 总采样数清零
    data[18:26] = word.to_bytes(8, "big")
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_bytes(bytes(data))
    return bytes(data)


def run_job(registry, src: Path, target_ext: str, out_dir: Path) -> Job:
    sched = Scheduler(registry, max_workers=1)
    job = Job(src=src, options=ConvertOptions(target_ext=target_ext), out_dir=out_dir)
    sched.run([job])
    while not job.is_finished:
        time.sleep(0.02)
    sched.shutdown()
    return job


def main() -> int:
    if WORK.exists():
        for p in WORK.rglob("*"):
            if p.is_file():
                p.unlink()
    (WORK / "src").mkdir(parents=True, exist_ok=True)
    (WORK / "out").mkdir(parents=True, exist_ok=True)

    print("=" * 82)
    print("调度器端到端自测：加密格式 → 剥壳 → （可选）转码")
    print("=" * 82)

    registry = get_registry()
    out_dir = WORK / "out"

    try:
        real_flac = make_real_audio(WORK / "src" / "real.flac", "flac")
        real_mp3 = make_real_audio(WORK / "src" / "real.mp3", "mp3")
    except RuntimeError as exc:
        print(f"  跳过：{exc}")
        return 0

    print(f"  源素材：flac {len(real_flac) // 1024} KB / mp3 {len(real_mp3) // 1024} KB")
    print()

    # 临时文件的基线快照：只看"本次运行新增的残留"，别把别人的垃圾算自己头上
    temp_root = Path(tempfile.gettempdir())
    dec_before = set(temp_root.glob("fm-dec-*"))
    cover_before = set(temp_root.glob("fm-cover-*"))

    cases: list[tuple[str, Path, str, str, bool]] = []

    # ---- 1. 仅解密路径（不解码，产物应与原始音频逐字节一致） ---- #
    ncm_flac = WORK / "src" / "song_flac.ncm"
    ncm_flac.write_bytes(make_ncm(real_flac, b"pipeline-key-000001", {
        "musicName": "端到端测试", "album": "测试专辑",
        "artist": [["测试歌手", 1]], "format": "flac",
    }))
    cases.append(("NCM → 原始格式(仅解密)", ncm_flac, ORIGINAL_EXT, "flac", True))

    kwm_mp3 = WORK / "src" / "song_mp3.kwm"
    kwm_mp3.write_bytes(make_kwm(real_mp3, b"\x11\x22\x33\x44\x55\x66\x77\x88"))
    cases.append(("KWM → 原始格式(仅解密)", kwm_mp3, ORIGINAL_EXT, "mp3", True))

    qmc_flac = WORK / "src" / "song_qmcflac.qmcflac"
    qmc_flac.write_bytes(make_qmc(real_flac))
    cases.append(("QMCFLAC → 原始格式(仅解密)", qmc_flac, ORIGINAL_EXT, "flac", True))

    # KGMA 是这次出事的主角：变体头 + 声明偏移与实际不符，两条自适应都得走一遍
    kgma_flac = WORK / "src" / "song_variant.kgma"
    kgma_flac.write_bytes(make_kgm(real_flac, bytes(range(16)), False,
                                   magic=VARIANT_KGM_HEADER, header_len=0x200,
                                   declared=0x400))
    cases.append(("KGMA 变体头+偏移错值 → 仅解密", kgma_flac, ORIGINAL_EXT, "flac", True))

    # ---- 2. 解密后转码路径 ---- #
    cases.append(("NCM → WAV", ncm_flac, "wav", "wav", False))
    cases.append(("KWM → FLAC", kwm_mp3, "flac", "flac", False))
    cases.append(("KGMA 变体头 → M4A", kgma_flac, "m4a", "m4a", False))
    cases.append(("酷狗 KGM → M4A", WORK / "src" / "song.kgm", "m4a", "m4a", False))
    (WORK / "src" / "song.kgm").write_bytes(make_kgm(real_flac, bytes(range(16)), False))

    passed = failed = 0
    for tag, src, target, expect_ext, byte_exact in cases:
        t0 = time.time()
        job = run_job(registry, src, target, out_dir)
        dt = time.time() - t0

        problems: list[str] = []
        if job.status is not JobStatus.DONE:
            problems.append(f"状态={job.status.label} / {job.message}")
        out = job.output
        if not problems and (out is None or not out.is_file()):
            problems.append("没有产出文件")
        if not problems and out is not None and out.suffix.lstrip(".").lower() != expect_ext:
            problems.append(f"产物后缀={out.suffix}，期望 .{expect_ext}")
        if not problems and not job.platform:
            problems.append("未识别出平台名")
        if not problems and not job.real_ext:
            problems.append("未解析出真实格式")

        detail = f"{out.name if out else '-'}"
        if not problems and byte_exact:
            got = out.read_bytes()
            ref = real_flac if expect_ext == "flac" else real_mp3
            if got != ref:
                problems.append(f"内容不一致（长度 {len(got)} vs {len(ref)}）")
        if not problems and not byte_exact:
            dur = probe_duration(out)
            if dur < 0.2:
                problems.append(f"产物不可解码（时长 {dur}）")
            else:
                detail += f" · 时长 {dur:.2f}s"

        ok = not problems
        passed += ok
        failed += not ok
        mark = "PASS" if ok else "FAIL"
        extra = "" if ok else "  ← " + "；".join(problems)
        print(f"  [{mark}] {tag:<26} {dt:5.2f}s  {detail}{extra}")

    # ---- 3. 纯音频 → 视频容器（2026-09-16 修的那个坑） ---- #
    #
    # 老版本这里一律走视频分支：ffmpeg 拿不到视频帧直接退出，而且 `-an` 还把
    # 音轨扔了 —— 用户点"转成 MP4"只会得到 0 秒空壳。下面几条把修复钉死：
    #   有封面 → mp4/mkv/mov 必须有 video+audio 两条流（真能播的视频）
    #   无封面 → 仍要有音轨，且能完整解码
    #   gif    → 音频不该出现在目标列表里（GIF 没有音轨）
    print()
    print("音频 → 视频容器：")
    try:
        cover_flac = WORK / "src" / "withcover.flac"
        make_audio_with_cover(cover_flac)
        no_cover = WORK / "src" / "real.flac"        # 注意要传路径，不是字节
    except RuntimeError as exc:
        print(f"  跳过：{exc}")
        cover_flac = None
        no_cover = WORK / "src" / "real.flac"

    video_cases: list[tuple[str, Path, str, bool, bool]] = []
    if cover_flac is not None:
        for ext in ("mp4", "mkv", "mov"):
            video_cases.append((f"带封面 FLAC → {ext.upper()}（带画面）", cover_flac, ext, True, True))
    video_cases.append(("无封面 FLAC → MP4（纯音轨）", no_cover, "mp4", True, False))
    video_cases.append(("无封面 FLAC → AVI（纯音轨）", no_cover, "avi", True, False))
    # 时长探测不出来的脏文件：必须靠降级交出能播的产物，而不是整个失败
    try:
        dirty = WORK / "src" / "unknown_len.flac"
        make_unknown_length(WORK / "src" / "real.flac", dirty)
        video_cases.append(("时长未知 FLAC → MP4（降级）", dirty, "mp4", True, False))
    except RuntimeError as exc:
        print(f"  跳过：{exc}")

    for tag, src, target, want_audio, want_video in video_cases:
        t0 = time.time()
        job = run_job(registry, src, target, out_dir)
        dt = time.time() - t0
        problems: list[str] = []
        if job.status is not JobStatus.DONE:
            problems.append(f"状态={job.status.label} / {job.message}")
        out = job.output
        if not problems and (out is None or not out.is_file()):
            problems.append("没有产出文件")
        streams: list[str] = []
        if not problems and out is not None:
            dur = probe_duration(out)
            if dur < 0.2:
                problems.append(f"产物不可解码（时长 {dur}）")
            streams = probe_streams(out)
            if want_audio and "audio" not in streams:
                problems.append(f"音轨被丢掉了（流={streams}）")
            if want_video and "video" not in streams:
                problems.append(f"没有画面轨（流={streams}）")
            if not want_video and "video" in streams:
                problems.append(f"不该有画面轨（流={streams}）")
        ok = not problems
        passed += ok
        failed += not ok
        detail = f"{out.name if out else '-'} · 流={'+'.join(streams) or '-'}"
        extra = "" if ok else "  ← " + "；".join(problems)
        print(f"  [{'PASS' if ok else 'FAIL'}] {tag:<26} {dt:5.2f}s  {detail}{extra}")

    # gif 不该出现在音频源的可选目标里
    gif_targets = [s.ext for s in registry.targets_for("flac")]
    ok = "gif" not in gif_targets and not registry.can_convert("flac", "gif")
    passed += ok
    failed += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] 音频源不提供 GIF 目标          "
          f"{'已排除' if ok else '仍可选中（GIF 装不下音轨）'}")

    # ---- 4. 异常路径：QQ 新版加密应给出可读提示而不是产出坏文件 ---- #
    print()
    print("异常路径：")
    v2 = WORK / "src" / "new.mflac"
    v2.write_bytes(make_qmc(real_flac))
    job = run_job(registry, v2, ORIGINAL_EXT, out_dir)
    ok = job.status is JobStatus.FAILED and "QMCv2" in job.message
    passed += ok
    failed += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] QQ 新版 mflac        {job.status.label}：{job.message[:52]}")

    # ---- 5. 临时文件必须清理 ---- #
    # 只统计本次运行新增的，别人留在系统临时目录里的文件不算（也就不会被误判）
    leftovers = [p for p in temp_root.glob("fm-dec-*")
                 if p.is_dir() and p not in dec_before]
    ok = not leftovers
    passed += ok
    failed += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] 临时目录已清理       残留 {len(leftovers)} 个")

    # 封面图是 prepare() 生成的临时文件，也必须一个不剩
    cover_left = [p for p in temp_root.glob("fm-cover-*") if p not in cover_before]
    ok = not cover_left
    passed += ok
    failed += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] 封面临时文件已清理     残留 {len(cover_left)} 个")

    # ---- 6. 降级重试本身也得验：主参数坏掉时必须自动退到纯音轨方案 ---- #
    class _BadFirstArg(FFmpegEngine):        # type: ignore[misc, valid-type]
        """故意把主参数弄坏，看基类会不会自动降级重试。"""

        def build_args(self, src, dst, options, info=None, scratch=()):
            args = super().build_args(src, dst, options, info, scratch)
            return ["-hide_banner", "-fm_bogus_flag_xyz", *args[1:]]

    fb_dst = out_dir / "fallback.mp4"
    if fb_dst.exists():
        fb_dst.unlink()
    res = _BadFirstArg().convert(
        WORK / "src" / "real.flac", fb_dst, ConvertOptions(target_ext="mp4", overwrite=True)
    )
    streams = probe_streams(fb_dst) if fb_dst.is_file() else []
    ok = res.ok and "audio" in streams
    passed += ok
    failed += not ok
    print(f"  [{'PASS' if ok else 'FAIL'}] 主方案失败自动降级        "
          f"{'已降级产出 ' + '+'.join(streams) if ok else res.message[:70]}")

    print()
    print("-" * 82)
    print(f"合计 {passed + failed} 项：通过 {passed}，失败 {failed}")
    print(f"产物目录：{out_dir}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
