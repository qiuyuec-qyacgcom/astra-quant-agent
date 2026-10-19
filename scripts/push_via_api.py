#!/usr/bin/env python3
"""经 GitHub Contents API 提交工作区改动（网络只允许短连接时的兜底通道）。

每个文件一次 PUT；同一分支连续 PUT 会依次追加提交。
"""
from __future__ import annotations

import base64
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path("/workspace/astra-quant-agent")
OWNER = "qiuyuec-qyacgcom"
REPO = "astra-quant-agent"
BRANCH = "main"


def gh(*args: str) -> str:
    r = subprocess.run(["gh", *args], capture_output=True, text=True, cwd=str(ROOT))
    if r.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args)} 失败: {r.stderr[:500]}")
    return r.stdout


def remote_sha(path: str) -> str | None:
    try:
        out = gh("api", f"repos/{OWNER}/{REPO}/contents/{path}?ref={BRANCH}", "--jq", ".sha")
        return out.strip() or None
    except RuntimeError:
        return None


def main() -> int:
    files = [ln.strip() for ln in sys.stdin.read().splitlines() if ln.strip()]
    if not files:
        print("没有待提交文件", file=sys.stderr)
        return 1
    ok, failed = [], []
    for i, rel in enumerate(files, 1):
        p = ROOT / rel
        if not p.is_file():
            failed.append((rel, "本地文件不存在"))
            continue
        b64 = base64.b64encode(p.read_bytes()).decode()
        sha = remote_sha(rel)
        msg = f"chore(release): {rel} 版本 8.8.1 / 仓库链接迁移"
        try:
            r = subprocess.run(
                ["gh", "api", "-X", "PUT", f"repos/{OWNER}/{REPO}/contents/{rel}",
                 "--input", "-", "--jq", ".commit.sha"],
                input=json.dumps({"message": msg, "content": b64, "branch": BRANCH, **({"sha": sha} if sha else {})}),
                capture_output=True, text=True, cwd=str(ROOT),
            )
            if r.returncode != 0:
                failed.append((rel, r.stderr.strip()[:300]))
            else:
                ok.append((rel, r.stdout.strip()[:7]))
                print(f"[{i}/{len(files)}] OK {rel} -> {r.stdout.strip()[:7]}")
        except Exception as exc:  # noqa: BLE001
            failed.append((rel, str(exc)[:300]))
    print(f"\n成功 {len(ok)} / 失败 {len(failed)}")
    for rel, err in failed:
        print(f"  FAIL {rel}: {err}")
    return 0 if not failed else 2


if __name__ == "__main__":
    sys.exit(main())
