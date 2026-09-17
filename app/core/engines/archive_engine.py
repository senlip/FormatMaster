"""压缩包引擎。

优先走 7-Zip 命令行（快、格式全、支持 ISO/RAR 读取），
没有 7z.exe 时退回 Python 标准库 + py7zr（覆盖 zip/tar/gz/bz2/xz/7z）。

转换模型：解包到临时目录 → 重新打包成目标格式。
"""
from __future__ import annotations

import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from ..formats import Kind
from ..runtime import seven_zip_path
from .base import BaseEngine, ConvertOptions, ConvertResult, MediaInfo, ProgressFn

#: 7-Zip 的类型开关
_SEVEN_TYPE = {
    "zip": "zip", "7z": "7z", "tar": "tar", "gz": "gzip",
    "bz2": "bzip2", "xz": "xz", "iso": "iso", "wim": "wim",
}
#: Python 通道能写的格式
_PY_WRITE = {"zip", "7z", "tar", "gz", "bz2", "xz"}
#: Python 通道能读的格式
_PY_READ = {"zip", "7z", "tar", "gz", "bz2", "xz"}


class ArchiveEngine(BaseEngine):
    name = "7-Zip"
    kinds = frozenset({Kind.ARCHIVE})
    unavailable_hint = "未找到 7z.exe 且缺少 py7zr"

    def __init__(self) -> None:
        super().__init__()
        self.exe = seven_zip_path()

    def _probe_available(self) -> bool:
        if self.exe:
            return True
        try:
            import py7zr  # noqa: F401

            return True
        except ImportError:
            return False

    # ------------------------------------------------------------------ #
    def handles(self, src_ext: str, dst_ext: str) -> bool:
        src_ext, dst_ext = src_ext.lower(), dst_ext.lower()
        if src_ext == dst_ext:
            return False
        if src_ext not in _PY_READ and not self.exe:
            return False
        return dst_ext in _PY_WRITE

    def describe(self, src_ext: str, dst_ext: str) -> str:
        return f"重新打包 {src_ext.upper()} → {dst_ext.upper()}"

    # ------------------------------------------------------------------ #
    def probe(self, path: Path) -> MediaInfo:
        ext = path.suffix.lstrip(".").lower()
        info = MediaInfo(path=path, ext=ext, kind=Kind.ARCHIVE)
        try:
            info.size = path.stat().st_size
        except OSError:
            pass
        count, raw_size = self._list_contents(path)
        if count is not None:
            info.video_codec = f"{count} 个条目"
            info.audio_codec = str(count)
        if raw_size:
            info.bitrate = raw_size
        return info

    def _list_contents(self, path: Path) -> tuple[int | None, int | None]:
        if self.exe:
            try:
                proc = subprocess.run(
                    [self.exe, "l", "-slt", str(path)],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                count = len([ln for ln in proc.stdout.splitlines() if ln.startswith("Path = ")])
                size = None
                for line in proc.stdout.splitlines():
                    if line.startswith("Size = "):
                        try:
                            size = int(line.split("=")[1])
                        except ValueError:
                            pass
                return max(0, count - 1), size
            except (OSError, subprocess.SubprocessError):
                pass
        return None, None

    # ------------------------------------------------------------------ #
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
            return ConvertResult(False, message=f"压缩引擎不可用：{self.unavailable_hint}")

        dst_ext = dst.suffix.lstrip(".").lower()
        dst.parent.mkdir(parents=True, exist_ok=True)
        if options.overwrite and dst.exists():
            try:
                dst.unlink()
            except OSError:
                pass

        workdir = Path(tempfile.mkdtemp(prefix="fm_arc_"))
        try:
            if on_progress:
                on_progress(0.05, "解包源文件")
            extract_root = workdir / "extract"
            extract_root.mkdir(parents=True, exist_ok=True)
            ok, msg = self._extract(src, extract_root, cancel)
            if not ok:
                return ConvertResult(False, message=msg, elapsed=time.time() - started,
                                     cancelled=msg == "已取消")
            if on_progress:
                on_progress(0.5, "重新打包")

            if dst_ext == "iso" and not self.exe:
                return ConvertResult(False, message="生成 ISO 需要 7z.exe")

            if self.exe:
                ok, msg = self._pack_with_7z(extract_root, dst, dst_ext, cancel)
            else:
                ok, msg = self._pack_with_python(extract_root, dst, dst_ext, cancel)
            if not ok:
                return ConvertResult(False, message=msg, elapsed=time.time() - started,
                                     cancelled=msg == "已取消")

            if on_progress:
                on_progress(1.0, "完成")
            return ConvertResult(True, output=dst, message="完成", elapsed=time.time() - started)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    # ------------------------------------------------------------------ #
    def _extract(self, src: Path, out: Path, cancel) -> tuple[bool, str]:
        if cancel is not None and cancel.is_set():
            return False, "已取消"
        if self.exe:
            try:
                proc = subprocess.run(
                    [self.exe, "x", "-y", f"-o{out}", str(src)],
                    capture_output=True, text=True, encoding="utf-8", errors="replace",
                    timeout=3600,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                if proc.returncode == 0:
                    return True, ""
                tail = (proc.stdout or "")[-400:]
                return False, f"解包失败：{tail.strip() or proc.returncode}"
            except (OSError, subprocess.SubprocessError) as exc:
                return False, f"解包失败：{exc}"
        return self._py_extract(src, out)

    def _py_extract(self, src: Path, out: Path) -> tuple[bool, str]:
        ext = src.suffix.lstrip(".").lower()
        try:
            if ext == "zip":
                import zipfile

                with zipfile.ZipFile(src) as zf:
                    _safe_extract_zip(zf, out)
            elif ext == "7z":
                import py7zr

                with py7zr.SevenZipFile(src, mode="r") as zf:
                    zf.extractall(path=out)
            elif ext in ("tar", "gz", "bz2", "xz"):
                import tarfile

                with tarfile.open(src, "r:*") as tf:
                    _safe_extract_tar(tf, out)
            else:
                return False, f"内置通道无法读取 .{ext}，请安装 7-Zip"
        except Exception as exc:
            return False, f"解包失败：{exc}"
        return True, ""

    def _pack_with_7z(self, src_dir: Path, dst: Path, dst_ext: str, cancel) -> tuple[bool, str]:
        if cancel is not None and cancel.is_set():
            return False, "已取消"
        type_flag = _SEVEN_TYPE.get(dst_ext)
        args = [self.exe, "a", "-y", "-mx=5"]
        if type_flag:
            args.append(f"-t{type_flag}")
        args.append(str(dst))
        args.append(str(src_dir / "*"))
        try:
            proc = subprocess.run(
                args, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=3600, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"打包失败：{exc}"
        if proc.returncode != 0:
            tail = (proc.stdout or "")[-400:]
            return False, f"打包失败：{tail.strip() or proc.returncode}"
        return True, ""

    def _pack_with_python(self, src_dir: Path, dst: Path, dst_ext: str, cancel) -> tuple[bool, str]:
        members = [p for p in src_dir.rglob("*") if p.is_file()]
        if cancel is not None and cancel.is_set():
            return False, "已取消"
        try:
            if dst_ext == "zip":
                import zipfile

                with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
                    for item in members:
                        zf.write(item, item.relative_to(src_dir))
            elif dst_ext == "7z":
                import py7zr

                with py7zr.SevenZipFile(dst, "w") as zf:
                    for item in members:
                        zf.write(item, item.relative_to(src_dir).as_posix())
            elif dst_ext == "tar":
                import tarfile

                with tarfile.open(dst, "w") as tf:
                    for item in members:
                        tf.add(item, item.relative_to(src_dir))
            elif dst_ext in ("gz", "bz2", "xz"):
                import tarfile

                mode = {"gz": "w:gz", "bz2": "w:bz2", "xz": "w:xz"}[dst_ext]
                with tarfile.open(dst, mode) as tf:
                    for item in members:
                        tf.add(item, item.relative_to(src_dir))
            else:
                return False, f"内置通道无法写出 .{dst_ext}"
        except Exception as exc:
            return False, f"打包失败：{exc}"
        return True, ""


# --------------------------------------------------------------------------- #
def _safe_extract_zip(zf, target: Path) -> None:
    for member in zf.infolist():
        dest = (target / member.filename).resolve()
        if not str(dest).startswith(str(target.resolve())):
            continue                     # 阻断 ../ 路径穿越
        zf.extract(member, target)


def _safe_extract_tar(tf, target: Path) -> None:
    for member in tf.getmembers():
        dest = (target / member.name).resolve()
        if not str(dest).startswith(str(target.resolve())):
            continue
        tf.extract(member, target)
