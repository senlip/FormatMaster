"""一键打包脚本。

流程：生成图标 → PyInstaller 冻结 → 冻结产物自检 → Inno Setup 编译安装包
      → 把安装包复制到 F:\\project\\

用法：
    python build/build.py              # 完整流程
    python build/build.py --skip-icon  # 跳过图标生成
    python build/build.py --no-installer   # 只打包，不做安装包
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
DIST = ROOT / "dist" / "FormatMaster"
DIST_INSTALLER = ROOT / "dist_installer"
APP_NAME = "FormatMaster"
VERSION = "1.2.0"
DELIVER_DIR = Path("F:/project")          # 二白的规矩：成品一律放这里

VENV_PYTHON = Path(
    "C:/Users/Administrator/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
)
ISCC_CANDIDATES = [
    BUILD / "tools" / "InnoSetup6" / "ISCC.exe",        # 便携安装，随项目走
    Path("C:/Program Files (x86)/Inno Setup 6/ISCC.exe"),
    Path("C:/Program Files/Inno Setup 6/ISCC.exe"),
]


def log(text: str) -> None:
    print(f"\n>>> {text}", flush=True)


def python_exe() -> str:
    if VENV_PYTHON.is_file():
        return str(VENV_PYTHON)
    return sys.executable


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    return subprocess.run(cmd, env=env, text=True, encoding="utf-8",
                          errors="replace", **kwargs)


def step_icon() -> None:
    log("生成应用图标")
    result = run([python_exe(), str(ROOT / "tools" / "make_icon.py")], capture_output=True)
    if result.returncode != 0:
        raise SystemExit(f"图标生成失败：{result.stderr}")
    print(result.stdout.strip())


def step_pyinstaller() -> None:
    log("PyInstaller 冻结")
    # 不用 --clean：它要清缓存目录，且 PyInstaller 在 COLLECT 阶段会删掉已存在的
    # 输出目录，在某些受限环境（沙箱/杀软）里会失败。改用全新输出目录绕开删除动作。
    result = run([
        python_exe(), "-m", "PyInstaller",
        "--noconfirm",
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(BUILD / "work"),
        str(BUILD / f"{APP_NAME}.spec"),
    ])
    if result.returncode != 0:
        raise SystemExit("PyInstaller 打包失败")
    size = sum(f.stat().st_size for f in DIST.rglob("*") if f.is_file())
    print(f"    产物：{DIST}  ({size / 1024 / 1024:.0f} MB)")


def step_selftest() -> None:
    """跑一次冻结产物的自检，确认引擎路径没被打包破坏。"""
    log("冻结产物自检")
    exe = DIST / f"{APP_NAME}.exe"
    if not exe.is_file():
        raise SystemExit("找不到打包后的 exe")

    report_path = Path(tempfile.gettempdir()) / "formatmaster_selftest.json"
    started = time.time()

    env = dict(os.environ)
    env["FORMATMASTER_SELFTEST"] = "1"
    subprocess.run([str(exe)], env=env, timeout=180,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    if not report_path.is_file():
        raise SystemExit("自检未产生报告，程序可能启动失败")
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if float(report.get("generated_at", 0)) < started - 5:
        raise SystemExit("读到的是上一次的旧报告，本次自检没真正执行")

    print(f"    冻结模式：{report['frozen']}")
    print(f"    资源根目录：{report['project_root']}")

    print("    外部工具：")
    for item in report["engines"]:
        mark = "OK " if item["ok"] else " --"
        path = item["path"] or "未找到（可选，有内置替代方案）"
        print(f"      [{mark}] {item['name']:<18} {path}")

    print("    转换引擎：")
    broken: list[str] = []
    for engine in report.get("engine_health", []):
        ok = bool(engine["ok"])
        if not ok:
            broken.append(str(engine["name"]))
        detail = "" if ok else f"  ← {engine['hint']}"
        print(f"      [{'OK ' if ok else ' !!'}] {engine['name']:<18}{detail}")

    print("    平台解密器：")
    broken_dec: list[str] = []
    for dec in report.get("decryptors", []):
        ok = bool(dec["ok"])
        if not ok:
            broken_dec.append(str(dec["label"]))
        detail = f"  ← {dec['detail']}" if not ok else f"  ({dec['detail']})"
        print(f"      [{'OK ' if ok else ' !!'}] {dec['label']:<18}{detail}")

    # 密钥流指纹必须与官方实现的金标向量一致 —— 这是防"整层漏算"的最后一道闸。
    # （酷狗曾漏掉 MASK_V2_PRE_DEF 一层，99% 字节解错，而自测全绿。）
    golden = ROOT / "tests" / "fixtures" / "kgm_official_keystream.bin"
    got = str(report.get("kugou_keystream_fingerprint", ""))
    if golden.is_file():
        want = golden.read_bytes()[:16].hex()
        match = got == want
        print(f"    酷狗密钥流指纹：{'与官方一致' if match else '与官方不一致'}  {got}")
        if not match:
            raise SystemExit(f"酷狗密钥流与官方金标不符：得到 {got}，期望 {want}")

    routes = report["routes"]
    print(
        "    格式路由："
        f"mp4→{len(routes.get('mp4', []))} 种，"
        f"png→{len(routes.get('png', []))} 种，"
        f"docx→{len(routes.get('docx', []))} 种，"
        f"zip→{len(routes.get('zip', []))} 种"
    )

    # 文档转换冒烟：真的转一份带中文的 docx，并核对产物内容。
    # 只 import 得到依赖是不够的 —— DOCX→PDF 曾经在冻结环境下排出几十页乱码
    # 还报"成功"，而当时所有自检全绿。
    smoke = report.get("document_smoke", {})
    print("    文档转换冒烟：")
    smoke_bad: list[str] = []
    for mode in ("builtin", "auto"):
        item = smoke.get(mode)
        if not isinstance(item, dict):
            continue
        ok = bool(item.get("ok"))
        detail = ""
        if item.get("mark_found") is not None:
            detail = (f"{item.get('bytes', 0) // 1024} KB · {item.get('pages')} 页 · "
                      f"{item.get('chars')} 字 · 中文{'命中' if item['mark_found'] else '缺失'} · "
                      f"未嵌字体 {len(item.get('unembedded', []))}")
        elif item.get("message"):
            detail = str(item["message"])[:70]
        if not ok:
            smoke_bad.append(mode)
        print(f"      [{'OK ' if ok else ' !!'}] {mode:<9}{detail}")
    if smoke_bad:
        raise SystemExit(
            f"冻结环境下文档转换冒烟未通过（{', '.join(smoke_bad)}）："
            f"{json.dumps(smoke, ensure_ascii=False)[:400]}"
        )

    enc = report.get("encrypted_routes", {})
    if enc:
        print("    加密格式路由：" + "，".join(
            f"{ext}→{len(targets)} 种" for ext, targets in enc.items()
        ))
        # 每个加密格式的首个目标都必须是"仅解密"，否则界面默认值就错了
        bad = [ext for ext, targets in enc.items()
               if not targets or targets[0] != "__original__"]
        if bad:
            raise SystemExit(f"加密格式的默认目标不是「仅解密」：{'、'.join(bad)}")

    # 只有"引擎"不可用才算致命；7-Zip / LibreOffice 这类外部工具缺失有替代路径
    if broken:
        raise SystemExit(f"以下引擎在冻结环境下不可用：{'、'.join(broken)}")
    if broken_dec:
        raise SystemExit(f"以下平台解密器在冻结环境下不可用：{'、'.join(broken_dec)}")


def find_iscc() -> Path | None:
    for candidate in ISCC_CANDIDATES:
        if candidate.is_file():
            return candidate
    found = shutil.which("ISCC")
    return Path(found) if found else None


def step_installer() -> Path | None:
    log("编译安装包")
    iscc = find_iscc()
    if iscc is None:
        print("    未找到 Inno Setup（ISCC.exe），跳过安装包制作")
        print("    下载：build/tools/innosetup.exe 静默安装，或去官网装 6.x")
        return None

    result = run([str(iscc), str(BUILD / "installer.iss")], capture_output=True)
    print(result.stdout.strip()[-1500:])
    if result.returncode != 0:
        print(result.stderr.strip()[-1500:])
        raise SystemExit("Inno Setup 编译失败")

    produced = sorted(DIST_INSTALLER.glob("*.exe"), key=lambda p: p.stat().st_mtime)
    if not produced:
        raise SystemExit("没有找到生成的安装包")
    return produced[-1]


def step_deliver(installer: Path | None) -> None:
    log("复制成品")
    DELIVER_DIR.mkdir(parents=True, exist_ok=True)
    copied = []
    if installer is not None:
        target = DELIVER_DIR / installer.name
        shutil.copy2(installer, target)
        copied.append(target)
    else:
        # 没有安装包时至少把绿色版目录交付出去
        target = DELIVER_DIR / f"{APP_NAME}_portable_v{VERSION}"
        shutil.copytree(DIST, target, dirs_exist_ok=True)
        copied.append(target)
    for item in copied:
        size_mb = (sum(f.stat().st_size for f in item.rglob("*") if f.is_file())
                   if item.is_dir() else item.stat().st_size) / 1024 / 1024
        print(f"    {item}  ({size_mb:.0f} MB)")


def main() -> int:
    parser = argparse.ArgumentParser(description="FormatMaster 打包")
    parser.add_argument("--skip-icon", action="store_true", help="不重新生成图标")
    parser.add_argument("--no-installer", action="store_true", help="只打包，不做安装包")
    args = parser.parse_args()

    if not args.skip_icon:
        step_icon()
    step_pyinstaller()
    step_selftest()

    installer = None
    if not args.no_installer:
        installer = step_installer()
    step_deliver(installer)

    log("完成")
    return 0


if __name__ == "__main__":
    sys.exit(main())
