#!/bin/bash
# tb21-bench.sh — Terminal-Bench 2.1 全量/选择性 benchmark 管理脚本
#
# 用法:
#   ./scripts/tb21-bench.sh sync          # 同步代码到远端
#   ./scripts/tb21-bench.sh run [tasks]   # 启动 benchmark (默认全量89)
#   ./scripts/tb21-bench.sh status        # 查看当前运行进度
#   ./scripts/tb21-bench.sh results       # 查看 pass/fail 详细结果
#   ./scripts/tb21-bench.sh compare <dir> # 与另一次运行对比
#
# 环境变量:
#   TB_HOST     — 远端地址 (默认 root@47.254.25.238)
#   TB_REMOTE   — 远端代码路径 (默认 /root/bench_workspace/modelscope-agent-si)
#   TB_WORK     — 输出目录名 (默认 tb21_$(date +%Y%m%d_%H%M%S))
#   TB_MODEL    — 模型名 (默认 qwen3.7-max)
#   TB_BATCH    — 并发数 (默认 4)

set -euo pipefail

# ── 配置 ───────────────────────────────────────────────
HOST="${TB_HOST:-root@47.254.25.238}"
REMOTE="${TB_REMOTE:-/root/bench_workspace/modelscope-agent-si}"
LOCAL="$(cd "$(dirname "$0")/.." && pwd)"
MODEL="${TB_MODEL:-qwen3.7-max}"
BATCH="${TB_BATCH:-4}"
WORK="${TB_WORK:-tb21_$(date +%Y%m%d_%H%M%S)}"

# ── 函数 ───────────────────────────────────────────────

cmd_sync() {
    echo "=== Syncing code to $HOST:$REMOTE ==="
    rsync -az --delete \
        --exclude='__pycache__' --exclude='*.pyc' \
        --exclude='.git' --exclude='outputs' \
        --exclude='._*' --exclude='.__*' \
        --exclude='node_modules' --exclude='.venv' \
        "$LOCAL/ms_agent/" "$HOST:$REMOTE/ms_agent/"
    rsync -az \
        --exclude='__pycache__' --exclude='*.pyc' \
        --exclude='._*' \
        "$LOCAL/scripts/" "$HOST:$REMOTE/scripts/"
    rsync -az \
        --exclude='__pycache__' --exclude='*.pyc' \
        --exclude='._*' \
        "$LOCAL/tests/" "$HOST:$REMOTE/tests/"
    # 清理 macOS 资源文件
    ssh "$HOST" "cd $REMOTE && find . -name '._*' -delete 2>/dev/null; find . -name '.__*' -delete 2>/dev/null; true"
    # 验证编译
    ssh "$HOST" "cd $REMOTE && python3 -m compileall -q ms_agent/ 2>&1 | grep -v 'null bytes' | head -5; echo 'Compile check done'"
    echo "=== Sync complete ==="
}

cmd_run() {
    local tasks="${1:-}"
    local limit=89

    if [ -n "$tasks" ]; then
        limit=$(echo "$tasks" | tr ',' '\n' | wc -l | tr -d ' ')
        echo "=== Running $limit selected tasks ==="
    else
        echo "=== Running full 89-task benchmark ==="
    fi

    echo "Work dir: outputs/$WORK"
    echo "Model: $MODEL | Batch: $BATCH"

    ssh "$HOST" "mkdir -p $REMOTE/outputs/$WORK/logs"

    # 构建并上传运行脚本
    local script_content
    script_content=$(cat <<SCRIPT_EOF
#!/bin/bash
set -euo pipefail
cd $REMOTE
export PATH=/root/miniconda3/bin:/usr/local/bin:/usr/bin:/bin
export TERMINAL_BENCH_VERSION=2.1
export TERMINAL_BENCH_REGISTRY_PATH=$REMOTE/../datasets/terminal-bench-2.1-registry.json
export TERMINAL_BENCH_MODEL=$MODEL
export TERMINAL_BENCH_LIMIT=$limit
export TERMINAL_BENCH_EVAL_BATCH_SIZE=$BATCH
export EVALSCOPE_WORK_DIR=$REMOTE/outputs/$WORK
export EVALSCOPE_NO_TIMESTAMP=true
export MS_AGENT_SOURCE_ROOT=$REMOTE
${tasks:+export TERMINAL_BENCH_TASK_NAMES="$tasks"}
exec python3 scripts/run_terminal_bench_ms_agent_smoke.py
SCRIPT_EOF
    )

    echo "$script_content" | ssh "$HOST" "cat > $REMOTE/outputs/$WORK/run.sh && chmod +x $REMOTE/outputs/$WORK/run.sh"

    # 启动
    ssh "$HOST" "cd $REMOTE && nohup bash outputs/$WORK/run.sh > outputs/$WORK/logs/run.log 2>&1 &"
    echo "=== Benchmark launched (PID on remote) ==="
    echo "Check progress: $0 status"
}

cmd_status() {
    local work_dir="${1:-$WORK}"
    ssh "$HOST" "
        trials=\$(ls $REMOTE/outputs/$work_dir/trials/ 2>/dev/null | wc -l)
        review=$REMOTE/outputs/$work_dir/reviews/ms-agent__${MODEL//./\\.}/terminal_bench_v2_test.jsonl
        if [ -f \"\$review\" ]; then
            python3 -c \"
import json
p,f=0,0
with open('\$review') as fh:
    for line in fh:
        acc=json.loads(line.strip()).get('sample_score',{}).get('score',{}).get('value',{}).get('acc',0)
        if acc==1: p+=1
        else: f+=1
total=p+f
pct=p*100//total if total else 0
print(f'Trials: $trials/89 | Reviews: {total} | Pass: {p} | Fail: {f} ({pct}%)')
\"
        else
            echo \"Trials: $trials/89 | Reviews: 0 (pending)\"
        fi
        # ETA
        tail -1 $REMOTE/outputs/$work_dir/logs/run.log 2>/dev/null | sed 's/\x1b\[[0-9;]*[a-zA-Z]//g' | grep -oP '\d+/89.*' || true
    "
}

cmd_results() {
    local work_dir="${1:-$WORK}"
    ssh "$HOST" "python3 -c \"
import json
passed, failed = [], []
review = '$REMOTE/outputs/$work_dir/reviews/ms-agent__${MODEL//./\\.}/terminal_bench_v2_test.jsonl'
try:
    with open(review) as fh:
        for line in fh:
            r = json.loads(line.strip())
            task = '?'
            for m in r.get('messages', []):
                c = str(m.get('content', ''))
                if 'trials/' in c:
                    task = c.split('trials/')[-1].split('__')[0]
                    break
            acc = r.get('sample_score', {}).get('score', {}).get('value', {}).get('acc', 0)
            if acc == 1: passed.append(task)
            else: failed.append(task)
    print(f'Total: {len(passed)+len(failed)} | Pass: {len(passed)} | Fail: {len(failed)} ({len(passed)*100//(len(passed)+len(failed))}%)')
    print()
    print(f'=== FAIL ({len(failed)}) ===')
    for t in sorted(failed): print(f'  {t}')
    print(f'\n=== PASS ({len(passed)}) ===')
    for t in sorted(passed): print(f'  {t}')
except FileNotFoundError:
    print('No reviews found. Run may still be in progress.')
\""
}

cmd_compare() {
    local other_dir="$1"
    local this_dir="${2:-$WORK}"
    ssh "$HOST" "python3 -c \"
import json

def load_results(work_dir):
    results = {}
    review = f'$REMOTE/outputs/{work_dir}/reviews/ms-agent__${MODEL//./\\.}/terminal_bench_v2_test.jsonl'
    try:
        with open(review) as fh:
            for line in fh:
                r = json.loads(line.strip())
                task = '?'
                for m in r.get('messages', []):
                    c = str(m.get('content', ''))
                    if 'trials/' in c:
                        task = c.split('trials/')[-1].split('__')[0]
                        break
                acc = r.get('sample_score', {}).get('score', {}).get('value', {}).get('acc', 0)
                results[task] = acc
    except FileNotFoundError:
        pass
    return results

old = load_results('$other_dir')
new = load_results('$this_dir')

recovered = [t for t in new if new[t]==1 and old.get(t,0)==0]
regressed = [t for t in new if new[t]==0 and old.get(t,1)==1]
new_fail  = [t for t in new if new[t]==0 and t not in old]

print(f'Base ($other_dir): {sum(old.values())}/{len(old)}')
print(f'New  ($this_dir):  {sum(new.values())}/{len(new)}')
print(f'Net change: {sum(new.values()) - sum(old.values()):+d}')
print()
if recovered:
    print(f'Recovered (+{len(recovered)}):')
    for t in sorted(recovered): print(f'  ✅ {t}')
if regressed:
    print(f'\nRegressed (-{len(regressed)}):')
    for t in sorted(regressed): print(f'  ❌ {t}')
if new_fail:
    print(f'\nNew failures ({len(new_fail)}):')
    for t in sorted(new_fail): print(f'  ⚠️  {t}')
\""
}

# ── 主入口 ─────────────────────────────────────────────

case "${1:-help}" in
    sync)    cmd_sync ;;
    run)     cmd_run "${2:-}" ;;
    status)  cmd_status "${2:-$WORK}" ;;
    results) cmd_results "${2:-$WORK}" ;;
    compare) cmd_compare "${2:?需要指定对比目录}" "${3:-$WORK}" ;;
    help|*)
        echo "用法: $0 {sync|run|status|results|compare}"
        echo ""
        echo "  sync              同步本地代码到远端服务器"
        echo "  run [tasks]       启动 benchmark (逗号分隔任务名，或空=全量89)"
        echo "  status [dir]      查看运行进度"
        echo "  results [dir]     查看详细 pass/fail 结果"
        echo "  compare <old> [new]  对比两次运行结果"
        echo ""
        echo "环境变量: TB_HOST TB_REMOTE TB_WORK TB_MODEL TB_BATCH"
        echo ""
        echo "示例:"
        echo "  $0 sync && $0 run                           # 同步 + 全量跑"
        echo "  $0 run fix-git,build-pmars,chess-best-move   # 选择性跑3个任务"
        echo "  TB_WORK=tb21_test $0 run                     # 指定输出目录名"
        echo "  $0 compare tb21_old tb21_new                 # 对比两次运行"
        ;;
esac
