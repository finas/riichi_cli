#!/usr/bin/env bash
set -euo pipefail

# 默认运行 100 局东风战，也可通过参数自定义：
# ./run_benchmark.sh 100
# ./run_benchmark.sh 20 hanchan

GAMES="${1:-100}"
MODE="${2:-tonpuu}"
MATCHUP="${3:-mortal_vs_refined}"

echo "========================================================"
echo " 启动麻将 AI 对抗基准评测"
echo " 对局数: ${GAMES} 局 | 赛制: ${MODE} | 对阵: ${MATCHUP}"
echo " 可选对阵:"
echo "   1. mortal_vs_refined   (2 Mortal vs 2 新版安全 GreedyAI)"
echo "   2. refined_vs_baseline (2 新版 GreedyAI vs 2 旧版原始 GreedyAI - 纯算法对比)"
echo "   3. mortal_vs_baseline  (2 Mortal vs 2 旧版原始 GreedyAI)"
echo "========================================================"

python3 scripts/benchmark_mortal_vs_greedy.py --games "$GAMES" --mode "$MODE" --matchup "$MATCHUP"
