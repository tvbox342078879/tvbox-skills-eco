#!/usr/bin/env python3
"""run.py — 入口（转发至 scripts/main.py，健康契约 v3.378）"""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "scripts"))
import main as _impl

main = _impl.main
run_selftest = getattr(_impl, 'run_selftest', None)
read_text_safe = getattr(_impl, '_read_text_safe', None) or getattr(_impl, 'read_text_safe', None)
dry_run = getattr(_impl, 'dry_run', False)

def _safe_main() -> int:
    try:
        return _impl.main()
    except Exception as e:
        print(f"[ERROR] 运行异常: {e}")
        return 1

if __name__ == "__main__":
    sys.exit(_safe_main())
