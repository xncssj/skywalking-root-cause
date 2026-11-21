#!/usr/bin/env bash
# SkyWalking 根因分析端到端验证（对应专栏第 15 章验收清单）
# 步骤：起栈 -> 等健康 -> 造流量 -> 真实分析(analyze.py) -> 单测回归 -> LLM 报告校验
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SKILL_SCRIPTS="$(cd "${ROOT}/../skywalking-rootcause-skill/scripts" && pwd)"
ANALYZE="python3 ${SKILL_SCRIPTS}/analyze.py"
OAP_URL="http://localhost:12800/graphql"
PROM_URL="http://localhost:9090"
MINUTES="${MINUTES:-6}"

cd "${ROOT}"

echo "=== [1/6] 检查 agent 就绪 ==="
if [ ! -f demo-app/agent/skywalking-agent/skywalking-agent.jar ]; then
  echo "ERROR: 缺少 demo-app/agent/skywalking-agent，请先下载 agent（见 README）" >&2
  exit 1
fi

echo "=== [2/6] 启动堆栈 ==="
docker compose up -d --build

echo "=== [3/6] 等待 OAP 与三服务健康 ==="
echo "等待 OAP GraphQL (12800) ..."
for i in $(seq 1 60); do
  if curl -sf -X POST -H 'Content-Type: application/json' \
      -d '{"query":"query{services:listServices(layer:\"GENERAL\"){id}}"}' \
      "${OAP_URL}" >/dev/null 2>&1; then echo "OAP 就绪"; break; fi
  [ "$i" -eq 60 ] && echo "ERROR: OAP 超时" >&2 && exit 1
  sleep 5
done
for app in "8080:/order" "8081:/cart" "8082:/member"; do
  port="${app%%:*}"
  ctx="${app##*:}"
  echo "等待 app :${port} ..."
  for i in $(seq 1 60); do
    if curl -sf "http://localhost:${port}${ctx}/test/sql/real-slow" >/dev/null 2>&1; then echo "  :${port} 就绪"; break; fi
    [ "$i" -eq 60 ] && echo "ERROR: :${port} 超时" >&2 && exit 1
    sleep 3
  done
done

echo "=== [4/6] 生成真实慢查询流量（order -> cart -> member -> MySQL）==="
CONCURRENCY="${CONCURRENCY:-2}" ROUNDS="${ROUNDS:-4}" bash scripts/generate-traffic.sh

echo "=== [5/6] 真实分析：analyze.py（OAP + Prometheus 双源）==="
${ANALYZE} --base-url "${OAP_URL}" --prometheus-url "${PROM_URL}" \
  --os-instance "10.0.0.5:9100" --minutes "${MINUTES}" --json > /tmp/analyze-result.json
${ANALYZE} --base-url "${OAP_URL}" --prometheus-url "${PROM_URL}" \
  --os-instance "10.0.0.5:9100" --minutes "${MINUTES}"

echo "=== [6/6] 单测回归 + LLM 报告校验 ==="
( cd "${SKILL_SCRIPTS}/.." && python3 -m pytest tests/ -q )
python3 scripts/llm_report.py /tmp/analyze-result.json

echo "=== 完成 ==="
echo "  UI:       http://localhost:8383"
echo "  Prom:     http://localhost:9090"
echo "  分析结果: /tmp/analyze-result.json"
