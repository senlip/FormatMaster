# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

产物结构（onedir 模式）::

    dist/FormatMaster/
        FormatMaster.exe
        _internal/
            app/assets/app.ico
            tools/ffmpeg/bin/ffmpeg.exe
            tools/ffmpeg/bin/ffprobe.exe

说明：runtime.py 通过 __file__ 反推项目根目录，onedir 冻结后该根目录
就是 _internal，所以 tools/ 与 app/ 必须放在 _internal 根下，
与这里 add-binary / add-data 的目标路径保持一致。
"""
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))
FFMPEG_BIN = os.path.join(ROOT, "tools", "ffmpeg", "bin")

binaries = [
    (os.path.join(FFMPEG_BIN, "ffmpeg.exe"), "tools/ffmpeg/bin"),
    (os.path.join(FFMPEG_BIN, "ffprobe.exe"), "tools/ffmpeg/bin"),
]

datas = [
    (os.path.join(ROOT, "app", "assets", "app.ico"), "app/assets"),
    (os.path.join(ROOT, "app", "assets", "app.png"), "app/assets"),
    # 酷狗 KGM/VPR 解密用的 4MB 掩码表。缺了它酷狗格式会解不出来，
    # 所以必须随包分发，不能指望用户机器上有。
    (os.path.join(ROOT, "app", "assets", "kgm.mask"), "app/assets"),
]

# 这些包是运行时按需 import 的，静态分析扫不到，必须显式声明
hiddenimports = [
    "pymupdf",
    "mammoth",
    "markdown",
    "markdown.extensions.tables",
    "markdown.extensions.fenced_code",
    "markdown.extensions.toc",
    "openpyxl",
    "openpyxl.styles",
    "docx",
    "py7zr",
    "pythoncom",
    "pywintypes",
    "win32com",
    "win32com.client",
    "PIL._tkinter_finder",
]

# 砍掉用不到的大块依赖，控制安装包体积
excludes = [
    "matplotlib", "numpy", "scipy", "pandas", "sympy",
    "tkinter", "unittest", "pydoc_data", "test",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuick3D", "PySide6.QtQuickWidgets",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.Qt3DAnimation",
    "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtBluetooth", "PySide6.QtNfc",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtTest", "PySide6.QtSql",
    "PySide6.QtSerialPort", "PySide6.QtPositioning", "PySide6.QtLocation",
    "PySide6.QtSensors", "PySide6.QtWebChannel", "PySide6.QtWebSockets",
    "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtSpatialAudio",
    "PySide6.QtGraphs", "PySide6.QtScxml", "PySide6.QtStateMachine",
    "PySide6.QtRemoteObjects", "PySide6.QtTextToSpeech", "PySide6.QtUiTools",
]

a = Analysis(
    [os.path.join(ROOT, "app", "main.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="FormatMaster",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,                  # GUI 程序，不弹黑框
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, "app", "assets", "app.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="FormatMaster",
)
