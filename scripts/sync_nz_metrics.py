"""把 nz-power-pipeline 的语义层字典同步进 NZ 领域包（方案 A：复制 + 校验，见 docs/11 第八节）。

    make sync-nz-metrics    复制成 domains/nz_power/metric_dictionary.yaml，文件头记下来源 commit 和 sha256
    make check-nz-metrics   核对两件事：① 复制过来的正文没被手改；② 和上游当前文件一致

为什么复制，不在运行时去读上游：ChemQ 是公开仓库，CI 和 Azure 部署时没有 nz-power-pipeline。
为什么上游有未提交改动就拒绝同步：文件头记的 commit 要能在上游仓库里找到同一份内容。
找不到上游仓库（比如 CI 上）时，--check 只做 ①，② 跳过并打印提示。
"""
from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "domains" / "nz_power" / "metric_dictionary.yaml"
SRC_REL = "semantic/nz_power_metrics.yaml"
HEADER = (
    "# synced from nz-power-pipeline/{src} @ {commit}\n"
    "# sha256 {sha}  (do not edit here: change it upstream, then make sync-nz-metrics)\n"
)
HEADER_LINES = HEADER.count("\n")


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(repo: Path, *args: str) -> str:
    # --no-optional-locks：只读命令不去写 .git/index.lock
    out = subprocess.run(["git", "--no-optional-locks", "-C", str(repo), *args],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def split_dest() -> tuple[str | None, bytes]:
    """返回（文件头记的 sha256，正文）。"""
    raw = DEST.read_bytes()
    parts = raw.split(b"\n", HEADER_LINES)
    head, body = parts[:HEADER_LINES], parts[-1]
    for line in head:
        text = line.decode("utf-8")
        if text.startswith("# sha256 "):
            return text.split()[2], body
    return None, raw


def sync(repo: Path) -> None:
    src = repo / SRC_REL
    if not src.exists():
        sys.exit(f"找不到 {src}：用 NZ_REPO=<nz-power-pipeline 路径> 指定")
    if git(repo, "status", "--porcelain", "--", SRC_REL):
        sys.exit(f"{src} 有未提交的改动：先在 nz-power-pipeline 提交，再同步")
    commit = git(repo, "log", "-1", "--format=%h", "--", SRC_REL)
    raw = src.read_bytes()
    tmp = DEST.with_name(DEST.name + ".tmp")
    tmp.write_bytes(HEADER.format(src=SRC_REL, commit=commit, sha=sha256(raw)).encode("utf-8") + raw)
    os.replace(tmp, DEST)
    print(f"已同步 {SRC_REL} @ {commit} → {DEST.relative_to(ROOT)}")


def check(repo: Path) -> None:
    if not DEST.exists():
        sys.exit(f"没有 {DEST.relative_to(ROOT)}：先 make sync-nz-metrics")
    recorded, body = split_dest()
    if recorded is None:
        sys.exit(f"{DEST.relative_to(ROOT)} 文件头没有 sha256：重新 make sync-nz-metrics")
    if sha256(body) != recorded:
        sys.exit(f"{DEST.relative_to(ROOT)} 被手改过（正文和文件头的 sha256 对不上）：改上游，再同步")
    src = repo / SRC_REL
    if not src.exists():
        print(f"找不到上游 {src}，只核对了本地文件没被手改")
        return
    if sha256(src.read_bytes()) != recorded:
        sys.exit("上游的指标字典变了：make sync-nz-metrics")
    print("NZ 指标字典和上游一致")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--repo", type=Path, required=True, help="nz-power-pipeline 仓库路径")
    ap.add_argument("--check", action="store_true", help="只核对，不复制")
    args = ap.parse_args()
    (check if args.check else sync)(args.repo.expanduser())


if __name__ == "__main__":
    main()
