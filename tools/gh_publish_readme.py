"""把 README.md 发布到 GitHub 仓库（只发 README，不碰代码与发行版）。

用法（token 从环境变量或文件读，绝不写进仓库）：

    # 方式一：环境变量
    GH_TOKEN=ghp_xxx python tools/gh_publish_readme.py

    # 方式二：从文件读（避免聊天框粘贴被截断）
    python tools/gh_publish_readme.py --token-file F:/project/.gh_token.txt

    # 预览要做什么，不做任何写操作
    python tools/gh_publish_readme.py --token-file ... --dry-run

依赖：标准库，无第三方包。
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://api.github.com"
REPO_NAME = "FormatMaster"
README = Path(__file__).resolve().parent.parent / "README.md"
COMMIT_MESSAGE = "docs: 添加仓库 README"


def call(
    method: str,
    path: str,
    token: str,
    payload: dict | None = None,
    *,
    dry_run: bool = False,
) -> tuple[int, dict]:
    """调用 GitHub API，返回 (状态码, 解析后的 JSON)。"""
    url = path if path.startswith("http") else f"{API}{path}"
    if dry_run and method != "GET":
        print(f"    [dry-run] {method} {url}")
        return 0, {}

    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    req.add_header("User-Agent", "FormatMaster-publisher")
    if data is not None:
        req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8", "replace")
            return resp.status, (json.loads(body) if body.strip() else {})
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"message": body[:300]}
    except urllib.error.URLError as exc:
        return 0, {"message": f"网络不可达：{exc.reason}"}


def explain(code: int, body: dict) -> str:
    msg = body.get("message", "")
    hints = {
        401: "token 无效、被吊销，或复制不完整（classic PAT 必须是 ghp_ + 36 位 = 40 字符）",
        403: "token 权限不足，或触发限流；确认勾选了 repo 权限",
        404: "路径不存在；401 也可能是被 GitHub 当成未认证处理",
        422: "参数被拒绝，通常是仓库已存在或分支名不对",
    }
    return f"{msg or '（无消息）'}" + (f"  ← {hints[code]}" if code in hints else "")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--token-file", help="从该文件读 token（首行）")
    ap.add_argument("--token-env", default="GH_TOKEN", help="环境变量名，默认 GH_TOKEN")
    ap.add_argument("--repo", default=REPO_NAME, help=f"仓库名，默认 {REPO_NAME}")
    ap.add_argument("--private", action="store_true", help="建为私有仓库（默认公开）")
    ap.add_argument("--description", default="万能格式转换器：视频 / 音频 / 图像 / 文档 / 压缩包，含国内音乐平台加密格式解锁")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不写任何东西")
    args = ap.parse_args()

    # ---- 取 token ---- #
    token = ""
    if args.token_file:
        p = Path(args.token_file)
        if not p.is_file():
            print(f"× token 文件不存在：{p}")
            return 2
        token = p.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    if not token:
        token = os.environ.get(args.token_env, "").strip()
    if not token:
        print("× 没有拿到 token。用 --token-file 指定文件，或设好环境变量。")
        return 2

    # ---- 本地自检：先证明 token 格式本身完整 ---- #
    print("=" * 68)
    print("0. 本地检查 token 形态")
    if token.startswith("ghp_") and len(token) != 40:
        print(f"   × classic token 应为 40 字符，当前 {len(token)} 字符 —— 复制不完整。")
        print("     请回到 https://github.com/settings/tokens 重新完整复制。")
        return 2
    if token.startswith("github_pat_") and len(token) < 80:
        print(f"   × fine-grained token 通常 90+ 字符，当前 {len(token)} 字符 —— 复制不完整。")
        return 2
    print(f"   ✓ 长度 {len(token)}，前缀 {token[:4]}…{token[-4:]}")

    # ---- 1. 身份 & 权限 ---- #
    print("1. 校验身份 GET /user")
    code, body = call("GET", "/user", token)
    if code != 200:
        print(f"   × HTTP {code} {explain(code, body)}")
        return 1
    login = body.get("login", "")
    scopes = ""
    print(f"   ✓ 已认证：{login}（{body.get('name') or '无昵称'}）")

    print("   检查 repo 权限 GET /user/repos?per_page=1")
    code, body = call("GET", "/user/repos?per_page=1", token)
    if code != 200:
        print(f"   × HTTP {code} {explain(code, body)} —— token 缺少 repo 权限。")
        return 1
    print("   ✓ 具备仓库读写权限")

    # ---- 2. 建仓（已存在则跳过） ---- #
    print(f"2. 检查仓库 {login}/{args.repo}")
    code, body = call("GET", f"/repos/{login}/{args.repo}", token)
    if code == 200:
        print(f"   · 已存在，跳过创建（{body.get('html_url')}）")
        html_url = body["html_url"]
    elif code == 404:
        print(f"   · 不存在，创建{'私有' if args.private else '公开'}仓库")
        code, body = call(
            "POST",
            "/user/repos",
            token,
            {
                "name": args.repo,
                "description": args.description,
                "private": bool(args.private),
                "has_issues": True,
                "has_wiki": False,
                "has_projects": False,
                "auto_init": False,
            },
            dry_run=args.dry_run,
        )
        if code not in (201, 0):
            print(f"   × HTTP {code} {explain(code, body)}")
            return 1
        html_url = body.get("html_url", f"https://github.com/{login}/{args.repo}")
        print(f"   ✓ 已创建：{html_url}")
    else:
        print(f"   × HTTP {code} {explain(code, body)}")
        return 1

    # ---- 3. 推 README ---- #
    if not README.is_file():
        print(f"× 找不到 {README}")
        return 2
    raw = README.read_bytes()
    print(f"3. 提交 README.md（{len(raw)} 字节）")
    payload = {
        "message": COMMIT_MESSAGE,
        "content": base64.b64encode(raw).decode("ascii"),
    }

    code, body = call("GET", f"/repos/{login}/{args.repo}/contents/README.md", token)
    existing_sha = body.get("sha") if code == 200 else None
    if existing_sha:
        print(f"   · 仓库里已有 README（blob {existing_sha[:7]}），将覆盖更新")
        payload["sha"] = existing_sha
        payload["branch"] = "main"
        dry_run_branch = "main"
    else:
        # 空仓库：不带 branch 让它自己建默认分支
        upper = call("GET", f"/repos/{login}/{args.repo}", token)[1].get("default_branch")
        dry_run_branch = upper or "main"
        print(f"   · 空仓库，首次提交将创建默认分支 {dry_run_branch}")

    print(f"   PUT /repos/{login}/{args.repo}/contents/README.md")
    code, body = call(
        "PUT",
        f"/repos/{login}/{args.repo}/contents/README.md",
        token,
        payload,
        dry_run=args.dry_run,
    )
    if code not in (200, 201, 0):
        print(f"   × HTTP {code} {explain(code, body)}")
        return 1
    if not args.dry_run:
        commit = body.get("commit", {})
        print(f"   ✓ 提交完成：{commit.get('sha', '')[:7]}  {commit.get('html_url', '')}")

    # ---- 4. 回读校验 ---- #
    if not args.dry_run:
        print("4. 回读校验")
        code, body = call("GET", f"/repos/{login}/{args.repo}/contents/README.md", token)
        if code != 200:
            print(f"   × 回读失败 HTTP {code}")
            return 1
        remote_size = body.get("size", 0)
        same = remote_size == len(raw)
        print(f"   远端 README：{remote_size} 字节 / 本地 {len(raw)} 字节 → "
              f"{'✓ 一致' if same else '× 不一致'}")
        code, body = call("GET", f"/repos/{login}/{args.repo}/contents?ref={dry_run_branch}", token)
        names = [f["name"] for f in body] if code == 200 else []
        print(f"   仓库根目录文件：{names}（应只有 README.md）")

    print("=" * 68)
    print(f"仓库地址：{html_url}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
