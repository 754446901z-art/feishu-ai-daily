"""Commit only runtime data; a failed checkpoint must prevent message delivery."""
import argparse
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parent


def git(*args):
    result = subprocess.run(["git", *args], cwd=ROOT, text=True, encoding="utf-8",
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        # Do not echo authenticated remote URLs or transport headers.
        raise RuntimeError("Git 状态保存失败：" + args[0])
    return result.stdout.strip()


def persist(state_only=False):
    if os.getenv("GITHUB_ACTIONS") != "true":
        raise RuntimeError("persist.py 仅在 GitHub Actions 中推送；本地请审查后手动 git commit/push")
    branch = os.environ["GITHUB_REF_NAME"]
    if os.getenv("GITHUB_REF_TYPE") != "branch":
        raise RuntimeError("只允许分支保存状态")
    if git("diff", "--name-only", "--diff-filter=U"):
        raise RuntimeError("存在冲突，拒绝覆盖状态")
    # No automatic rebase of delivery journals: a concurrent human edit is a stop.
    git("fetch", "--quiet", "origin", branch)
    head = git("rev-parse", "HEAD")
    remote = git("rev-parse", "FETCH_HEAD")
    if head != remote:
        git("merge-base", "--is-ancestor", remote, head)
    if git("diff", "--cached", "--name-only"):
        raise RuntimeError("暂存区非空，拒绝夹带其他文件")
    git("config", "user.name", "github-actions[bot]")
    git("config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com")
    paths = ["state.json"] if state_only else ["state.json", "new_methods.json"]
    git("add", "--", *paths)
    if git("diff", "--cached", "--name-only"):
        git("commit", "-m", "Save discovery and delivery checkpoint [skip ci]")
    # Also retries an earlier push whose server outcome was ambiguous, without
    # replaying a message. The same commit is safe to push again.
    git("push", "--quiet", "origin", f"HEAD:refs/heads/{branch}")
    print("运行状态已持久化到 GitHub")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--state-only", action="store_true")
    args = parser.parse_args()
    try:
        persist(args.state_only)
    except Exception:
        print("状态提交失败；请检查仓库写权限或分支冲突。禁止绕过检查点补发。")
        raise SystemExit(1)
