"""外部程序定位。

软件不捆绑任何引擎的商业版权问题：FFmpeg 是 LGPL/GPL 开源、7-Zip 是 LGPL、
LibreOffice 是 MPL。这里按优先级查找：

1. 项目自带的 ``tools/`` 目录（便携、可随安装包分发）
2. 系统 PATH
3. Windows 常见安装位置
"""
from __future__ import annotations

import os
import shutil
from functools import lru_cache
from pathlib import Path

#: 项目根目录（.../FormatMaster）
PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = PROJECT_ROOT / "tools"
#: 随包分发的数据资源（图标、酷狗密钥表等）
ASSETS_DIR = PROJECT_ROOT / "app" / "assets"


@lru_cache(maxsize=None)
def _search(names: tuple[str, ...], extra_dirs: tuple[Path, ...]) -> str | None:
    search_dirs: list[Path] = list(extra_dirs)

    # PATH
    for name in names:
        found = shutil.which(name)
        if found:
            return found
        for d in os.environ.get("PATH", "").split(os.pathsep):
            if d:
                search_dirs.append(Path(d))

    for d in search_dirs:
        try:
            if not d.is_dir():
                continue
        except OSError:
            continue
        for name in names:
            cand = d / name
            if cand.is_file():
                return str(cand)
    return None


@lru_cache(maxsize=None)
def ffmpeg_path() -> str | None:
    return _search(
        ("ffmpeg.exe", "ffmpeg"),
        (
            TOOLS_DIR / "ffmpeg" / "bin",
            Path("C:/ffmpeg/bin"),
            Path("C:/Program Files/ffmpeg/bin"),
        ),
    )


@lru_cache(maxsize=None)
def ffprobe_path() -> str | None:
    return _search(
        ("ffprobe.exe", "ffprobe"),
        (
            TOOLS_DIR / "ffmpeg" / "bin",
            Path("C:/ffmpeg/bin"),
            Path("C:/Program Files/ffmpeg/bin"),
        ),
    )


_7Z_CANDIDATES = ("7z.exe", "7za.exe", "7zr.exe")


@lru_cache(maxsize=None)
def seven_zip_path() -> str | None:
    return _search(
        _7Z_CANDIDATES,
        (
            TOOLS_DIR / "7zip",
            Path("C:/Program Files/7-Zip"),
            Path("C:/Program Files (x86)/7-Zip"),
        ),
    )


@lru_cache(maxsize=None)
def libreoffice_path() -> str | None:
    return _search(
        ("soffice.exe", "soffice"),
        (
            Path("C:/Program Files/LibreOffice/program"),
            Path("C:/Program Files (x86)/LibreOffice/program"),
        ),
    )


def wps_path() -> str | None:
    """WPS 的 exe（文档转换走 COM 自动化，这里只用于展示检测结果）。"""
    roots = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Kingsoft" / "WPS Office",
        Path("C:/Program Files/WPS Office"),
        Path("C:/Program Files (x86)/Kingsoft/WPS Office"),
    ]
    for root in roots:
        if not root.exists():
            continue
        for hit in root.glob("**/wps.exe"):
            return str(hit)
    return None


def wps_installed() -> bool:
    """WPS 是否注册了 COM 组件（走注册表判断更准）。"""
    try:
        import winreg
    except ImportError:  # 非 Windows
        return False
    for hive in (winreg.HKEY_CLASSES_ROOT,):
        for prog_id in ("KWPS.Application", "Word.Application", "WPS.Application"):
            try:
                with winreg.OpenKey(hive, prog_id):
                    return True
            except OSError:
                continue
    return False


def office_com_available() -> bool:
    """是否可以走 Office/WPS COM 做高保真文档转换。"""
    if not wps_installed():
        return False
    try:
        import win32com.client  # noqa: F401
    except ImportError:
        return False
    return True


def environment_report() -> list[dict[str, object]]:
    """给"关于/环境检测"面板用的清单。"""
    items = [
        ("FFmpeg", ffmpeg_path(), "音视频、动图转换核心"),
        ("FFprobe", ffprobe_path(), "媒体信息探测"),
        ("7-Zip", seven_zip_path(), "压缩包格式互转"),
        ("LibreOffice", libreoffice_path(), "文档格式转换（高保真）"),
        ("WPS / Office COM", "yes" if office_com_available() else None, "文档格式转换（备用通道）"),
    ]
    return [
        {"name": name, "path": path, "ok": bool(path), "desc": desc}
        for name, path, desc in items
    ]
