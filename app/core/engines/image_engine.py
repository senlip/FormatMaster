"""图像引擎（Pillow 内核）。

为什么图像不用 FFmpeg？因为 FFmpeg 处理静图时对 EXIF、ICC 色彩配置、
多帧 TIFF、透明通道的处理都很粗糙，Pillow 更专业。这是"按格式选引擎"
而不是"一个引擎打天下"的典型例子。
"""
from __future__ import annotations

import threading
from pathlib import Path

from ..formats import Kind
from .base import BaseEngine, ConvertOptions, ConvertResult, MediaInfo, ProgressFn

# Pillow 保存时的扩展名归一（有些格式有多个别名）
_EXT_ALIAS = {
    "jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP",
    "bmp": "BMP", "tiff": "TIFF", "tif": "TIFF", "gif": "GIF",
    "ico": "ICO", "tga": "TGA", "ppm": "PPM", "pcx": "PCX",
    "jp2": "JPEG2000", "avif": "AVIF", "heic": "HEIF", "heif": "HEIF",
}

# 不支持透明通道的目标格式
_NO_ALPHA = {"JPEG", "BMP", "PPM", "PCX"}


class ImageEngine(BaseEngine):
    """基于 Pillow 的图像格式转换。"""

    name = "Pillow"
    kinds = frozenset({Kind.IMAGE})
    unavailable_hint = "未安装 Pillow"

    def _probe_available(self) -> bool:
        try:
            import PIL  # noqa: F401
        except ImportError:
            return False
        self._register_heif()
        return True

    @staticmethod
    def _register_heif() -> None:
        """注册 HEIC/HEIF 支持（手机照片常用）。"""
        try:
            import pillow_heif

            pillow_heif.register_heif_opener()
            try:
                pillow_heif.register_avif_opener()
            except Exception:
                pass
        except ImportError:
            pass

    # ------------------------------------------------------------------ #
    def capability(self, ext: str) -> bool:
        """Pillow 是否真的能写这个格式。"""
        try:
            from PIL import Image
        except ImportError:
            return False
        self._register_heif()
        pil_name = _EXT_ALIAS.get(ext.lower())
        if not pil_name:
            return False
        if pil_name == "HEIF":
            return "HEIF" in Image.registered_extensions().values() or \
                hasattr(Image, "OPEN")
        try:
            return pil_name in Image.SAVE
        except Exception:
            return False

    def probe(self, path: Path) -> MediaInfo:
        try:
            from PIL import Image
        except ImportError:
            return MediaInfo(path=path, ext=path.suffix.lstrip("."), kind=Kind.IMAGE)
        self._register_heif()
        info = MediaInfo(path=path, ext=path.suffix.lstrip(".").lower(), kind=Kind.IMAGE)
        try:
            info.size = path.stat().st_size
        except OSError:
            pass
        try:
            with Image.open(path) as im:
                info.width, info.height = im.size
                info.video_codec = im.format
                frames = getattr(im, "n_frames", 1)
                if frames > 1:
                    info.audio_codec = f"{frames} 帧"
        except Exception as exc:
            info.error = str(exc)
        return info

    # ------------------------------------------------------------------ #
    def convert(
        self,
        src: Path,
        dst: Path,
        options: ConvertOptions,
        on_progress: ProgressFn | None = None,
        cancel: threading.Event | None = None,
    ) -> ConvertResult:
        if not self.available():
            return ConvertResult(False, message=f"Pillow 不可用：{self.unavailable_hint}")

        from PIL import Image, ImageOps

        ext = dst.suffix.lstrip(".").lower()
        pil_name = _EXT_ALIAS.get(ext)
        if not pil_name:
            return ConvertResult(False, message=f"Pillow 无法写出 .{ext}")

        if on_progress:
            on_progress(0.15, "读取源图")

        if options.overwrite and dst.exists():
            try:
                dst.unlink()
            except OSError:
                pass
        dst.parent.mkdir(parents=True, exist_ok=True)

        try:
            with Image.open(src) as im:
                n_frames = getattr(im, "n_frames", 1)
                is_animated = n_frames > 1

                if is_animated and pil_name == "GIF":
                    return self._convert_animated(
                        im, src, dst, options, on_progress, cancel
                    )

                if cancel is not None and cancel.is_set():
                    return ConvertResult(False, message="已取消", cancelled=True)

                frame = ImageOps.exif_transpose(im) if options.keep_exif else im.copy()
                if frame is im:
                    frame = im.copy()

                frame = self._apply_resize(frame, options)
                frame = self._fit_mode(frame, pil_name)
                if on_progress:
                    on_progress(0.6, "编码输出")
                self._save(frame, dst, pil_name, options)
        except Exception as exc:
            return ConvertResult(False, message=f"图像转换失败：{exc}")

        if on_progress:
            on_progress(1.0, "完成")
        return ConvertResult(True, output=dst, message="完成")

    # ------------------------------------------------------------------ #
    def _convert_animated(self, im, src, dst, options, on_progress, cancel) -> ConvertResult:
        """GIF 动图逐帧转换，保留帧序与时长。"""
        from PIL import Image

        frames = []
        durations = []
        try:
            for idx in range(getattr(im, "n_frames", 1)):
                if cancel is not None and cancel.is_set():
                    return ConvertResult(False, message="已取消", cancelled=True)
                im.seek(idx)
                frames.append(self._fit_mode(im.convert("RGBA"), "GIF"))
                durations.append(im.info.get("duration", 100))
                if on_progress:
                    on_progress(0.2 + 0.6 * idx / max(1, getattr(im, "n_frames", 1)), "逐帧处理")
            if not frames:
                return ConvertResult(False, message="源图没有可用帧")
            frames[0].save(
                dst, save_all=True, append_images=frames[1:],
                duration=durations, loop=im.info.get("loop", 0), optimize=True,
            )
        except Exception as exc:
            return ConvertResult(False, message=f"动图转换失败：{exc}")
        if on_progress:
            on_progress(1.0, "完成")
        return ConvertResult(True, output=dst, message="完成")

    @staticmethod
    def _apply_resize(frame, options: ConvertOptions):
        from PIL import Image

        spec = options.resize
        if spec == "keep" or not spec:
            return frame
        w, h = frame.size
        if spec.endswith("%"):
            try:
                ratio = float(spec[:-1]) / 100.0
            except ValueError:
                return frame
            size = (max(1, int(w * ratio)), max(1, int(h * ratio)))
        elif spec.lower().endswith("px"):
            try:
                target = int(spec[:-2])
            except ValueError:
                return frame
            if w >= h:
                size = (target, max(1, int(h * target / w)))
            else:
                size = (max(1, int(w * target / h)), target)
        else:
            return frame
        return frame.resize(size, Image.LANCZOS)

    @staticmethod
    def _fit_mode(frame, pil_name: str):
        """把像素模式调整为目标格式能接受的样子。"""
        from PIL import Image

        mode = frame.mode
        if pil_name in _NO_ALPHA:
            if mode in ("RGBA", "LA", "P"):
                frame = frame.convert("RGBA")
                canvas = Image.new("RGB", frame.size, (255, 255, 255))
                canvas.paste(frame, mask=frame.split()[-1])
                frame = canvas
            elif mode not in ("RGB", "L"):
                frame = frame.convert("RGB")
        elif pil_name in ("PNG", "WEBP", "TIFF", "GIF", "ICO", "TGA"):
            if mode == "CMYK":
                frame = frame.convert("RGB")
            elif mode not in ("RGB", "RGBA", "L", "LA", "P", "1"):
                frame = frame.convert("RGBA" if pil_name != "GIF" else "P")
        elif pil_name in ("JPEG2000", "AVIF", "HEIF"):
            if mode in ("P", "1", "CMYK"):
                frame = frame.convert("RGBA" if pil_name != "JPEG2000" else "RGB")
        return frame

    @staticmethod
    def _save(frame, dst: Path, pil_name: str, options: ConvertOptions) -> None:
        params: dict = {}
        if pil_name == "JPEG":
            params = {
                "quality": {"high": 95, "balanced": 85, "small": 70}.get(options.quality, 85),
                "optimize": True,
                "progressive": True,
            }
            if options.keep_exif and frame.info.get("exif"):
                params["exif"] = frame.info["exif"]
        elif pil_name == "PNG":
            params = {"optimize": True, "compress_level": 6}
        elif pil_name == "WEBP":
            params = {
                "quality": {"high": 92, "balanced": 80, "small": 60}.get(options.quality, 80),
                "method": 4,
            }
        elif pil_name == "ICO":
            params = {"sizes": [(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)]}
        elif pil_name == "TIFF":
            params = {"compression": "tiff_lzw"}
        elif pil_name == "BMP":
            params = {}
        elif pil_name == "AVIF":
            params = {"quality": {"high": 80, "balanced": 65, "small": 45}.get(options.quality, 65)}
        elif pil_name == "HEIF":
            params = {"quality": {"high": 90, "balanced": 80, "small": 60}.get(options.quality, 80)}
        elif pil_name == "JPEG2000":
            params = {"irreversible": options.quality != "high"}

        try:
            frame.save(dst, format=pil_name, **params)
        except (ValueError, OSError):
            # 参数不被该格式接受时退化为基本保存
            frame.save(dst, format=pil_name)
