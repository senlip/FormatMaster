"""引擎注册表与格式路由。

这是"可插拔"落地的地方：新增一个引擎只要写一个适配器 + 在这里注册一行。
UI 通过 :meth:`EngineRegistry.targets_for` 就知道某个源文件能转成什么，
不需要任何硬编码的格式列表。
"""
from __future__ import annotations

from functools import lru_cache

from .engines.archive_engine import ArchiveEngine
from .decryptors import decryptor_for_ext, is_encrypted, offline_reason
from .engines.base import BaseEngine
from .engines.doc_engine import DocumentEngine
from .engines.ffmpeg_engine import FFmpegEngine
from .engines.image_engine import ImageEngine
from .formats import (
    ORIGINAL_EXT,
    ORIGINAL_SPEC,
    Kind,
    FormatSpec,
    all_of_kind,
    kind_of,
    spec_of,
)


class EngineRegistry:
    """持有所有引擎，负责把 (源格式, 目标格式) 路由到具体引擎。"""

    def __init__(self) -> None:
        # 顺序即优先级：同一组合若有多个引擎能处理，靠前的优先
        self._engines: list[BaseEngine] = [
            ImageEngine(),      # 图像优先给 Pillow，比 FFmpeg 更懂 EXIF/透明通道
            FFmpegEngine(),     # 音视频与动图
            DocumentEngine(),
            ArchiveEngine(),
        ]

    # ------------------------------------------------------------------ #
    @property
    def engines(self) -> list[BaseEngine]:
        return list(self._engines)

    def engine_by_name(self, name: str) -> BaseEngine | None:
        for engine in self._engines:
            if engine.name == name:
                return engine
        return None

    def route(self, src_ext: str, dst_ext: str) -> BaseEngine | None:
        """找出能完成这次转换的引擎。

        注意：平台加密格式（.ncm/.qmc*/...）返回 None —— 它们需要先剥壳，
        真实格式要读了文件才知道，静态路由给不出答案。
        判断"这个转换能不能做"请用 :meth:`can_convert`。
        """
        src_ext = src_ext.lower().lstrip(".")
        dst_ext = dst_ext.lower().lstrip(".")
        if not src_ext or not dst_ext or src_ext == dst_ext:
            return None
        for engine in self._engines:
            if engine.available() and engine.handles(src_ext, dst_ext):
                return engine
        return None

    def decrypt_engine(self, src_ext: str, dst_ext: str) -> BaseEngine | None:
        """加密源剥壳后，真正负责转码的引擎。"""
        dec = decryptor_for_ext(src_ext)
        if dec is None:
            return None
        for real in (dec.outputs or ("mp3", "flac")):
            engine = self.route(real, dst_ext)
            if engine is not None:
                return engine
        return None

    def targets_for(self, src_ext: str, *, only_available: bool = True) -> list[FormatSpec]:
        """列出某个源格式可以转成的所有目标格式。"""
        src_ext = src_ext.lower().lstrip(".")
        src_kind = kind_of(src_ext)
        if src_kind is None:
            return []

        # 平台加密格式：先剥壳拿到真实格式，再按真实格式算可达目标；
        # "仅解密"永远排第一，作为默认目标。
        src_spec = spec_of(src_ext)
        if src_spec is not None and src_spec.encrypted:
            if offline_reason(src_ext):
                return [ORIGINAL_SPEC]      # 仍算可识别输入，但不给虚假的可转目标
            return [ORIGINAL_SPEC, *self._targets_after_decrypt(src_ext, only_available)]

        pool: list[FormatSpec] = []
        for kind in Kind:
            pool.extend(all_of_kind(kind))
        # 图像源额外允许输出 PDF，视频源允许输出音频
        if src_kind is Kind.IMAGE:
            pool.append(spec_of("pdf"))
        if src_kind is Kind.VIDEO:
            pool.extend(all_of_kind(Kind.AUDIO))

        seen: set[str] = set()
        out: list[FormatSpec] = []
        for spec in pool:
            if spec is None or spec.ext in seen or spec.ext == src_ext:
                continue
            if spec.input_only:
                continue
            if only_available and self.route(src_ext, spec.ext) is None:
                continue
            seen.add(spec.ext)
            out.append(spec)
        out.sort(key=lambda s: (list(Kind).index(s.kind), s.ext))
        return out

    def _targets_after_decrypt(self, src_ext: str, only_available: bool) -> list[FormatSpec]:
        """加密源的可达目标 = 并集(各可能真实格式能转出的格式)。

        探测真实格式要读整个文件，列表阶段不该付这个代价，
        所以用解密器声明的 outputs 做上界，取并集。
        """
        dec = decryptor_for_ext(src_ext)
        reals = list(dec.outputs) if dec else []
        if not reals:
            reals = ["mp3", "flac"]

        pool: list[FormatSpec] = []
        for kind in Kind:
            pool.extend(all_of_kind(kind))

        seen: set[str] = set()
        out: list[FormatSpec] = []
        for spec in pool:
            if spec.input_only or spec.ext in seen or spec.ext == src_ext:
                continue
            if only_available and not any(self.route(real, spec.ext) for real in reals):
                continue
            seen.add(spec.ext)
            out.append(spec)
        out.sort(key=lambda s: (list(Kind).index(s.kind), s.ext))
        return out

    def can_convert(self, src_ext: str, dst_ext: str) -> bool:
        """能否完成这次转换 —— 对平台加密格式也成立。

        加密源的可达性来自"剥壳后的真实格式"，所以不能用 route() 直接判。
        """
        src_ext = src_ext.lower().lstrip(".")
        dst_ext = dst_ext.lower().lstrip(".")
        if not src_ext or not dst_ext or src_ext == dst_ext:
            return False
        if is_encrypted(src_ext):
            if offline_reason(src_ext):
                return False          # 本机解不开，别让用户白等
            if dst_ext == ORIGINAL_EXT:
                return True
            return self.decrypt_engine(src_ext, dst_ext) is not None
        return self.route(src_ext, dst_ext) is not None

    def explain(self, src_ext: str, dst_ext: str) -> tuple[bool, str]:
        """给界面用的一句话说明：(能否执行, 说明文本)。"""
        src_ext = src_ext.lower().lstrip(".")
        dst_ext = dst_ext.lower().lstrip(".")

        if is_encrypted(src_ext):
            dec = decryptor_for_ext(src_ext)
            platform = dec.label if dec else "加密格式"
            reason = offline_reason(src_ext)
            if reason:
                return False, reason
            reals = "、".join(x.upper() for x in (dec.outputs if dec else ()))
            if dst_ext == ORIGINAL_EXT:
                return True, f"{platform} → 剥壳解密（{reals}），保持原始音质"
            engine = self.decrypt_engine(src_ext, dst_ext)
            if engine is None:
                return False, f"{platform} 剥壳后无法转成 {dst_ext.upper()}"
            return True, f"{platform} → 先解密再由 {engine.name} 转成 {dst_ext.upper()}"

        engine = self.route(src_ext, dst_ext)
        if engine is None:
            return False, f"没有引擎能完成 {src_ext.upper()} → {dst_ext.upper()}"
        return True, f"由 {engine.name} 处理"

    def supported_inputs(self) -> set[str]:
        """所有能作为输入的后缀（去掉点）。"""
        exts = set()
        for spec_kind in Kind:
            for spec in all_of_kind(spec_kind, include_input_only=True):
                if self.targets_for(spec.ext):
                    exts.add(spec.ext)
        return exts

    def health(self) -> list[dict[str, object]]:
        """各引擎的可用状态，供 UI 展示。"""
        return [
            {"name": e.name, "ok": e.available(), "hint": e.unavailable_hint}
            for e in self._engines
        ]


@lru_cache(maxsize=1)
def get_registry() -> EngineRegistry:
    return EngineRegistry()
