"""FFmpeg 引擎：视频、音频、动图。

关键设计：**容器 ↔ 编码器是硬约束**。
把 AAC 塞进 WebM、把 H.264 塞进 MPEG-PS，FFmpeg 都会直接报 EINVAL 退出。
所以这里用一张规则表严格枚举"每种容器允许装什么编码"，再由用户偏好
在允许集合里挑，而不是凭扩展名猜。

另一个坑（2026-09 修）：**源是纯音频、目标是视频容器**时不能套用视频那条路。
ffmpeg 拿不到任何视频帧，`-c:v libx264` 会以 4294967274 退出，所以早期版本
直接 `-an` 把音轨也扔了，产出 0 秒空壳。现在分两种情况：
* 能拿到内嵌封面 → 做成"静态封面 + 音轨"的真视频（mp4/m4v/mov/mkv）；
* 拿不到封面 → 把音轨按容器允许的编码器装进去，仍是一个能播的文件。
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Sequence

from ..formats import Kind, kind_of
from ..runtime import ffmpeg_path, ffprobe_path
from .base import ConvertOptions, MediaInfo, SubprocessEngine

# 画质档 -> (视频CRF, 音频码率kbps)
_QUALITY_TABLE = {
    "high": ("18", "256k"),
    "balanced": ("23", "192k"),
    "small": ("28", "128k"),
}

#: 容器规则表。v=允许的视频编码器，a=允许的音频编码器，dv/da=默认选择。
_CONTAINER_RULES: dict[str, dict] = {
    "mp4": {"v": ["libx264", "libx265", "mpeg4"], "a": ["aac", "libmp3lame", "ac3"],
            "dv": "libx264", "da": "aac", "faststart": True},
    "m4v": {"v": ["libx264", "libx265", "mpeg4"], "a": ["aac", "libmp3lame"],
            "dv": "libx264", "da": "aac", "faststart": True},
    "mov": {"v": ["libx264", "libx265", "mpeg4"], "a": ["aac", "libmp3lame", "ac3", "pcm_s16le"],
            "dv": "libx264", "da": "aac", "faststart": True},
    "mkv": {"v": ["libx264", "libx265", "libvpx-vp9", "mpeg4"],
            "a": ["aac", "libmp3lame", "libopus", "libvorbis", "flac", "ac3"],
            "dv": "libx264", "da": "aac"},
    "webm": {"v": ["libvpx-vp9", "libvpx", "libaom-av1"],
             "a": ["libopus", "libvorbis"],
             "dv": "libvpx-vp9", "da": "libopus"},
    "ogv": {"v": ["libtheora"], "a": ["libvorbis"],
            "dv": "libtheora", "da": "libvorbis"},
    "avi": {"v": ["libx264", "mpeg4"], "a": ["libmp3lame", "ac3", "pcm_s16le"],
            "dv": "libx264", "da": "libmp3lame"},
    "wmv": {"v": ["wmv2"], "a": ["wmav2"], "dv": "wmv2", "da": "wmav2"},
    "flv": {"v": ["libx264", "flv1"], "a": ["aac", "libmp3lame"],
            "dv": "libx264", "da": "aac"},
    "ts": {"v": ["libx264", "libx265", "mpeg2video"], "a": ["aac", "ac3", "libmp3lame"],
           "dv": "libx264", "da": "aac"},
    "m2ts": {"v": ["libx264", "libx265", "mpeg2video"], "a": ["aac", "ac3"],
             "dv": "libx264", "da": "aac"},
    "mpg": {"v": ["mpeg2video", "mpeg1video"], "a": ["mp2", "ac3", "libmp3lame"],
            "dv": "mpeg2video", "da": "mp2", "bitrate": "8M"},
    "mpeg": {"v": ["mpeg2video", "mpeg1video"], "a": ["mp2", "ac3", "libmp3lame"],
             "dv": "mpeg2video", "da": "mp2", "bitrate": "8M"},
    "vob": {"v": ["mpeg2video"], "a": ["ac3", "mp2"],
            "dv": "mpeg2video", "da": "ac3", "bitrate": "8M"},
    "mxf": {"v": ["mpeg2video", "dnxhd"], "a": ["pcm_s16le"],
            "dv": "mpeg2video", "da": "pcm_s16le", "bitrate": "25M"},
    "3gp": {"v": ["libx264", "mpeg4", "h263"], "a": ["aac", "libopencore_amrnb"],
            "dv": "libx264", "da": "aac"},
    "gif": {"v": ["gif"], "a": [], "dv": "gif", "da": None},
}

#: 纯音频容器 -> 编码器
_AUDIO_CODEC_BY_EXT = {
    "mp3": "libmp3lame", "wav": "pcm_s16le", "flac": "flac", "aac": "aac",
    "m4a": "aac", "ogg": "libvorbis", "opus": "libopus", "wma": "wmav2",
    "ac3": "ac3", "aiff": "pcm_s16be", "amr": "libopencore_amrnb", "mka": "libvorbis",
}

#: 源是**纯音频**、目标却是视频容器时，用哪个音频编码器把音轨装进去。
#: gif 故意不在表里 —— 把音频做成 GIF 没有意义，路由阶段就排除掉。
_AUDIO_IN_CONTAINER = {
    "mp4": "aac", "m4v": "aac", "mov": "aac", "mkv": "aac",
    "webm": "libopus", "ogv": "libvorbis",
    "avi": "libmp3lame", "wmv": "wmav2", "flv": "aac",
    "ts": "aac", "m2ts": "aac", "mpg": "mp2", "mpeg": "mp2",
    "vob": "ac3", "mxf": "pcm_s16le", "3gp": "aac",
}

#: 这些容器能装"静态封面 + 音轨"的真视频，且默认播放器都认。
_COVER_VIDEO_CONTAINERS = ("mp4", "m4v", "mov", "mkv")

#: 生成静态视频轨时允许用的视频编码器
_COVER_VIDEO_CODECS = ("libx264", "libx265")

#: 需要 +faststart 的容器（MP4 系）
_FASTSTART_EXTS = ("mp4", "m4v", "mov")

#: 无损/未压缩音频编码器（不需要设码率）
_LOSSLESS_AUDIO = {"pcm_s16le", "pcm_s16be", "flac", "pcm_s24le"}

_VIDEO_EXTS = set(_CONTAINER_RULES) | {"rmvb", "rm"}

_AUDIO_EXTS = set(_AUDIO_CODEC_BY_EXT) | {"ape", "wv", "m4b", "dsf", "dff"}

_RESOLUTIONS = {
    "3840x2160": "3840:2160",
    "2560x1440": "2560:1440",
    "1920x1080": "1920:1080",
    "1280x720": "1280:720",
    "854x480": "854:480",
    "640x360": "640:360",
}

#: -progress 输出的键名，报错信息里要过滤掉
_PROGRESS_KEYS = {
    "frame", "fps", "stream_0_0_q", "bitrate", "total_size", "out_time_us",
    "out_time_ms", "out_time", "dup_frames", "drop_frames", "speed", "progress",
}

#: 用户选的编码器 -> 规则表里实际使用的编码器
_CODEC_ALIAS = {"h264": "libx264", "h265": "libx265", "vp9": "libvpx-vp9"}


class FFmpegEngine(SubprocessEngine):
    """音视频 + 动图引擎。"""

    name = "FFmpeg"
    kinds = frozenset({Kind.VIDEO, Kind.AUDIO})
    unavailable_hint = "未找到 ffmpeg.exe，请把 FFmpeg 放入 tools/ffmpeg/bin"

    def __init__(self) -> None:
        super().__init__()
        self.exe = ffmpeg_path()

    def _probe_available(self) -> bool:
        return bool(self.exe and Path(self.exe).is_file())

    # ------------------------------------------------------------------ #
    def handles(self, src_ext: str, dst_ext: str) -> bool:
        src_ext, dst_ext = src_ext.lower(), dst_ext.lower()
        src_kind, dst_kind = kind_of(src_ext), kind_of(dst_ext)
        if src_kind is None or dst_kind is None:
            return False
        if src_kind not in (Kind.VIDEO, Kind.AUDIO):
            return False
        if dst_kind not in (Kind.VIDEO, Kind.AUDIO):
            return False
        # 音频源不给 GIF：GIF 没有音轨，转出来只能是丢音轨的
        if src_kind is Kind.AUDIO and dst_ext == "gif":
            return False
        return dst_ext in _AUDIO_CODEC_BY_EXT or dst_ext in _CONTAINER_RULES

    def describe(self, src_ext: str, dst_ext: str) -> str:
        src_ext, dst_ext = src_ext.lower(), dst_ext.lower()
        if src_ext in _VIDEO_EXTS and dst_ext in _AUDIO_CODEC_BY_EXT:
            return f"提取音轨 {src_ext.upper()} → {dst_ext.upper()}"
        if src_ext in _VIDEO_EXTS and dst_ext in _CONTAINER_RULES:
            return f"转码 {src_ext.upper()} → {dst_ext.upper()}"
        if src_ext in _AUDIO_EXTS and dst_ext in _COVER_VIDEO_CONTAINERS:
            return f"音频 {src_ext.upper()} → {dst_ext.upper()}（封面图 + 音轨）"
        return f"转换 {src_ext.upper()} → {dst_ext.upper()}"

    # ------------------------------------------------------------------ #
    # 预准备：纯音频转视频容器时，把内嵌封面导出来当静态视频轨的输入
    # ------------------------------------------------------------------ #
    def prepare(self, src: Path, dst: Path, options: ConvertOptions,
                info: MediaInfo | None = None) -> list[Path]:
        dst_ext = dst.suffix.lstrip(".").lower()
        if not options.cover_video or dst_ext not in _COVER_VIDEO_CONTAINERS:
            return []
        if src.suffix.lstrip(".").lower() in _VIDEO_EXTS:
            return []
        # 有真视频的源走原本的视频编码路径，不需要封面
        if info is not None and (info.has_video or not info.has_cover):
            return []
        if info is None:
            return []
        # 时长都探不出来（ffprobe 报 duration=N/A）的文件做不出封面视频：
        # `-loop 1` 的无限画面 + `-shortest` 没有"最短流"可依，ffmpeg 会在滤镜
        # 阶段直接返回 ENOSPC（Error while filtering: No space left on device），
        # 产出 0 帧空壳。这里提前放弃，省掉一次必然失败的导出。
        if not info.duration:
            return []

        fd, name = tempfile.mkstemp(prefix="fm-cover-", suffix=".jpg")
        os.close(fd)
        out = Path(name)
        cmd = [self.exe, "-hide_banner", "-v", "error", "-y", "-i", str(src),
               "-map", "0:v:0", "-frames:v", "1", str(out)]
        try:
            subprocess.run(cmd, capture_output=True, timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except (OSError, subprocess.SubprocessError):
            return []
        try:
            if out.is_file() and out.stat().st_size > 0:
                return [out]
        except OSError:
            pass
        return []

    # ------------------------------------------------------------------ #
    # 探测
    # ------------------------------------------------------------------ #
    def probe(self, path: Path) -> MediaInfo:
        ext = path.suffix.lstrip(".").lower()
        kind = kind_of(ext) or Kind.VIDEO
        info = MediaInfo(path=path, ext=ext, kind=kind)
        try:
            info.size = path.stat().st_size
        except OSError:
            pass

        probe = ffprobe_path()
        if not probe:
            return self._probe_via_ffmpeg(path, info)

        args = [probe, "-v", "quiet", "-print_format", "json",
                "-show_format", "-show_streams", str(path)]
        try:
            raw = subprocess.run(
                args, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=30,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            ).stdout
            data = json.loads(raw or "{}")
        except (OSError, ValueError, subprocess.SubprocessError) as exc:
            info.error = f"探测失败：{exc}"
            return self._probe_via_ffmpeg(path, info)

        fmt = data.get("format", {})
        try:
            info.duration = float(fmt.get("duration") or 0) or None
            info.bitrate = int(fmt.get("bit_rate") or 0) or None
        except (TypeError, ValueError):
            pass

        for stream in data.get("streams", []):
            ctype = stream.get("codec_type")
            if ctype == "video":
                # 内嵌封面（disposition.attached_pic=1）在 FLAC/MP3 里也是一个
                # video 流。把它当成"有视频"会让 音频→MP4 走进视频编码分支，
                # 最后一帧都编不出来。这里必须区分开。
                if (stream.get("disposition") or {}).get("attached_pic"):
                    info.has_cover = True
                    info.cover_codec = stream.get("codec_name")
                    continue
                if not info.has_video:
                    info.has_video = True
                    info.video_codec = stream.get("codec_name")
                    info.width = stream.get("width")
                    info.height = stream.get("height")
            elif ctype == "audio" and not info.audio_codec:
                info.audio_codec = stream.get("codec_name")
        return info

    def _probe_via_ffmpeg(self, path: Path, info: MediaInfo) -> MediaInfo:
        """没有 ffprobe 时退化为解析 ffmpeg -i 的 stderr。"""
        if not self.exe:
            return info
        try:
            proc = subprocess.run(
                [self.exe, "-hide_banner", "-i", str(path)],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            text = (proc.stderr or "") + (proc.stdout or "")
        except (OSError, subprocess.SubprocessError) as exc:
            info.error = f"探测失败：{exc}"
            return info

        for line in text.splitlines():
            line = line.strip()
            if line.startswith("Duration:") and info.duration is None:
                stamp = line.split("Duration:")[1].split(",")[0].strip()
                try:
                    h, m, s = stamp.split(":")
                    info.duration = int(h) * 3600 + int(m) * 60 + float(s)
                except ValueError:
                    pass
            if "Video:" in line and not info.has_video:
                if "attached pic" in line:
                    info.has_cover = True
                    continue
                info.has_video = True
                parts = line.split("Video:")[1].split(",")
                info.video_codec = parts[0].strip()
                for part in parts:
                    token = part.strip().split(" ")[0]
                    if "x" in token and token[0].isdigit():
                        wh = token.split("x")
                        if len(wh) == 2 and wh[0].isdigit() and wh[1].isdigit():
                            info.width, info.height = int(wh[0]), int(wh[1])
                            break
            if "Audio:" in line and info.audio_codec is None:
                info.audio_codec = line.split("Audio:")[1].split(",")[0].strip()
        return info

    def pre_run(self, src: Path) -> MediaInfo | None:
        return self.probe(src)

    # ------------------------------------------------------------------ #
    # 参数构建
    # ------------------------------------------------------------------ #
    def build_args(self, src: Path, dst: Path, options: ConvertOptions,
                   info: MediaInfo | None = None,
                   scratch: Sequence[Path] = ()) -> list[str]:
        src_ext = src.suffix.lstrip(".").lower()
        dst_ext = dst.suffix.lstrip(".").lower()
        crf, audio_kbps = _QUALITY_TABLE.get(options.quality, _QUALITY_TABLE["balanced"])

        # 有没有真视频流：优先信 ffprobe（FLAC/MP3 的内嵌封面也是 video 流，
        # 只看扩展名会把它误判成视频），探测不可用时才退回扩展名判断。
        src_has_video = info.has_video if info is not None else src_ext in _VIDEO_EXTS
        src_is_audio = src_ext in _AUDIO_EXTS or (
            info is not None and bool(info.audio_codec) and not info.has_video
        )

        # ---- 纯音频 → 视频容器：产出真正能播的视频文件 ---- #
        if (src_is_audio and not src_has_video
                and dst_ext in _CONTAINER_RULES and dst_ext != "gif"):
            cover = Path(scratch[0]) if scratch else None
            if (cover is not None and cover.is_file()
                    and dst_ext in _COVER_VIDEO_CONTAINERS
                    and (info is None or info.duration)):
                return self._build_cover_video_args(
                    src, dst, dst_ext, cover, options, crf, audio_kbps
                )
            return self._build_audio_in_container_args(
                src, dst, dst_ext, options, audio_kbps
            )

        args: list[str] = ["-hide_banner", "-nostats", "-progress", "pipe:1"]
        if options.overwrite:
            args.append("-y")
        if options.hardware_accel:
            args += ["-hwaccel", "auto"]
        args += ["-i", str(src)]

        if dst_ext in _AUDIO_CODEC_BY_EXT:
            return self._build_audio_args(args, dst, dst_ext, options, audio_kbps)

        rule = _CONTAINER_RULES.get(dst_ext)
        if rule is None:
            raise ValueError(f"不支持的输出容器 .{dst_ext}")

        if dst_ext == "gif":
            return self._build_gif_args(args, dst, options)

        return self._build_video_args(
            args, dst, dst_ext, rule, options, crf, audio_kbps, src_has_video, info
        )

    # ---- 纯音频输出 ---- #
    def _build_audio_args(self, args, dst, dst_ext, options, audio_kbps) -> list[str]:
        return self._build_audio_track(
            args, dst, dst_ext, options, audio_kbps, _AUDIO_CODEC_BY_EXT[dst_ext]
        )

    def _build_audio_track(self, args, dst, dst_ext, options, audio_kbps, codec) -> list[str]:
        args.append("-vn")
        args += ["-c:a", codec]
        if codec not in _LOSSLESS_AUDIO:
            rate = options.audio_bitrate if options.audio_bitrate != "auto" else audio_kbps
            args += ["-b:a", rate]
        if options.sample_rate != "keep":
            args += ["-ar", options.sample_rate]
        if options.channels != "keep":
            args += ["-ac", options.channels]
        if dst_ext == "mp3":
            args += ["-id3v2_version", "3"]
        if dst_ext in _FASTSTART_EXTS:
            args += ["-movflags", "+faststart"]
        args += list(options.extra_args)
        args.append(str(dst))
        return args

    # ---- 纯音频装进视频容器（无封面可用时的兜底） ---- #
    def _build_audio_in_container_args(self, src, dst, dst_ext, options, audio_kbps) -> list[str]:
        rule = _CONTAINER_RULES.get(dst_ext) or {}
        codec = options.audio_codec if options.audio_codec != "auto" else ""
        allowed = rule.get("a") or []
        if not codec or codec not in allowed:
            codec = _AUDIO_IN_CONTAINER.get(dst_ext) or rule.get("da") or "aac"

        args: list[str] = ["-hide_banner", "-nostats", "-progress", "pipe:1"]
        if options.overwrite:
            args.append("-y")
        args += ["-i", str(src), "-map", "0:a:0"]
        return self._build_audio_track(args, dst, dst_ext, options, audio_kbps, codec)

    # ---- 静态封面 + 音轨：纯音频转出"任何播放器都认"的视频 ---- #
    def _build_cover_video_args(self, src, dst, dst_ext, cover, options, crf,
                                audio_kbps) -> list[str]:
        vcodec = _CODEC_ALIAS.get(options.video_codec, options.video_codec)
        if vcodec not in _COVER_VIDEO_CODECS:
            vcodec = "libx264"
        rule = _CONTAINER_RULES.get(dst_ext) or {}
        acodec = options.audio_codec if options.audio_codec != "auto" else ""
        if not acodec or acodec not in (rule.get("a") or []):
            acodec = _AUDIO_IN_CONTAINER.get(dst_ext) or rule.get("da") or "aac"

        # 静态图不需要高帧率，2fps 足够，体积几乎不涨
        fps = options.fps if options.fps.isdigit() else "2"

        args: list[str] = ["-hide_banner", "-nostats", "-progress", "pipe:1"]
        if options.overwrite:
            args.append("-y")
        args += ["-loop", "1", "-framerate", fps, "-i", str(cover), "-i", str(src)]
        args += ["-map", "0:v:0", "-map", "1:a:0", "-c:v", vcodec]
        if vcodec == "libx264":
            args += ["-tune", "stillimage", "-crf", crf, "-preset", "veryfast"]
        else:
            args += ["-crf", crf, "-preset", "veryfast", "-tag:v", "hvc1"]
        # H.264/H.265 的 yuv420p 要求宽高都是偶数，封面尺寸不可控，先规整
        scale = "scale=trunc(iw/2)*2:trunc(ih/2)*2"
        if options.resolution != "keep" and options.resolution in _RESOLUTIONS:
            scale = (f"scale={_RESOLUTIONS[options.resolution]}:force_original_aspect_ratio=decrease,"
                     f"pad=ceil(iw/2)*2:ceil(ih/2)*2")
        args += ["-vf", scale, "-pix_fmt", "yuv420p"]

        args += ["-c:a", acodec]
        if acodec not in _LOSSLESS_AUDIO:
            rate = options.audio_bitrate if options.audio_bitrate != "auto" else audio_kbps
            args += ["-b:a", rate]
        if options.sample_rate != "keep":
            args += ["-ar", options.sample_rate]
        if options.channels != "keep":
            args += ["-ac", options.channels]

        if dst_ext in _FASTSTART_EXTS:
            args += ["-movflags", "+faststart"]
        args += list(options.extra_args)
        # -shortest：封面图是无限循环的，靠音轨长度收尾
        args += ["-shortest"]
        args.append(str(dst))
        return args

    # ---- 降级：主方案失败时退成"只把音轨装进容器" ---- #
    def build_fallback_args(self, src: Path, dst: Path, options: ConvertOptions,
                            info: MediaInfo | None = None,
                            scratch: Sequence[Path] = ()) -> list[str] | None:
        """封面视频方案走不通时的兜底：只把音轨装进目标容器。

        宁可交出一个纯音轨的 MP4（能播、时长正确），也不要让用户拿到"失败"。
        典型触发：内嵌封面图解不出来、音频时长探测不到等。
        """
        src_ext = src.suffix.lstrip(".").lower()
        dst_ext = dst.suffix.lstrip(".").lower()
        if dst_ext not in _CONTAINER_RULES or dst_ext == "gif":
            return None
        if src_ext in _VIDEO_EXTS:
            return None
        if info is not None and info.has_video:
            return None
        _, audio_kbps = _QUALITY_TABLE.get(options.quality, _QUALITY_TABLE["balanced"])
        return self._build_audio_in_container_args(src, dst, dst_ext, options, audio_kbps)

    # ---- 动图输出 ---- #
    def _build_gif_args(self, args, dst, options) -> list[str]:
        fps = "12" if options.fps == "keep" else options.fps
        width = "640"
        if options.resolution in _RESOLUTIONS:
            width = _RESOLUTIONS[options.resolution].split(":")[0]
        args += [
            "-vf",
            f"fps={fps},scale={width}:-1:flags=lanczos,"
            f"split[a][b];[a]palettegen[p];[b][p]paletteuse",
            "-loop", "0",
        ]
        args += list(options.extra_args)
        args.append(str(dst))
        return args

    # ---- 视频容器输出 ---- #
    def _build_video_args(self, args, dst, dst_ext, rule, options, crf,
                          audio_kbps, src_has_video: bool,
                          info: MediaInfo | None = None) -> list[str]:
        vcodec = _CODEC_ALIAS.get(options.video_codec, options.video_codec)
        if vcodec == "auto" or vcodec not in rule["v"]:
            vcodec = rule["dv"]

        args += ["-c:v", vcodec]

        if vcodec in ("libx264", "libx265"):
            args += ["-crf", crf, "-preset", "medium", "-pix_fmt", "yuv420p"]
            if vcodec == "libx265" and dst_ext in ("mp4", "m4v", "mov", "mkv"):
                args += ["-tag:v", "hvc1"]
        elif vcodec == "libvpx-vp9":
            args += ["-crf", crf, "-b:v", "0", "-pix_fmt", "yuv420p", "-row-mt", "1"]
        elif vcodec == "libaom-av1":
            args += ["-crf", max(20, int(crf)), "-b:v", "0", "-pix_fmt", "yuv420p"]
        elif vcodec == "libtheora":
            args += ["-q:v", "7", "-pix_fmt", "yuv420p"]
        elif vcodec in ("mpeg2video", "mpeg1video"):
            args += ["-b:v", rule.get("bitrate", "8M"), "-pix_fmt", "yuv420p", "-g", "15"]
        elif vcodec == "dnxhd":
            args += ["-b:v", rule.get("bitrate", "25M"), "-pix_fmt", "yuv422p"]
        elif vcodec == "wmv2":
            args += ["-b:v", "4M", "-pix_fmt", "yuv420p"]
        elif vcodec == "flv1":
            args += ["-b:v", "2M", "-pix_fmt", "yuv420p"]
        elif vcodec == "h263":
            args += ["-b:v", "500k", "-pix_fmt", "yuv420p"]
        elif vcodec == "mpeg4":
            args += ["-b:v", "4M", "-pix_fmt", "yuv420p"]

        if options.video_bitrate != "auto" and vcodec in (
            "wmv2", "flv1", "h263", "mpeg4", "mpeg2video", "mpeg1video", "libtheora"
        ):
            args += ["-b:v", options.video_bitrate]

        filters: list[str] = []
        if options.resolution != "keep" and options.resolution in _RESOLUTIONS:
            filters.append(
                f"scale={_RESOLUTIONS[options.resolution]}:force_original_aspect_ratio=decrease,"
                f"pad=ceil(iw/2)*2:ceil(ih/2)*2"
            )
        if filters:
            args += ["-vf", ",".join(filters)]
        if options.fps != "keep" and options.fps.isdigit():
            args += ["-r", options.fps]

        # 音频
        has_audio = bool(info.audio_codec) if info is not None else src_has_video
        if not src_has_video or not rule["a"] or not has_audio:
            args.append("-an")
        else:
            acodec = options.audio_codec if options.audio_codec != "auto" else rule["da"]
            if acodec not in rule["a"]:
                acodec = rule["da"]
            args += ["-c:a", acodec]
            if acodec not in _LOSSLESS_AUDIO:
                rate = options.audio_bitrate if options.audio_bitrate != "auto" else audio_kbps
                args += ["-b:a", rate]
            if options.sample_rate != "keep":
                args += ["-ar", options.sample_rate]
            if options.channels != "keep":
                args += ["-ac", options.channels]

        if rule.get("faststart") and dst_ext in _FASTSTART_EXTS:
            args += ["-movflags", "+faststart"]

        args += list(options.extra_args)
        args.append(str(dst))
        return args

    # ------------------------------------------------------------------ #
    def is_noise(self, line: str) -> bool:
        key = line.split("=", 1)[0].strip()
        return key in _PROGRESS_KEYS

    def parse_progress(self, line: str, info: MediaInfo | None) -> float | None:
        if line.startswith("progress=end"):
            return 1.0
        if not info or not info.duration:
            return None
        if line.startswith("out_time_us=") or line.startswith("out_time_ms="):
            try:
                micros = int(line.split("=", 1)[1])
            except ValueError:
                return None
            return max(0.0, min(1.0, (micros / 1_000_000.0) / info.duration))
        return None
