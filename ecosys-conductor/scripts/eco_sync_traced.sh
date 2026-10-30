#!/bin/bash
# eco_sync_traced.sh —— 三件套「带留痕」包装（可验证闭环骨架·治本层）
#
# 设计目的（回应「如何确定你们真按规则走」）：
#   原 sync.sh / push_gist.py 失败静默、零留痕 → 无法验证 → 等于没做。
#   本包装在每次真正执行前后，向 /root/.codebuddy/telemetry/YYYY-MM-DD.jsonl
#   追加一条「硬指标」留痕（ts / action / rc / 可事后验证的指标），模型自报无效。
#
# 用法：bash eco_sync_traced.sh <restore|learn|gist|sync|all>
# 注意：本文件不修改你的生产 sync.sh，仅在外部包装并自行核验真实成败。
set -u
HERE=/root/.codebuddy
TL=$HERE/telemetry
mkdir -p "$TL"
DAY=$(date +%Y-%m-%d)
LOG="$TL/$DAY.jsonl"
ts=$(date -Iseconds)

log() {
  # $1=action $2=rc $3=detail
  printf '%s\t%s\trc=%s\t%s\n' "$ts" "$1" "$2" "$3" >> "$LOG"
}

action="${1:-all}"

case "$action" in
  restore)
    before=$(stat -c %Y "$HERE/memory_core.md" 2>/dev/null || echo 0)
    python3 "$HERE/memory_restore.py" >/tmp/_restore.log 2>&1
    rc=$?
    after=$(stat -c %Y "$HERE/memory_core.md" 2>/dev/null || echo 0)
    log "memory_restore" "$rc" "memory_core_mtime_delta=$((after-before))"
    ;;
  learn)
    python3 "$HERE/skills/tvbox-source-hub/scripts/link.py" learn >/tmp/_learn.log 2>&1
    rc=$?
    log "link_learn" "$rc" "see /tmp/_learn.log"
    ;;
  gist)
    python3 "$HERE/push_gist.py" >/tmp/_gist.log 2>&1
    rc=$?
    log "push_gist" "$rc" "see /tmp/_gist.log"
    ;;
  sync)
    # 包装 sync.sh，但其内部静默（|| echo / | tail），故自行核验真实成败：
    #   成功 = backup_repo HEAD 有变 且 远端 HEAD == 本地 HEAD
    cd "$HERE/backup_repo" 2>/dev/null || { log "sync" "1" "backup_repo_missing"; exit 1; }
    head_before=$(git rev-parse HEAD 2>/dev/null || echo none)
    bash "$HERE/sync.sh" >/tmp/_sync.log 2>&1
    head_after=$(git rev-parse HEAD 2>/dev/null || echo none)
    remote_head=$(git ls-remote origin HEAD 2>/dev/null | awk '{print $1}' || echo none)
    if [ "$head_before" != "$head_after" ]; then committed=1; else committed=0; fi
    if [ "$head_after" = "$remote_head" ]; then pushed=1; else pushed=0; fi
    if [ "$committed" = 1 ] && [ "$pushed" = 1 ]; then rc=0; else rc=1; fi
    log "sync" "$rc" "committed=$committed pushed=$pushed head_after=${head_after:0:8}"
    ;;
  all)
    bash "$0" restore
    bash "$0" learn
    bash "$0" gist
    bash "$0" sync
    ;;
  *)
    echo "用法: bash eco_sync_traced.sh <restore|learn|gist|sync|all>" >&2
    exit 2
    ;;
esac
