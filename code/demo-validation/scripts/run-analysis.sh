#!/usr/bin/env bash
# =============================================================================
# run-analysis.sh —— 专栏第 15 章 §15.2 的真实 runner
# （替代文档里示意性的 hermes-agent：接收指令 -> 调 Skill(analyze.py) -> LLM 落报告）
#
# 用法：
#   ./scripts/run-analysis.sh                                    # 用默认配置分析最近 MINUTES 分钟
#   ./scripts/run-analysis.sh "帮我分析最近 6 分钟 demo 性能瓶颈"  # 自然语言指令（归一化：固定走 分析->LLM）
#
# 配置（对应 §15.2 "三个地址"，命令行/环境变量 > 默认值）：
#   SKYWALKING_BASE_URL  OAP GraphQL 地址    默认 http://localhost:12800/graphql
#   PROMETHEUS_URL       Prometheus 地址     默认 http://localhost:9090
#   OS_INSTANCE          node-exporter 实例  默认 10.0.0.5:9100（本 demo 栈固定 IP）
#   MINUTES              分析窗口（分钟）     默认 6（OAP 分钟级聚合有 ~4-6min 延迟）
#   OLLAMA_URL / OLLAMA_MODEL  透传给 llm_report.py（LLM 地址/模型，默认 localhost:11434 / deepseek-r1:1.5b）
# =============================================================================
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SKILL_SCRIPTS="$(cd "${ROOT}/../skywalking-rootcause-skill/scripts" && pwd)"
ANALYZE="python3 ${SKILL_SCRIPTS}/analyze.py"

BASE_URL="${SKYWALKING_BASE_URL:-http://localhost:12800/graphql}"
PROM_URL="${PROMETHEUS_URL:-http://localhost:9090}"
OS_INSTANCE="${OS_INSTANCE:-10.0.0.5:9100}"
MINUTES="${MINUTES:-6}"

echo ">>> [1/2] 双源采集分析：${BASE_URL} + ${PROM_URL}（OS=${OS_INSTANCE}，窗口 ${MINUTES}min）"
${ANALYZE} --base-url "${BASE_URL}" --prometheus-url "${PROM_URL}" \
  --os-instance "${OS_INSTANCE}" --minutes "${MINUTES}" --json > /tmp/analyze-result.json
${ANALYZE} --base-url "${BASE_URL}" --prometheus-url "${PROM_URL}" \
  --os-instance "${OS_INSTANCE}" --minutes "${MINUTES}"

echo ""
echo ">>> [2/2] LLM 报告生成 + 硬约束校验"
python3 "${ROOT}/scripts/llm_report.py" /tmp/analyze-result.json

echo ""
echo ">>> 完成。结构化结果已存 /tmp/analyze-result.json"
