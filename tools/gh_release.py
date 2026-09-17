"""发布 GitHub Release：建/更新发行版 + 上传安装包附件。

用法（token 从环境变量或文件读，绝不写进仓库）：

    # 环境变量
    GH_TOKEN=ghp_xxx python tools/gh_release.py --tag v1.2.0

    # 从文件读
    python tools/gh_release.py --tag v1.2.0 --token-file F:/project/.gh_token.txt

    # 指定发行说明与附件（附件可重复）
    python tools/gh_release.py --tag v1.2.0 \
        --notes-file docs/release-notes/v1.2.0.md \
        --asset F:/project/FormatMaster_Setup_v1.2.0.exe

    # 先看看会发生什么，不做任何写操作
    python tools/gh_release.py --tag v1.2.0 --dry-run

约定：发行说明默认读 docs/release-notes/<tag>.md；标题默认与 tag 相同。

依赖：标准库，无第三方包。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://api.github.com"
UPLOAD_HOST = "https://uploads.github.com"
REPO = "senlip/FormatMaster"
ROOT = Path(__file__).resolve().parent.parent
CHUNK = 1024 * 1024  # 1 MiB

STATUS_HINT = {
    401: "token 无效、被吊销，或复制不完整（classic PAT 必须是 ghp_ + 36 位 = 40 字符）",
    403: "token 权限不足，或触发限流；确认勾选了 repo 权限",
    404: "仓库不存在，或 token 看不到它（私有仓库要 repo 权限）",
    422: "参数被拒：通常是 tag 已存在同名发行版，或附件名重复",
}


def _request(url: str, token: str, method: str = "GET", payload=None,
             headers: dict | None = None, data=None, timeout: int = 120):
    head = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "FormatMaster-release",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if headers:
        head.update(headers)
    body = data
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        head["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=body, headers=head, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = raw.decode("utf-8", "replace")
        return exc.code, parsed
    except urllib.error.URLError as exc:
        return 0, f"网络不通：{exc.reason}"


def _fail(status: int, detail) -> None:
    hint = STATUS_HINT.get(status, "")
    msg = detail.get("message") if isinstance(detail, dict) else detail
    print(f"× HTTP {status}：{msg}")
    if isinstance(detail, dict) and detail.get("errors"):
        for err in detail["errors"]:
            print(f"    {err}")
    if hint:
        print(f"  提示：{hint}")
    raise SystemExit(1)


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


def _read_token(args) -> str:
    token = ""
    if args.token_file:
        path = Path(args.token_file)
        if not path.is_file():
            print(f"× token 文件不存在：{path}")
            raise SystemExit(1)
        token = path.read_text(encoding="utf-8").strip().splitlines()[0].strip()
    if not token:
        token = os.environ.get(args.token_env, "").strip()
    if not token:
        print(f"× 没有拿到 token。用 --token-file 指定文件，或设好环境变量 {args.token_env}。")
        raise SystemExit(1)
    return token


def ensure_release(token: str, tag: str, name: str, body: str,
                   target: str, draft: bool, prerelease: bool) -> dict:
    """建发行版；已存在就更新它的说明（不重建，保住已有附件）。"""
    payload = {
        "tag_name": tag,
        "name": name,
        "body": body,
        "draft": draft,
        "prerelease": prerelease,
        "target_commitish": target,
    }
    status, data = _request(f"{API}/repos/{REPO}/releases", token, "POST", payload)
    if status in (200, 201):
        print(f"✓ 已创建发行版 {tag}（id={data['id']}）")
        return data
    if status == 422:
        status, data = _request(f"{API}/repos/{REPO}/releases/tags/{tag}", token)
        if status != 200:
            _fail(status, data)
        rid = data["id"]
        status, updated = _request(f"{API}/repos/{REPO}/releases/{rid}", token, "PATCH", payload)
        if status != 200:
            _fail(status, updated)
        print(f"✓ 发行版 {tag} 已存在，已更新说明（id={rid}）")
        return updated
    _fail(status, data)
    return {}


def existing_assets(token: str, release_id: int) -> dict:
    status, data = _request(f"{API}/repos/{REPO}/releases/{release_id}/assets", token)
    if status != 200:
        return {}
    return {a["name"]: a["id"] for a in data}


def drop_asset(token: str, asset_id: int, name: str) -> None:
    status, data = _request(f"{API}/repos/{REPO}/releases/assets/{asset_id}", token, "DELETE")
    if status in (204, 200):
        print(f"  · 已移除旧附件 {name}")
    else:
        _fail(status, data)


def upload_asset(token: str, upload_url: str, path: Path, retries: int = 3) -> None:
    """流式上传，显式给 Content-Length。

    必须显式给长度：http.client 拿到文件对象时不会自己算，
    缺了它会退化成 chunked，而 GitHub 的附件上传接口不接受。
    """
    size = path.stat().st_size
    base = upload_url.split("{")[0]
    url = f"{base}?{urllib.parse.urlencode({'name': path.name})}"

    for attempt in range(1, retries + 1):
        print(f"  ↑ 上传 {path.name}（{_human(size)}）第 {attempt}/{retries} 次…")
        started = time.monotonic()
        try:
            with path.open("rb") as fh:
                req = urllib.request.Request(
                    url,
                    data=fh,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/vnd.github+json",
                        "Content-Type": "application/octet-stream",
                        "Content-Length": str(size),
                        "User-Agent": "FormatMaster-release",
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=1800) as resp:
                    data = json.loads(resp.read())
            spent = time.monotonic() - started
            speed = size / max(spent, 0.001) / 1048576
            print(f"  ✓ {path.name} 上传完成，用时 {spent:.0f}s（{speed:.1f} MB/s）")
            print(f"    下载地址：{data.get('browser_download_url')}")
            return
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                detail = json.loads(raw)
            except Exception:
                detail = raw.decode("utf-8", "replace")
            if exc.code in (422,) and attempt < retries:
                print(f"  ! HTTP 422，稍后重试：{detail}")
                time.sleep(5 * attempt)
                continue
            _fail(exc.code, detail)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt < retries:
                print(f"  ! 传输中断（{exc}），{5 * attempt}s 后重试")
                time.sleep(5 * attempt)
                continue
            print(f"× 上传失败：{exc}")
            raise SystemExit(1)


def main() -> int:
    ap = argparse.ArgumentParser(description="发布 GitHub Release 并上传附件")
    ap.add_argument("--tag", required=True, help="形如 v1.2.0")
    ap.add_argument("--name", help="发行版标题，默认与 --tag 相同")
    ap.add_argument("--notes-file", help="发行说明 Markdown，默认 docs/release-notes/<tag>.md")
    ap.add_argument("--asset", action="append", default=[], help="要上传的附件路径（可重复）")
    ap.add_argument("--target", default="main", help="tag 未创建时指向的分支/提交，默认 main")
    ap.add_argument("--draft", action="store_true", help="建为草稿（不公开）")
    ap.add_argument("--prerelease", action="store_true", help="标为预发行")
    ap.add_argument("--token-file", help="从该文件读 token（首行）")
    ap.add_argument("--token-env", default="GH_TOKEN", help="环境变量名，默认 GH_TOKEN")
    ap.add_argument("--no-replace", action="store_true",
                    help="附件同名时不覆盖，直接跳过")
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不做写操作")
    args = ap.parse_args()

    notes = Path(args.notes_file) if args.notes_file else \
        ROOT / "docs" / "release-notes" / f"{args.tag}.md"
    body = notes.read_text(encoding="utf-8") if notes.is_file() else ""
    if not body:
        print(f"! 没找到发行说明 {notes}，将使用空说明")

    assets = [Path(a) for a in args.asset]
    print("=" * 74)
    print(f"仓库      {REPO}")
    print(f"tag       {args.tag}")
    print(f"标题      {args.name or args.tag}")
    print(f"说明      {notes if notes.is_file() else '（无）'}"
          f"{f'  {len(body)} 字' if body else ''}")
    print(f"附件      {len(assets)} 个")
    for a in assets:
        if a.is_file():
            print(f"          {a.name}  {_human(a.stat().st_size)}")
        else:
            print(f"          {a}  ← 文件不存在")
    print("=" * 74)

    missing = [a for a in assets if not a.is_file()]
    if missing:
        print("× 附件不存在，先确认路径：")
        for a in missing:
            print(f"    {a}")
        return 1

    if args.dry_run:
        print("（--dry-run：以上为计划，未做任何写操作）")
        return 0

    token = _read_token(args)
    release = ensure_release(token, args.tag, args.name or args.tag, body,
                             args.target, args.draft, args.prerelease)

    if assets:
        have = existing_assets(token, release["id"])
        for path in assets:
            if path.name in have:
                if args.no_replace:
                    print(f"  · 附件 {path.name} 已存在，按 --no-replace 跳过")
                    continue
                drop_asset(token, have[path.name], path.name)
            upload_asset(token, release["upload_url"], path)

    print()
    print(f"✓ 完成，访问 {release.get('html_url')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
