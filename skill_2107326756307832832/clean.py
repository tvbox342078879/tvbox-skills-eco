"""Clean build/run artifacts from the skill package.

SkillHub rejects packages containing binary files (.pyc, .zip, .db ...), so
`check` is meant to be run right before packaging or upload.

`data/scrape_history.json` is the user's crawl history — real data, not a build
artifact. It is never touched unless `--all` is passed explicitly.
"""

import os
import shutil
import sys

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

DIRS_TO_REMOVE = [
    "__pycache__",
    ".pytest_cache",
    "data/cache",
    "data/ima_export",
    "data/dify_export",
    "data/obsidian_export",
    "output",
]

ARTIFACT_SUFFIXES = (".pyc", ".pyo", ".zip", ".db", ".sqlite", ".sqlite3", ".log")

LOCAL_DATA_FILES = ["data/scrape_history.json"]

_SKIP_DIRS = {".git", "node_modules"}


def _rel(path: str) -> str:
    return os.path.relpath(path, PROJECT_ROOT).replace("\\", "/")


def _is_local_data(rel: str) -> bool:
    return rel in LOCAL_DATA_FILES or rel.startswith(
        tuple(f"{name}/" for name in LOCAL_DATA_FILES)
    )


def _walk():
    for root, dirs, files in os.walk(PROJECT_ROOT):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        yield root, dirs, files


def _find_artifacts(include_local_data: bool = False):
    """Return (dirs, files) that must not ship with the skill."""
    bad_dirs = []
    bad_files = []
    for root, dirs, files in _walk():
        for d in dirs:
            if d in DIRS_TO_REMOVE:
                bad_dirs.append(os.path.join(root, d))
        for f in files:
            path = os.path.join(root, f)
            rel = _rel(path)
            if f.endswith(ARTIFACT_SUFFIXES):
                bad_files.append(path)
            elif include_local_data and _is_local_data(rel):
                bad_files.append(path)
    return bad_dirs, bad_files


def clean(include_local_data: bool = False):
    removed = []
    bad_dirs, bad_files = _find_artifacts(include_local_data)
    for path in bad_dirs:
        shutil.rmtree(path, ignore_errors=True)
        removed.append(path)
    for path in bad_files:
        if os.path.exists(path):
            os.remove(path)
            removed.append(path)
    return removed


def check(include_local_data: bool = False):
    """Report artifacts without removing them. Returns the offending paths."""
    bad_dirs, bad_files = _find_artifacts(include_local_data)
    return bad_dirs + bad_files


if __name__ == "__main__":
    include_data = "--all" in sys.argv
    if "--check" in sys.argv:
        issues = check(include_data)
        if issues:
            print(f"发现 {len(issues)} 个需清理的产物:")
            for p in issues:
                print("  " + p)
            sys.exit(1)
        print("干净：无二进制/运行期产物")
    else:
        removed = clean(include_data)
        if include_data:
            print("警告：--all 已一并移除本地数据（含爬取历史）")
        print(f"已清理 {len(removed)} 项")
        sys.exit(0)
