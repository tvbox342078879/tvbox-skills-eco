"""Test runner for WeChat Article Scraper."""

import os
import sys
import subprocess

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))


def main():
    """Run all tests."""
    os.chdir(PROJECT_ROOT)
    # Keep the package free of .pyc — SkillHub rejects binary files on upload.
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-v", "--tb=short"],
        cwd=PROJECT_ROOT,
        env=env,
    )
    sys.exit(result.returncode)


if __name__ == "__main__":
    main()