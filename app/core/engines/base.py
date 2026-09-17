"""引擎适配层的公共契约。

设计要点：
* 所有引擎都是"外部程序驱动"（FFmpeg / 7-Zip / Office COM），
  本进程只负责拼参数、起进程、读进度、收尸。
* UI 只认识 :class:`ConvertRequest` 和 :class:`ConvertResult`，
  完全不知道背后是谁在干活——这就是"可插拔"的关键。
"""
from __future__ import annotations

import subprocess
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

from ..formats import Kind, kind_of, label_of

#: 进度回调：(0.0~1.0, 提示文本)
ProgressFn = Callable[[float, str], None]


class Cancelled(Exception):
    """任务被用户取消。"""


@dataclass
class MediaInfo:
    """输入文件的基本信息，主要给界面展示和进度换算用。"""

    path: Path
    ext: str
    kind: Kind
    size: int = 0
    duration: float | None = None       # 秒
    width: int | None = None
    height: int | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    bitrate: int | None = None
    error: str | None = None
    #: 是否存在**真正的**视频流。FLAC/MP3 里的内嵌封面在 ffprobe 眼里也是一个
    #: video 流（disposition.attached_pic=1），但它不是视频 —— 不区分的话
    #: "音频转 MP4" 会被误判成"视频转 MP4"，最后产出一个没有音轨的空壳。
    has_video: bool = False
    #: 是否存在内嵌封面（attached_pic）
    has_cover: bool = False
    cover_codec: str | None = None

    @property
    def size_text(self) -> str:
        n = float(self.size)
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if n < 1024 or unit == "TB":
                return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} TB"

    @property
    def duration_text(self) -> str:
        if not self.duration:
            return "—"
        total = int(self.duration)
        h, rem = divmod(total, 3600)
        m, s = divmod(rem, 60)
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

    @property
    def resolution_text(self) -> str:
        # 注意：不判断 has_video —— 图片引擎也会设置 width/height（用来显示
        # 像素尺寸）。音频源的 width/height 保持为 None，这里自然显示"—"。
        if self.width and self.height:
            return f"{self.width}×{self.height}"
        return "—"


@dataclass
class ConvertOptions:
    """一次转换的参数。字段覆盖四类引擎的公共需求，各引擎按需取用。"""

    target_ext: str

    # 通用画质档位：high / balanced / small
    quality: str = "balanced"

    # 视频
    video_codec: str = "auto"       # auto / h264 / h265 / vp9 / av1 / copy
    resolution: str = "keep"        # keep / 3840x2160 / 1920x1080 / 1280x720 / 854x480
    fps: str = "keep"               # keep / 24 / 30 / 60
    video_bitrate: str = "auto"     # auto / 8M / 4M / 2M
    hardware_accel: bool = False

    # 音频
    audio_codec: str = "auto"
    audio_bitrate: str = "auto"     # auto / 320k / 192k / 128k
    sample_rate: str = "keep"       # keep / 48000 / 44100
    channels: str = "keep"          # keep / 2 / 1
    # 纯音频转视频容器（如 FLAC → MP4）时：
    #   True  = 把内嵌封面做成静态视频轨，产出一个任何播放器都认的真视频；
    #   False = 只把音轨装进容器（等价于把 .m4a 改名为 .mp4）。
    cover_video: bool = True

    # 图像
    resize: str = "keep"            # keep / 50% / 1920px ...
    keep_exif: bool = True

    # 文档
    doc_engine: str = "auto"        # auto / office / builtin

    # 杂项
    overwrite: bool = True
    extra_args: list[str] = field(default_factory=list)


@dataclass
class ConvertResult:
    ok: bool
    output: Path | None = None
    message: str = ""
    elapsed: float = 0.0
    cancelled: bool = False


# --------------------------------------------------------------------------- #
# 引擎基类
# --------------------------------------------------------------------------- #
class BaseEngine(ABC):
    """所有引擎的抽象基类。"""

    #: 引擎显示名，出现在日志和"关于"里
    name: str = "engine"
    #: 本引擎负责的大类
    kinds: frozenset[Kind] = frozenset()
    #: 引擎不可用时给出的提示
    unavailable_hint: str = ""

    def __init__(self) -> None:
        self._available: bool | None = None

    # -- 可用性 ---------------------------------------------------------- #
    def available(self) -> bool:
        if self._available is None:
            self._available = self._probe_available()
        return self._available

    @abstractmethod
    def _probe_available(self) -> bool:
        """检测依赖是否就绪（如 ffmpeg.exe 是否存在）。"""

    # -- 能力声明 -------------------------------------------------------- #
    def handles(self, src_ext: str, dst_ext: str) -> bool:
        """本引擎能否完成 src_ext -> dst_ext。默认：两个格式同属本引擎的大类。"""
        src_kind = kind_of(src_ext)
        dst_kind = kind_of(dst_ext)
        if src_kind is None or dst_kind is None:
            return False
        return src_kind in self.kinds and dst_kind in self.kinds

    # -- 执行 ------------------------------------------------------------ #
    @abstractmethod
    def convert(
        self,
        src: Path,
        dst: Path,
        options: ConvertOptions,
        on_progress: ProgressFn | None = None,
        cancel: threading.Event | None = None,
    ) -> ConvertResult:
        ...

    def describe(self, src_ext: str, dst_ext: str) -> str:
        return f"{label_of(src_ext)} → {label_of(dst_ext)}"


# --------------------------------------------------------------------------- #
# 子进程型引擎：绝大多数引擎都是这个模式
# --------------------------------------------------------------------------- #
class SubprocessEngine(BaseEngine):
    """通过命令行程序完成转换的引擎基类。

    子类只需实现 :meth:`build_args` 和（可选）:meth:`parse_progress`。
    """

    #: 可执行文件路径，由子类在 __init__ 里确定
    exe: str | None = None

    # ---------------------------------------------------------------- #
    @abstractmethod
    def build_args(self, src: Path, dst: Path, options: ConvertOptions,
                   info: MediaInfo | None = None,
                   scratch: Sequence[Path] = ()) -> list[str]:
        """返回不含可执行文件本身的参数列表。

        ``info`` 是 :meth:`pre_run` 的探测结果（可能为 None），
        ``scratch`` 是 :meth:`prepare` 生成的临时输入文件。
        """

    def prepare(self, src: Path, dst: Path, options: ConvertOptions,
                info: MediaInfo | None = None) -> list[Path]:
        """执行前的准备，返回值是本次运行需要的**临时文件**列表。

        典型场景：把音频里的内嵌封面导出成图片，好当成静态视频轨的输入。
        临时文件在本次运行结束后由基类统一清理，子类不必自己管。
        """
        return []

    def build_fallback_args(self, src: Path, dst: Path, options: ConvertOptions,
                            info: MediaInfo | None = None,
                            scratch: Sequence[Path] = ()) -> list[str] | None:
        """主参数失败后的**降级方案**，返回 None 表示没有降级方案。

        有些输入会让"更漂亮"的那条路走不通（例：音频带内嵌封面→做成带
        画面的 MP4，遇上时长探测不出来的文件时 ffmpeg 会在滤镜阶段直接
        报 ENOSPC）。这时宁可交出一个能播的朴素产物，也不要整个任务失败。
        返回值同样是"不含可执行文件本身"的参数列表。
        """
        return None

    def parse_progress(self, line: str, info: MediaInfo | None) -> float | None:
        """解析一行进度输出，返回 0~1；返回 None 表示这行无关。"""
        return None

    def is_noise(self, line: str) -> bool:
        """是否是进度噪声——报错信息里不该混入这些行。"""
        return False

    def pre_run(self, src: Path) -> MediaInfo | None:
        """执行前的探测，供进度换算使用。默认不探测。"""
        return None

    # ---------------------------------------------------------------- #
    def _spawn(self, args: Sequence[str]) -> subprocess.Popen[str]:
        assert self.exe, f"{self.name} 未配置可执行文件"
        creationflags = 0
        if hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags = subprocess.CREATE_NO_WINDOW  # type: ignore[attr-defined]
        return subprocess.Popen(
            [self.exe, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            universal_newlines=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            creationflags=creationflags,
        )

    def _run_once(
        self,
        args: Sequence[str],
        info: MediaInfo | None,
        on_progress: ProgressFn | None,
        cancel: threading.Event | None,
    ) -> tuple[int, list[str], bool]:
        """跑一次子进程，返回 (退出码, 日志尾巴, 是否被取消)。"""
        tail: list[str] = []
        try:
            proc = self._spawn(args)
        except OSError as exc:
            return -1, [f"无法启动 {self.exe}：{exc}"], False

        cancelled = False
        last = 0.0
        try:
            assert proc.stdout is not None
            for raw in proc.stdout:
                if cancel is not None and cancel.is_set():
                    cancelled = True
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    return -1, tail, True

                line = raw.strip()
                if not line:
                    continue
                tail.append(line)
                if len(tail) > 12:
                    tail.pop(0)

                ratio = self.parse_progress(line, info)
                if ratio is not None and on_progress and ratio > last:
                    last = ratio
                    on_progress(min(ratio, 0.999), f"转换中 {ratio * 100:.0f}%")

            code = proc.wait()
        finally:
            if proc.stdout:
                proc.stdout.close()
        return code, tail, cancelled

    def convert(
        self,
        src: Path,
        dst: Path,
        options: ConvertOptions,
        on_progress: ProgressFn | None = None,
        cancel: threading.Event | None = None,
    ) -> ConvertResult:
        started = time.time()
        if not self.available():
            return ConvertResult(False, message=f"{self.name} 不可用：{self.unavailable_hint}")

        dst.parent.mkdir(parents=True, exist_ok=True)
        info = self.pre_run(src)

        scratch: list[Path] = []
        try:
            try:
                scratch = list(self.prepare(src, dst, options, info) or [])
                args = self.build_args(src, dst, options, info, scratch)
            except Exception as exc:  # 参数拼装失败属于开发期错误，直接暴露
                return ConvertResult(False, message=f"参数构建失败：{exc}")

            if on_progress:
                on_progress(0.0, "启动引擎")

            code, tail, cancelled = self._run_once(args, info, on_progress, cancel)
            if cancelled:
                _cleanup(dst)
                return ConvertResult(
                    False, message="已取消", elapsed=time.time() - started, cancelled=True
                )

            failed = code != 0 or not dst.exists()
            note = ""
            if failed:
                # ---- 降级：主方案走不通时退到朴素方案，别让任务整个失败 ---- #
                try:
                    fb = self.build_fallback_args(src, dst, options, info, scratch)
                except Exception:
                    fb = None
                if fb:
                    primary = _tail_text(tail, code)
                    _cleanup(dst)
                    if on_progress:
                        on_progress(0.02, "改用兼容方案重试")
                    code, tail, cancelled = self._run_once(fb, info, on_progress, cancel)
                    if cancelled:
                        _cleanup(dst)
                        return ConvertResult(
                            False, message="已取消",
                            elapsed=time.time() - started, cancelled=True,
                        )
                    if code == 0 and dst.exists():
                        failed = False
                        note = f"（主方案不可用，已降级：{primary[:60]}）"
                    else:
                        note = f"；降级方案也失败：{_tail_text(tail, code)[:80]}"
        finally:
            _discard(scratch)

        elapsed = time.time() - started
        if failed:
            detail = "\n".join([ln for ln in tail[-6:] if ln]) or f"退出码 {code}"
            return ConvertResult(
                False,
                message=f"引擎返回错误（{code}）：{detail}{note}",
                elapsed=elapsed,
            )

        if on_progress:
            on_progress(1.0, "完成")
        return ConvertResult(True, output=dst, message=f"完成{note}", elapsed=elapsed)


# --------------------------------------------------------------------------- #
# 辅助
# --------------------------------------------------------------------------- #
def _discard(paths: Sequence[Path]) -> None:
    """删除本次运行产生的临时文件，失败也不抛（临时目录本来就该被忽略）。"""
    for p in paths:
        try:
            path = Path(p)
            if path.is_file():
                path.unlink()
        except OSError:
            pass


def _cleanup(path: Path) -> None:
    """删掉失败/取消时留下的半成品，避免用户看到一个能点开的坏文件。"""
    try:
        if path.is_file():
            path.unlink()
    except OSError:
        pass


def _tail_text(tail: Sequence[str], code: int) -> str:
    """把引擎日志尾巴压成一行人话，空日志时退回退出码。"""
    lines = [ln for ln in tail[-6:] if ln]
    if not lines:
        return f"退出码 {code}"
    return " / ".join(lines)


def which_in_dirs(names: Iterable[str], dirs: Iterable[Path]) -> str | None:
    """在给定目录里找可执行文件（不依赖 PATH）。"""
    for d in dirs:
        for n in names:
            cand = d / n
            if cand.is_file():
                return str(cand)
    return None
